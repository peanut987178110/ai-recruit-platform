"""时间格式化工具。"""
from __future__ import annotations

from datetime import datetime, timedelta


def day_key(dt: datetime | None = None, offset_days: int = 0) -> str:
    d = (dt or datetime.now()) + timedelta(days=offset_days)
    return d.strftime("%Y-%m-%d")


def humanize(dt: datetime | None) -> str:
    if not dt:
        return ""
    delta = datetime.now() - dt
    s = int(delta.total_seconds())
    if s < 60:
        return "刚刚"
    if s < 3600:
        return f"{s // 60} 分钟前"
    if s < 86400:
        return f"{s // 3600} 小时前"
    if s < 86400 * 7:
        return f"{s // 86400} 天前"
    return dt.strftime("%Y-%m-%d")
