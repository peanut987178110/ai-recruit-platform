"""智能体接口。

提供两组能力：
- 对话：自然语言驱动，Agent 自己选择技能串起来
- 技能直调：前端按钮直接触发某个技能，走同一份实现

两条路径共用 app/agents/skills/impl.py 里的 handler，避免两套逻辑漂移。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.skills import register_all_skills
from app.agents.skills.base import SKILL_REGISTRY, describe_skills
from app.agents.skills import impl as _impl  # noqa: F401  导入即注册
from app.core.deps import current_user, require
from app.db.models import TrackEvent, User
from app.db.session import get_db

router = APIRouter(prefix="/agent", tags=["智能体"])


@router.get("/skills")
async def list_skills(user: User = Depends(current_user)):
    """列出平台所有技能，供前端展示与直接调用。"""
    return {
        "skills": describe_skills(),
        "note": "每个技能都有两个入口：被智能体自动调用，或由界面按钮直接触发。",
    }


@router.post("/chat")
async def chat_api(body: dict, db: AsyncSession = Depends(get_db),
                   user: User = Depends(current_user)):
    """对话入口。Agent 会自己判断该调用哪些技能。"""
    from app.agents.orchestrator import chat

    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "请输入内容")

    out = await chat(message, body.get("history") or [])

    db.add(TrackEvent(event="agent_chat", user_id=user.id,
                      payload={"message": message[:200],
                               "steps": out.get("steps", []),
                               "degraded": out.get("degraded", False)}))
    await db.commit()
    return out


@router.post("/invoke/{skill_name}")
async def invoke_skill(skill_name: str, body: dict, db: AsyncSession = Depends(get_db),
                       user: User = Depends(current_user)):
    """直接调用某个技能。前端按钮走这条路。"""
    register_all_skills()
    _impl.register_all_skills()

    skill = SKILL_REGISTRY.get(skill_name)
    if not skill:
        raise HTTPException(404, f"技能 {skill_name} 不存在")

    try:
        kwargs = skill.args_schema.model_validate(body or {})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"参数不合法：{str(e)[:200]}") from None

    res = await skill.handler(**kwargs.model_dump())

    db.add(TrackEvent(event="skill_invoke", user_id=user.id,
                      payload={"skill": skill_name, "ok": res.ok}))
    await db.commit()

    return {
        "ok": res.ok,
        "skill": skill_name,
        "label": skill.label,
        "summary": res.summary,
        "data": res.data,
        "meta": res.meta,
        "error": res.error,
        "needs_human": skill.needs_human,
    }
