"""权限与数据隔离（PRD 1.4、5.4）。

两条约束是硬要求，来自数据合规评估意见：
  一、面试官只能看到被指派的候选人，避免简历在组织内无边界流动。
  二、系统管理员可配置阈值但不可查看简历正文，防止运维角色成为数据合规的缺口。

实现方式：不靠前端隐藏，而是在数据查询层统一加过滤条件。
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import parse_token
from app.db.models import User
from app.db.session import get_db

# 角色常量
ROLE_HR = "招聘HR"
ROLE_HR_LEAD = "HR负责人"
ROLE_MANAGER = "用人经理"
ROLE_INTERVIEWER = "业务面试官"
ROLE_MENTOR = "带教人"
ROLE_LEGAL = "法务审计"
ROLE_ADMIN = "系统管理员"
ROLE_NEWCOMER = "新人"

ALL_ROLES = [ROLE_HR, ROLE_HR_LEAD, ROLE_MANAGER, ROLE_INTERVIEWER,
             ROLE_MENTOR, ROLE_LEGAL, ROLE_ADMIN, ROLE_NEWCOMER]

# 各角色对模块的操作权限（PRD 1.4 表格的代码化）
PERMISSIONS: dict[str, dict[str, set[str]]] = {
    ROLE_HR: {
        "model": {"view"}, "screen": {"view", "edit", "adopt", "reject", "rescue", "import"},
        "interview": {"view", "schedule"}, "training": {"view"}, "board": {"view_own"},
    },
    ROLE_HR_LEAD: {
        "model": {"view", "edit"}, "screen": {"view", "edit", "adopt", "reject", "rescue", "import"},
        "interview": {"view", "schedule"}, "training": {"view"}, "board": {"view_all"},
    },
    ROLE_MANAGER: {
        "model": {"nominate"}, "screen": {"view_review"},
        "interview": {"view", "edit", "schedule"},
        "training": {"view"}, "board": {"view_dept"},
    },
    ROLE_INTERVIEWER: {
        "model": set(), "screen": {"view_assigned"}, "interview": {"view", "edit"},
        "training": set(), "board": set(),
    },
    # 带教人需要读岗位与能力项：培训大纲是按能力项组织的，
    # 不给他读模型权限就没法生成方案。只给 view，不给 edit —— 
    # 录用标准由 HR 定，带教人不该改。
    ROLE_MENTOR: {
        "model": {"view"}, "screen": set(), "interview": set(),
        "training": {"view", "edit", "publish", "grade", "assign"},
        "board": {"view_own"},
    },
    ROLE_LEGAL: {
        "model": {"view"}, "screen": {"view_log"}, "interview": {"view_log"},
        "training": set(), "board": {"view_all"},
    },
    # 新人只能看指派给自己的培训与考试，看不到任何候选人数据。
    # 这是最小权限：新人没有招聘职责，让他能看到候选人简历是数据越界。
    ROLE_NEWCOMER: {
        "model": set(), "screen": set(), "interview": set(),
        "training": {"view", "learn", "take_exam"},
        "board": set(),
    },
    ROLE_ADMIN: {
        "model": {"config"}, "screen": {"config"}, "interview": {"config"},
        "training": {"config"}, "board": {"view_all"},
    },
}


# 超级管理员账号（userid == "admin"）的权限集合。
# 它拥有全部模块的全部动作 —— admin 是平台所有者，需要能检查和配置任何模块。
# 「不可查看简历正文」这条合规约束仍单独生效（见 User.can_view_resume），
# 它是数据脱敏层面的限制，与「能否访问模块」是两件事。
SUPER_PERMISSIONS: dict[str, set[str]] = {
    "model": {"view", "edit", "config", "nominate"},
    "screen": {"view", "view_review", "view_assigned", "view_log", "edit",
               "adopt", "reject", "rescue", "import", "config"},
    "interview": {"view", "view_log", "edit", "config", "schedule"},
    "training": {"view", "edit", "publish", "config", "learn", "take_exam",
                 "grade", "assign"},
    "board": {"view_all", "view_own", "view_dept"},
}


def can(role: str, module: str, action: str, is_super: bool = False) -> bool:
    """是否有权限。超管账号绕开角色矩阵，直接拥有全部动作。"""
    if is_super:
        return action in SUPER_PERMISSIONS.get(module, set())
    return action in PERMISSIONS.get(role, {}).get(module, set())


async def current_user(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """解析当前用户。

    优先用 `Authorization: Bearer <token>`，这是正常登录路径。
    `X-User-Id` 是给自动化测试用的旁路，由 settings.allow_header_auth 控制，
    生产环境应把 APP_ALLOW_HEADER_AUTH 设为 false 关掉它 ——
    否则任何人伪造一个请求头就能变成超管。
    """
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    if token:
        payload = parse_token(token)
        if not payload:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期，请重新登录")
        u = (await db.execute(select(User).where(
            User.userid == payload.get("s")))).scalar_one_or_none()
        if not u or not u.active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号不存在或已被停用")
        return u

    if settings.allow_header_auth and x_user_id:
        u = (await db.execute(select(User).where(
            User.userid == x_user_id))).scalar_one_or_none()
        if u:
            return u

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")


def require(module: str, action: str):
    """依赖注入式的权限校验。"""
    async def _dep(user: User = Depends(current_user)) -> User:
        if not can(user.role, module, action, user.is_super):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"角色「{user.role}」无权在{module}模块执行 {action} 操作",
            )
        return user
    return _dep


def mask_resume_text(user: User, text: str) -> str:
    """系统管理员不可查看简历正文（PRD 1.4）。返回占位说明而非内容。"""
    if not user.can_view_resume:
        return "［当前角色无权查看简历正文。系统管理员可配置参数但不可查看简历内容，这是数据合规要求。］"
    return text


def mask_contact(user: User, phone: str, email: str) -> tuple[str, str]:
    """法务审计全量只读且不含联系方式（PRD 1.4）。"""
    if not user.mask_contact:
        return phone, email
    return ("［已隐藏］" if phone else ""), ("［已隐藏］" if email else "")
