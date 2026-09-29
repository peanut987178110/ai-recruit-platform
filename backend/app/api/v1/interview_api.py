"""M3 面试助手接口（R-09、R-10、R-14）。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ROLE_INTERVIEWER, ROLE_MANAGER, can, current_user, require
from app.db.models import (
    AbilityItem, AbilityModel, Candidate, DecisionLog, InterviewReport, InterviewSchedule,
    Position, QuestionRecord, ReflowSample, ResumeText, ScoreRecord, TrackEvent, User,
)
from app.db.session import get_db
from app.services.interview_service import generate_questions
from app.services import schedule_service as S
from app.services.schedule_service import ACTIVE_STATUS
from app.services.scoring_service import ability_to_dict

router = APIRouter(prefix="/interview", tags=["M3 面试助手"])


async def _visible_schedule_filter(db: AsyncSession, user: User, q):
    """面试日程的可见范围，与候选人列表保持同一套口径。"""
    if user.role == ROLE_INTERVIEWER:
        return q.where(InterviewSchedule.interviewer_id == user.id)
    if user.role == ROLE_MANAGER and user.business_line and not user.is_super:
        pos_ids = [p.id for p in (await db.execute(select(Position).where(
            Position.business_line == user.business_line))).scalars().all()]
        cand_ids = [c.id for c in (await db.execute(select(Candidate).where(
            Candidate.position_id.in_(pos_ids or [0])))).scalars().all()]
        return q.where(InterviewSchedule.candidate_id.in_(cand_ids or [0]))
    return q


def _can_schedule(user: User) -> bool:
    return can(user.role, "interview", "schedule", user.is_super)


def _schedule_out(r: InterviewSchedule, *, cand, pos, itv, qcount: int, report,
                  show_invite: bool) -> dict:
    return {
        "id": r.id, "round_name": r.round_name,
        "candidate_id": r.candidate_id,
        "candidate_name": cand.name if cand else "",
        "position_name": pos.name if pos else "",
        "business_line": pos.business_line if pos else "",
        "interviewer_id": r.interviewer_id,
        "interviewer": itv.name if itv else "",
        "interviewer_userid": itv.userid if itv else "",
        "scheduled_at": str(r.scheduled_at or ""),
        "plan_minutes": r.plan_minutes, "status": r.status,
        "mode": r.mode or "视频",
        # 面试官需要会议链接才能入会，所以对能看到这场面试的人都返回
        "meeting_url": r.meeting_url, "meeting_code": r.meeting_code,
        "location": r.location, "note": r.note,
        "consent_recorded": r.consent_recorded,
        "consent_status": r.consent_status or "未回复",
        # 候选人邀请令牌只给安排人：面试官不需要，也不该能替候选人点同意
        "invite_token": r.invite_token if show_invite and r.status == ACTIVE_STATUS else "",
        "invite_expires_at": str(r.invite_expires_at or "") if show_invite else "",
        "scheduled_by": r.scheduled_by,
        "question_count": qcount,
        "has_report": report is not None,
        "report_confirmed": report.human_confirmed if report else False,
    }


@router.get("/interviewers")
async def list_interviewers(candidate_id: int = 0, db: AsyncSession = Depends(get_db),
                            user: User = Depends(current_user)):
    """可指派的面试官。

    同业务线的排在前面，并附上待面试场次数 —— 安排人要看到「谁更合适、谁更空」，
    而不是在一个平铺的名单里凭记忆挑。用人经理也常亲自面试，所以一并列出。
    """
    if not _can_schedule(user):
        raise HTTPException(403, "无权安排面试")
    line = ""
    if candidate_id:
        c = (await db.execute(select(Candidate).where(Candidate.id == candidate_id))).scalar_one_or_none()
        pos = (await db.execute(select(Position).where(
            Position.id == c.position_id))).scalar_one_or_none() if c else None
        line = pos.business_line if pos else ""

    people = (await db.execute(select(User).where(
        User.role.in_([ROLE_INTERVIEWER, ROLE_MANAGER]),
        User.active.is_(True)))).scalars().all()
    load: dict[int, int] = {}
    for (iid, n) in (await db.execute(
            select(InterviewSchedule.interviewer_id, func.count())
            .where(InterviewSchedule.status == ACTIVE_STATUS)
            .group_by(InterviewSchedule.interviewer_id))).all():
        if iid:
            load[iid] = n

    def match(u: User) -> str:
        if line and u.business_line == line:
            return "同业务线"
        if not u.business_line:
            return "跨业务线"   # 没限定业务线的面试官，任何业务线都能面
        return "其它业务线"

    rank = {"同业务线": 0, "跨业务线": 1, "其它业务线": 2}
    out = [{
        "id": u.id, "userid": u.userid, "name": u.name, "role": u.role,
        "business_line": u.business_line, "department": u.department,
        "pending": load.get(u.id, 0),
        "match": match(u),
        "same_line": match(u) != "其它业务线",
    } for u in people]
    out.sort(key=lambda x: (rank[x["match"]], x["role"] != ROLE_INTERVIEWER,
                            x["pending"], x["name"]))
    return {"business_line": line, "items": out}


@router.get("/schedules")
async def list_schedules(status: str = "", candidate_id: int = 0,
                         db: AsyncSession = Depends(get_db),
                         user: User = Depends(require("interview", "view"))):
    """面试日程列表。面试官仅可见被指派给自己的场次；已取消的默认不显示。"""
    q = select(InterviewSchedule).order_by(InterviewSchedule.scheduled_at.desc())
    q = await _visible_schedule_filter(db, user, q)
    if status:
        q = q.where(InterviewSchedule.status == status)
    else:
        q = q.where(InterviewSchedule.status != "已取消")
    if candidate_id:
        q = q.where(InterviewSchedule.candidate_id == candidate_id)
    rows = (await db.execute(q.limit(300))).scalars().all()

    cand_ids = [r.candidate_id for r in rows] or [0]
    cands = {c.id: c for c in (await db.execute(select(Candidate).where(
        Candidate.id.in_(cand_ids)))).scalars().all()}
    positions = {p.id: p for p in (await db.execute(select(Position))).scalars().all()}
    users = {u.id: u for u in (await db.execute(select(User))).scalars().all()}
    sids = [r.id for r in rows] or [0]
    reports = {r.schedule_id: r for r in (await db.execute(select(InterviewReport).where(
        InterviewReport.schedule_id.in_(sids)))).scalars().all()}
    qcount: dict[int, int] = {}
    for (sid, n) in (await db.execute(select(QuestionRecord.schedule_id, func.count())
                                      .where(QuestionRecord.schedule_id.in_(sids))
                                      .group_by(QuestionRecord.schedule_id))).all():
        qcount[sid] = n

    show_invite = _can_schedule(user)
    out = []
    for r in rows:
        cand = cands.get(r.candidate_id)
        pos = positions.get(cand.position_id) if cand else None
        out.append(_schedule_out(r, cand=cand, pos=pos, itv=users.get(r.interviewer_id),
                                 qcount=qcount.get(r.id, 0), report=reports.get(r.id),
                                 show_invite=show_invite))
    return out


async def _load_interviewer(db: AsyncSession, iid) -> User:
    try:
        iid = int(iid)
    except (TypeError, ValueError):
        raise HTTPException(400, "请选择面试官") from None
    itv = (await db.execute(select(User).where(User.id == iid))).scalar_one_or_none()
    if not itv or not itv.active:
        raise HTTPException(400, "所选面试官不存在或已停用")
    if itv.role not in (ROLE_INTERVIEWER, ROLE_MANAGER):
        raise HTTPException(400, f"「{itv.name}」的角色是{itv.role}，不能担任面试官")
    return itv


async def _conflicts(db: AsyncSession, interviewer_id: int, when: datetime, minutes: int,
                     exclude_id: int = 0) -> list[str]:
    """同一面试官时间重叠的场次。只提示不拦截 —— 有时确实要连着排或临时加塞。"""
    rows = (await db.execute(select(InterviewSchedule).where(
        InterviewSchedule.interviewer_id == interviewer_id,
        InterviewSchedule.status == ACTIVE_STATUS,
        InterviewSchedule.id != exclude_id))).scalars().all()
    hits = [r for r in rows if r.scheduled_at
            and S.overlaps(when, minutes, r.scheduled_at, r.plan_minutes or 30)]
    if not hits:
        return []
    names = {c.id: c.name for c in (await db.execute(select(Candidate).where(
        Candidate.id.in_([h.candidate_id for h in hits])))).scalars().all()}
    return [f"{h.scheduled_at:%m-%d %H:%M} 已有 {names.get(h.candidate_id, '候选人')} 的{h.round_name}"
            for h in hits]


async def _check_scope(db: AsyncSession, user: User, c: Candidate) -> Position | None:
    pos = (await db.execute(select(Position).where(Position.id == c.position_id))).scalar_one_or_none()
    if user.role == ROLE_MANAGER and not user.is_super and user.business_line:
        if not pos or pos.business_line != user.business_line:
            raise HTTPException(403, "用人经理只能安排本业务线候选人的面试")
    return pos


@router.post("/schedules")
async def create_schedule(body: dict, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require("interview", "schedule"))):
    """安排面试：指定面试官、时间与方式。

    此前未传面试官时会静默挂到「第一个业务面试官」名下，结果所有面试都堆给了同一个人，
    而其他面试官什么都看不到。现在面试官必须显式选择。
    """
    cid = int(body.get("candidate_id") or 0)
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
    await _check_scope(db, user, c)

    round_name = str(body.get("round_name") or "初面")
    if round_name not in S.ROUNDS:
        raise HTTPException(400, f"面试轮次须为：{'、'.join(S.ROUNDS)}")
    dup = (await db.execute(select(InterviewSchedule).where(
        InterviewSchedule.candidate_id == cid,
        InterviewSchedule.round_name == round_name,
        InterviewSchedule.status == S.ACTIVE_STATUS))).scalars().first()
    if dup:
        raise HTTPException(
            400, f"{c.name} 已有一场待进行的{round_name}，请改期或取消原场次，不要重复安排")

    itv = await _load_interviewer(db, body.get("interviewer_id"))
    minutes = int(body.get("plan_minutes") or 30)
    if minutes not in (30, 45, 60, 90):
        raise HTTPException(400, "时长须为 30、45、60 或 90 分钟")
    try:
        when = S.parse_when(body.get("scheduled_at"))
        S.check_when(when)
        mode, url, location = S.validate_mode(body.get("mode"), body.get("meeting_url"),
                                              body.get("location"))
    except S.ScheduleError as e:
        raise HTTPException(400, str(e)) from None

    warnings = await _conflicts(db, itv.id, when, minutes)
    sch = InterviewSchedule(
        candidate_id=cid, round_name=round_name, interviewer_id=itv.id,
        scheduled_at=when, plan_minutes=minutes, status=S.ACTIVE_STATUS,
        mode=mode, meeting_url=url, location=location,
        meeting_code=str(body.get("meeting_code") or "")[:64],
        note=str(body.get("note") or "")[:256],
        scheduled_by=user.name,
        invite_token=S.new_token(), invite_expires_at=S.invite_expiry(when, minutes),
    )
    db.add(sch)
    c.status = "待安排面试"
    # 把候选人指派给面试官，满足「面试官仅可见被指派候选人」
    c.assignee_id = itv.id
    await db.flush()
    db.add(DecisionLog(kind="human", actor=user.name, ability="面试安排",
                       candidate_id=cid,
                       summary=f"为候选人 {c.name} 安排{round_name}：面试官 {itv.name}，"
                               f"{when:%Y-%m-%d %H:%M}，{mode}，{minutes} 分钟"))
    await db.commit()
    return {"ok": True, "schedule_id": sch.id, "invite_token": sch.invite_token,
            "warnings": warnings}


async def _get_active(db: AsyncSession, sid: int) -> InterviewSchedule:
    sch = (await db.execute(select(InterviewSchedule).where(
        InterviewSchedule.id == sid))).scalar_one_or_none()
    if not sch:
        raise HTTPException(404, "面试日程不存在")
    if sch.status != S.ACTIVE_STATUS:
        raise HTTPException(400, f"该场面试状态为「{sch.status}」，不可修改")
    return sch


@router.put("/schedules/{sid}")
async def update_schedule(sid: int, body: dict, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require("interview", "schedule"))):
    """改期、换面试官、改面试方式。"""
    sch = await _get_active(db, sid)
    c = (await db.execute(select(Candidate).where(Candidate.id == sch.candidate_id))).scalar_one_or_none()
    if c:
        await _check_scope(db, user, c)

    changes: list[str] = []
    users = {u.id: u for u in (await db.execute(select(User))).scalars().all()}
    if body.get("interviewer_id") and int(body["interviewer_id"]) != sch.interviewer_id:
        itv = await _load_interviewer(db, body["interviewer_id"])
        old = users.get(sch.interviewer_id)
        changes.append(f"面试官 {old.name if old else '未指派'}→{itv.name}")
        sch.interviewer_id = itv.id
        if c:
            c.assignee_id = itv.id
    try:
        if body.get("scheduled_at"):
            when = S.parse_when(body["scheduled_at"])
            if when != sch.scheduled_at:
                S.check_when(when)
                changes.append(f"时间 {sch.scheduled_at:%m-%d %H:%M}→{when:%m-%d %H:%M}"
                               if sch.scheduled_at else f"时间→{when:%m-%d %H:%M}")
                sch.scheduled_at = when
        if body.get("plan_minutes"):
            m = int(body["plan_minutes"])
            if m not in (30, 45, 60, 90):
                raise S.ScheduleError("时长须为 30、45、60 或 90 分钟")
            sch.plan_minutes = m
        if "mode" in body:
            mode, url, location = S.validate_mode(body.get("mode"), body.get("meeting_url"),
                                                  body.get("location"))
            if (mode, url, location) != (sch.mode, sch.meeting_url, sch.location):
                changes.append(f"方式 {sch.mode}→{mode}" if mode != sch.mode else "会议信息")
            sch.mode, sch.meeting_url, sch.location = mode, url, location
    except S.ScheduleError as e:
        raise HTTPException(400, str(e)) from None
    if "meeting_code" in body:
        sch.meeting_code = str(body.get("meeting_code") or "")[:64]
    if "note" in body:
        sch.note = str(body.get("note") or "")[:256]

    if sch.scheduled_at:
        sch.invite_expires_at = S.invite_expiry(sch.scheduled_at, sch.plan_minutes)
    warnings = await _conflicts(db, sch.interviewer_id, sch.scheduled_at, sch.plan_minutes,
                                exclude_id=sch.id) if sch.scheduled_at and sch.interviewer_id else []
    db.add(DecisionLog(kind="human", actor=user.name, ability="面试安排",
                       candidate_id=sch.candidate_id,
                       summary=f"调整 {c.name if c else ''} 的{sch.round_name}："
                               + ("；".join(changes) or "更新备注")))
    await db.commit()
    return {"ok": True, "changes": changes, "warnings": warnings}


@router.post("/schedules/{sid}/cancel")
async def cancel_schedule(sid: int, body: dict, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require("interview", "schedule"))):
    """取消面试。邀请链接随之失效，面试官的日程里也不再出现。"""
    sch = await _get_active(db, sid)
    reason = str(body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "请填写取消原因")
    c = (await db.execute(select(Candidate).where(Candidate.id == sch.candidate_id))).scalar_one_or_none()
    if c:
        await _check_scope(db, user, c)
    sch.status = "已取消"
    sch.invite_token = ""
    sch.note = (f"已取消：{reason}")[:256]
    db.add(DecisionLog(kind="human", actor=user.name, ability="面试安排",
                       candidate_id=sch.candidate_id,
                       summary=f"取消 {c.name if c else ''} 的{sch.round_name}：{reason}"))
    await db.commit()
    return {"ok": True}


@router.post("/schedules/{sid}/invite/reset")
async def reset_invite(sid: int, db: AsyncSession = Depends(get_db),
                       user: User = Depends(require("interview", "schedule"))):
    """重新生成候选人邀请链接。旧链接立即失效，用于链接误发或疑似外泄。"""
    sch = await _get_active(db, sid)
    sch.invite_token = S.new_token()
    if sch.scheduled_at:
        sch.invite_expires_at = S.invite_expiry(sch.scheduled_at, sch.plan_minutes)
    db.add(DecisionLog(kind="access", actor=user.name, ability="面试安排",
                       candidate_id=sch.candidate_id,
                       summary=f"重新生成第 {sid} 场面试的候选人邀请链接，旧链接已失效"))
    await db.commit()
    return {"ok": True, "invite_token": sch.invite_token}


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
        "consent_status": sch.consent_status or "未回复",
        "status": sch.status,
        "scheduled_at": str(sch.scheduled_at or ""),
        "mode": sch.mode or "视频",
        "meeting_url": sch.meeting_url, "meeting_code": sch.meeting_code,
        "location": sch.location, "note": sch.note,
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

    # 候选人在邀请链接里明确「不同意」时，以候选人本人的表态为准，
    # 面试官不能在报告页勾选覆盖 —— 否则两处记录互相矛盾，举证时说不清。
    # 「未回复」时允许面试官确认当面取得的同意，这是邀请链接之外的正常路径。
    if consent and (sch.consent_status or "未回复") == "不同意":
        raise HTTPException(
            400, "候选人已在邀请链接中选择「不同意录音」，不能基于录音生成报告。请改用手动摘要。")

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

    via_link = (sch.consent_status or "") == "同意"
    sch.consent_recorded = True
    if not via_link:
        sch.consent_status = "同意"
    db.add(DecisionLog(kind="human", actor=user.name, ability="录音合规",
                       candidate_id=sch.candidate_id,
                       summary="候选人已明示同意录音（" + ("邀请链接中确认" if via_link
                               else "面试官确认当面取得同意") + "），同意记录写入决策日志"))

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
