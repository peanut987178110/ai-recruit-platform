"""M1 能力模型接口（R-01）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ROLE_ADMIN, ROLE_HR_LEAD, current_user, require
from app.db.models import (
    AbilityItem, AbilityModel, Candidate, DecisionLog, Position, SampleCase, User,
)
from app.db.session import get_db
from app.llm.client import llm
from app.services.scoring_service import ability_to_dict
from app.llm.registry import registry
from app.prompts.manager import prompts

router = APIRouter(prefix="/models", tags=["M1 能力模型"])

EVIDENCE_LABEL = {
    "project": "项目经历", "tenure": "任职履历", "skill": "技能标签",
    "metric": "量化成果", "education": "教育背景",
}


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    weight: int = Field(ge=1, le=10)
    evidence_types: list[str] = Field(default_factory=lambda: ["project"])
    criteria: str = Field(default="", max_length=200)
    is_veto: bool = False
    # positive = 必须具备（未命中即归零）；negative = 排除条款（命中即归零）
    veto_polarity: str = "positive"


class ModelIn(BaseModel):
    items: list[ItemIn]


class PositionIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    seq: str = "技术"
    level_range: str = ""
    business_line: str = "通用"
    headcount: int = 1
    jd_text: str = ""
    template_position_id: int | None = None


def _model_out(m: AbilityModel, pos: Position | None) -> dict:
    return {
        "id": m.id, "position_id": m.position_id, "version": m.version,
        "active": m.active, "total_weight": m.total_weight, "veto_count": m.veto_count,
        "position_name": pos.name if pos else "",
        "seq": pos.seq if pos else "", "business_line": pos.business_line if pos else "",
        "level_range": pos.level_range if pos else "",
        "headcount": pos.headcount if pos else 0,
        "status": pos.status if pos else "",
        "items": [{
            "id": i.id, "name": i.name, "weight": i.weight,
            "weight_pct": round(i.weight / m.total_weight * 100, 1) if m.total_weight else 0,
            "evidence_types": i.evidence_types or [],
            "evidence_labels": [EVIDENCE_LABEL.get(e, e) for e in (i.evidence_types or [])],
            "criteria": i.criteria, "is_veto": i.is_veto,
            "veto_polarity": getattr(i, "veto_polarity", "positive") or "positive",
            "sort": i.sort,
        } for i in sorted(m.items, key=lambda x: x.sort)],
        "updated_at": str(m.updated_at),
    }


@router.get("")
async def list_models(db: AsyncSession = Depends(get_db),
                      _: User = Depends(require("model", "view"))):
    """能力模型列表（P-05）。"""
    ms = (await db.execute(select(AbilityModel).order_by(AbilityModel.position_id,
                                                         AbilityModel.id.desc()))).scalars().all()
    positions = {p.id: p for p in (await db.execute(select(Position))).scalars().all()}
    latest: dict[int, AbilityModel] = {}
    for m in ms:
        if m.position_id not in latest or (m.active and not latest[m.position_id].active):
            latest.setdefault(m.position_id, m)
        if m.active:
            latest[m.position_id] = m
    return [_model_out(m, positions.get(m.position_id)) for m in latest.values()]


@router.get("/positions")
async def list_positions(db: AsyncSession = Depends(get_db),
                         _: User = Depends(require("model", "view"))):
    rows = (await db.execute(select(Position))).scalars().all()
    return [{"id": p.id, "name": p.name, "seq": p.seq, "level_range": p.level_range,
             "business_line": p.business_line, "status": p.status,
             "headcount": p.headcount, "jd_text": p.jd_text} for p in rows]


@router.get("/positions/{pid}")
async def get_position(pid: int, db: AsyncSession = Depends(get_db),
                       _: User = Depends(require("model", "view"))):
    pos = (await db.execute(select(Position).where(Position.id == pid))).scalar_one_or_none()
    if not pos:
        raise HTTPException(404, "岗位不存在")
    m = (await db.execute(select(AbilityModel).where(
        AbilityModel.position_id == pid, AbilityModel.active == True))).scalars().first()  # noqa: E712
    if not m:
        m = (await db.execute(select(AbilityModel).where(
            AbilityModel.position_id == pid).order_by(AbilityModel.id.desc()))).scalars().first()
    if not m:
        raise HTTPException(404, "该岗位尚未配置能力模型")
    return _model_out(m, pos)


@router.get("/{mid}")
async def get_model(mid: int, db: AsyncSession = Depends(get_db),
                    _: User = Depends(require("model", "view"))):
    m = (await db.execute(select(AbilityModel).where(AbilityModel.id == mid))).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "模型不存在")
    pos = (await db.execute(select(Position).where(Position.id == m.position_id))).scalar_one_or_none()
    return _model_out(m, pos)


@router.get("/{mid}/versions")
async def model_versions(mid: int, db: AsyncSession = Depends(get_db),
                         _: User = Depends(require("model", "view"))):
    """历史版本可供查看与回滚（PRD 3.1.2）。"""
    m = (await db.execute(select(AbilityModel).where(AbilityModel.id == mid))).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "模型不存在")
    rows = (await db.execute(select(AbilityModel).where(
        AbilityModel.position_id == m.position_id).order_by(AbilityModel.id.desc()))).scalars().all()
    return [{"id": r.id, "version": r.version, "active": r.active,
             "total_weight": r.total_weight, "created_at": str(r.created_at),
             "item_count": len(r.items)} for r in rows]


@router.post("")
async def create_position(payload: PositionIn, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require("model", "edit"))):
    """新建岗位。可从同序列模板派生，派生后能力项与权重完整继承（验收 A1-1）。"""
    # 岗位的业务线决定哪些用人经理能看到它的候选人，必须来自字典
    from app.api.v1.business_line_api import validate_line
    payload.business_line = await validate_line(db, payload.business_line, allow_empty=False)
    dup = (await db.execute(select(Position).where(
        Position.name == payload.name,
        Position.business_line == payload.business_line))).scalars().first()
    if dup:
        raise HTTPException(400, f"同一业务线下已存在岗位「{payload.name}」，不可重名")

    pos = Position(name=payload.name, seq=payload.seq, level_range=payload.level_range,
                   business_line=payload.business_line, headcount=payload.headcount,
                   jd_text=payload.jd_text, status="在招")
    db.add(pos)
    await db.flush()

    src_items: list[dict] = []
    if payload.template_position_id:
        src = (await db.execute(select(AbilityModel).where(
            AbilityModel.position_id == payload.template_position_id,
            AbilityModel.active == True))).scalars().first()  # noqa: E712
        if src:
            src_items = [{"name": i.name, "weight": i.weight,
                          "evidence_types": i.evidence_types, "criteria": i.criteria,
                          "is_veto": i.is_veto,
                          "veto_polarity": getattr(i, "veto_polarity", "positive") or "positive",
                          "sort": i.sort} for i in src.items]

    # 停用状态不参与打分，新建默认停用需显式启用（PRD 3.1.2）
    model = AbilityModel(position_id=pos.id, version="v1", active=False,
                         note=f"从岗位 {payload.template_position_id} 派生" if src_items else "新建")
    db.add(model)
    await db.flush()
    for i, it in enumerate(src_items):
        db.add(AbilityItem(model_id=model.id, **it))
    await db.commit()
    return _model_out(model, pos)


@router.put("/{mid}")
async def update_model(mid: int, payload: ModelIn, db: AsyncSession = Depends(get_db),
                       user: User = Depends(require("model", "edit"))):
    """保存模型。

    两条关键规则：
    - 权重总和不等于 100% 时保存按钮置灰并提示差额，**不做自动归一化**
      （避免用户以为已生效实则被系统改过）。
    - 否决项数量上限 3 个，超出禁止添加。
    - 修改已启用模型时生成新版本，不影响已完成打分的历史记录（验收 A1-4）。
    """
    m = (await db.execute(select(AbilityModel).where(AbilityModel.id == mid))).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "模型不存在")

    total = sum(i.weight for i in payload.items)
    if total != 10:
        raise HTTPException(400, f"权重总和当前为 {total * 10}%（{total}/10），"
                                 f"需等于 100% 才能保存。差额 {abs(10 - total) * 10}%。"
                                 f"系统不会自动归一化，请手动调整。")
    veto_n = sum(1 for i in payload.items if i.is_veto)
    if veto_n > 3:
        raise HTTPException(400, f"否决项最多 3 个，当前 {veto_n} 个。"
                                 f"否决项过多会使打分退化为硬性筛选，失去模型意义。")

    pos = (await db.execute(select(Position).where(
        Position.id == m.position_id))).scalar_one_or_none()

    if m.active:
        # 生成新版本，历史打分记录仍显示当时使用的版本
        n = int(m.version.lstrip("v") or 1) + 1
        new = AbilityModel(position_id=m.position_id, version=f"v{n}", active=True)
        db.add(new)
        await db.flush()
        for i, it in enumerate(payload.items):
            db.add(AbilityItem(model_id=new.id, name=it.name, weight=it.weight,
                               evidence_types=it.evidence_types, criteria=it.criteria,
                               is_veto=it.is_veto, veto_polarity=it.veto_polarity, sort=i))
        m.active = False
        db.add(DecisionLog(
            kind="human", actor=user.name, ability="能力模型",
            summary=f"修改已启用模型「{pos.name if pos else ''}」，生成新版本 {new.version}",
            detail={"old_version": m.version, "new_version": new.version,
                    "position_id": m.position_id},
        ))
        await db.commit()
        return _model_out(new, pos)

    # 未启用模型直接覆盖
    for old in list(m.items):
        await db.delete(old)
    await db.flush()
    for i, it in enumerate(payload.items):
        db.add(AbilityItem(model_id=m.id, name=it.name, weight=it.weight,
                           evidence_types=it.evidence_types, criteria=it.criteria,
                           is_veto=it.is_veto, veto_polarity=it.veto_polarity, sort=i))
    db.add(DecisionLog(
        kind="human", actor=user.name, ability="能力模型",
        summary=f"编辑未启用模型「{pos.name if pos else ''}」",
        detail={"version": m.version},
    ))
    await db.commit()
    await db.refresh(m)
    return _model_out(m, pos)


@router.post("/{mid}/toggle")
async def toggle_model(mid: int, db: AsyncSession = Depends(get_db),
                       user: User = Depends(require("model", "edit"))):
    """启用/停用模型。

    修改已启用模型时，需提示影响范围（在招岗位数、在库候选人数）（PRD 3.1.3）。
    """
    m = (await db.execute(select(AbilityModel).where(AbilityModel.id == mid))).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "模型不存在")

    # 校验权重总和才能启用
    if not m.active and m.total_weight != 10:
        raise HTTPException(400, f"权重总和为 {m.total_weight * 10}%，需等于 100% 才能启用")

    if not m.active:
        others = (await db.execute(select(AbilityModel).where(
            AbilityModel.position_id == m.position_id,
            AbilityModel.id != m.id))).scalars().all()
        for o in others:
            o.active = False

    m.active = not m.active

    affected_positions = (await db.execute(select(func.count(Position.id)).where(
        Position.id == m.position_id, Position.status == "在招"))).scalar_one()
    affected_candidates = (await db.execute(select(func.count(Candidate.id)).where(
        Candidate.position_id == m.position_id))).scalar_one()

    db.add(DecisionLog(
        kind="human", actor=user.name, ability="能力模型",
        summary=f"{'启用' if m.active else '停用'}能力模型 {m.version}",
        detail={"position_id": m.position_id,
                "affected_positions": affected_positions,
                "affected_candidates": affected_candidates},
    ))
    await db.commit()
    pos = (await db.execute(select(Position).where(Position.id == m.position_id))).scalar_one_or_none()
    return {
        "model": _model_out(m, pos),
        "impact": {"在招岗位数": affected_positions, "在库候选人数": affected_candidates},
        "note": "启用后不影响已完成打分的历史记录，历史记录仍显示当时使用的版本。",
    }


@router.delete("/{mid}")
async def delete_model(mid: int, db: AsyncSession = Depends(get_db),
                       user: User = Depends(require("model", "edit"))):
    m = (await db.execute(select(AbilityModel).where(AbilityModel.id == mid))).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "模型不存在")
    if m.active:
        raise HTTPException(400, "启用中的模型不可删除，请先停用")
    await db.delete(m)
    await db.commit()
    return {"ok": True}


@router.post("/{mid}/trial")
async def trial(mid: int, body: dict, db: AsyncSession = Depends(get_db),
                user: User = Depends(require("model", "view"))):
    """试算区：选择历史简历实时查看打分结果与证据（PRD 3.1.3 / 验收 A1-3）。

    用户可在保存前判断标准是否合理。返回结果包含每个能力项的得分与证据位置。
    """
    sample_ids = body.get("sample_ids") or []
    limit = int(body.get("limit", 3))
    if len(sample_ids) < 1:
        # 默认取该岗位序列的 3 份历史简历
        m0 = (await db.execute(select(AbilityModel).where(AbilityModel.id == mid))).scalar_one_or_none()
        pos0 = (await db.execute(select(Position).where(
            Position.id == m0.position_id))).scalar_one_or_none() if m0 else None
        rows = (await db.execute(select(SampleCase).where(
            SampleCase.position_seq == (pos0.seq if pos0 else "技术")
        ).limit(limit))).scalars().all()
        samples = [{"id": s.id, "name": s.name, "text": s.resume_text,
                    "label": s.label} for s in rows]
    else:
        rows = (await db.execute(select(SampleCase).where(
            SampleCase.id.in_(sample_ids)))).scalars().all()
        samples = [{"id": s.id, "name": s.name, "text": s.resume_text,
                    "label": s.label} for s in rows]

    if not samples:
        raise HTTPException(400, "没有可用的历史简历样本")

    # 支持用「未保存的草稿能力项」试算，这是试算区最有价值的用法
    draft_items = body.get("items")
    m = (await db.execute(select(AbilityModel).where(AbilityModel.id == mid))).scalar_one_or_none()
    pos = (await db.execute(select(Position).where(
        Position.id == m.position_id))).scalar_one_or_none() if m else None

    if draft_items:
        items = [{"id": f"draft-{i}", "name": it["name"], "weight": it["weight"],
                  "evidence_types": it.get("evidence_types", []),
                  "criteria": it.get("criteria", ""), "is_veto": it.get("is_veto", False),
                  "veto_polarity": it.get("veto_polarity", "positive")}
                 for i, it in enumerate(draft_items)]
        version = "草稿（未保存）"
    else:
        items = [ability_to_dict(i) for i in m.items] if m else []
        version = m.version if m else ""

    if not items:
        raise HTTPException(400, "该模型没有能力项，无法试算")

    from app.services.resume_service import parse_resume
    from app.services.scoring_service import decide_tier, score_candidate

    out = []
    for s in samples:
        parsed, pmeta = await parse_resume(s["text"], candidate_id=s["id"])
        res = await score_candidate(
            candidate_id=s["id"], raw_text=s["text"], parsed=parsed.model_dump(),
            position_name=pos.name if pos else "", items=items, model_version=version,
            high_conf=0.85, high_score=75, mid_score=45,
        )
        sc = res.result
        tier = decide_tier(sc.total, sc.confidence, sc.veto_hit, 0.85, 75, 45) if sc else ""
        out.append({
            "sample_id": s["id"], "name": s["name"], "truth_label": s["label"],
            "total": sc.total if sc else 0,
            "confidence": sc.confidence if sc else 0,
            "tier": tier,
            "veto_hit": sc.veto_hit if sc else False,
            "veto_reason": sc.veto_reason if sc else "",
            "api_ok": res.ok, "error": res.error,
            "abilities": [{
                "ability_id": a.ability_id, "ability_name": a.ability_name,
                "state": a.state.value, "score": a.score, "weight": a.weight,
                "reason": a.reason,
                "evidence": [{
                    "quote": e.quote, "start": e.start, "end": e.end,
                    "field_source": e.field_source,
                } for e in res.evidence if e.ability_id == a.ability_id],
            } for a in (sc.ability_scores if sc else [])],
            "risk_tags": sc.risk_tags if sc else [],
            "meta": res.meta.model_dump(),
        })
    return {
        "version": version,
        "is_draft": bool(draft_items),
        "samples": out,
        "note": "试算不写入打分记录，不影响在库候选人状态。",
    }


@router.get("/samples/list")
async def list_samples(seq: str = "", db: AsyncSession = Depends(get_db),
                       _: User = Depends(require("model", "view"))):
    """历史简历样本列表，供试算区选择。"""
    q = select(SampleCase)
    if seq:
        q = q.where(SampleCase.position_seq == seq)
    rows = (await db.execute(q.limit(50))).scalars().all()
    return [{"id": s.id, "name": s.name, "position_seq": s.position_seq,
             "position_name": s.position_name, "label": s.label,
             "is_edge_case": s.is_edge_case,
             "preview": s.resume_text[:160]} for s in rows]


@router.post("/analyze-jd")
async def analyze_jd(body: dict, _: User = Depends(require("model", "edit"))):
    """从 JD 提炼能力项候选池。"""
    from app.core.schemas import JDAnalysisResult

    jd = (body.get("jd_text") or "").strip()
    seq = body.get("seq", "技术")
    if not jd:
        raise HTTPException(400, "请输入职位描述")

    p = await prompts.resolve("JD 分析", {"jd_text": jd[:6000], "seq": seq}, route_key=jd[:60])
    res = await llm.complete_json(
        ability="JD 分析", prompt_id=p.prompt_id, prompt_version=p.version,
        system=p.system_prompt, user=p.user_prompt,
        schema=JDAnalysisResult, tier=p.tier, max_tokens=2500,
    )
    if not res.ok or not res.result:
        raise HTTPException(503, res.error or "JD 分析失败，请稍后重试")
    return {"analysis": res.result.model_dump(), "meta": res.meta.model_dump()}


@router.get("/model-routing/status")
async def model_routing(_: User = Depends(require("model", "view"))):
    """当前模型自动选型结果。"""
    return {
        "auto_selected": registry.snapshot(),
        "llm_enabled": True,
        "candidates": {
            tier: [{"id": m.id, "vendor": m.vendor, "score": m.tier_affinity.get(tier, 0)}
                   for m in registry.available(tier, limit=6)]  # type: ignore[arg-type]
            for tier in ("small", "medium", "large")
        },
    }
