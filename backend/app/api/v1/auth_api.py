"""认证与账号管理。

权限模型：
- 初始超管账号 `admin` 拥有全部权限，可创建/停用任何账号。
- 其它角色有三条注册路径：
    1. 自助注册（开放注册时）—— 可选角色但受白名单限制，不能自封管理员
    2. 由 HR 负责人创建 —— 可指定业务线与部门
    3. 由超管创建 —— 可创建任何角色，含系统管理员
- 超管账号不可被非超管停用或删除，避免把自己锁在门外。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    ALL_ROLES, ROLE_ADMIN, ROLE_HR_LEAD, current_user, require,
)
from app.core.security import (
    hash_password, make_token, parse_token, password_strength_issue, verify_password,
)
from app.db.models import DecisionLog, User
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["认证"])

# 自助注册时可选择的角色。系统管理员不在其中 —— 管理员只能由超管创建，
# 否则任何人都能给自己开一个看全量数据的高权限账号。
SELF_SIGNUP_ROLES = ["招聘HR", "业务面试官", "用人经理", "带教人"]


class LoginIn(BaseModel):
    userid: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RegisterIn(BaseModel):
    userid: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    role: str = "招聘HR"
    business_line: str = ""
    department: str = ""
    email: str = ""


class UserOut(BaseModel):
    id: int
    userid: str
    name: str
    role: str
    business_line: str
    department: str
    email: str
    active: bool
    can_view_resume: bool
    mask_contact: bool
    is_super: bool
    created_by: str
    last_login_at: str


def _out(u: User) -> UserOut:
    return UserOut(
        id=u.id, userid=u.userid, name=u.name, role=u.role,
        business_line=u.business_line, department=u.department, email=u.email,
        active=u.active, can_view_resume=u.can_view_resume, mask_contact=u.mask_contact,
        is_super=u.is_super, created_by=u.created_by,
        last_login_at=str(u.last_login_at or ""),
    )


@router.post("/login")
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)):
    """账号密码登录。"""
    u = (await db.execute(select(User).where(
        User.userid == body.userid.strip()))).scalar_one_or_none()

    # 账号不存在与密码错误返回同一句话，避免账号枚举
    if not u or not u.password_hash or not verify_password(body.password, u.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号或密码错误")
    if not u.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该账号已被停用，请联系管理员")

    token, exp = make_token(u.userid, u.role)
    u.last_login_at = datetime.now()
    db.add(DecisionLog(kind="access", actor=u.name if False else u.userid,
                       ability="登录", summary=f"账号 {u.userid}（{u.role}）登录成功"))
    await db.commit()

    return {"token": token, "expires_at": exp, "user": _out(u).model_dump()}


@router.post("/register")
async def register(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    """自助注册。开放注册，但角色受限 —— 不能自封管理员。"""
    uid = body.userid.strip()
    name = body.name.strip()

    if not uid.isascii() or not uid.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(400, "账号只能包含字母、数字、下划线和短横线")
    if body.role not in SELF_SIGNUP_ROLES:
        raise HTTPException(
            400,
            f"该角色不支持自助注册。可选：{'、'.join(SELF_SIGNUP_ROLES)}。"
            f"系统管理员等角色请由 admin 账号创建。",
        )
    if issue := password_strength_issue(body.password):
        raise HTTPException(400, issue)

    exists = (await db.execute(select(User).where(User.userid == uid))).scalar_one_or_none()
    if exists:
        raise HTTPException(400, f"账号「{uid}」已被使用，请换一个")

    u = User(
        userid=uid, name=name or uid, role=body.role,
        business_line=body.business_line, department=body.department,
        email=body.email, password_hash=hash_password(body.password),
        created_by="self",
    )
    db.add(u)
    await db.flush()
    db.add(DecisionLog(kind="access", actor=uid, ability="注册",
                       summary=f"新账号 {uid}（{body.role}）自助注册"))
    await db.commit()

    token, exp = make_token(u.userid, u.role)
    return {"token": token, "expires_at": exp, "user": _out(u).model_dump()}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return _out(user).model_dump()


@router.post("/change-password")
async def change_password(body: dict, db: AsyncSession = Depends(get_db),
                          user: User = Depends(current_user)):
    old = str(body.get("old_password", ""))
    new = str(body.get("new_password", ""))

    if user.password_hash and not verify_password(old, user.password_hash):
        raise HTTPException(400, "原密码不正确")
    if issue := password_strength_issue(new):
        raise HTTPException(400, issue)

    u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    u.password_hash = hash_password(new)
    db.add(DecisionLog(kind="access", actor=u.userid, ability="修改密码",
                       summary=f"账号 {u.userid} 修改了密码"))
    await db.commit()
    return {"ok": True, "message": "密码已修改，请用新密码重新登录"}


# ---------------- 账号管理 ----------------

@router.get("/users")
async def list_accounts(db: AsyncSession = Depends(get_db),
                        user: User = Depends(current_user)):
    """账号列表。普通用户只能看到基础信息，管理动作需要权限。"""
    rows = (await db.execute(select(User).order_by(User.id))).scalars().all()
    return {
        "users": [_out(u).model_dump() for u in rows],
        "can_manage": user.is_super or user.role == ROLE_HR_LEAD,
        "self_signup_roles": SELF_SIGNUP_ROLES,
        "assignable_roles": ALL_ROLES if user.is_super else SELF_SIGNUP_ROLES,
    }


@router.post("/users")
async def create_account(body: RegisterIn, db: AsyncSession = Depends(get_db),
                         admin: User = Depends(current_user)):
    """由管理员创建账号。超管可创建任何角色；HR 负责人只能创建非管理类角色。"""
    if not (admin.is_super or admin.role == ROLE_HR_LEAD):
        raise HTTPException(403, "只有超级管理员或 HR 负责人可以创建账号")

    uid = body.userid.strip()
    if not uid.isascii() or not uid.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(400, "账号只能包含字母、数字、下划线和短横线")
    if body.role not in ALL_ROLES:
        raise HTTPException(400, f"角色须为：{'、'.join(ALL_ROLES)}")
    if body.role in (ROLE_ADMIN, ROLE_HR_LEAD) and not admin.is_super:
        raise HTTPException(403, f"只有超级管理员可以创建「{body.role}」账号")
    if issue := password_strength_issue(body.password):
        raise HTTPException(400, issue)

    exists = (await db.execute(select(User).where(User.userid == uid))).scalar_one_or_none()
    if exists:
        raise HTTPException(400, f"账号「{uid}」已存在")

    u = User(
        userid=uid, name=body.name.strip() or uid, role=body.role,
        business_line=body.business_line, department=body.department,
        email=body.email, password_hash=hash_password(body.password),
        created_by=admin.userid,
    )
    db.add(u)
    await db.flush()
    db.add(DecisionLog(kind="access", actor=admin.userid, ability="账号管理",
                       summary=f"{admin.userid} 创建了账号 {uid}（{body.role}）"))
    await db.commit()
    return {"ok": True, "user": _out(u).model_dump()}


@router.post("/users/{uid}/toggle")
async def toggle_account(uid: str, db: AsyncSession = Depends(get_db),
                         admin: User = Depends(current_user)):
    """启用 / 停用账号。"""
    if not (admin.is_super or admin.role == ROLE_HR_LEAD):
        raise HTTPException(403, "只有超级管理员或 HR 负责人可以停用账号")

    u = (await db.execute(select(User).where(User.userid == uid))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "账号不存在")
    if u.is_super and not admin.is_super:
        raise HTTPException(403, "不能停用超级管理员账号")
    if u.userid == admin.userid:
        raise HTTPException(400, "不能停用自己的账号")

    u.active = not u.active
    db.add(DecisionLog(kind="access", actor=admin.userid, ability="账号管理",
                       summary=f"{admin.userid} {'启用' if u.active else '停用'}了账号 {uid}"))
    await db.commit()
    return {"ok": True, "active": u.active}


@router.post("/users/{uid}/reset-password")
async def reset_password(uid: str, body: dict, db: AsyncSession = Depends(get_db),
                         admin: User = Depends(current_user)):
    """重置他人密码。"""
    if not (admin.is_super or admin.role == ROLE_HR_LEAD):
        raise HTTPException(403, "只有超级管理员或 HR 负责人可以重置密码")

    u = (await db.execute(select(User).where(User.userid == uid))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "账号不存在")
    if u.is_super and not admin.is_super:
        raise HTTPException(403, "不能重置超级管理员的密码")

    new = str(body.get("new_password", ""))
    if issue := password_strength_issue(new):
        raise HTTPException(400, issue)

    u.password_hash = hash_password(new)
    db.add(DecisionLog(kind="access", actor=admin.userid, ability="账号管理",
                       summary=f"{admin.userid} 重置了账号 {uid} 的密码"))
    await db.commit()
    return {"ok": True, "message": f"已重置 {uid} 的密码"}


@router.delete("/users/{uid}")
async def delete_account(uid: str, db: AsyncSession = Depends(get_db),
                         admin: User = Depends(current_user)):
    """删除账号。仅超管可操作，且不能删自己。"""
    if not admin.is_super:
        raise HTTPException(403, "只有超级管理员可以删除账号")
    if uid == admin.userid:
        raise HTTPException(400, "不能删除自己的账号")

    u = (await db.execute(select(User).where(User.userid == uid))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "账号不存在")

    db.add(DecisionLog(kind="access", actor=admin.userid, ability="账号管理",
                       summary=f"{admin.userid} 删除了账号 {uid}"))
    await db.delete(u)
    await db.commit()
    return {"ok": True}


@router.get("/roles")
async def role_list():
    """可选角色及其权限摘要，供注册页展示。"""
    from app.core.deps import PERMISSIONS

    desc = {
        "系统管理员": "配置阈值与参数、管理账号；不可查看简历正文",
        "HR负责人": "全部业务线数据、可编辑能力模型、创建账号",
        "招聘HR": "本人负责岗位的候选人，日常复核与面试安排",
        "用人经理": "本部门在招岗位候选人，可查看复核结果",
        "业务面试官": "仅被指派面试的候选人，出题与评分",
        "带教人": "培训方案生成、考核终评",
        "法务审计": "全量只读（不含联系方式），审计日志",
    }
    return [{
        "role": r,
        "description": desc.get(r, ""),
        "self_signup": r in SELF_SIGNUP_ROLES,
        "permissions": {k: sorted(v) for k, v in PERMISSIONS.get(r, {}).items()},
    } for r in ALL_ROLES]
