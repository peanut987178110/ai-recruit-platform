"""M4 培训考核接口（R-11、R-15）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ROLE_MENTOR, current_user, require
from app.db.models import (
    AbilityItem, AbilityModel, DecisionLog, ExamAssignment, ExamAttempt,
    ExamQuestionRecord, ExamSubmission, Position, ReflowSample, TrackEvent,
    TrainingMaterial, TrainingPlan, User,
)
from app.db.session import get_db
from app.services.training_service import (
    FULL_SCORE, build_radar, gen_exam, gen_outline, judge_objective, judge_subjective,
)

router = APIRouter(prefix="/training", tags=["M4 培训考核"])

# 未勾选资料时，最多回退取资料库里的几份，避免把整库都塞进提示词
LIBRARY_FALLBACK_MAX = 5


def _material_text(mats: list, max_chars: int = 5200) -> str:
    """把上传的公司资料拼成提示词上下文。

    与全局知识库分开：这些资料是发布者为这份方案专门准备的，
    出题时应当优先于通用 SOP 被引用。
    """
    if not mats:
        return ""
    parts, used = [], 0
    for m in mats:
        block = f"[公司资料] {m.title}\n{m.content}"
        if used + len(block) > max_chars:
            block = block[:max(0, max_chars - used)]
        if not block.strip():
            break
        parts.append(block)
        used += len(block)
        if used >= max_chars:
            break
    return "\n\n".join(parts)


@router.get("/plans")
async def list_plans(db: AsyncSession = Depends(get_db),
                     user: User = Depends(current_user)):
    rows = (await db.execute(select(TrainingPlan).order_by(
        TrainingPlan.id.desc()))).scalars().all()
    positions = {p.id: p for p in (await db.execute(select(Position))).scalars().all()}
    subs = (await db.execute(select(ExamSubmission))).scalars().all()
    sub_count: dict[int, int] = {}
    for s in subs:
        sub_count[s.plan_id] = sub_count.get(s.plan_id, 0) + 1
    qcount: dict[int, int] = {}
    for q in (await db.execute(select(ExamQuestionRecord))).scalars().all():
        qcount[q.plan_id] = qcount.get(q.plan_id, 0) + 1

    return [{
        "id": p.id, "title": p.title, "status": p.status,
        "position_name": positions.get(p.position_id).name if positions.get(p.position_id) else "",
        "business_line": positions.get(p.position_id).business_line if positions.get(p.position_id) else "",
        "description": p.description or "",
        "start_date": str(p.start_date or ""),
        "end_date": str(p.end_date or ""),
        "exam_minutes": p.exam_minutes or 60,
        "pass_score": p.pass_score if p.pass_score is not None else 60,
        "chapter_count": len(p.outline or []),
        "question_count": qcount.get(p.id, 0),
        "submission_count": sub_count.get(p.id, 0),
        "updated_at": str(p.updated_at),
        "ai_meta": p.ai_meta,
    } for p in rows]


@router.post("/plans")
async def create_plan(body: dict, db: AsyncSession = Depends(get_db),
                      user: User = Depends(require("training", "edit"))):
    """P-09 生成培训方案（大纲 + 题库）。内容一律标记为草稿。"""
    from app.api.v1._plan_helpers import material_text as _mt, validate_plan_fields

    position_id = int(body.get("position_id", 0))
    with_exam = bool(body.get("with_exam", True))
    fields = validate_plan_fields(body)
    material_ids = [int(x) for x in (body.get("material_ids") or []) if str(x).isdigit()]

    pos = (await db.execute(select(Position).where(Position.id == position_id))).scalar_one_or_none()
    if not pos:
        raise HTTPException(404, "岗位不存在")
    model = (await db.execute(select(AbilityModel).where(
        AbilityModel.position_id == position_id,
        AbilityModel.active == True))).scalars().first()  # noqa: E712
    if not model:
        raise HTTPException(400, "该岗位未绑定启用的能力模型，请先在 M1 配置")

    items = [{"id": r.id, "name": r.name, "criteria": r.criteria}
             for r in (await db.execute(select(AbilityItem).where(
                 AbilityItem.model_id == model.id).order_by(AbilityItem.sort))).scalars().all()]

    # Materials for this plan: the ones the publisher explicitly ticked. If they
    # ticked nothing, fall back to the shared library (plan_id == 0) so docs they
    # uploaded on the 资料库 page still reach generation instead of being ignored.
    # Docs already bound to another plan never appear here, so one position's
    # material can't leak into another's questions.
    mats = []
    if material_ids:
        mats = (await db.execute(select(TrainingMaterial).where(
            TrainingMaterial.id.in_(material_ids)))).scalars().all()
    else:
        # New uploads carry plan_id = NULL until bound, older ones use 0.
        # Both mean "in the shared library, not owned by any plan".
        mats = (await db.execute(select(TrainingMaterial).where(
            or_(TrainingMaterial.plan_id.is_(None),
                TrainingMaterial.plan_id == 0)).order_by(
            TrainingMaterial.id.desc()).limit(LIBRARY_FALLBACK_MAX))).scalars().all()
    material_text = _mt(mats)

    plan, meta = await gen_outline(position_name=pos.name, items=items,
                                   business_line=pos.business_line,
                                   extra_context=material_text)
    row = TrainingPlan(position_id=position_id,
                       title=fields["title"] or f"{pos.name} 新人培训方案",
                       description=fields["description"],
                       start_date=fields["start_date"], end_date=fields["end_date"],
                       exam_minutes=fields["exam_minutes"], pass_score=fields["pass_score"],
                       outline=[o.model_dump() for o in plan.outline], status="草稿",
                       mentor_id=user.id, ai_meta=meta)
    db.add(row)
    await db.flush()
    # bind chosen materials to this plan so later regenerate uses the same set
    for m in mats:
        m.plan_id = row.id

    exam_meta: dict = {}
    if with_exam:
        exam, exam_meta = await gen_exam(position_name=pos.name, items=items,
                                         business_line=pos.business_line,
                                         extra_context=material_text)
        for q in exam.questions:
            db.add(ExamQuestionRecord(
                plan_id=row.id, qtype=q.qtype, stem=q.stem, options=q.options,
                answer=q.answer, explanation=q.explanation, difficulty=q.difficulty,
                ability_id=q.ability_id, ability_name=q.ability_name, ref=q.ref,
                need_manual_answer=not bool(q.ref),
            ))
        row.ai_meta = {**meta, "exam_meta": exam_meta}
        if exam_meta.get("degraded"):
            db.add(TrackEvent(event="ai_degrade", payload={
                "ability": "题库生成", "level": exam_meta.get("degrade", "human"),
                "reason": exam_meta.get("degrade_reason", "")}))

    db.add(DecisionLog(kind="ai", actor=user.name, ability="培训方案生成",
                       summary=f"为岗位「{pos.name}」生成培训方案草稿，"
                               f"{len(plan.outline)} 章"
                               + (f"、{len(exam.questions)} 道题" if with_exam else ""),
                       model=meta.get("model", ""), prompt_version=meta.get("prompt_version", ""),
                       confidence=float(meta.get("confidence") or 0),
                       latency_ms=int(meta.get("latency_ms") or 0),
                       degrade=meta.get("degrade", "none"),
                       degrade_reason=meta.get("degrade_reason", "")))
    await db.commit()
    await db.refresh(row)

    # 明确告知生成质量，而不是让用户自己数题目数量发现问题
    warnings: list[str] = []
    if with_exam and not exam.questions:
        warnings.append(
            "题库生成失败（AI 返回内容不合规，已走降级）。"
            "可点「重新生成」重试，或先上传公司资料再生成。")
    elif with_exam and exam_meta.get("degraded"):
        warnings.append(
            "题库走了降级路径，题目可能未完全贴合本岗位能力项，请逐题核对。")
    if not plan.outline:
        warnings.append("大纲生成失败，请重试。")
    if material_ids and not mats:
        warnings.append("所选资料未能读取到内容，出题未引用公司资料。")
    if not material_ids and mats:
        names = "、".join(m.title for m in mats[:3])
        warnings.append(
            f"未勾选资料，已自动引用资料库中的 {len(mats)} 份（{names}）。"
            "如需限定范围，请在生成时勾选。")

    return {"plan_id": row.id, "outline": row.outline, "meta": meta,
            "warnings": warnings,
            "note": "生成内容为草稿状态，须带教人确认后才可发布给新人。"}


@router.get("/plans/{pid}")
async def get_plan(pid: int, db: AsyncSession = Depends(get_db),
                   user: User = Depends(current_user)):
    p = (await db.execute(select(TrainingPlan).where(TrainingPlan.id == pid))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "培训方案不存在")
    pos = (await db.execute(select(Position).where(Position.id == p.position_id))).scalar_one_or_none()
    qs = (await db.execute(select(ExamQuestionRecord).where(
        ExamQuestionRecord.plan_id == pid))).scalars().all()
    subs = (await db.execute(select(ExamSubmission).where(
        ExamSubmission.plan_id == pid).order_by(ExamSubmission.id.desc()))).scalars().all()

    return {
        "id": p.id, "title": p.title, "status": p.status,
        "description": p.description or "",
        "start_date": str(p.start_date or ""),
        "end_date": str(p.end_date or ""),
        "exam_minutes": p.exam_minutes or 60,
        "pass_score": p.pass_score if p.pass_score is not None else 60,
        "material_ids": [m.id for m in (await db.execute(
            select(TrainingMaterial).where(TrainingMaterial.plan_id == pid))).scalars().all()],
        "position_id": p.position_id,
        "position_name": pos.name if pos else "",
        "outline": p.outline or [],
        "questions": [{
            "id": q.id, "qtype": q.qtype, "stem": q.stem, "options": q.options or [],
            "answer": q.answer, "explanation": q.explanation, "difficulty": q.difficulty,
            "ability_id": q.ability_id, "ability_name": q.ability_name, "ref": q.ref,
            "need_manual_answer": q.need_manual_answer,
            "full_score": FULL_SCORE.get(q.qtype, 10.0),
        } for q in qs],
        "submissions": [{
            "id": s.id, "trainee_name": s.trainee_name,
            "objective_score": s.objective_score, "objective_full": s.objective_full,
            "radar": s.radar or {}, "final_score": s.final_score,
            "mentor_confirmed": s.mentor_confirmed,
            "judge": s.judge, "ai_meta": s.ai_meta,
            "created_at": str(s.created_at),
        } for s in subs],
        "ai_meta": p.ai_meta,
        "note": "主观题 AI 只给要点覆盖分析，不给终评分数；终评权在人手里。",
    }


@router.post("/plans/{pid}/publish")
async def publish(pid: int, body: dict, db: AsyncSession = Depends(get_db),
                  user: User = Depends(require("training", "publish"))):
    """带教人确认后发布。发布前校验参考答案是否可溯源（验收 A4-4）。"""
    p = (await db.execute(select(TrainingPlan).where(TrainingPlan.id == pid))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "培训方案不存在")

    qs = (await db.execute(select(ExamQuestionRecord).where(
        ExamQuestionRecord.plan_id == pid))).scalars().all()
    missing = [q for q in qs if not q.ref]
    if missing and not body.get("allow_missing_ref"):
        raise HTTPException(
            400,
            f"有 {len(missing)} 道题缺少知识库出处，需先补充答案出处或确认接受。"
            f"缺失题目：{'；'.join(q.stem[:24] for q in missing[:3])}",
        )

    # 校验大纲章节与能力项一一对应（验收 A4-1）
    if p.outline:
        if any(not o.get("ability_name") for o in p.outline):
            raise HTTPException(400, "存在无对应能力项的孤立章节，请先修正大纲")

    p.status = "已发布"
    p.mentor_id = user.id
    db.add(DecisionLog(kind="human", actor=user.name, ability="培训方案",
                       summary=f"发布培训方案「{p.title}」",
                       detail={"missing_ref": len(missing)}))
    await db.commit()
    return {"ok": True, "status": p.status,
            "message": "已发布。新人可在培训模块查看并参加考核。"}


@router.delete("/plans/{pid}")
async def delete_plan(pid: int, db: AsyncSession = Depends(get_db),
                      user: User = Depends(require("training", "edit"))):
    """删除培训方案，连同它的题库、指派、作答记录与资料绑定。

    必须一并清理子记录：SQLite 会复用被释放的自增 id，留下孤儿记录后，
    新建的方案一旦拿到同一个 id，就会莫名其妙继承上一个方案的作答历史 ——
    表现为「刚指派就说考试机会已用完」。资料不删除，只解绑回资料库。
    """
    p = (await db.execute(select(TrainingPlan).where(TrainingPlan.id == pid))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "培训方案不存在")

    # 先删作答里的答卷，再删作答本身，避免留下无主的判卷结果
    attempts = (await db.execute(select(ExamAttempt).where(
        ExamAttempt.plan_id == pid))).scalars().all()
    for a in attempts:
        if a.submission_id:
            sub = (await db.execute(select(ExamSubmission).where(
                ExamSubmission.id == a.submission_id))).scalar_one_or_none()
            if sub:
                await db.delete(sub)
        await db.delete(a)

    for m in (await db.execute(select(ExamAssignment).where(
            ExamAssignment.plan_id == pid))).scalars().all():
        await db.delete(m)

    for q in (await db.execute(select(ExamQuestionRecord).where(
            ExamQuestionRecord.plan_id == pid))).scalars().all():
        await db.delete(q)

    # 资料是公司资产，删方案不该连带删除；解绑回资料库供其它方案复用
    for mat in (await db.execute(select(TrainingMaterial).where(
            TrainingMaterial.plan_id == pid))).scalars().all():
        mat.plan_id = None

    await db.delete(p)
    await db.commit()
    return {"ok": True, "removed_attempts": len(attempts)}






@router.post("/plans/{pid}/regenerate")
async def regenerate_plan(pid: int, body: dict, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require("training", "edit"))):
    """用当前的资料与能力项重新生成大纲与题库。

    为什么需要这个：发布后如果换了资料、或能力模型改了，旧的方案不会自动更新。
    而重新生成不该要求发布者先删掉方案再建一次 —— 那会丢失指派关系，
    已经考过的人也算白考了。这里原地替换内容，保留方案 id 与指派记录。

    已有作答记录时不覆盖题库：那些人考的必须是同一份题，否则成绩不可比。
    """
    from app.db.models import ExamAttempt, ExamQuestionRecord

    plan = (await db.execute(select(TrainingPlan).where(
        TrainingPlan.id == pid))).scalar_one_or_none()
    if not plan:
        raise HTTPException(404, "培训方案不存在")

    attempts = (await db.execute(select(func.count(ExamAttempt.id)).where(
        ExamAttempt.plan_id == pid))).scalar_one()
    if attempts and not body.get("force"):
        raise HTTPException(
            400,
            f"该方案已有 {attempts} 次作答记录。重新生成会改变题目，"
            f"导致新旧成绩不可比。如确需重建，请先撤销指派或确认强制覆盖。",
        )

    pos = (await db.execute(select(Position).where(
        Position.id == plan.position_id))).scalar_one_or_none()
    model = (await db.execute(select(AbilityModel).where(
        AbilityModel.position_id == plan.position_id,
        AbilityModel.active == True))).scalars().first()  # noqa: E712
    if not model:
        raise HTTPException(400, "该岗位未绑定启用的能力模型，无法重新生成")

    items = [{"id": r.id, "name": r.name, "criteria": r.criteria}
             for r in (await db.execute(select(AbilityItem).where(
                 AbilityItem.model_id == model.id).order_by(AbilityItem.sort))).scalars().all()]

    mats = (await db.execute(select(TrainingMaterial).where(
        TrainingMaterial.plan_id == pid).order_by(
        TrainingMaterial.id.desc()).limit(5))).scalars().all()
    material_text = _material_text(mats)

    outline, meta = await gen_outline(position_name=pos.name if pos else "",
                                      items=items,
                                      business_line=pos.business_line if pos else "",
                                      extra_context=material_text)
    exam, exam_meta = await gen_exam(position_name=pos.name if pos else "", items=items,
                                     business_line=pos.business_line if pos else "",
                                     extra_context=material_text)

    # 替换大纲与题库，保留方案本身与指派关系
    plan.outline = [o.model_dump() for o in outline.outline]
    plan.ai_meta = {**meta, "exam_meta": exam_meta,
                    "regenerated_at": datetime.now().isoformat()}
    for old in (await db.execute(select(ExamQuestionRecord).where(
            ExamQuestionRecord.plan_id == pid))).scalars().all():
        await db.delete(old)
    await db.flush()
    for q in exam.questions:
        db.add(ExamQuestionRecord(
            plan_id=pid, qtype=q.qtype, stem=q.stem, options=q.options,
            answer=q.answer, explanation=q.explanation, difficulty=q.difficulty,
            ability_id=q.ability_id, ability_name=q.ability_name, ref=q.ref,
            need_manual_answer=not bool(q.ref),
        ))

    db.add(DecisionLog(
        kind="human", actor=user.name, ability="培训方案",
        summary=f"重新生成培训方案「{plan.title}」：{len(plan.outline)} 章、"
                f"{len(exam.questions)} 道题"
                + (f"（原方案有 {attempts} 次作答，已强制覆盖）" if attempts else ""),
    ))
    await db.commit()
    return {
        "ok": True, "plan_id": pid,
        "chapter_count": len(plan.outline),
        "question_count": len(exam.questions),
        "message": f"已重新生成 {len(plan.outline)} 章大纲与 {len(exam.questions)} 道题，"
                   f"指派关系与方案 ID 保持不变。",
    }


@router.put("/plans/{pid}")
async def update_plan(pid: int, body: dict, db: AsyncSession = Depends(get_db),
                      user: User = Depends(require("training", "edit"))):
    """修改方案的名称、周期与考试规则。

    已发布后仍可改，但改动会写进决策日志 —— 培训周期与及格分
    直接影响考生的判定结果，改了什么必须留痕。
    """
    from app.api.v1._plan_helpers import validate_plan_fields

    plan = (await db.execute(select(TrainingPlan).where(
        TrainingPlan.id == pid))).scalar_one_or_none()
    if not plan:
        raise HTTPException(404, "培训方案不存在")

    before = {
        "title": plan.title, "start_date": str(plan.start_date or ""),
        "end_date": str(plan.end_date or ""),
        "exam_minutes": plan.exam_minutes, "pass_score": plan.pass_score,
    }
    fields = validate_plan_fields(body)

    plan.title = fields["title"] or plan.title
    plan.description = fields["description"]
    plan.start_date = fields["start_date"]
    plan.end_date = fields["end_date"]
    plan.exam_minutes = fields["exam_minutes"]
    plan.pass_score = fields["pass_score"]

    changed = {k: v for k, v in {
        "title": plan.title, "start_date": str(plan.start_date or ""),
        "end_date": str(plan.end_date or ""),
        "exam_minutes": plan.exam_minutes, "pass_score": plan.pass_score,
    }.items() if before.get(k) != v}

    if changed:
        db.add(DecisionLog(kind="human", actor=user.name, ability="培训方案",
                           summary=f"修改方案「{plan.title}」：" +
                                   "、".join(f"{k} {before.get(k)} → {v}"
                                             for k, v in changed.items()),
                           detail={"before": before, "after": changed}))
    await db.commit()
    return {"ok": True, "changed": changed,
            "message": f"已保存{len(changed)} 项修改。" if changed else "没有检测到变化。"}
