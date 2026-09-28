"""M3 面试助手接口（R-09、R-10、R-14）。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ROLE_INTERVIEWER, current_user, require
from app.db.models import (
    AbilityItem, AbilityModel, Candidate, DecisionLog, InterviewReport, InterviewSchedule,
    Position, QuestionRecord, ReflowSample, ResumeText, ScoreRecord, TrackEvent, User,
)
from app.db.session import get_db
from app.services.interview_service import generate_questions
from app.services.scoring_service import ability_to_dict

router = APIRouter(prefix="/interview", tags=["M3 面试助手"])


@router.get("/schedules")
async def list_schedules(db: AsyncSession = Depends(get_db),
                         user: User = Depends(require("interview", "view"))):
    """面试日程列表。面试官仅可见被指派给自己的场次。"""
    q = select(InterviewSchedule).order_by(InterviewSchedule.scheduled_at.desc())
    if user.role == ROLE_INTERVIEWER:
        q = q.where(InterviewSchedule.interviewer_id == user.id)
    rows = (await db.execute(q.limit(200))).scalars().all()

    cands = {c.id: c for c in (await db.execute(select(Candidate).where(
        Candidate.id.in_([r.candidate_id for r in rows] or [0])))).scalars().all()}
    positions = {p.id: p for p in (await db.execute(select(Position))).scalars().all()}
    users = {u.id: u for u in (await db.execute(select(User))).scalars().all()}
    reports = {r.schedule_id: r for r in (await db.execute(select(InterviewReport))).scalars().all()}
    qcount: dict[int, int] = {}
    for qr in (await db.execute(select(QuestionRecord))).scalars().all():
        qcount[qr.schedule_id] = qcount.get(qr.schedule_id, 0) + 1

    return [{
        "id": r.id, "round_name": r.round_name,
        "candidate_id": r.candidate_id,
        "candidate_name": cands.get(r.candidate_id).name if cands.get(r.candidate_id) else "",
        "position_name": positions.get(cands[r.candidate_id].position_id).name
                         if cands.get(r.candidate_id) and positions.get(cands[r.candidate_id].position_id) else "",
        "interviewer": users.get(r.interviewer_id).name if users.get(r.interviewer_id) else "",
        "scheduled_at": str(r.scheduled_at or ""),
        "plan_minutes": r.plan_minutes, "status": r.status,
        "consent_recorded": r.consent_recorded,
        "question_count": qcount.get(r.id, 0),
        "has_report": r.id in reports,
        "report_confirmed": reports[r.id].human_confirmed if r.id in reports else False,
    } for r in rows]


@router.post("/schedules")
async def create_schedule(body: dict, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require("screen", "adopt"))):
    """采纳后安排面试。"""
    cid = int(body.get("candidate_id", 0))
    c = (await db.execute(select(Candidate).where(Candidate.id == cid))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "候选人不存在")
    if c.status not in ("待安排面试", "待复核"):
        raise HTTPException(400, f"该候选人当前状态为「{c.status}」，不可安排面试")
    # 姓名未补录的记录不允许进入面试：面试官拿到的日程上会显示「未识别-12」
    # 这种标识，既无法核对身份，也无法记录评价。
    if c.name.startswith("未识别-") or c.name in ("待解析", ""):
        raise HTTPException(
            400,
            f"该候选人的姓名尚未补录（当前显示为「{c.name}」），不可安排面试。"
            f"请先在候选人详情页补录姓名。",
        )

    interviewer_id = body.get("interviewer_id")
    if not interviewer_id:
        # 默认指派一名业务面试官，同时把候选人指派给他，满足「面试官仅可见被指派候选人」
        itv = (await db.execute(select(User).where(User.role == ROLE_INTERVIEWER))).scalars().first()
        interviewer_id = itv.id if itv else None

    sch = InterviewSchedule(
        candidate_id=cid, round_name=body.get("round_name", "初面"),
        interviewer_id=interviewer_id,
        scheduled_at=datetime.fromisoformat(body["scheduled_at"])
        if body.get("scheduled_at") else datetime.now(),
        plan_minutes=int(body.get("plan_minutes", 30)), status="待面试",
    )
    db.add(sch)
    c.status = "待安排面试"
    if interviewer_id:
        c.assignee_id = int(interviewer_id)
    await db.flush()
    db.add(DecisionLog(kind="human", actor=user.name, ability="面试安排",
                       candidate_id=cid,
                       summary=f"为候选人 {c.name} 安排 {sch.round_name}，"
                               f"时长 {sch.plan_minutes} 分钟"))
    await db.commit()
    return {"ok": True, "schedule_id": sch.id}


@router.get("/{sid}/prepare")
async def prepare(sid: int, plan_minutes: int = 0, db: AsyncSession = Depends(get_db),
                  user: User = Depends(require("interview", "view"))):
    """P-07 面试准备页：分层题目、评分锚点、能力项缺口提示。"""
    sch = (await db.execute(select(InterviewSchedule).where(
        InterviewSchedule.id == sid))).scalar_one_or_none()
    if not sch:
        raise HTTPException(404, "面试日程不存在")
    if user.role == ROLE_INTERVIEWER and sch.interviewer_id != user.id:
        raise HTTPException(403, "业务面试官仅可查看被指派给自己的面试")

    c = (await db.execute(select(Candidate).where(
        Candidate.id == sch.candidate_id))).scalar_one_or_none()
    pos = (await db.execute(select(Position).where(
        Position.id == c.position_id))).scalar_one_or_none() if c else None
    rt = (await db.execute(select(ResumeText).where(
        ResumeText.candidate_id == sch.candidate_id))).scalar_one_or_none()
    sr = (await db.execute(select(ScoreRecord).where(
        ScoreRecord.candidate_id == sch.candidate_id).order_by(ScoreRecord.id.desc()))).scalars().first()

    model = (await db.execute(select(AbilityModel).where(
        AbilityModel.position_id == c.position_id,
        AbilityModel.active == True))).scalars().first() if c else None  # noqa: E712
    items = [ability_to_dict(r) for r in (await db.execute(select(AbilityItem).where(
        AbilityItem.model_id == model.id).order_by(AbilityItem.sort))).scalars().all()] \
        if model else []

    existing = (await db.execute(select(QuestionRecord).where(
        QuestionRecord.schedule_id == sid).order_by(QuestionRecord.id))).scalars().all()

    score_items = sr.items if sr else []
    state_map = {str(s.get("ability_id")): s.get("state") for s in score_items}

    # 能力项缺口提示：优先列出未体现与部分命中
    gaps = []
    for i in items:
        st = state_map.get(str(i["id"]), "absent")
        if st in ("absent", "partial", "mismatch"):
            gaps.append({
                "ability_id": str(i["id"]), "ability_name": i["name"],
                "state": st, "weight": i["weight"],
                "hint": {"absent": "简历未提及，需在面试中验证",
                         "partial": "简历有部分证据，需确认深度",
                         "mismatch": "简历表述与标准不符，需澄清"}.get(st, ""),
            })
    gaps.sort(key=lambda x: -x["weight"])

    return {
        "schedule_id": sid, "round_name": sch.round_name,
        "plan_minutes": plan_minutes or sch.plan_minutes,
        "candidate": {"id": c.id, "name": c.name} if c else {},
        "position_name": pos.name if pos else "",
        "total_score": c.total_score if c else 0,
        "tier": c.tier if c else "",
        "consent_recorded": sch.consent_recorded,
        "gaps": gaps,
        "questions": [{
            "id": q.id, "layer": q.layer, "content": q.content,
            "ability_id": q.ability_id, "ability_name": q.ability_name,
            "basis": q.basis, "anchors": q.anchors or {},
            "duration_min": q.duration_min, "probe": q.probe, "action": q.action,
            "edited_content": q.edited_content,
        } for q in existing],
        "total_minutes": sum(q.duration_min for q in existing),
    }


@router.post("/{sid}/generate")
async def generate(sid: int, body: dict, db: AsyncSession = Depends(get_db),
                   user: User = Depends(current_user)):
    """生成分层面试题。"""
    plan_minutes = int(body.get("plan_minutes", 30))
    sch = (await db.execute(select(InterviewSchedule).where(
        InterviewSchedule.id == sid))).scalar_one_or_none()
    if not sch:
        raise HTTPException(404, "面试日程不存在")
    if user.role == ROLE_INTERVIEWER and sch.interviewer_id != user.id:
        raise HTTPException(403, "无权操作该面试")

    c = (await db.execute(select(Candidate).where(
        Candidate.id == sch.candidate_id))).scalar_one_or_none()
    pos = (await db.execute(select(Position).where(
        Position.id == c.position_id))).scalar_one_or_none() if c else None
    model = (await db.execute(select(AbilityModel).where(
        AbilityModel.position_id == c.position_id,
        AbilityModel.active == True))).scalars().first() if c else None  # noqa: E712
    if not model:
        raise HTTPException(400, "该岗位未绑定启用的能力模型")
    items = [ability_to_dict(r) for r in (await db.execute(select(AbilityItem).where(
        AbilityItem.model_id == model.id).order_by(AbilityItem.sort))).scalars().all()]
    sr = (await db.execute(select(ScoreRecord).where(
        ScoreRecord.candidate_id == sch.candidate_id).order_by(ScoreRecord.id.desc()))).scalars().first()
    rt = (await db.execute(select(ResumeText).where(
        ResumeText.candidate_id == sch.candidate_id))).scalar_one_or_none()

    gen, meta, dropped = await generate_questions(
        candidate_id=sch.candidate_id, position_name=pos.name if pos else "",
        items=items, score_items=sr.items if sr else [],
        parsed=rt.parsed if rt else {}, plan_minutes=plan_minutes,
    )

    # 覆盖旧题（保留已采纳的）
    for old in (await db.execute(select(QuestionRecord).where(
            QuestionRecord.schedule_id == sid))).scalars().all():
        if old.action != "采纳":
            await db.delete(old)
    await db.flush()

    for q in gen.questions:
        db.add(QuestionRecord(
            schedule_id=sid, layer=q.layer, content=q.content,
            ability_id=q.ability_id, ability_name=q.ability_name,
            basis=q.basis, anchors=q.anchors, duration_min=q.duration_min,
            probe=q.probe, ai_meta=meta,
        ))
    sch.plan_minutes = plan_minutes

    if dropped:
        db.add(DecisionLog(kind="ai", actor="system", ability="面试题生成",
                           candidate_id=sch.candidate_id,
                           summary=f"拦截敏感话题题目 {len(dropped)} 道，已整条丢弃",
                           detail={"dropped": dropped}))
    await db.commit()
    await db.refresh(sch)

    rows = (await db.execute(select(QuestionRecord).where(
        QuestionRecord.schedule_id == sid).order_by(QuestionRecord.id))).scalars().all()
    return {
        "questions": [{
            "id": q.id, "layer": q.layer, "content": q.content,
            "ability_id": q.ability_id, "ability_name": q.ability_name,
            "basis": q.basis, "anchors": q.anchors or {},
            "duration_min": q.duration_min, "probe": q.probe, "action": q.action,
        } for q in rows],
        "total_minutes": sum(q.duration_min for q in rows),
        "blocked": dropped,
        "meta": meta,
        "note": "出题优先针对简历中未体现或部分命中的能力项；已充分证明的项最多 1 道确认题。",
    }


@router.post("/questions/{qid}/action")
async def question_action(qid: int, body: dict, db: AsyncSession = Depends(get_db),
                          user: User = Depends(current_user)):
    """面试官对题目的采纳 / 删除 / 编辑。操作行为即采纳率的统计来源（PRD 3.3.2）。"""
    action = body.get("action")
    if action not in ("采纳", "删除", "编辑"):
        raise HTTPException(400, "动作须为采纳/删除/编辑")
    q = (await db.execute(select(QuestionRecord).where(
        QuestionRecord.id == qid))).scalar_one_or_none()
    if not q:
        raise HTTPException(404, "题目不存在")

    q.action = action
    if action == "编辑":
        q.edited_content = body.get("content", "")
    elif action == "删除":
        await db.delete(q)

    db.add(TrackEvent(event="question_action", user_id=user.id, payload={
        "question_id": qid, "action": action, "layer": q.layer,
        "ability_id": q.ability_id,
    }))
    await db.commit()
    return {"ok": True}


@router.post("/{sid}/report")
async def build_report_api(sid: int, body: dict, db: AsyncSession = Depends(get_db),
                           user: User = Depends(current_user)):
    """生成面试评估报告（R-14）。录音需候选人明示同意后开启。"""
    from app.services.interview_service import build_report

    transcript = (body.get("transcript") or "").strip()
    if not transcript:
        raise HTTPException(400, "请提供面试逐字稿")
    consent = bool(body.get("consent_recorded", False))

    sch = (await db.execute(select(InterviewSchedule).where(
        InterviewSchedule.id == sid))).scalar_one_or_none()
    if not sch:
        raise HTTPException(404, "面试日程不存在")

    if not consent:
        # 未同意时该模块降级为面试官手动填写摘要，不阻断流程
        sch.consent_recorded = False
        db.add(DecisionLog(kind="human", actor=user.name, ability="录音合规",
                           candidate_id=sch.candidate_id,
                           summary="候选人未同意录音，已降级为面试官手动填写摘要"))
        await db.commit()
        return {
            "degraded": True,
            "reason": "候选人未明示同意录音，按合规要求降级为手动摘要模式",
            "note": "可在下方手动填写结构化摘要，不阻断面试流程。",
        }

    sch.consent_recorded = True
    db.add(DecisionLog(kind="human", actor=user.name, ability="录音合规",
                       candidate_id=sch.candidate_id,
                       summary="候选人已明示同意录音，同意记录写入决策日志"))

    c = (await db.execute(select(Candidate).where(
        Candidate.id == sch.candidate_id))).scalar_one_or_none()
    pos = (await db.execute(select(Position).where(
        Position.id == c.position_id))).scalar_one_or_none() if c else None
    itv = (await db.execute(select(User).where(
        User.id == sch.interviewer_id))).scalar_one_or_none() if sch.interviewer_id else None
    model = (await db.execute(select(AbilityModel).where(
        AbilityModel.position_id == c.position_id,
        AbilityModel.active == True))).scalars().first() if c else None  # noqa: E712
    items = [ability_to_dict(r) for r in (await db.execute(select(AbilityItem).where(
        AbilityItem.model_id == model.id))).scalars().all()] if model else []
    qs = [{"layer": r.layer, "content": r.content, "ability_id": r.ability_id,
           "ability_name": r.ability_name}
          for r in (await db.execute(select(QuestionRecord).where(
              QuestionRecord.schedule_id == sid))).scalars().all()]

    rep, meta = await build_report(
        schedule_id=sid, candidate_name=c.name if c else "",
        position_name=pos.name if pos else "",
        interviewer=itv.name if itv else "", duration_min=sch.plan_minutes,
        round_name=sch.round_name, transcript=transcript, questions=qs, items=items,
    )

    row = InterviewReport(
        schedule_id=sid, candidate_id=sch.candidate_id, transcript=transcript,
        by_ability=[a.model_dump() for a in rep.by_ability],
        uncovered=rep.uncovered_abilities, suggestions=rep.suggestions,
        ai_meta=meta,
    )
    db.add(row)
    sch.status = "已完成"
    await db.commit()
    await db.refresh(row)

    db.add(TrackEvent(event="report_confirm", user_id=user.id,
                      payload={"schedule_id": sid, "report_id": row.id}))
    await db.commit()
    return {"report_id": row.id, "report": rep.model_dump(), "meta": meta}


@router.get("/{sid}/report")
async def get_report(sid: int, db: AsyncSession = Depends(get_db),
                     user: User = Depends(require("interview", "view"))):
    """P-08 面试评估报告。面试官评分区域无任何 AI 预填值（验收 A3-6）。"""
    r = (await db.execute(select(InterviewReport).where(
        InterviewReport.schedule_id == sid).order_by(InterviewReport.id.desc()))).scalars().first()
    if not r:
        raise HTTPException(404, "该面试尚无评估报告")
    sch = (await db.execute(select(InterviewSchedule).where(
        InterviewSchedule.id == sid))).scalar_one_or_none()
    c = (await db.execute(select(Candidate).where(
        Candidate.id == r.candidate_id))).scalar_one_or_none()

    return {
        "id": r.id, "schedule_id": sid,
        "candidate_name": c.name if c else "",
        "by_ability": r.by_ability or [],
        "uncovered": r.uncovered or [],
        "suggestions": r.suggestions or [],
        "transcript": r.transcript,
        "scores": r.scores or {},          # 面试官录入，AI 不预填
        "conclusion": r.conclusion,
        "human_confirmed": r.human_confirmed,
        "ai_meta": r.ai_meta,
        "note": "AI 建议仅供参照，评分由面试官本人录入。AI 不预填分数以避免锚定效应。",
    }


@router.post("/{sid}/scores")
async def submit_scores(sid: int, body: dict, db: AsyncSession = Depends(get_db),
                        user: User = Depends(current_user)):
    """面试官录入评分（1 至 5 分逐项 + 整体结论）。"""
    r = (await db.execute(select(InterviewReport).where(
        InterviewReport.schedule_id == sid).order_by(InterviewReport.id.desc()))).scalars().first()
    if not r:
        raise HTTPException(404, "请先生成评估报告")

    scores = body.get("scores") or {}
    for k, v in scores.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"评分须为数字：{k}") from None
        if not (1 <= fv <= 5):
            raise HTTPException(400, "评分范围为 1 至 5 分")
        scores[k] = fv

    r.scores = scores
    r.conclusion = body.get("conclusion", "")
    r.human_confirmed = True

    db.add(ReflowSample(candidate_id=r.candidate_id, kind="interview_score",
                        payload={"schedule_id": sid, "scores": scores,
                                 "conclusion": r.conclusion}))
    db.add(DecisionLog(kind="human", actor=user.name, ability="面试评分",
                       candidate_id=r.candidate_id,
                       summary=f"面试官完成评分，整体结论：{r.conclusion or '未填写'}",
                       detail={"scores": scores}))
    await db.commit()
    return {"ok": True}
