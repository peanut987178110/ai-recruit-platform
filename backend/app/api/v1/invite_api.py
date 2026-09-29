"""候选人面试邀请（免登录）。

候选人不是平台用户，不该为一场面试注册账号。安排面试时生成一个随机令牌，
HR 通过邮件或 ATS 把链接发给候选人，候选人凭链接：
  - 查看面试时间、方式、会议入口或地点；
  - 明示是否同意面试录音（PRD 3.3.3「录音需候选人明示同意后开启」）。

安全边界（这两个接口不要求登录，所以必须收紧）：
  - 令牌是 32 字节随机串，不可枚举；无效与过期统一返回 404，不泄露「这个令牌曾经存在」。
  - 只返回候选人本人应当知道的信息：不含面试官姓名、题目、评分、能力项、简历内容。
  - 取消或重发后旧令牌立即失效；面试结束 24 小时后自动过期。
  - 同意动作写入决策日志，供事后举证。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Candidate, DecisionLog, InterviewSchedule, Position, TrackEvent
from app.db.session import get_db
from app.services.schedule_service import ACTIVE_STATUS

router = APIRouter(prefix="/public/invite", tags=["M3 候选人邀请（免登录）"])

_NOT_FOUND = "邀请链接无效或已过期。如需参加面试，请联系发送邀请的 HR。"


async def _by_token(db: AsyncSession, token: str) -> InterviewSchedule:
    # 长度不对直接拒绝，不去查库
    if not token or len(token) < 20 or len(token) > 64:
        raise HTTPException(404, _NOT_FOUND)
    sch = (await db.execute(select(InterviewSchedule).where(
        InterviewSchedule.invite_token == token))).scalar_one_or_none()
    if not sch or sch.status != ACTIVE_STATUS:
        raise HTTPException(404, _NOT_FOUND)
    if sch.invite_expires_at and sch.invite_expires_at < datetime.now():
        raise HTTPException(404, _NOT_FOUND)
    return sch


@router.get("/{token}")
async def view_invite(token: str, db: AsyncSession = Depends(get_db)):
    sch = await _by_token(db, token)
    c = (await db.execute(select(Candidate).where(Candidate.id == sch.candidate_id))).scalar_one_or_none()
    pos = (await db.execute(select(Position).where(
        Position.id == c.position_id))).scalar_one_or_none() if c else None
    now = datetime.now()
    started = bool(sch.scheduled_at and sch.scheduled_at <= now)
    return {
        "candidate_name": c.name if c else "",
        "position_name": pos.name if pos else "",
        "round_name": sch.round_name,
        "scheduled_at": str(sch.scheduled_at or ""),
        "plan_minutes": sch.plan_minutes,
        "mode": sch.mode or "视频",
        "meeting_url": sch.meeting_url,
        "meeting_code": sch.meeting_code,
        "location": sch.location,
        "consent_status": sch.consent_status or "未回复",
        "consent_editable": not started,
        "started": started,
    }


@router.post("/{token}/consent")
async def submit_consent(token: str, body: dict, db: AsyncSession = Depends(get_db)):
    """候选人表态是否同意录音。面试开始前可改；开始后以最后一次表态为准。"""
    sch = await _by_token(db, token)
    if sch.scheduled_at and sch.scheduled_at <= datetime.now():
        raise HTTPException(400, "面试已开始，录音意愿请当面告知面试官")
    agree = body.get("agree")
    if not isinstance(agree, bool):
        raise HTTPException(400, "请选择同意或不同意")

    sch.consent_status = "同意" if agree else "不同意"
    sch.consent_recorded = agree
    sch.consent_at = datetime.now()
    db.add(DecisionLog(kind="human", actor="候选人（邀请链接）", ability="录音同意",
                       candidate_id=sch.candidate_id,
                       summary=f"候选人通过邀请链接{'同意' if agree else '不同意'}第 {sch.id} 场面试录音"))
    db.add(TrackEvent(event="interview_consent", payload={
        "schedule_id": sch.id, "agree": agree}))
    await db.commit()
    return {"ok": True, "consent_status": sch.consent_status}
