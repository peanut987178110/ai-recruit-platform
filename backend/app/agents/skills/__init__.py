"""技能包。导入 impl 即完成所有技能的注册（装饰器在类定义时执行）。"""
from app.agents.skills import impl  # noqa: F401
from app.agents.skills.base import (  # noqa: F401
    SKILL_REGISTRY, Skill, SkillResult, describe_skills, get_tools, register,
)


def register_all_skills() -> int:
    """确保所有技能已注册，返回注册数量。导入本包时已完成，这里只为幂等调用。"""
    return len(SKILL_REGISTRY)


__all__ = [
    "SKILL_REGISTRY", "Skill", "SkillResult", "describe_skills",
    "get_tools", "register", "register_all_skills",
]
