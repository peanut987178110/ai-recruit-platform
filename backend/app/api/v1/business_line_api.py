"""业务线字典管理。

业务线是数据隔离的边界：用人经理按业务线看候选人，知识库按业务线检索。
所以它必须是受控字典而不是自由文本 —— 账号填「电商业务线」、岗位填「电商线」，
两边字符串对不上，隔离就悄悄失效，而且没有任何报错。

规则：
- 列表对所有人开放（注册页、新建账号、新建岗位都要用），不含敏感信息。
- 增、改、停用、删除只有超管与 HR 负责人可做。
- 改名会同步到所有引用它的账号、岗位、知识库，保证隔离不断。
- 删除时若仍有引用，必须指定迁移目标；否则拒绝删除。停用是更安全的替代。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ROLE_HR_LEAD, current_user
from app.db.models import BusinessLine, DecisionLog, KnowledgeDoc, Position, User
from app.db.session import get_db

router = APIRouter(prefix="/business-lines", tags=["平台 业务线"])

# 引用业务线的三张表。改名与删除迁移都要一起处理，漏一张隔离就断。
_REF_MODELS = ((User, "账号"), (Position, "岗位"), (KnowledgeDoc, "知识库文档"))


class LineIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    description: str = Field(default="", max_length=200)
    sort: int = 0


class LineUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=32)
    description: str | None = Field(default=None, max_length=200)
    sort: int | None = None
    active: bool | None = None


def _can_manage(user: User) -> bool:
    return user.is_super or user.role == ROLE_HR_LEAD


async def _usage(db: AsyncSession, name: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for model, label in _REF_MODELS:
        out[label] = (await db.execute(select(func.count()).select_from(model).where(
            model.business_line == name))).scalar_one()
    return out


async def active_line_names(db: AsyncSession) -> set[str]:
    """供其它模块校验业务线取值。"""
    return {b.name for b in (await db.execute(select(BusinessLine).where(
        BusinessLine.active.is_(True)))).scalars().all()}


async def validate_line(db: AsyncSession, name: str, *, allow_empty: bool = True) -> str:
    """校验业务线取值必须来自字典。空串表示「不限」。"""
    name = (name or "").strip()
    if not name:
        if allow_empty:
            return ""
        raise HTTPException(400, "请选择业务线")
    if name not in await active_line_names(db):
        raise HTTPException(400, f"业务线「{name}」不存在或已停用，请从列表中选择")
    return name


@router.get("")
async def list_lines(include_inactive: bool = False, db: AsyncSession = Depends(get_db)):
    """业务线列表。注册页在登录前就要用，所以不要求登录；只返回名称与说明。"""
    q = select(BusinessLine).order_by(BusinessLine.sort, BusinessLine.id)
    if not include_inactive:
        q = q.where(BusinessLine.active.is_(True))
    rows = (await db.execute(q)).scalars().all()
    return [{"id": b.id, "name": b.name, "description": b.description,
             "sort": b.sort, "active": b.active} for b in rows]


@router.get("/manage")
async def manage_lines(db: AsyncSession = Depends(get_db),
                       user: User = Depends(current_user)):
    """管理视图：含停用项与引用数量，便于判断能否删除。"""
    if not _can_manage(user):
        raise HTTPException(403, "只有超级管理员或 HR 负责人可以管理业务线")
    rows = (await db.execute(select(BusinessLine).order_by(
        BusinessLine.sort, BusinessLine.id))).scalars().all()
    out = []
    for b in rows:
        usage = await _usage(db, b.name)
        out.append({"id": b.id, "name": b.name, "description": b.description,
                    "sort": b.sort, "active": b.active,
                    "usage": usage, "in_use": sum(usage.values())})
    return out


@router.post("")
async def create_line(body: LineIn, db: AsyncSession = Depends(get_db),
                      user: User = Depends(current_user)):
    if not _can_manage(user):
        raise HTTPException(403, "只有超级管理员或 HR 负责人可以新增业务线")
    name = body.name.strip()
    if (await db.execute(select(BusinessLine).where(BusinessLine.name == name))).scalar_one_or_none():
        raise HTTPException(400, f"业务线「{name}」已存在")
    b = BusinessLine(name=name, description=body.description.strip(), sort=body.sort)
    db.add(b)
    await db.flush()
    db.add(DecisionLog(kind="config", actor=user.userid, ability="业务线",
                       summary=f"新增业务线「{name}」"))
    await db.commit()
    return {"ok": True, "id": b.id, "name": b.name}


@router.put("/{lid}")
async def update_line(lid: int, body: LineUpdate, db: AsyncSession = Depends(get_db),
                      user: User = Depends(current_user)):
    """修改业务线。改名会级联到账号、岗位、知识库，在同一事务里完成。"""
    if not _can_manage(user):
        raise HTTPException(403, "只有超级管理员或 HR 负责人可以修改业务线")
    b = (await db.execute(select(BusinessLine).where(BusinessLine.id == lid))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "业务线不存在")

    changes: list[str] = []
    old_name = b.name
    if body.name is not None and body.name.strip() != old_name:
        new_name = body.name.strip()
        if (await db.execute(select(BusinessLine).where(BusinessLine.name == new_name))).scalar_one_or_none():
            raise HTTPException(400, f"业务线「{new_name}」已存在")
        moved = await _migrate(db, old_name, new_name)
        b.name = new_name
        changes.append(f"改名「{old_name}」→「{new_name}」，同步 {moved} 条引用")
    if body.description is not None:
        b.description = body.description.strip()
    if body.sort is not None:
        b.sort = body.sort
    if body.active is not None and body.active != b.active:
        b.active = body.active
        changes.append("启用" if body.active else "停用")

    db.add(DecisionLog(kind="config", actor=user.userid, ability="业务线",
                       summary=f"修改业务线「{old_name}」：" + ("；".join(changes) or "更新说明")))
    await db.commit()
    return {"ok": True, "changes": changes}


@router.delete("/{lid}")
async def delete_line(lid: int, migrate_to: str = "", db: AsyncSession = Depends(get_db),
                      user: User = Depends(current_user)):
    """删除业务线。

    仍被引用时不能直接删：那些账号和岗位会变成「属于一个不存在的业务线」，
    用人经理从此看不到本该看到的候选人。必须指定迁移目标，或改用停用。
    """
    if not _can_manage(user):
        raise HTTPException(403, "只有超级管理员或 HR 负责人可以删除业务线")
    b = (await db.execute(select(BusinessLine).where(BusinessLine.id == lid))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "业务线不存在")

    usage = await _usage(db, b.name)
    total = sum(usage.values())
    moved = 0
    if total:
        target = (migrate_to or "").strip()
        if not target:
            detail = "、".join(f"{k} {v} 个" for k, v in usage.items() if v)
            raise HTTPException(
                400,
                f"业务线「{b.name}」仍被 {detail} 引用，不能直接删除。"
                f"请选择迁移到哪条业务线，或改为停用。")
        if target == b.name:
            raise HTTPException(400, "迁移目标不能是自身")
        await validate_line(db, target, allow_empty=False)
        moved = await _migrate(db, b.name, target)

    name = b.name
    await db.delete(b)
    db.add(DecisionLog(kind="config", actor=user.userid, ability="业务线",
                       summary=f"删除业务线「{name}」"
                               + (f"，{moved} 条引用迁移至「{migrate_to}」" if moved else "")))
    await db.commit()
    return {"ok": True, "moved": moved}


async def _migrate(db: AsyncSession, old: str, new: str) -> int:
    moved = 0
    for model, _label in _REF_MODELS:
        res = await db.execute(update(model).where(model.business_line == old)
                               .values(business_line=new))
        moved += res.rowcount or 0
    return moved
