"""面试安排的规则。与 HTTP 无关的校验与计算放在这里，便于测试与复用。"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from urllib.parse import urlparse

MODES = ("视频", "现场", "电话")
ROUNDS = ("初面", "二面", "三面", "终面", "HR面")
ACTIVE_STATUS = "待面试"

# 邀请链接在面试结束后再保留 24 小时，方便候选人事后回看时间与地点。
INVITE_GRACE = timedelta(hours=24)
# 回填过去的面试最多允许 1 天前；再早通常是填错了日期。
PAST_TOLERANCE = timedelta(days=1)
FUTURE_LIMIT = timedelta(days=180)


class ScheduleError(ValueError):
    """面向用户的校验错误，消息可直接展示。"""


def new_token() -> str:
    # 32 字节随机数，URL 安全。不可枚举 —— 这是候选人免登录访问的唯一凭据。
    return secrets.token_urlsafe(32)


def invite_expiry(scheduled_at: datetime, plan_minutes: int) -> datetime:
    return scheduled_at + timedelta(minutes=plan_minutes) + INVITE_GRACE


def parse_when(raw) -> datetime:
    if not raw:
        raise ScheduleError("请选择面试时间")
    try:
        # 前端 <input type="datetime-local"> 给出 2026-10-08T14:30
        return datetime.fromisoformat(str(raw).replace("Z", ""))
    except ValueError:
        raise ScheduleError("面试时间格式不正确") from None


def check_when(when: datetime, now: datetime | None = None) -> None:
    now = now or datetime.now()
    if when < now - PAST_TOLERANCE:
        raise ScheduleError("面试时间早于昨天，请检查日期是否填错")
    if when > now + FUTURE_LIMIT:
        raise ScheduleError("面试时间不能晚于 180 天后")


def check_meeting_url(url: str) -> str:
    """只接受 http(s) 链接。

    这个链接会原样渲染成候选人页面上的「进入会议」按钮，
    若放行 javascript: 之类的协议，就成了面向外部人员的脚本注入入口。
    """
    url = (url or "").strip()
    if not url:
        raise ScheduleError("视频面试请填写会议链接（腾讯会议、飞书、Zoom 等）")
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ScheduleError("会议链接须以 http:// 或 https:// 开头")
    if len(url) > 500:
        raise ScheduleError("会议链接过长")
    return url


def validate_mode(mode: str, meeting_url: str, location: str) -> tuple[str, str, str]:
    mode = (mode or "视频").strip()
    if mode not in MODES:
        raise ScheduleError(f"面试方式须为：{'、'.join(MODES)}")
    if mode == "视频":
        return mode, check_meeting_url(meeting_url), ""
    if mode == "现场":
        location = (location or "").strip()
        if not location:
            raise ScheduleError("现场面试请填写面试地点")
        return mode, "", location[:128]
    return mode, "", ""


def overlaps(a_start: datetime, a_min: int, b_start: datetime, b_min: int) -> bool:
    a_end = a_start + timedelta(minutes=a_min)
    b_end = b_start + timedelta(minutes=b_min)
    return a_start < b_end and b_start < a_end
