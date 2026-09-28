"""培训方案的参数校验与资料拼接（从 training_api 拆出，便于复用与测试）。"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException


def parse_date(v, field: str) -> datetime | None:
    """解析前端传来的日期（YYYY-MM-DD），空值返回 None。"""
    if v in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(v)[:10])
    except ValueError:
        raise HTTPException(400, f"{field}格式不正确，应为 YYYY-MM-DD") from None


def validate_plan_fields(body: dict) -> dict:
    """统一校验方案的名称、周期与考试规则，返回清洗后的字段。"""
    title = str(body.get("title") or "").strip()
    if len(title) > 60:
        raise HTTPException(400, "方案名称最多 60 个字")

    start = parse_date(body.get("start_date"), "开始日期")
    end = parse_date(body.get("end_date"), "结束日期")
    if start and end and end < start:
        raise HTTPException(400, "结束日期不能早于开始日期")

    try:
        minutes = int(body.get("exam_minutes") or 60)
        pass_score = int(body.get("pass_score") or 60)
    except (TypeError, ValueError):
        raise HTTPException(400, "考试时长与及格分须为整数") from None
    if not 10 <= minutes <= 180:
        raise HTTPException(400, "考试时长须在 10 至 180 分钟之间")
    if not 0 <= pass_score <= 100:
        raise HTTPException(400, "及格分须在 0 至 100 之间")

    return {
        "title": title,
        "description": str(body.get("description") or "").strip()[:500],
        "start_date": start,
        # 结束日按当天 23:59:59 计，否则选「今天结束」当天就过期
        "end_date": end.replace(hour=23, minute=59, second=59) if end else None,
        "exam_minutes": minutes,
        "pass_score": pass_score,
    }


def material_text(mats: list, max_chars: int = 6000) -> str:
    """把公司资料拼成提示词上下文，超长时截断。"""
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
