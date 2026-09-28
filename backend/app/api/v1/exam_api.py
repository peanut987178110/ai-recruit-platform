"""培训考核接口：资料上传、组卷、考试、判卷、账号指派。

反作弊的服务端部分都在这里：
- 开考即冻结试卷（ExamAttempt.paper），交卷按同一份卷子判
- 倒计时以服务端 deadline_at 为准，前端显示只是参考
- 题目下发前剔除答案（strip_answers）
- 交卷幂等，过期或重复提交被拒
- 风险分只影响是否进人工复核队列，不影响成绩
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import ROLE_NEWCOMER, current_user, require
from app.db.models import (
    AbilityItem, AbilityModel, DecisionLog, ExamAssignment, ExamAttempt,
    ExamQuestionRecord, ExamSubmission, Position, ReflowSample, TrainingMaterial,
    TrainingPlan, TrackEvent, User,
)
from app.db.session import get_db
from app.services import exam_service as EX
from app.services.training_service import (
    FULL_SCORE, build_radar, gen_exam, gen_outline, judge_subjective,
)

router = APIRouter(prefix="/training", tags=["M4 培训考核"])

# 考试时长上限与默认值（分钟）
EXAM_MINUTES_DEFAULT = 60
EXAM_MINUTES_MAX = 180


# ==================== 元信息 ====================

@router.get("/meta")
async def meta(user: User = Depends(current_user)):
    """考试相关枚举，供前端展示。"""
    return {
        "signal_labels": EX.SIGNAL_LABEL,
        "signal_weights": EX.SIGNAL_WEIGHTS,
        "risk_thresholds": {"watch": EX.RISK_WATCH, "suspect": EX.RISK_SUSPECT},
        "exam_minutes_default": EXAM_MINUTES_DEFAULT,
        "exam_minutes_max": EXAM_MINUTES_MAX,
        "note": "客户端信号只作线索，是否作废由带教人复核决定。",
    }


# ==================== 培训资料（发布者上传）====================

@router.get("/materials")
async def list_materials(plan_id: int = 0, db: AsyncSession = Depends(get_db),
                         user: User = Depends(current_user)):
    q = select(TrainingMaterial).order_by(TrainingMaterial.id.desc())
    if plan_id:
        q = q.where(TrainingMaterial.plan_id == plan_id)
    rows = (await db.execute(q.limit(100))).scalars().all()
    return [{
        "id": m.id, "plan_id": m.plan_id, "title": m.title, "filename": m.filename,
        "file_type": m.file_type, "char_count": m.char_count, "chunk_count": len(m.chunks or []),
        "preview": (m.content or "")[:180], "created_at": str(m.created_at),
    } for m in rows]


@router.post("/materials")
async def upload_material(
    file: UploadFile = File(...),
    title: str = Form(""),
    plan_id: int = Form(0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("training", "edit")),
):
    """上传公司文档资料，作为 AI 出题的语料。

    支持 PDF / Word / 纯文本 / Markdown / Excel，走与简历解析同一套文本抽取。
    """
    from app.rag.splitter import split_text
    from app.services.resume_service import extract_text

    suffix = ("." + file.filename.split(".")[-1].lower()) if file.filename and "." in file.filename else ".txt"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    saved = settings.upload_dir / f"material_{int(datetime.now().timestamp() * 1000)}{suffix}"
    content = await file.read()

    if len(content) > 30 * 1024 * 1024:
        raise HTTPException(400, "文件超过 30MB，请拆分后再上传")
    saved.write_bytes(content)

    try:
        text = extract_text(saved, suffix)
    except ValueError as e:
        raise HTTPException(400, f"无法读取该文件：{e}") from None

    if len(text.strip()) < 100:
        raise HTTPException(400, "文档内容过少（不足 100 字），无法用于出题")

    row = TrainingMaterial(
        plan_id=plan_id or None,
        title=title.strip() or (file.filename or "未命名资料").rsplit(".", 1)[0],
        filename=file.filename or "", file_type=suffix.lstrip("."),
        content=text, chunks=split_text(text), char_count=len(text),
        uploaded_by=user.id,
    )
    db.add(row)
    await db.flush()

    warning = EX.material_quality_warning(text)
    db.add(DecisionLog(
        kind="human", actor=user.name, ability="培训资料",
        summary=f"上传培训资料「{row.title}」（{len(text)} 字，{len(row.chunks)} 个片段）",
    ))
    await db.commit()
    return {
        "ok": True, "id": row.id, "title": row.title,
        "char_count": len(text), "chunk_count": len(row.chunks),
        "warning": warning,
        "message": f"已上传「{row.title}」，AI 生成大纲与题库时会优先使用这份资料。"
                   + (f" {warning}" if warning else ""),
    }


@router.delete("/materials/{mid}")
async def delete_material(mid: int, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require("training", "edit"))):
    m = (await db.execute(select(TrainingMaterial).where(
        TrainingMaterial.id == mid))).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "资料不存在")
    await db.delete(m)
    await db.commit()
    return {"ok": True}


# ==================== 生成方案（优先用上传的资料）====================

def _material_context(materials: list[TrainingMaterial], max_chars: int = 5200) -> str:
    """把上传资料拼成提示词上下文。"""
    if not materials:
        return ""
    parts, used = [], 0
    for m in materials:
        block = f"[资料] {m.title}\n{m.content}"
        if used + len(block) > max_chars:
            block = block[:max(0, max_chars - used)]
        if not block.strip():
            break
        parts.append(block)
        used += len(block)
        if used >= max_chars:
            break
    return "\n\n".join(parts)


# ==================== 指派给新人 ====================

@router.get("/assignments")
async def list_assignments(plan_id: int = 0, db: AsyncSession = Depends(get_db),
                           user: User = Depends(current_user)):
    q = select(ExamAssignment).order_by(ExamAssignment.id.desc())
    if plan_id:
        q = q.where(ExamAssignment.plan_id == plan_id)
    rows = (await db.execute(q.limit(200))).scalars().all()
    users = {u.id: u for u in (await db.execute(select(User))).scalars().all()}
    plans = {p.id: p for p in (await db.execute(select(TrainingPlan))).scalars().all()}

    out = []
    for a in rows:
        u = users.get(a.user_id)
        # 已用次数按「同一方案 + 同一考生」统计
        attempts = (await db.execute(select(func.count(ExamAttempt.id)).where(
            ExamAttempt.plan_id == a.plan_id,
            ExamAttempt.user_id == a.user_id,
            ExamAttempt.status.in_(["已交卷", "已过期", "已作废"])))).scalar_one()
        out.append({
            "id": a.id, "plan_id": a.plan_id,
            "plan_title": plans.get(a.plan_id).title if plans.get(a.plan_id) else "",
            "user_id": a.user_id, "userid": u.userid if u else "",
            "name": u.name if u else "", "role": u.role if u else "",
            "status": a.status, "due_at": str(a.due_at or ""),
            "max_attempts": a.max_attempts, "used_attempts": attempts,
            "note": a.note, "created_at": str(a.created_at),
        })
    return out


@router.get("/newcomers")
async def list_newcomers(db: AsyncSession = Depends(get_db),
                         user: User = Depends(require("training", "assign"))):
    """可作为指派对象的新人列表。"""
    rows = (await db.execute(select(User).where(
        User.role == ROLE_NEWCOMER, User.active == True))).scalars().all()  # noqa: E712
    return [{"id": u.id, "userid": u.userid, "name": u.name,
             "department": u.department, "business_line": u.business_line} for u in rows]


@router.post("/assign")
async def create_assignment(body: dict, db: AsyncSession = Depends(get_db),
                            user: User = Depends(require("training", "assign"))):
    """把培训方案指派给新人（可多选）。"""
    plan_id = int(body.get("plan_id", 0))
    user_ids = [int(x) for x in (body.get("user_ids") or [])]
    if not plan_id or not user_ids:
        raise HTTPException(400, "请选择培训方案与至少一名新人")

    plan = (await db.execute(select(TrainingPlan).where(
        TrainingPlan.id == plan_id))).scalar_one_or_none()
    if not plan:
        raise HTTPException(404, "培训方案不存在")
    if plan.status != "已发布":
        raise HTTPException(400, "只能指派已发布的培训方案，请先发布")

    due = None
    if body.get("due_days"):
        due = datetime.now() + timedelta(days=int(body["due_days"]))

    created, skipped = [], []
    for uid in user_ids:
        u = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        if not u:
            skipped.append(uid)
            continue
        exists = (await db.execute(select(ExamAssignment).where(
            ExamAssignment.plan_id == plan_id,
            ExamAssignment.user_id == uid))).scalars().first()
        if exists:
            skipped.append(u.name)
            continue
        db.add(ExamAssignment(
            plan_id=plan_id, user_id=uid, assigned_by=user.id, due_at=due,
            max_attempts=int(body.get("max_attempts", 1) or 1),
            note=str(body.get("note", ""))[:200],
        ))
        created.append(u.name)

    db.add(DecisionLog(
        kind="human", actor=user.name, ability="培训指派",
        summary=f"把「{plan.title}」指派给 {len(created)} 名新人：{'、'.join(created) or '无'}",
    ))
    await db.commit()
    return {
        "ok": True, "created": created, "skipped": skipped,
        "message": f"已指派给 {len(created)} 人。"
                   + (f" 跳过 {len(skipped)} 人（已指派或不存在）。" if skipped else ""),
    }


@router.delete("/assignments/{aid}")
async def delete_assignment(aid: int, db: AsyncSession = Depends(get_db),
                            user: User = Depends(require("training", "assign"))):
    a = (await db.execute(select(ExamAssignment).where(
        ExamAssignment.id == aid))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "指派记录不存在")
    await db.delete(a)
    await db.commit()
    return {"ok": True}


# ==================== 我的培训（新人视角）====================

@router.get("/my")
async def my_training(db: AsyncSession = Depends(get_db),
                      user: User = Depends(current_user)):
    """新人自己的培训与考核列表。

    只返回指派给当前账号的记录 —— 这是在查询层做的隔离，
    不依赖前端过滤。带教人/HR 调用时返回空列表（他们没有「我的培训」）。
    """
    rows = (await db.execute(select(ExamAssignment).where(
        ExamAssignment.user_id == user.id).order_by(ExamAssignment.id.desc()))).scalars().all()
    plans = {p.id: p for p in (await db.execute(select(TrainingPlan))).scalars().all()}

    out = []
    for a in rows:
        plan = plans.get(a.plan_id)
        if not plan:
            continue
        attempts = (await db.execute(select(ExamAttempt).where(
            ExamAttempt.plan_id == a.plan_id,
            ExamAttempt.user_id == user.id))).scalars().all()
        done = [x for x in attempts if x.status in ("已交卷", "已过期")]
        best_sub = None
        for x in done:
            if x.submission_id:
                s = (await db.execute(select(ExamSubmission).where(
                    ExamSubmission.id == x.submission_id))).scalar_one_or_none()
                if s and (best_sub is None or (s.objective_score or 0) > (best_sub.objective_score or 0)):
                    best_sub = s

        ongoing = next((x for x in attempts if x.status == "进行中"), None)
        now = datetime.now()
        expired = bool(ongoing and ongoing.deadline_at < now)
        if ongoing and expired:
            ongoing.status = "已过期"
            ongoing = None
            await db.commit()

        out.append({
            "assignment_id": a.id,
            "plan_id": plan.id, "title": plan.title,
            # 方案级设置一并下发，新人看得到「限时多久、多少分算过」，
            # 而不是开始考试后才发现
            "description": plan.description or "",
            "start_date": str(plan.start_date or ""),
            "end_date": str(plan.end_date or ""),
            "exam_minutes": plan.exam_minutes or 60,
            "pass_score": plan.pass_score if plan.pass_score is not None else 60,
            "outline": plan.outline or [],
            "status": a.status,
            "due_at": str(a.due_at or ""),
            "overdue": bool(a.due_at and a.due_at < now and a.status not in ("已通过",)),
            "max_attempts": a.max_attempts,
            "used_attempts": len(done),
            "can_attempt": len(done) < a.max_attempts,
            "ongoing_attempt_id": ongoing.id if ongoing else None,
            "seconds_left": (int((ongoing.deadline_at - now).total_seconds())
                             if ongoing else None),
            "last_score": {
                "objective": best_sub.objective_score if best_sub else None,
                "objective_full": best_sub.objective_full if best_sub else None,
                "final": best_sub.final_score if best_sub else None,
                "confirmed": best_sub.mentor_confirmed if best_sub else False,
                "radar": best_sub.radar if best_sub else {},
            } if best_sub else None,
        })
    return {"items": out, "user": {"name": user.name, "role": user.role}}


# ==================== 考试（反作弊核心）====================

@router.post("/plans/{pid}/start")
async def start_exam(pid: int, body: dict | None = None,
                     db: AsyncSession = Depends(get_db),
                     user: User = Depends(current_user)):
    """开考。冻结试卷、服务端计时、下发不含答案的题目。

    返回的题目**已剔除答案与解析** —— 这是整个反作弊体系的前提。
    """
    body = body or {}
    plan = (await db.execute(select(TrainingPlan).where(
        TrainingPlan.id == pid))).scalar_one_or_none()
    if not plan:
        raise HTTPException(404, "培训方案不存在")

    # 1) 必须是自己的指派
    assignment = (await db.execute(select(ExamAssignment).where(
        ExamAssignment.plan_id == pid,
        ExamAssignment.user_id == user.id))).scalars().first()

    # 1.5) 培训周期校验：未开始与已结束都不允许开考。
    # 周期由发布者设定，是培训节奏的一部分，不该由考生自行决定何时开考。
    _now0 = datetime.now()
    if plan.start_date and _now0 < plan.start_date:
        raise HTTPException(
            400,
            f"本次培训自 {plan.start_date:%Y-%m-%d} 开始，尚未到开放时间。",
        )
    if plan.end_date and _now0 > plan.end_date:
        raise HTTPException(
            400,
            f"本次培训已于 {plan.end_date:%Y-%m-%d} 结束，无法再参加考核。"
            f"如仍需作答，请联系带教人调整截止时间。",
        )

    # 2) 已有进行中的考试：直接返回它，避免刷新页面就重新计时
    ongoing = (await db.execute(select(ExamAttempt).where(
        ExamAttempt.plan_id == pid,
        ExamAttempt.user_id == user.id,
        ExamAttempt.status == "进行中").order_by(ExamAttempt.id.desc()))).scalars().first()

    now = datetime.now()
    if ongoing:
        if ongoing.deadline_at < now:
            ongoing.status = "已过期"
            await db.commit()
            ongoing = None
        else:
            return await _attempt_payload(db, ongoing, resumed=True)

    if ongoing is None:
        # 3) 限次
        done = (await db.execute(select(func.count(ExamAttempt.id)).where(
            ExamAttempt.plan_id == pid,
            ExamAttempt.user_id == user.id,
            ExamAttempt.status.in_(["已交卷", "已过期"])))).scalar_one()
        limit = assignment.max_attempts if assignment else 1
        if done >= limit:
            raise HTTPException(
                400,
                f"该考核已用完 {limit} 次机会，无法再次作答。如确需重考，请联系带教人增加次数。",
            )

        # 4) 组卷
        qs = (await db.execute(select(ExamQuestionRecord).where(
            ExamQuestionRecord.plan_id == pid))).scalars().all()
        if not qs:
            raise HTTPException(400, "该方案还没有题目，请先在培训方案页生成题库")

        pool = [{
            "id": q.id, "qtype": q.qtype, "stem": q.stem, "options": q.options or [],
            "answer": q.answer, "explanation": q.explanation,
            "difficulty": q.difficulty, "ability_id": q.ability_id,
            "ability_name": q.ability_name, "ref": q.ref,
            "full_score": FULL_SCORE.get(q.qtype, 10.0),
        } for q in qs]

        # 组卷：每份卷子抽客观题 + 主观题，数量取题库存量的合理子集
        counts = {}
        for q in pool:
            counts[q["qtype"]] = counts.get(q["qtype"], 0) + 1
        counts = {k: (v if k in ("单选", "多选") else max(1, v // 2))
                  for k, v in counts.items()}

        paper, option_order = EX.build_paper(pool, counts,
                                             seed=random.randint(0, 10**9))
        # 时长取自方案，不接受客户端传值 —— 否则考生可以给自己加时间
        minutes = max(5, min(int(plan.exam_minutes or EXAM_MINUTES_DEFAULT),
                             EXAM_MINUTES_MAX))
        attempt = ExamAttempt(
            plan_id=pid, user_id=user.id, status="进行中",
            paper=paper, option_order=option_order,
            started_at=now, deadline_at=now + timedelta(minutes=minutes),
        )
        db.add(attempt)
        await db.flush()

        if assignment and assignment.status == "待开始":
            assignment.status = "进行中"

        db.add(DecisionLog(
            kind="human", actor=user.name, ability="考试",
            summary=f"{user.name} 开始作答《{plan.title}》，"
                    f"{len(paper)} 道题，限时 {minutes} 分钟",
        ))
        db.add(TrackEvent(event="exam_start", user_id=user.id, payload={
            "plan_id": pid, "paper_size": len(paper), "minutes": minutes}))
        await db.commit()

    return await _attempt_payload(db, ongoing or attempt, resumed=False)


async def _attempt_payload(db: AsyncSession, attempt: ExamAttempt, resumed: bool) -> dict:
    """组装下发给考生的试卷。**不含答案**。"""
    rows = (await db.execute(select(ExamQuestionRecord).where(
        ExamQuestionRecord.plan_id == attempt.plan_id))).scalars().all()
    qs = {q.id: q for q in rows}

    items = []
    for qid in attempt.paper or []:
        q = qs.get(int(qid)) if str(qid).isdigit() else None
        if not q:
            continue
        order = (attempt.option_order or {}).get(str(qid))
        opts = q.options or []
        if order:
            # 按本次乱序呈现选项，考生看到的是打乱后的顺序
            paired = [(order[i], opts[order[i]]) for i in range(len(order))
                      if 0 <= order[i] < len(opts)]
            opts = [text for _i, text in paired]
        items.append({
            "id": q.id, "qtype": q.qtype, "stem": q.stem,
            "options": opts, "difficulty": q.difficulty,
            "ability_name": q.ability_name,
            "full_score": FULL_SCORE.get(q.qtype, 10.0),
        })

    now = datetime.now()
    return {
        "attempt_id": attempt.id,
        "resumed": resumed,
        "plan_id": attempt.plan_id,
        "status": attempt.status,
        "started_at": str(attempt.started_at),
        "deadline_at": str(attempt.deadline_at),
        # 服务端时间，供前端校准倒计时 —— 前端本地时间不可信
        "server_now": str(now),
        "seconds_left": max(0, int((attempt.deadline_at - now).total_seconds())),
        "draft_answers": attempt.draft_answers or {},
        "questions": items,
        "total_questions": len(items),
        "note": "答案由服务端保管，交卷后判分。切屏等行为会被记录并汇总给带教人。",
    }


@router.post("/attempts/{aid}/save")
async def save_progress(aid: int, body: dict, db: AsyncSession = Depends(get_db),
                        user: User = Depends(current_user)):
    """暂存作答进度，防止刷新或意外断线丢失。"""
    a = await _get_attempt(db, aid, user)
    if a.status != "进行中":
        raise HTTPException(400, f"该次作答状态为「{a.status}」，无法保存")
    if a.deadline_at < datetime.now():
        a.status = "已过期"
        await db.commit()
        raise HTTPException(400, "考试时间已结束，无法继续作答")

    drafts = body.get("answers") or {}
    # 只接受试卷内的题号，防止塞入无关数据
    valid = {str(x) for x in (a.paper or [])}
    a.draft_answers = {k: str(v)[:2000] for k, v in drafts.items() if str(k) in valid}
    await db.commit()
    return {"ok": True, "saved": len(a.draft_answers),
            "seconds_left": max(0, int((a.deadline_at - datetime.now()).total_seconds()))}


@router.get("/attempts/{aid}/paper")
async def get_paper(aid: int, db: AsyncSession = Depends(get_db),
                    user: User = Depends(current_user)):
    """取回进行中的试卷。

    刷新页面、换浏览器、甚至换设备都走这里 —— 服务端按 attempt 里冻结的
    paper 与 option_order 重新组装同一份卷子，题目与选项顺序都不会变。
    这样「刷新换一份简单卷子」这条路就被堵死了。
    """
    a = await _get_attempt(db, aid, user)
    if a.status != "进行中":
        raise HTTPException(400, f"本次作答状态为「{a.status}」，无法继续答题")
    if a.deadline_at < datetime.now():
        a.status = "已过期"
        await db.commit()
        raise HTTPException(400, "考试时间已结束")
    return await _attempt_payload(db, a, resumed=True)


@router.post("/attempts/{aid}/heartbeat")
async def heartbeat(aid: int, body: dict | None = None,
                    db: AsyncSession = Depends(get_db),
                    user: User = Depends(current_user)):
    """心跳：上报作弊信号，并返回服务端剩余时间。

    前端每 20 秒左右调一次。用服务端时间做权威，
    前端改本地时钟也无法延长考试。
    """
    body = body or {}
    a = await _get_attempt(db, aid, user)

    signals = EX.normalize_signals(body.get("signals") or [])
    if signals:
        a.cheat_signals = (a.cheat_signals or []) + signals
        # 实时更新风险分，让带教人能在考试进行中就看到异常
        a.risk_score, a.risk_level = EX.compute_risk(
            a.cheat_signals, int((datetime.now() - a.started_at).total_seconds()),
            len(a.paper or []))
        await db.commit()

    now = datetime.now()
    left = int((a.deadline_at - now).total_seconds())
    if left <= 0 and a.status == "进行中":
        a.status = "已过期"
        await db.commit()

    return {
        "server_now": str(now),
        "seconds_left": max(0, left),
        "status": a.status if left > 0 else "已过期",
        "expired": left <= 0,
        "risk_level": a.risk_level,
    }


async def _get_attempt(db: AsyncSession, aid: int, user: User) -> ExamAttempt:
    """取作答记录并校验归属 —— 不能操作别人的考试。"""
    a = (await db.execute(select(ExamAttempt).where(
        ExamAttempt.id == aid))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "作答记录不存在")
    if a.user_id != user.id and not (user.is_super or user.role == "带教人"):
        raise HTTPException(403, "不能操作他人的考试记录")
    return a


@router.post("/attempts/{aid}/submit")
async def submit_exam(aid: int, body: dict, db: AsyncSession = Depends(get_db),
                      user: User = Depends(current_user)):
    """交卷判分。

    反作弊相关的处理：
    - 幂等：已交卷的直接返回原结果，不重复判分
    - 超时：允许交卷但标记，且不计入额外时间
    - 判分用开考时冻结的 paper 与 option_order，按同一份卷子算
    - 选项乱序的答案先还原再判分
    """
    a = await _get_attempt(db, aid, user)

    if a.status == "已交卷":
        sub = (await db.execute(select(ExamSubmission).where(
            ExamSubmission.id == a.submission_id))).scalar_one_or_none() if a.submission_id else None
        return {"ok": True, "already_submitted": True,
                "submission_id": a.submission_id,
                "objective_score": sub.objective_score if sub else None,
                "objective_full": sub.objective_full if sub else None,
                "note": "本次已交卷，未重复判分。"}

    if a.status == "已作废":
        raise HTTPException(400, "本次作答已被带教人作废，无法交卷")

    now = datetime.now()
    overtime = a.deadline_at < now

    raw_answers = body.get("answers") or {}
    signals = EX.normalize_signals(body.get("signals") or [])
    all_signals = (a.cheat_signals or []) + signals
    if overtime:
        all_signals.append({"kind": "fast_submit", "label": "超时交卷",
                            "at": 0, "detail": "在截止时间之后提交"})

    # 取题目（含答案，仅在服务端使用）
    qs = {q.id: q for q in (await db.execute(select(ExamQuestionRecord).where(
        ExamQuestionRecord.plan_id == a.plan_id))).scalars().all()}

    duration = int((now - a.started_at).total_seconds())
    judged: list = []
    qmap: dict[str, dict] = {}
    subjective: list[dict] = []
    obj_score = obj_full = 0.0

    for qid_raw in a.paper or []:
        q = qs.get(int(qid_raw)) if str(qid_raw).isdigit() else None
        if not q:
            continue
        order = (a.option_order or {}).get(str(qid_raw)) or []
        given = str(raw_answers.get(str(qid_raw), "") or "")[:2000]
        full = FULL_SCORE.get(q.qtype, 10.0)
        qmap[str(q.id)] = {"ability_id": q.ability_id, "ability_name": q.ability_name,
                           "qtype": q.qtype}

        if q.qtype in ("单选", "多选"):
            # 关键一步：把考生看到的乱序字母还原成题库原始顺序再判分
            normalized = EX.unmap_answer(given, order) if order else given
            score, note, hit, miss = EX.grade_objective(q.qtype, q.answer, normalized, full)
            obj_score += score
            obj_full += full
            from app.core.schemas import JudgeItem
            judged.append(JudgeItem(
                question_id=str(q.id), qtype=q.qtype, score=score, full_score=full,
                method="规则自动判", hit_points=hit, miss_points=miss, comment=note,
            ))
        else:
            # 主观题：把还原后的答案交给 AI 做要点覆盖分析，不给终评
            subjective.append({
                "id": str(q.id), "qtype": q.qtype, "stem": q.stem,
                "ref_answer": q.answer, "explanation": q.explanation,
                "answer": given or "（未作答）",
            })

    sub_meta: dict = {}
    if subjective:
        subs, sub_meta = await judge_subjective(qa_pairs=subjective)
        judged.extend(subs)

    radar = build_radar(
        [{"id": q.id, "name": q.ability_name} for q in qs.values() if q.ability_name],
        judged, qmap)

    # 风险分：结合时长与全部信号
    risk_score, risk_level = EX.compute_risk(all_signals, duration, len(a.paper or []))

    submission = ExamSubmission(
        plan_id=a.plan_id, trainee_name=user.name, user_id=user.id,
        answers={k: str(v)[:2000] for k, v in raw_answers.items()},
        judge={"items": [j.model_dump() for j in judged]},
        objective_score=round(obj_score, 1), objective_full=round(obj_full, 1),
        radar=radar, ai_meta=sub_meta,
    )
    db.add(submission)
    await db.flush()

    a.status = "已交卷"
    a.submitted_at = now
    a.duration_seconds = duration
    a.cheat_signals = all_signals
    a.risk_score = risk_score
    a.risk_level = risk_level
    a.submission_id = submission.id
    a.draft_answers = {}

    assignment = (await db.execute(select(ExamAssignment).where(
        ExamAssignment.plan_id == a.plan_id,
        ExamAssignment.user_id == user.id))).scalars().first()
    if assignment:
        assignment.status = "已完成"

    db.add(ReflowSample(kind="exam", payload={
        "plan_id": a.plan_id, "trainee": user.name, "user_id": user.id,
        "objective_score": submission.objective_score,
        "objective_full": submission.objective_full,
        "radar": radar, "risk_level": risk_level, "risk_score": risk_score}))
    db.add(TrackEvent(event="exam_submit", user_id=user.id, payload={
        "plan_id": a.plan_id, "objective_score": submission.objective_score,
        "objective_full": submission.objective_full,
        "risk_level": risk_level, "duration": duration,
        "signal_count": len(all_signals)}))
    db.add(DecisionLog(
        kind="human", actor=user.name, ability="考试",
        summary=f"{user.name} 交卷：客观题 {submission.objective_score}/"
                f"{submission.objective_full}，用时 {duration // 60} 分 {duration % 60} 秒"
                + (f"，风险等级 {risk_level}" if risk_level != "正常" else ""),
        detail={"risk_score": risk_score, "risk_level": risk_level,
                "signals": EX.summarize_signals(all_signals)},
    ))
    await db.commit()

    return {
        "ok": True,
        "submission_id": submission.id,
        "objective_score": submission.objective_score,
        "objective_full": submission.objective_full,
        "duration_seconds": duration,
        "overtime": overtime,
        "risk_level": risk_level,
        "items": [j.model_dump() for j in judged],
        "radar": radar,
        "need_human_final": True,
        "note": "客观题已按规则自动判卷。主观题仅提供要点覆盖分析，"
                "最终分数需带教人给出后确认。"
                + (" 本次超出截止时间，已标记。" if overtime else ""),
    }


@router.get("/attempts/{aid}/result")
async def attempt_result(aid: int, db: AsyncSession = Depends(get_db),
                         user: User = Depends(current_user)):
    """查看某次作答的结果与（给带教人看的）风险详情。"""
    a = await _get_attempt(db, aid, user)
    sub = None
    if a.submission_id:
        sub = (await db.execute(select(ExamSubmission).where(
            ExamSubmission.id == a.submission_id))).scalar_one_or_none()

    is_reviewer = user.is_super or user.role in ("带教人", "HR负责人")
    return {
        "attempt_id": a.id, "status": a.status,
        "started_at": str(a.started_at), "submitted_at": str(a.submitted_at or ""),
        "duration_seconds": a.duration_seconds,
        "risk_level": a.risk_level, "risk_score": a.risk_score,
        "review_note": a.review_note,
        "objective_score": sub.objective_score if sub else None,
        "objective_full": sub.objective_full if sub else None,
        "final_score": sub.final_score if sub else None,
        "mentor_confirmed": sub.mentor_confirmed if sub else False,
        "judge": sub.judge if sub else None,
        "radar": sub.radar if sub else {},
        # 风险明细只给复核者看，考生看不到自己被记录了什么
        "signals": (EX.summarize_signals(a.cheat_signals or []) if is_reviewer else None),
        "note": "风险信号仅作线索，是否作废由带教人复核决定。" if is_reviewer else "",
    }


# ==================== 带教人复核 ====================

@router.get("/review/queue")
async def review_queue(plan_id: int = 0, db: AsyncSession = Depends(get_db),
                       user: User = Depends(require("training", "grade"))):
    """待复核队列：按风险分降序，只列出需要人看的。"""
    q = select(ExamAttempt).where(ExamAttempt.status == "已交卷")
    if plan_id:
        q = q.where(ExamAttempt.plan_id == plan_id)
    rows = (await db.execute(q.order_by(
        ExamAttempt.risk_score.desc(), ExamAttempt.id.desc()).limit(200))).scalars().all()

    users = {u.id: u for u in (await db.execute(select(User))).scalars().all()}
    plans = {p.id: p for p in (await db.execute(select(TrainingPlan))).scalars().all()}
    subs = {s.id: s for s in (await db.execute(select(ExamSubmission).where(
        ExamSubmission.id.in_([r.submission_id for r in rows if r.submission_id] or [0])
    ))).scalars().all()}

    out = []
    for a in rows:
        s = subs.get(a.submission_id)
        if s and s.mentor_confirmed and a.risk_level == "正常":
            continue   # 已终评且无异常的不再占用复核队列
        out.append({
            "attempt_id": a.id, "submission_id": a.submission_id, "plan_id": a.plan_id,
            "plan_title": plans.get(a.plan_id).title if plans.get(a.plan_id) else "",
            "user_id": a.user_id,
            "name": users.get(a.user_id).name if users.get(a.user_id) else "",
            "userid": users.get(a.user_id).userid if users.get(a.user_id) else "",
            "duration_seconds": a.duration_seconds,
            "risk_score": a.risk_score, "risk_level": a.risk_level,
            "signals": EX.summarize_signals(a.cheat_signals or []),
            "objective_score": s.objective_score if s else None,
            "objective_full": s.objective_full if s else None,
            "final_score": s.final_score if s else None,
            "mentor_confirmed": s.mentor_confirmed if s else False,
            "submitted_at": str(a.submitted_at or ""),
        })
    return {
        "items": out,
        "counts": {
            "total": len(out),
            "suspect": sum(1 for x in out if x["risk_level"] == "可疑"),
            "watch": sum(1 for x in out if x["risk_level"] == "关注"),
            "pending_final": sum(1 for x in out if not x["mentor_confirmed"]),
        },
        "note": "风险等级由客户端信号汇总得出，只作线索。切屏可能是弹窗或输入法导致，"
                "请结合信号明细与作答内容判断。",
    }


@router.post("/attempts/{aid}/void")
async def void_attempt(aid: int, body: dict, db: AsyncSession = Depends(get_db),
                       user: User = Depends(require("training", "grade"))):
    """作废某次作答（判定作弊）。作废后该次不计成绩，并返还一次机会。"""
    a = (await db.execute(select(ExamAttempt).where(
        ExamAttempt.id == aid))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "作答记录不存在")

    note = str(body.get("note", ""))[:500]
    a.status = "已作废"
    a.review_note = note
    a.reviewed_by = user.id

    assignment = (await db.execute(select(ExamAssignment).where(
        ExamAssignment.plan_id == a.plan_id,
        ExamAssignment.user_id == a.user_id))).scalars().first()
    if assignment:
        # 返还一次机会：作废不应剥夺考生重考的权利
        assignment.max_attempts += 1
        assignment.status = "待开始"

    target = (await db.execute(select(User).where(User.id == a.user_id))).scalar_one_or_none()
    db.add(DecisionLog(
        kind="human", actor=user.name, ability="考试作废",
        summary=f"{user.name} 作废了 {target.name if target else a.user_id} 的作答"
                f"（风险 {a.risk_level} {a.risk_score} 分）"
                + (f"，理由：{note}" if note else ""),
        detail={"risk_score": a.risk_score,
                "signals": EX.summarize_signals(a.cheat_signals or [])},
    ))
    await db.commit()
    return {"ok": True,
            "message": f"已作废该次作答，并返还一次重考机会。"
                       f"考生可在「我的培训」中重新作答。"}


@router.post("/submissions/{sid}/confirm")
async def mentor_confirm(sid: int, body: dict, db: AsyncSession = Depends(get_db),
                         user: User = Depends(require("training", "grade"))):
    """带教人给出终评分数并确认。考核成绩回流至 M5，用于权重复盘（R-18）。"""
    s = (await db.execute(select(ExamSubmission).where(
        ExamSubmission.id == sid))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "考核记录不存在")

    try:
        final = float(body.get("final_score"))
    except (TypeError, ValueError):
        raise HTTPException(400, "请填写终评分数") from None
    if not (0 <= final <= 100):
        raise HTTPException(400, "终评分数范围为 0 至 100")

    s.final_score = final
    s.mentor_confirmed = True

    # 及格线取方案设定，不再写死 60
    _plan = (await db.execute(select(TrainingPlan).where(
        TrainingPlan.id == s.plan_id))).scalar_one_or_none()
    _pass = (_plan.pass_score if _plan and _plan.pass_score is not None else 60)

    assignment = (await db.execute(select(ExamAssignment).where(
        ExamAssignment.plan_id == s.plan_id,
        ExamAssignment.user_id == s.user_id))).scalars().first()
    if assignment:
        assignment.status = "已通过" if final >= _pass else "已完成"

    db.add(ReflowSample(kind="exam", payload={
        "plan_id": s.plan_id, "trainee": s.trainee_name,
        "objective_score": s.objective_score, "final_score": final,
        "radar": s.radar, "confirmed_by": user.name}))
    db.add(DecisionLog(kind="human", actor=user.name, ability="培训考核终评",
                       summary=f"带教人确认 {s.trainee_name} 的考核终评分数：{final}",
                       detail={"objective_score": s.objective_score, "final_score": final}))
    await db.commit()
    return {"ok": True, "final_score": final}


@router.post("/submissions/{sid}/probation")
async def write_probation(sid: int, body: dict, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require("training", "grade"))):
    """试用期表现回写（R-18）。"""
    s = (await db.execute(select(ExamSubmission).where(
        ExamSubmission.id == sid))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "考核记录不存在")

    passed = bool(body.get("passed", True))
    db.add(ReflowSample(kind="probation", payload={
        "plan_id": s.plan_id, "trainee": s.trainee_name, "passed": passed,
        "note": body.get("note", ""), "objective_score": s.objective_score,
        "final_score": s.final_score, "radar": s.radar}))
    db.add(DecisionLog(kind="human", actor=user.name, ability="试用期回流",
                       summary=f"{s.trainee_name} 试用期评估：{'通过' if passed else '未通过'}"))
    await db.commit()
    return {"ok": True, "message": "已回写至 M5，用于季度权重复盘。"}
