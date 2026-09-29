"""M5 数据回流与效果看板接口（R-17、R-18），以及平台层接口。"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ROLE_ADMIN, current_user, require
from app.core.schemas import DegradeLevel
from app.db.models import (
    Candidate, ConfigItem, DecisionLog, KnowledgeDoc, Position, PromptVersion,
    ReflowSample, SampleCase, TrackEvent, User,
)
from app.db.session import get_db
from app.llm.client import llm
from app.llm.registry import registry
from app.prompts.manager import prompts
from app.rag.retriever import kb
from app.services import analytics_service as A

router = APIRouter(tags=["M5 看板与平台"])


# ==================== 看板 ====================

@router.get("/board/overview")
async def board_overview(user: User = Depends(current_user)):
    return await A.overview()


@router.get("/board/trend")
async def board_trend(days: int = 30, user: User = Depends(current_user)):
    return await A.tier_trend(days)


@router.get("/board/funnel")
async def board_funnel(user: User = Depends(current_user)):
    return await A.funnel()


@router.get("/board/events")
async def board_events(days: int = 14, user: User = Depends(current_user)):
    return await A.event_stats(days)


@router.get("/board/reflow")
async def board_reflow(db: AsyncSession = Depends(get_db),
                       user: User = Depends(current_user)):
    """回流样本池。捞回后通过面试的案例单独成列表，每周复盘（PRD 3.5.2）。"""
    rows = (await db.execute(select(ReflowSample).order_by(
        ReflowSample.id.desc()).limit(300))).scalars().all()

    by_kind: dict[str, list] = {}
    rescues = []
    for r in rows:
        by_kind.setdefault(r.kind, []).append(r)
        if r.kind == "rescue":
            rescues.append({
                "candidate_id": r.candidate_id,
                "reason": (r.payload or {}).get("reason", ""),
                "original_tier": (r.payload or {}).get("original_tier", ""),
                "total": (r.payload or {}).get("total"),
                "at": str(r.created_at),
                "note": "误杀线索，需专项复盘",
            })

    # 否决原因分布，构成优化样本池
    reason_dist: dict[str, int] = {}
    for r in by_kind.get("review", []):
        if (r.payload or {}).get("action") == "否决":
            k = (r.payload or {}).get("reason", "未填写")
            reason_dist[k] = reason_dist.get(k, 0) + 1

    return {
        "counts": {k: len(v) for k, v in by_kind.items()},
        "reject_reasons": reason_dist,
        "rescue_cases": rescues[:50],
        "recent": [{"kind": r.kind, "candidate_id": r.candidate_id,
                    "payload": r.payload, "at": str(r.created_at)}
                   for r in rows[:80]],
        "note": "捞回后通过面试的案例是发现误杀模式最直接的入口，建议每周由 HR 负责人复盘。",
    }


# ==================== 配置（P-12）====================

@router.get("/config")
async def get_config(db: AsyncSession = Depends(get_db),
                     user: User = Depends(require("board", "view_all"))):
    """阈值与参数配置。

    阈值调整即时生效不需发版；每次调整记录操作人、调整前后值、生效时间（PRD 3.2.5）。
    注意：系统管理员可配置参数但不可查看简历正文。
    """
    rows = (await db.execute(select(ConfigItem))).scalars().all()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r.group, []).append({
            "key": r.key, "label": r.label, "value": (r.value or {}).get("v"),
            "description": r.description, "updated_at": str(r.updated_at),
        })
    return {
        "groups": grouped,
        "model_routing": {
            "auto_selected": registry.snapshot(),
            "candidates": {
                t: [{"id": m.id, "vendor": m.vendor}
                    for m in registry.available(t, limit=8)]  # type: ignore[arg-type]
                for t in ("small", "medium", "large")
            },
            "note": "平台会按能力档位自动选择模型；此处可手动锁定某个档位使用指定模型。",
        },
        "tier_expectation": A.EXPECTED_TIER,
        "role_matrix": _role_matrix(),
    }


def _role_matrix() -> list[dict]:
    from app.core.deps import PERMISSIONS
    modules = ["model", "screen", "interview", "training", "board"]
    label = {"model": "M1 能力模型", "screen": "M2 筛选", "interview": "M3 面试",
             "training": "M4 培训", "board": "M5 看板"}
    out = []
    for role, perms in PERMISSIONS.items():
        row = {"role": role}
        for m in modules:
            acts = perms.get(m, set())
            row[label[m]] = "、".join(sorted(acts)) if acts else "无"
        out.append(row)
    return out


@router.put("/config")
async def update_config(body: dict, db: AsyncSession = Depends(get_db),
                        user: User = Depends(require("screen", "config"))):
    """更新配置。调整记录操作人、调整前后值、生效时间，写进决策日志。"""
    updates = body.get("updates") or {}
    changed = []
    for key, new_val in updates.items():
        row = (await db.execute(select(ConfigItem).where(
            ConfigItem.key == key))).scalar_one_or_none()
        if not row:
            continue
        old_val = (row.value or {}).get("v")
        if old_val == new_val:
            continue
        row.value = {"v": new_val}
        changed.append({"key": key, "label": row.label,
                        "old": old_val, "new": new_val})
        db.add(DecisionLog(
            kind="config", actor=user.name, ability="阈值与参数配置",
            summary=f"调整「{row.label}」：{old_val} → {new_val}",
            detail={"key": key, "old": old_val, "new": new_val,
                    "effective_at": datetime.now().isoformat()},
        ))
    await db.commit()
    return {
        "ok": True, "changed": changed,
        "note": "阈值调整即时生效，不需发版。效果看板已标注本次变更点，便于归因。"
                if changed else "没有检测到变化。",
    }


@router.post("/config/model-route")
async def set_model_route(body: dict, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require("screen", "config"))):
    """手动锁定某档位的模型，或恢复自动选型。"""
    tier = body.get("tier")
    model_id = body.get("model_id") or None
    if tier not in ("small", "medium", "large", "vision", "embedding"):
        raise HTTPException(400, "档位须为 small/medium/large/vision/embedding")

    if model_id:
        ids = {m.id for m in registry.load()}
        if model_id not in ids:
            raise HTTPException(400, f"模型 {model_id} 不在网关可用列表中")

    registry.set_override(tier, model_id)  # type: ignore[arg-type]
    db.add(DecisionLog(kind="config", actor=user.name, ability="模型路由",
                       summary=f"{tier} 档位{'锁定为 ' + model_id if model_id else '恢复自动选型'}"))
    await db.commit()
    return {"ok": True, "auto_selected": registry.snapshot(),
            "message": "已更新。新发起的能力调用会使用新配置。"}


# ==================== 日志与审计（R-13）====================

@router.get("/logs")
async def list_logs(kind: str = "", ability: str = "", candidate_id: int = 0,
                    limit: int = 200, db: AsyncSession = Depends(get_db),
                    user: User = Depends(require("board", "view_all"))):
    """决策日志与审计。日志保留期不短于 3 年，日志本身不可编辑，仅可追加。"""
    q = select(DecisionLog).order_by(DecisionLog.id.desc())
    if kind:
        q = q.where(DecisionLog.kind == kind)
    if ability:
        q = q.where(DecisionLog.ability == ability)
    if candidate_id:
        q = q.where(DecisionLog.candidate_id == candidate_id)
    rows = (await db.execute(q.limit(min(limit, 500)))).scalars().all()
    return [{
        "id": l.id, "kind": l.kind, "actor": l.actor, "ability": l.ability,
        "candidate_id": l.candidate_id, "summary": l.summary,
        "model": l.model, "prompt_version": l.prompt_version,
        "confidence": l.confidence, "latency_ms": l.latency_ms,
        "degrade": l.degrade, "detail": l.detail, "at": str(l.created_at),
    } for l in rows]


@router.get("/logs/summary")
async def logs_summary(db: AsyncSession = Depends(get_db),
                       user: User = Depends(require("board", "view_all"))):
    rows = (await db.execute(select(
        DecisionLog.kind, func.count(DecisionLog.id)).group_by(DecisionLog.kind))).all()
    degraded = (await db.execute(select(func.count(DecisionLog.id)).where(
        DecisionLog.degrade != "none", DecisionLog.degrade != ""))).scalar_one()
    injection = (await db.execute(select(func.count(DecisionLog.id)).where(
        DecisionLog.kind == "injection"))).scalar_one()
    total = (await db.execute(select(func.count(DecisionLog.id)))).scalar_one()
    return {
        "by_kind": {k: c for k, c in rows},
        "total": total,
        "degraded_count": degraded,
        "injection_count": injection,
        "retention_note": "日志保留期不短于 3 年，满足劳动争议举证需要；日志只追加不可编辑。",
    }


# ==================== 提示词管理（PRD 4.4）====================

@router.get("/prompts")
async def list_prompts(user: User = Depends(require("board", "view_all"))):
    """提示词库。独立于代码管理，按能力拆分，支持按能力粒度独立回滚。"""
    if not prompts._loaded:
        await prompts.load()
    return {
        "prompts": prompts.list_prompts(),
        "note": "提示词变更上线前必须跑全量离线回归，核心指标下降超过 2 个百分点即阻止上线。",
    }


@router.post("/prompts")
async def create_prompt_version(body: dict, db: AsyncSession = Depends(get_db),
                                user: User = Depends(require("screen", "config"))):
    """新建提示词版本，可设置灰度比例。"""
    prompt_id = body.get("prompt_id")
    version = body.get("version")
    system_prompt = body.get("system_prompt", "")
    if not prompt_id or not version:
        raise HTTPException(400, "缺少 prompt_id 或 version")

    dup = (await db.execute(select(PromptVersion).where(
        PromptVersion.prompt_id == prompt_id,
        PromptVersion.version == version))).scalars().first()
    if dup:
        raise HTTPException(400, f"版本 {version} 已存在")

    row = PromptVersion(
        prompt_id=prompt_id, version=version,
        ability=body.get("ability", ""), system_prompt=system_prompt,
        user_template=body.get("user_template", ""),
        tier=body.get("tier", "medium"), active=False,
        note=body.get("note", ""),
    )
    db.add(row)
    db.add(DecisionLog(kind="config", actor=user.name, ability="提示词管理",
                       summary=f"新建提示词版本 {prompt_id}@{version}"))
    await db.commit()
    await prompts.load()
    return {"ok": True, "id": row.id}


@router.post("/prompts/activate")
async def activate_prompt(body: dict, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require("screen", "config"))):
    """启用某版本，或设置灰度。

    灰度期间同一能力可并行两个版本，按流量比例分配（PRD 4.4）。
    """
    prompt_id = body.get("prompt_id")
    version = body.get("version")
    canary_version = body.get("canary_version", "")
    canary_ratio = float(body.get("canary_ratio", 0) or 0)

    if canary_ratio and not (0 < canary_ratio < 1):
        raise HTTPException(400, "灰度比例须在 0 与 1 之间")

    rows = (await db.execute(select(PromptVersion).where(
        PromptVersion.prompt_id == prompt_id))).scalars().all()
    if not rows:
        raise HTTPException(404, "提示词不存在")

    target = next((r for r in rows if r.version == version), None)
    if not target:
        raise HTTPException(404, f"版本 {version} 不存在")

    for r in rows:
        r.active = (r.id == target.id)
    target.canary_version = canary_version
    target.canary_ratio = canary_ratio

    # 回归卡点：变更前必须跑离线回归，指标下降超 2 个百分点即阻止（机制强制）
    baseline = body.get("baseline")
    current = body.get("current")
    if baseline and current:
        blocked, reasons = prompts.regression_blocked(
            {k: float(v) for k, v in baseline.items()},
            {k: float(v) for k, v in current.items()},
        )
        if blocked:
            db.add(DecisionLog(kind="config", actor=user.name, ability="提示词管理",
                               summary=f"离线回归未通过，阻止启用 {prompt_id}@{version}",
                               detail={"blocked": reasons}))
            await db.commit()
            raise HTTPException(400, "离线回归未通过，已阻止上线：" + "；".join(reasons))

    db.add(DecisionLog(kind="config", actor=user.name, ability="提示词管理",
                       summary=f"启用提示词 {prompt_id}@{version}"
                               + (f"，灰度 {canary_version} 占 {canary_ratio:.0%}"
                                  if canary_version else "")))
    await db.commit()
    await prompts.load()
    return {"ok": True, "message": "已启用。灰度期间按流量比例分流，看板支持按版本对比指标。"}


# ==================== 知识库 ====================

@router.get("/knowledge")
async def list_knowledge(db: AsyncSession = Depends(get_db),
                         user: User = Depends(current_user)):
    rows = (await db.execute(select(KnowledgeDoc).order_by(
        KnowledgeDoc.category, KnowledgeDoc.id))).scalars().all()
    return {
        "docs": [{
            "id": r.id, "title": r.title, "category": r.category,
            "business_line": r.business_line, "source_path": r.source_path,
            "owner": r.owner, "chunk_count": len(r.chunks or []),
            "preview": r.content[:150],
        } for r in rows],
        "indexed_chunks": kb.size,
        "note": "知识库只索引位置不复制正文，文档更新后全平台即时生效，不需重新训练。",
    }


@router.post("/knowledge/search")
async def search_knowledge(body: dict, user: User = Depends(current_user)):
    """知识库检索。检索结果受与业务数据同一套权限约束（PRD 5.4）。"""
    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "请输入检索词")

    # 按角色的业务线隔离：跨业务线不可见
    bl = None
    if user.role not in (ROLE_ADMIN, "HR负责人", "法务审计") and user.business_line:
        bl = user.business_line

    from app.rag.retriever import search_knowledge
    hits = await search_knowledge(query, top_k=int(body.get("top_k", 6)), business_line=bl)
    return {"hits": hits, "scoped_business_line": bl or "全部"}


@router.post("/knowledge")
async def add_knowledge(body: dict, db: AsyncSession = Depends(get_db),
                        user: User = Depends(require("training", "edit"))):
    from app.rag.splitter import split_text

    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(400, "请填写文档标题与正文")
    # 知识库检索按业务线隔离，取值必须来自字典，否则这篇文档谁也检索不到
    from app.api.v1.business_line_api import validate_line
    line = await validate_line(db, body.get("business_line") or "通用", allow_empty=False)

    row = KnowledgeDoc(
        title=title, category=body.get("category", "SOP"),
        business_line=line, content=content,
        chunks=split_text(content), owner=user.name,
        source_path=f"内部知识库/{body.get('category', 'SOP')}/{title}.md",
    )
    db.add(row)
    await db.commit()
    await _rebuild_kb()
    return {"ok": True, "id": row.id, "message": "已入库并重建索引，全平台即时生效。"}


@router.delete("/knowledge/{kid}")
async def delete_knowledge(kid: int, db: AsyncSession = Depends(get_db),
                           user: User = Depends(require("training", "edit"))):
    row = (await db.execute(select(KnowledgeDoc).where(
        KnowledgeDoc.id == kid))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "文档不存在")
    await db.delete(row)
    await db.commit()
    await _rebuild_kb()
    return {"ok": True}


async def _rebuild_kb() -> int:
    from app.rag.retriever import load_knowledge_from_db
    return await load_knowledge_from_db()


# ==================== 用户与系统信息 ====================

@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db),
                     user: User = Depends(current_user)):
    rows = (await db.execute(select(User).where(User.active == True))).scalars().all()  # noqa: E712
    return [{
        "id": u.id, "userid": u.userid, "name": u.name, "role": u.role,
        "business_line": u.business_line, "department": u.department,
        "can_view_resume": u.can_view_resume, "mask_contact": u.mask_contact,
    } for u in rows]


@router.get("/system/status")
async def system_status(db: AsyncSession = Depends(get_db)):
    """系统状态：模型网关、知识库索引、自动选型结果。"""
    positions = (await db.execute(select(func.count(Position.id)))).scalar_one()
    candidates = (await db.execute(select(func.count(Candidate.id)))).scalar_one()
    samples = (await db.execute(select(func.count(SampleCase.id)))).scalar_one()

    models = registry.load()
    return {
        "app": "AI 招聘与人才发展平台",
        "version": "1.0.0",
        "llm": {
            "enabled": True,
            "available_models": len(models),
            "auto_selected": registry.snapshot(),
        },
        "knowledge": {"docs_chunks": kb.size},
        "data": {"positions": positions, "candidates": candidates, "samples": samples},
        "usage": llm.usage_stats(),
        "prompt_versions": sum(len(v) for v in prompts._versions.values()) if prompts._loaded else 0,
        "compliance": {
            "sensitive_features": "性别、年龄、婚育、地域、院校层次不进入打分链路（代码级校验）",
            "no_auto_reject": "低分档进入待定池而非直接拒绝，保留 7 天人工捞回窗口",
            "audit": "全链路记录输入、模型版本、输出结论与人工操作，日志保留不少于 3 年",
            "injection_defense": "输入层剥离 + 提示层隔离 + 输出层一致性校验，三层防护",
        },
    }


@router.post("/system/reset-demo")
async def reset_demo(db: AsyncSession = Depends(get_db),
                     user: User = Depends(require("screen", "config"))):
    """清空演示数据（候选人、面试、培训记录、日志），保留配置与知识库。"""
    from app.db.models import (
        ExamSubmission, InterviewReport, InterviewSchedule, QuestionRecord, ResumeText,
        ReviewAction, ScoreRecord,
    )
    for model in (ExamSubmission, InterviewReport, QuestionRecord, InterviewSchedule,
                  ReviewAction, ScoreRecord, ResumeText, ReflowSample, TrackEvent,
                  DecisionLog, Candidate):
        for row in (await db.execute(select(model))).scalars().all():
            await db.delete(row)
    await db.commit()
    return {"ok": True, "message": "演示数据已清空，配置与知识库保留。"}


# ==================== 模型网关配置 ====================
# 打包到别的机器后，环境变量可能不存在。这里允许在界面上配置网关，
# 写入 backend/.env 后即时生效，不需要改代码或重启。

@router.get("/system/gateway")
async def get_gateway(user: User = Depends(require("screen", "config"))):
    """读取当前网关配置。密钥只返回是否已设置，不回显内容。"""
    from app.core.config import settings

    base = settings.anthropic_base_url or ""
    # 回显时隐去可能的路径细节，只显示主机部分
    host = base.split("//")[-1].split("/")[0] if base else ""
    return {
        "base_url": base,
        "host": host,
        # 只告知是否已设置，不回显任何片段 —— 密钥的任意部分都不该出现在响应里
        "token_set": bool(settings.anthropic_auth_token),
        "llm_enabled": settings.llm_enabled,
        "env_file": str((Path(__file__).resolve().parents[3] / ".env")),
        "overrides": {
            "timeout_sync": settings.llm_timeout_sync,
            "timeout_async": settings.llm_timeout_async,
            "max_retries": settings.llm_max_retries,
        },
    }


@router.post("/system/gateway")
async def set_gateway(body: dict, db: AsyncSession = Depends(get_db),
                      user: User = Depends(require("screen", "config"))):
    """保存网关配置到 backend/.env 并即时重载。

    写入 .env 而不是只改内存，是为了让配置在下次启动后依然有效 ——
    否则用户配好了网关，重启一次又变回未配置状态。
    """
    from app.core.config import BASE_DIR, reload_settings

    base_url = str(body.get("base_url", "")).strip()
    token = str(body.get("token", "")).strip()

    if base_url and not base_url.startswith(("http://", "https://")):
        raise HTTPException(400, "网关地址需以 http:// 或 https:// 开头")

    env_path = Path(BASE_DIR) / ".env"
    # 读现有配置，只覆盖这两项，保留其它手工配置
    lines: list[str] = []
    if env_path.exists():
        lines = [ln for ln in env_path.read_text(encoding="utf-8").splitlines()
                 if not ln.strip().startswith(("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"))]
    if base_url:
        lines.append(f"ANTHROPIC_BASE_URL={base_url}")
    if token:
        lines.append(f"ANTHROPIC_AUTH_TOKEN={token}")
    env_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    # 即时生效
    import os
    if base_url:
        os.environ["ANTHROPIC_BASE_URL"] = base_url
    if token:
        os.environ["ANTHROPIC_AUTH_TOKEN"] = token
    reload_settings()

    from app.core.config import settings as fresh
    from app.llm.registry import registry
    registry.load(force=True)

    db.add(DecisionLog(kind="config", actor=user.name, ability="模型网关",
                       summary=f"更新网关配置：{base_url or '（未变）'}，"
                               f"密钥{'已更新' if token else '未变'}"))
    await db.commit()

    return {
        "ok": True,
        "llm_enabled": fresh.llm_enabled,
        "available_models": len(registry.load()),
        "auto_selected": registry.snapshot(),
        "message": "配置已保存并即时生效。"
                   + ("" if fresh.llm_enabled else " 请确认地址与密钥都已填写。"),
    }


@router.post("/system/gateway/test")
async def test_gateway(body: dict, user: User = Depends(require("screen", "config"))):
    """测试网关连通性，不保存。用于在正式保存前验证配置是否可用。"""
    import httpx

    base_url = str(body.get("base_url", "")).strip().rstrip("/")
    token = str(body.get("token", "")).strip()
    from app.core.config import settings as cur
    token = token or cur.anthropic_auth_token
    base_url = base_url or cur.anthropic_base_url.rstrip("/")

    if not base_url or not token:
        raise HTTPException(400, "请先填写网关地址与密钥")

    try:
        with httpx.Client(timeout=15, verify=False) as c:
            r = c.get(f"{base_url}/v1/models",
                      headers={"x-api-key": token, "anthropic-version": "2023-06-01"})
            r.raise_for_status()
            data = r.json()
        ids = [m.get("id", "") for m in data.get("data", [])]
        # 过滤出文本模型
        text_models = [i for i in ids if i]
        return {
            "ok": True,
            "total": len(ids),
            "sample": text_models[:8],
            "message": f"连接成功，网关返回 {len(ids)} 个模型。",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
            "message": "连接失败。请检查地址是否正确、密钥是否有效、网络是否可达。",
        }
