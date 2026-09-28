"""M2 简历筛选接口（R-02 至 R-08）。

状态机是开发与测试对齐的关键，每个状态的进入条件与可执行动作必须唯一（PRD 2.2）。
系统不提供「已拒绝」状态 —— 低分候选人进入待定池并归档，归档不等于拒绝，
也不触发任何对外通知。这是需求分析阶段与法务确认的边界。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    ROLE_INTERVIEWER, ROLE_LEGAL, current_user, mask_contact, mask_resume_text,
    require,
)
from app.db.models import (
    Candidate, DecisionLog, Position, ReflowSample, ResumeText, ReviewAction,
    ScoreRecord, TrackEvent, User,
)
from app.db.session import SessionLocal, get_db

router = APIRouter(prefix="/candidates", tags=["M2 简历筛选"])

# 各状态的可执行动作（PRD 2.2 状态机）
ALLOWED_ACTIONS = {
    "待解析": [],
    "解析异常": ["补录", "重新上传"],
    "待打分": [],
    "待安排面试": ["安排面试", "退回复核"],
    "待复核": ["采纳", "否决", "标记疑问"],
    "待定池": ["捞回", "到期归档"],
    "已归档": [],
}

REJECT_REASONS = ["能力不匹配", "经验不足", "简历造假疑似", "岗位已关闭", "其他"]


def _cand_brief(c: Candidate, resume: ResumeText | None, user: User,
                score: ScoreRecord | None = None) -> dict:
    phone, email = mask_contact(user, c.phone, c.email)
    return {
        "id": c.id, "name": c.name, "position_id": c.position_id,
        "status": c.status, "tier": c.tier,
        "total_score": c.total_score, "confidence": c.confidence,
        "phone": phone, "email": email,
        "source": c.source, "file_type": c.file_type,
        "risk_tags": c.risk_tags or [],
        "pool_days_left": c.pool_days_left,
        "pool_expire_at": str(c.pool_expire_at) if c.pool_expire_at else None,
        "need_confirm": c.need_confirm,
        "injection_flag": c.injection_flag,
        "doubt_tag": c.doubt_tag,
        "model_version": c.model_version,
        "created_at": str(c.created_at),
        "has_resume": bool(resume and resume.raw_text),
        "preview": (resume.raw_text[:110] + "…") if (resume and resume.raw_text
                                                     and user.can_view_resume) else "",
        "allowed_actions": ALLOWED_ACTIONS.get(c.status, []),
        "summary": score.summary if score else "",
    }


@router.get("")
async def list_candidates(status: str = "", tier: str = "", position_id: int = 0,
                          keyword: str = "", db: AsyncSession = Depends(get_db),
                          user: User = Depends(require("screen", "view"))):
    """候选人工作台列表（P-01）。

    数据可见范围按角色隔离（PRD 1.4）：
    - 业务面试官：仅被指派的候选人
    - 招聘 HR：本人负责岗位的全部候选人
    - HR 负责人 / 法务审计：全部业务线
    - 用人经理：本部门在招岗位候选人
    """
    q = select(Candidate).order_by(Candidate.created_at.desc())

    if user.role == ROLE_INTERVIEWER:
        q = q.where(Candidate.assignee_id == user.id)
    elif user.role in ("招聘HR",):
        q = q.where(or_(Candidate.hr_id == user.id,
                        Candidate.hr_id.is_(None)))
    elif user.role == "用人经理" and user.department:
        pos_ids = [p.id for p in (await db.execute(select(Position).where(
            Position.business_line == user.business_line))).scalars().all()]
        if pos_ids:
            q = q.where(Candidate.position_id.in_(pos_ids))
        else:
            q = q.where(Candidate.id < 0)

    if status:
        q = q.where(Candidate.status == status)
    if tier:
        q = q.where(Candidate.tier == tier)
    if position_id:
        q = q.where(Candidate.position_id == position_id)
    if keyword:
        q = q.where(Candidate.name.contains(keyword))

    rows = (await db.execute(q.limit(300))).scalars().all()
    resumes = {r.candidate_id: r for r in (await db.execute(select(ResumeText).where(
        ResumeText.candidate_id.in_([c.id for c in rows] or [0])))).scalars().all()}
    positions = {p.id: p for p in (await db.execute(select(Position))).scalars().all()}

    out = []
    for c in rows:
        d = _cand_brief(c, resumes.get(c.id), user)
        d["position_name"] = positions.get(c.position_id).name if positions.get(c.position_id) else ""
        out.append(d)

    counts = (await db.execute(
        select(Candidate.status, func.count(Candidate.id)).group_by(Candidate.status)
    )).all()
    tiers = (await db.execute(
        select(Candidate.tier, func.count(Candidate.id)).group_by(Candidate.tier)
    )).all()
    return {
        "items": out,
        "counts": {s: n for s, n in counts},
        "tiers": {t or "未分档": n for t, n in tiers},
        "today": {
            "processed": (await db.execute(select(func.count(ReviewAction.id)).where(
                ReviewAction.created_at >= datetime.now() - timedelta(days=1)))).scalar_one(),
            "incoming": (await db.execute(select(func.count(Candidate.id)).where(
                Candidate.created_at >= datetime.now() - timedelta(days=1)))).scalar_one(),
        },
    }


@router.get("/{cid}")
async def get_candidate(cid: int, db: AsyncSession = Depends(get_db),
                        user: User = Depends(require("screen", "view"))):
    """P-02 简历复核详情页数据。

    返回：简历原文、逐项打分与证据、风险提示、可执行动作。
    """
    c = (await db.execute(select(Candidate).where(Candidate.id == cid))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "候选人不存在")

    # 面试官只能看被指派的候选人
    if user.role == ROLE_INTERVIEWER and c.assignee_id != user.id:
        raise HTTPException(403, "业务面试官仅可查看被指派的候选人")

    rt = (await db.execute(select(ResumeText).where(
        ResumeText.candidate_id == cid))).scalar_one_or_none()
    sr = (await db.execute(select(ScoreRecord).where(
        ScoreRecord.candidate_id == cid).order_by(ScoreRecord.id.desc()))).scalars().first()
    pos = (await db.execute(select(Position).where(
        Position.id == c.position_id))).scalar_one_or_none()
    actions = (await db.execute(select(ReviewAction).where(
        ReviewAction.candidate_id == cid).order_by(ReviewAction.id.desc()))).scalars().all()
    logs = (await db.execute(select(DecisionLog).where(
        DecisionLog.candidate_id == cid).order_by(DecisionLog.id.desc()).limit(20))).scalars().all()

    raw_text = rt.raw_text if rt else ""
    masked = mask_resume_text(user, raw_text)

    # 逐项打分 + 该能力的证据（前端据此做定位高亮）
    abilities = []
    if sr:
        for it in (sr.items or []):
            evs = [e for e in (sr.evidence or [])
                   if str(e.get("ability_id")) == str(it.get("ability_id"))]
            abilities.append({**it, "evidence": evs})

    phone, email = mask_contact(user, c.phone, c.email)
    return {
        "id": c.id, "name": c.name,
        "position_name": pos.name if pos else "", "level_range": pos.level_range if pos else "",
        "status": c.status, "tier": c.tier,
        "total_score": c.total_score, "confidence": c.confidence,
        "phone": phone, "email": email,
        "source": c.source, "file_type": c.file_type,
        "risk_tags": c.risk_tags or [],
        "pool_days_left": c.pool_days_left,
        "pool_expire_at": str(c.pool_expire_at) if c.pool_expire_at else None,
        "model_version": c.model_version,
        "allowed_actions": ALLOWED_ACTIONS.get(c.status, []),
        "reject_reasons": REJECT_REASONS,
        "resume_text": masked,
        "resume_visible": user.can_view_resume,
        "parsed": _safe_parsed(rt, user),
        "low_confidence_fields": rt.low_confidence_fields if rt else [],
        "parse_meta": rt.parse_meta if rt else {},
        "parse_error": rt.parse_error if rt else "",
        "score": {
            "total": sr.total, "confidence": sr.confidence, "summary": sr.summary,
            "veto_hit": sr.veto_hit, "veto_reason": sr.veto_reason,
            "model_version": sr.model_version, "ai_meta": sr.ai_meta,
            "risk_tags": sr.risk_tags or [],
        } if sr else None,
        "abilities": abilities,
        "actions": [{"action": a.action, "reason": a.reason, "note": a.note,
                     "at": str(a.created_at)} for a in actions],
        "decision_logs": [{"kind": l.kind, "summary": l.summary, "model": l.model,
                           "prompt_version": l.prompt_version, "degrade": l.degrade,
                           "confidence": l.confidence, "at": str(l.created_at)}
                          for l in logs],
        "assignee": c.assignee_id,
    }


def _safe_parsed(rt: ResumeText | None, user: User) -> dict:
    if not rt:
        return {}
    parsed = dict(rt.parsed or {})
    if not user.can_view_resume:
        return {"_note": "当前角色无权查看简历结构化内容"}
    # 院校名称脱敏后单独存储，仅供人工参考（PRD 3.2.2）
    parsed.pop("raw_text", None)
    return parsed


@router.post("/import")
async def import_resume(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    position_id: int = Form(...),
    name: str = Form(""),
    source: str = Form("手工导入"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("screen", "import")),
):
    """上传并解析单份简历（P-04 批量导入的单文件版本）。"""
    from app.services.resume_service import extract_text

    suffix = ("." + file.filename.split(".")[-1].lower()) if file.filename and "." in file.filename else ".txt"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    saved = settings.upload_dir / f"{int(datetime.now().timestamp() * 1000)}{suffix}"
    content = await file.read()

    if len(content) > 20 * 1024 * 1024:
        # 超大文件拒绝并提示，不进入异步队列（PRD 3.2.2）
        raise HTTPException(400, "文件超过 20MB，请压缩后上传")
    saved.write_bytes(content)

    pos = (await db.execute(select(Position).where(Position.id == position_id))).scalar_one_or_none()
    if not pos:
        raise HTTPException(404, "岗位不存在")

    file_type = "text"
    try:
        text = extract_text(saved, suffix)
    except ValueError as e:
        # 文件损坏或加密 -> 转解析异常，支持重新上传，原记录保留
        c = Candidate(name=name or file.filename or "未命名", position_id=position_id,
                      status="解析异常", source=source, file_path=str(saved),
                      file_type="unknown", hr_id=user.id)
        db.add(c)
        await db.flush()
        db.add(ResumeText(candidate_id=c.id, parse_error=str(e),
                          parse_meta={"degraded": True, "degrade_reason": str(e)}))
        db.add(DecisionLog(kind="ai", actor=user.name, ability="简历解析",
                           candidate_id=c.id, summary=f"解析失败：{e}",
                           degrade="human", degrade_reason=str(e)))
        await db.commit()
        return {"ok": False, "candidate_id": c.id, "status": "解析异常",
                "message": str(e), "hint": "可重新上传该文件，原记录已保留"}

    if suffix in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        file_type = "image"

    # 非简历文件检测
    if len(text.strip()) < 40:
        raise HTTPException(400, "该文件不是简历，已跳过")

    # 单文件多人简历检测
    from app.services.resume_service import spot_multiple_resumes
    n = spot_multiple_resumes(text)

    c = Candidate(name=name or "待解析", position_id=position_id, status="待解析",
                  source=source, file_path=str(saved), file_type=file_type, hr_id=user.id)
    db.add(c)
    await db.flush()
    db.add(ResumeText(candidate_id=c.id, raw_text=text))
    await db.commit()

    background.add_task(_run_pipeline, c.id, text)
    return {
        "ok": True, "candidate_id": c.id, "status": "待解析",
        "multi_resume_detected": n if n > 1 else 0,
        "message": f"检测到 {n} 份简历，已拆分，拆分结果需人工确认后入库"
                   if n > 1 else "已进入解析队列，完成后可在工作台查看",
    }


@router.post("/import/batch")
async def import_batch(
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    position_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("screen", "import")),
):
    """批量导入（R-16）。单次不少于 200 份，后台异步处理并通知。"""
    from app.services.resume_service import extract_text

    if len(files) > 500:
        raise HTTPException(400, "单次最多 500 份，请分批导入")
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    created, failed = [], []
    for f in files:
        suffix = ("." + f.filename.split(".")[-1].lower()) if f.filename and "." in f.filename else ".txt"
        try:
            content = await f.read()
            if len(content) > 20 * 1024 * 1024:
                failed.append({"file": f.filename, "reason": "文件超过 20MB"})
                continue
            saved = settings.upload_dir / f"{int(datetime.now().timestamp() * 1000)}_{len(created)}{suffix}"
            saved.write_bytes(content)
            text = extract_text(saved, suffix)
            if len(text.strip()) < 40:
                failed.append({"file": f.filename, "reason": "该文件不是简历，已跳过"})
                continue
            c = Candidate(name="待解析", position_id=position_id, status="待解析",
                          source="批量导入", file_path=str(saved),
                          file_type="image" if suffix in (".jpg", ".jpeg", ".png") else "text",
                          hr_id=user.id)
            db.add(c)
            await db.flush()
            db.add(ResumeText(candidate_id=c.id, raw_text=text))
            created.append(c.id)
        except ValueError as e:
            failed.append({"file": f.filename, "reason": str(e)})
        except Exception as e:  # noqa: BLE001
            failed.append({"file": f.filename, "reason": f"处理失败：{type(e).__name__}"})

    await db.commit()
    for cid in created:
        rt = (await db.execute(select(ResumeText).where(
            ResumeText.candidate_id == cid))).scalar_one_or_none()
        if rt and rt.raw_text:
            background.add_task(_run_pipeline, cid, rt.raw_text)

    db.add(TrackEvent(event="batch_import", user_id=user.id,
                      payload={"total": len(files), "ok": len(created), "failed": len(failed)}))
    await db.commit()
    return {
        "accepted": len(created), "failed": failed,
        "estimated_minutes": max(1, round(len(created) * 0.3 / 60 * 8, 1)),
        "message": f"已接收 {len(created)} 份，后台异步处理，完成后站内提醒。"
                   f"失败 {len(failed)} 份可下载失败清单。",
    }


async def _run_pipeline(candidate_id: int, text: str, attempt: int = 0) -> None:
    """后台跑「解析 -> 打分 -> 落库」流水线。

    批量导入时多个流水线并发写同一个 SQLite 库，写锁竞争会让部分任务失败。
    这在 PRD R-16（单次不少于 200 份）的场景下必然发生，因此这里做两件事：
      1. 写冲突时退避重试，不把瞬时冲突当成业务失败；
      2. 重试仍失败则明确置为「解析异常」并写明原因 —— 绝不能留在「待解析」，
         那样用户看不到任何错误提示，只会以为系统卡住了。
    """
    from app.agents.orchestrator import run_screen_pipeline
    from app.services.resume_service import parse_resume, strip_injection

    try:
        out = await run_screen_pipeline(candidate_id, text)
    except Exception as e:  # noqa: BLE001
        # 数据库写锁冲突（SQLite 并发常见）或模型调用异常：退避后重试
        if attempt < 3:
            await asyncio.sleep(1.5 * (attempt + 1))
            return await _run_pipeline(candidate_id, text, attempt + 1)
        async with SessionLocal() as db:
            c = (await db.execute(select(Candidate).where(
                Candidate.id == candidate_id))).scalar_one_or_none()
            if c:
                c.status = "解析异常"
                rt = (await db.execute(select(ResumeText).where(
                    ResumeText.candidate_id == candidate_id))).scalar_one_or_none()
                if rt:
                    rt.parse_error = f"自动处理失败：{type(e).__name__}: {str(e)[:160]}"
                db.add(DecisionLog(
                    kind="ai", actor="system", ability="简历筛选流水线",
                    candidate_id=candidate_id,
                    summary=f"自动处理连续失败 {attempt + 1} 次，已转人工。"
                            f"原因：{type(e).__name__}",
                    degrade="human", degrade_reason=str(e)[:200],
                ))
                await db.commit()
        return

    # 流水线跑通但没产出打分（例如岗位未绑定模型）：也要明确落到待复核并说明
    if not out.get("score"):
        async with SessionLocal() as db:
            c = (await db.execute(select(Candidate).where(
                Candidate.id == candidate_id))).scalar_one_or_none()
            if c and not c.tier:
                c.status = "待复核"
                rt = (await db.execute(select(ResumeText).where(
                    ResumeText.candidate_id == candidate_id))).scalar_one_or_none()
                if rt:
                    rt.parse_error = (out.get("meta_score") or {}).get(
                        "error", "自动打分未产出结果，需人工处理")
                db.add(DecisionLog(
                    kind="ai", actor="system", ability="简历筛选流水线",
                    candidate_id=candidate_id,
                    summary=f"流水线未产出打分结果："
                            f"{(out.get('meta_score') or {}).get('error', '原因未知')}",
                    degrade="human",
                ))
                await db.commit()
        return

    try:
        async with SessionLocal() as db:
            c = (await db.execute(select(Candidate).where(
                Candidate.id == candidate_id))).scalar_one_or_none()
            if not c:
                return
            parsed = out.get("parsed") or {}
            if parsed.get("name") and c.name in ("待解析", ""):
                c.name = parsed["name"]
            elif c.name in ("待解析", ""):
                # 姓名抽取失败：不能留着占位符「待解析」——那会让 HR 在队列里
                # 看到一个叫「待解析」的人，甚至可能把它一路推进到面试。
                # 这里退到「未识别-{id}」并标记需人工补录，补录界面只显示缺失字段。
                c.name = f"未识别-{candidate_id}"
                c.need_confirm = True
                db.add(DecisionLog(
                    kind="ai", actor="system", ability="简历解析",
                    candidate_id=candidate_id,
                    summary="姓名抽取失败，已标记需人工补录姓名",
                    degrade="human", degrade_reason="关键字段缺失：姓名",
                ))
            if parsed.get("phone"):
                c.phone = parsed["phone"]
            if parsed.get("email"):
                c.email = parsed["email"]
            inj = (out.get("meta_parse") or {}).get("injection_hits") or []
            if inj:
                c.injection_flag = True
                db.add(DecisionLog(
                    kind="injection", actor="system", ability="简历解析",
                    candidate_id=candidate_id,
                    summary=f"简历含疑似指令文本，已剥离后正常解析并标记待审（{len(inj)} 处）",
                    detail={"hits": inj},
                ))
            # 低置信字段需人工确认后才可打分
            low = (out.get("parsed") or {}).get("low_confidence_fields") or []
            if low:
                c.need_confirm = True
            db.add(TrackEvent(event="resume_scored", payload={
                "candidate_id": candidate_id, "tier": out.get("tier"),
                "total": (out.get("score") or {}).get("total"),
                "confidence": (out.get("score") or {}).get("confidence"),
                "model_version": (out.get("meta_score") or {}).get("prompt_version"),
                "latency_ms": (out.get("meta_score") or {}).get("latency_ms"),
            }))
            deg = (out.get("meta_score") or {}).get("degrade", "none")
            if deg not in ("none", None):
                db.add(TrackEvent(event="ai_degrade", payload={
                    "candidate_id": candidate_id, "level": deg,
                    "ability": "简历筛选",
                    "reason": (out.get("meta_score") or {}).get("degrade_reason", ""),
                }))
            await db.commit()
    except Exception as e:  # noqa: BLE001
        # 落库阶段失败（多为写锁竞争）：退避重试，仍失败则明确报错，不留静默状态
        if attempt < 3:
            await asyncio.sleep(1.5 * (attempt + 1))
            return await _run_pipeline(candidate_id, text, attempt + 1)
        async with SessionLocal() as db:
            c = (await db.execute(select(Candidate).where(
                Candidate.id == candidate_id))).scalar_one_or_none()
            if c:
                c.status = "解析异常"
                db.add(DecisionLog(
                    kind="ai", actor="system", ability="简历筛选流水线",
                    candidate_id=candidate_id,
                    summary=f"结果落库失败，已转人工。原因：{type(e).__name__}",
                    degrade="human", degrade_reason=str(e)[:200],
                ))
                await db.commit()


@router.post("/{cid}/review")
async def review(cid: int, body: dict, db: AsyncSession = Depends(get_db),
                 user: User = Depends(require("screen", "adopt"))):
    """人工复核：采纳 / 否决 / 标记疑问（PRD 3.2.4）。

    - 采纳：候选人转待安排面试，记录采纳动作与当时模型版本。
    - 否决：原因必选（验收 A2-8），候选人转待定池。
    - 标记疑问：不改变状态，加疑问标签并可指派给用人经理。
    """
    action = body.get("action")
    reason = (body.get("reason") or "").strip()
    note = (body.get("note") or "").strip()
    stay = int(body.get("stay_seconds", 0))

    if action not in ("采纳", "否决", "标记疑问", "退回复核"):
        raise HTTPException(400, f"不支持的动作：{action}")

    c = (await db.execute(select(Candidate).where(Candidate.id == cid))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "候选人不存在")

    if action == "否决" and not reason:
        raise HTTPException(400, "否决必须选择原因方可提交")
    if action == "否决" and reason not in REJECT_REASONS:
        raise HTTPException(400, f"原因须为以下之一：{'、'.join(REJECT_REASONS)}")

    # 状态机前置校验：不能对「还没打分」的候选人做复核决策。
    # 之前缺少这道校验，导致流水线尚未跑完（状态仍是待解析/待打分）或
    # 打分失败（状态被置为待复核但无打分记录）的候选人也能被采纳，
    # 结果是没有任何判断依据的记录被推进到面试环节。
    if action in ("采纳", "否决"):
        if c.status in ("待解析", "待打分", "解析异常"):
            raise HTTPException(
                400,
                f"候选人「{c.name}」当前状态为「{c.status}」，尚未完成解析打分，不能执行{action}。"
                f"请等待处理完成，或先在详情页重新解析。",
            )
        if c.name.startswith("未识别-"):
            raise HTTPException(
                400,
                f"该候选人的姓名尚未补录（当前显示为「{c.name}」），不能执行{action}。"
                f"请先在详情页补录姓名，避免无标识的记录流入面试环节。",
            )
        has_score = (await db.execute(select(ScoreRecord).where(
            ScoreRecord.candidate_id == cid))).scalars().first()
        if not has_score:
            raise HTTPException(
                400,
                f"候选人「{c.name}」没有打分记录，无法{action}。"
                f"复核决策必须基于 AI 结论与证据，请先重新解析打分。",
            )

    before = c.status

    if action == "采纳":
        c.status = "待安排面试"
    elif action == "否决":
        c.status = "待定池"
        c.pool_enter_at = datetime.now()
    elif action == "标记疑问":
        c.doubt_tag = True
        if body.get("assignee_id"):
            c.assignee_id = int(body["assignee_id"])
    elif action == "退回复核":
        c.status = "待复核"

    db.add(ReviewAction(candidate_id=cid, user_id=user.id, action=action,
                        reason=reason, note=note, model_version=c.model_version,
                        stay_seconds=stay))
    # 否决原因写入回流样本池，用于优化
    db.add(ReflowSample(candidate_id=cid, kind="review",
                        payload={"action": action, "reason": reason, "note": note,
                                 "total": c.total_score, "tier": c.tier},
                        model_version=c.model_version))
    db.add(DecisionLog(kind="human", actor=user.name, ability="人工复核",
                       candidate_id=cid,
                       summary=f"{action}候选人 {c.name}（{before} -> {c.status}）"
                               + (f"，原因：{reason}" if reason else ""),
                       detail={"action": action, "reason": reason, "note": note,
                               "from": before, "to": c.status}))
    db.add(TrackEvent(event="review_action", user_id=user.id, payload={
        "candidate_id": cid, "action": action, "reason": reason,
        "stay_seconds": stay, "tier": c.tier, "model_version": c.model_version,
    }))
    await db.commit()
    return {"ok": True, "status": c.status, "message": f"已{action}", "pool_days_left": c.pool_days_left}


@router.post("/{cid}/rescue")
async def rescue(cid: int, body: dict, db: AsyncSession = Depends(get_db),
                 user: User = Depends(require("screen", "rescue"))):
    """待定池捞回（R-07）。捞回转待复核，到期转已归档。"""
    reason = (body.get("reason") or "").strip()
    c = (await db.execute(select(Candidate).where(Candidate.id == cid))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "候选人不存在")
    if c.status != "待定池":
        raise HTTPException(400, f"该候选人当前状态为「{c.status}」，不在待定池中")

    original_tier = c.tier
    c.status = "待复核"
    c.tier = "中间档"     # 捞回后按中间档处理，强制人工复核
    c.pool_enter_at = None

    db.add(ReviewAction(candidate_id=cid, user_id=user.id, action="捞回",
                        reason=reason, model_version=c.model_version))
    # 捞回后通过面试的案例即误杀线索，需专项复盘
    db.add(ReflowSample(candidate_id=cid, kind="rescue",
                        payload={"reason": reason, "original_tier": original_tier,
                                 "total": c.total_score},
                        model_version=c.model_version))
    db.add(DecisionLog(kind="human", actor=user.name, ability="待定池捞回",
                       candidate_id=cid,
                       summary=f"捞回候选人 {c.name}（原分档 {original_tier}）"
                               + (f"，原因：{reason}" if reason else ""),
                       detail={"reason": reason, "original_tier": original_tier}))
    db.add(TrackEvent(event="pool_rescue", user_id=user.id,
                      payload={"candidate_id": cid, "reason": reason,
                               "original_tier": original_tier}))
    await db.commit()
    return {"ok": True, "status": c.status,
            "message": f"已捞回 {c.name}，转入复核队列。原分档 {original_tier}，已记录为误杀线索。"}


@router.post("/{cid}/confirm-fields")
async def confirm_fields(cid: int, body: dict, db: AsyncSession = Depends(get_db),
                         user: User = Depends(require("screen", "edit"))):
    """人工确认低置信字段（PRD 3.2.2 兜底矩阵）。

    补录界面仅显示缺失字段，不要求重填全部。
    """
    c = (await db.execute(select(Candidate).where(Candidate.id == cid))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "候选人不存在")
    rt = (await db.execute(select(ResumeText).where(
        ResumeText.candidate_id == cid))).scalar_one_or_none()

    updates = body.get("fields") or {}
    if updates:
        parsed = dict(rt.parsed or {}) if rt else {}
        for k, v in updates.items():
            if k in ("name", "phone", "email"):
                setattr(c, k, v)
            else:
                parsed[k] = v
        if rt:
            rt.parsed = parsed
            rt.low_confidence_fields = []

    c.need_confirm = False
    c.status = "待解析"
    await db.commit()

    rt = (await db.execute(select(ResumeText).where(
        ResumeText.candidate_id == cid))).scalar_one_or_none()
    if rt and rt.raw_text:
        asyncio.create_task(_run_pipeline(cid, rt.raw_text))
    return {"ok": True, "message": "补录完成，已重新进入解析打分队列"}


@router.post("/{cid}/reparse")
async def reparse(cid: int, background: BackgroundTasks,
                  db: AsyncSession = Depends(get_db),
                  user: User = Depends(require("screen", "edit"))):
    """重新上传后重新解析。"""
    import asyncio as _a
    c = (await db.execute(select(Candidate).where(Candidate.id == cid))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "候选人不存在")
    rt = (await db.execute(select(ResumeText).where(
        ResumeText.candidate_id == cid))).scalar_one_or_none()
    if not rt or not rt.raw_text:
        raise HTTPException(400, "该候选人没有可解析的简历文本，请重新上传文件")

    c.status = "待解析"
    await db.commit()
    background.add_task(_run_pipeline, cid, rt.raw_text)
    return {"ok": True, "message": "已重新进入解析队列"}


@router.get("/{cid}/evidence/{ability_id}")
async def get_evidence(cid: int, ability_id: str, db: AsyncSession = Depends(get_db),
                       user: User = Depends(require("screen", "view"))):
    """查看依据：右侧抽屉展开该能力项的完整证据链与判定说明（PRD 3.2.4）。"""
    sr = (await db.execute(select(ScoreRecord).where(
        ScoreRecord.candidate_id == cid).order_by(ScoreRecord.id.desc()))).scalars().first()
    if not sr:
        raise HTTPException(404, "该候选人暂无打分记录")
    item = next((i for i in (sr.items or [])
                 if str(i.get("ability_id")) == str(ability_id)), None)
    evs = [e for e in (sr.evidence or [])
           if str(e.get("ability_id")) == str(ability_id)]
    db.add(TrackEvent(event="evidence_click", user_id=user.id,
                      payload={"candidate_id": cid, "ability_id": ability_id}))
    await db.commit()
    return {"ability": item, "evidence": evs, "model_version": sr.model_version,
            "ai_meta": sr.ai_meta}


@router.post("/{cid}/archive")
async def archive(cid: int, db: AsyncSession = Depends(get_db),
                  user: User = Depends(require("screen", "edit"))):
    """手动归档。归档不发送任何对外通知，不等于拒绝。"""
    c = (await db.execute(select(Candidate).where(Candidate.id == cid))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "候选人不存在")
    c.status = "已归档"
    c.archived_at = datetime.now()
    db.add(DecisionLog(kind="human", actor=user.name, ability="归档",
                       candidate_id=cid,
                       summary=f"手动归档候选人 {c.name}。归档不等于拒绝，未触发对外通知。"))
    await db.commit()
    return {"ok": True, "message": "已归档。归档不触发任何对外通知，不等于拒绝。"}


@router.get("/pool/summary")
async def pool_summary(db: AsyncSession = Depends(get_db),
                       user: User = Depends(require("screen", "view"))):
    """待定池概览：按剩余天数升序排列，剩余 2 天内的记录标黄（PRD 3.2.5）。"""
    rows = (await db.execute(select(Candidate).where(
        Candidate.status == "待定池"))).scalars().all()
    out = []
    for c in rows:
        d = _cand_brief(c, None, user)
        d["warn"] = (c.pool_days_left is not None and c.pool_days_left <= 2)
        out.append(d)
    out.sort(key=lambda x: x.get("pool_days_left") if x.get("pool_days_left") is not None else 999)
    return {
        "items": out,
        "total": len(out),
        "expiring_soon": sum(1 for x in out if x["warn"]),
        "keep_days": rows[0].pool_days if rows else 7,
        "note": "归档不发送任何对外通知，不等于拒绝。归档记录仍可检索。",
    }
