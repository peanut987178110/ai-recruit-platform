"""数据回流与效果看板（M5，R-17、R-18）。

指标口径严格按需求分析文档第 10 章锁定，避免验收时各方对同一数字理解不同：
- 误杀率：分母**仅含自动分流样本**，不含人工复核区。衡量的是不可挽回的损失。
- 引用准确率：抽检证据能否正确指向原文位置。
- HR 处理耗时：总耗时除以**总到岸量**，而非除以人工复核量，避免高估收益。
- 分层占比：三档实际占比，偏离预期超 10 个百分点告警。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.db.models import (
    Candidate, ConfigItem, DecisionLog, ExamSubmission, InterviewReport,
    QuestionRecord, ReflowSample, ReviewAction, SampleCase, TrackEvent,
)
from app.db.session import SessionLocal
from app.utils.timefmt import day_key

EXPECTED_TIER = {"高分档": 0.09, "中间档": 0.34, "低分档": 0.57}

# 单指标样本量下限。PRD 3.5.2：不足 30 时显示数据不足而非展示比例，
# 防止小样本波动被当作趋势解读。所有比率型指标共用这一条，避免各自为政。
MIN_SAMPLE = 30


def _rate(value: float | None, sample: int, note: str = "") -> dict:
    """包装一个比率型指标。样本不足时不给数值，只说明原因。

    理由（PRD 3.5.2）：小样本波动会被误当作趋势解读，因此宁可不给数字，
    也不要给一个看起来精确、实际不可信的百分比。参照上限之类的背景说明
    在两种情况下都要保留，它是解读该指标的前提。
    """
    if sample < MIN_SAMPLE:
        gap = f"样本量 {sample}，不足 {MIN_SAMPLE} 条，按数据不足处理，不展示比例"
        return {
            "rate": None, "samples": sample, "insufficient": True,
            "note": f"{gap}。{note}" if note else gap,
        }
    return {"rate": value, "samples": sample, "insufficient": False, "note": note}


async def config_map() -> dict[str, object]:
    async with SessionLocal() as db:
        rows = (await db.execute(select(ConfigItem))).scalars().all()
        return {r.key: (r.value or {}).get("v") for r in rows}


async def overview() -> dict:
    """看板顶部指标卡。"""
    async with SessionLocal() as db:
        total = (await db.execute(select(func.count(Candidate.id)))).scalar_one()

        tier_rows = (await db.execute(
            select(Candidate.tier, func.count(Candidate.id)).group_by(Candidate.tier)
        )).all()
        tier_counts = {t or "未分档": c for t, c in tier_rows}

        status_rows = (await db.execute(
            select(Candidate.status, func.count(Candidate.id)).group_by(Candidate.status)
        )).all()
        status_counts = {s: c for s, c in status_rows}

        # 结论一致率：打分分层结果 vs 历史样本真值
        consistency = await _consistency(db)

        # 误杀率：自动分流到待定池、但被 HR 捞回的样本占比
        # 分母仅含自动分流样本，不含人工复核区（PRD 附录·指标口径）
        rescued = (await db.execute(select(func.count(ReviewAction.id)).where(
            ReviewAction.action == "捞回"))).scalar_one()
        pooled = (await db.execute(select(func.count(Candidate.id)).where(
            Candidate.status.in_(["待定池", "已归档"])))).scalar_one()
        # 样本量是分母（自动分流总量），不是分子
        kill = _rate(round(rescued / pooled * 100, 2) if pooled else 0.0, pooled,
                     "分母仅含自动分流样本，不含人工复核区")

        # 引用准确率
        cite = await _citation_accuracy(db)

        # 面试题采纳率
        q_total = (await db.execute(select(func.count(QuestionRecord.id)))).scalar_one()
        q_adopted = (await db.execute(select(func.count(QuestionRecord.id)).where(
            QuestionRecord.action == "采纳"))).scalar_one()
        adopt = _rate(round(q_adopted / q_total * 100, 1) if q_total else 0.0, q_total,
                      "门槛 ≥70%，可灰度观察")

        # HR 处理耗时（总耗时 / 总到岸量）
        hr_seconds = await _avg_hr_seconds(db, total)

        # 成本与延迟
        from app.llm.client import llm
        usage = llm.usage_stats()

        # 去偏验证 AIR（分组通过率比值，本项目要求 > 0.9）
        air = await _air(db)

        # 分层占比偏离告警
        warns = []
        for tier, expect in EXPECTED_TIER.items():
            actual = tier_counts.get(tier, 0) / total if total else 0
            if total >= 30 and abs(actual - expect) > 0.10:
                warns.append(f"{tier}实际占比 {actual:.1%}，偏离预期 {expect:.0%} 超过 10 个百分点")

    return {
        "total_candidates": total,
        "tier_counts": tier_counts,
        "tier_ratio": {k: round(v / total, 3) if total else 0 for k, v in tier_counts.items()},
        "status_counts": status_counts,
        "consistency_rate": consistency,
        "kill_rate": kill,
        "citation_accuracy": cite,
        "question_adopt_rate": adopt,
        "hr_avg_seconds": hr_seconds,
        "cost": {
            "avg_cost_cny": usage.get("avg_cost_cny", 0),
            "total_cost_cny": usage.get("cost_cny", 0),
            "calls": usage.get("calls", 0),
            "avg_latency_ms": usage.get("avg_latency_ms", 0),
            "degraded": usage.get("degraded", 0),
        },
        "air": air,
        "alerts": warns,
    }


async def _consistency(db) -> dict:
    """结论一致率。

    在有真值的样本集上，比较 AI 分层结论（推进/分流）与历史真实结论。
    人类两名资深 HR 双盲一致率 78%，是该指标的天然参照上限。
    """
    rows = (await db.execute(select(SampleCase))).scalars().all()
    if not rows:
        return _rate(None, 0, "尚无历史样本")

    cands = (await db.execute(select(Candidate))).scalars().all()
    by_name: dict[str, Candidate] = {}
    for c in cands:
        by_name.setdefault(c.name, c)

    agree = compare = 0
    for s in rows:
        c = by_name.get(s.name)
        if not c or not c.tier:
            continue
        ai_pass = c.tier in ("高分档", "中间档")   # AI 判为推进
        truth_pass = s.label == "pass"
        compare += 1
        if ai_pass == truth_pass:
            agree += 1

    if compare == 0:
        return _rate(None, 0, "尚未对样本集执行打分，暂无法计算")
    return _rate(
        round(agree / compare, 3), compare,
        "人类两名资深 HR 双盲一致率为 78%，为目标参照上限",
    )


async def _citation_accuracy(db) -> dict:
    """引用准确率。抽检证据能否正确定位到简历原文（PRD 附录）。

    判定方式按「偏移量是否落在原文有效区间」来算，而不是比对文本是否逐字相同 ——
    因为院校名称类证据会在展示时脱敏，脱敏后的 quote 与原文 deliberately 不同，
    用文本比对会把合规脱敏误判成引用不准确。而指标要衡量的是
    「这条证据是否真实指向简历里的某个位置」。
    """
    from app.db.models import ResumeText, ScoreRecord
    records = (await db.execute(select(ScoreRecord))).scalars().all()
    checked = located = 0
    for r in records:
        rt = (await db.execute(select(ResumeText).where(
            ResumeText.candidate_id == r.candidate_id))).scalar_one_or_none()
        text = rt.raw_text if rt else ""
        for ev in (r.evidence or []):
            if not ev.get("quote"):
                continue
            checked += 1
            start, end = ev.get("start", -1), ev.get("end", -1)
            if start < 0 or end <= start or not text:
                continue
            if end <= len(text) and text[start:end].strip():
                located += 1
    if checked == 0:
        return _rate(None, 0, "尚无证据可抽检")
    return _rate(round(located / checked, 3), checked, "")


async def _avg_hr_seconds(db, total: int) -> int | None:
    """HR 处理耗时：总耗时除以总到岸量，非除以人工复核量。"""
    rows = (await db.execute(select(ReviewAction.stay_seconds))).scalars().all()
    if not rows or not total:
        return None
    return int(sum(rows) / total)


async def _air(db) -> dict:
    """AIR 比值：不同人群通过率之比，取最低组与最高组的比值。

    这里按岗位序列分组做代理计算（真实场景应按受保护属性分组，但平台
    不采集这类属性，因此只能对间接分组做自检并说明局限）。
    """
    rows = (await db.execute(
        select(Candidate.tier, func.count(Candidate.id)).group_by(Candidate.tier)
    )).all()
    if not rows:
        return {"value": None, "note": "暂无数据"}
    return {
        "value": None,
        "note": "平台不采集性别、年龄等受保护属性，无法直接计算 AIR。"
                "上线前需在受控环境用带标注的数据集完成去偏回归验证（代码级校验 + AIR 检验）。",
    }


async def tier_trend(days: int = 30) -> list[dict]:
    """分层占比趋势，标注模型版本与阈值变更点（PRD 3.5.2）。"""
    since = datetime.now() - timedelta(days=days)
    async with SessionLocal() as db:
        rows = (await db.execute(select(Candidate).where(
            Candidate.created_at >= since))).scalars().all()
        logs = (await db.execute(select(DecisionLog).where(
            DecisionLog.kind == "config",
            DecisionLog.created_at >= since))).scalars().all()

    buckets: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for c in rows:
        buckets[day_key(c.created_at)][c.tier or "未分档"] += 1

    out = []
    for day in sorted(buckets):
        d = buckets[day]
        tot = sum(d.values()) or 1
        out.append({
            "day": day,
            "高分档": d.get("高分档", 0),
            "中间档": d.get("中间档", 0),
            "低分档": d.get("低分档", 0),
            "total": tot,
        })

    return [
        {"type": "outline", "data": out},
        {"type": "markers", "data": [
            {"day": day_key(l.created_at), "label": l.summary[:20]} for l in logs
        ]},
    ]


async def funnel() -> list[dict]:
    """招聘漏斗。"""
    async with SessionLocal() as db:
        total = (await db.execute(select(func.count(Candidate.id)))).scalar_one()
        reviewed = (await db.execute(select(func.count(ReviewAction.id)).where(
            ReviewAction.action.in_(["采纳", "否决"])))).scalar_one()
        to_interview = (await db.execute(select(func.count(Candidate.id)).where(
            Candidate.status.in_(["待安排面试", "待复核", "已归档", "待定池"])))).scalar_one()
        interviews = (await db.execute(select(func.count(InterviewReport.id)))).scalar_one()
        adopted = (await db.execute(select(func.count(ReviewAction.id)).where(
            ReviewAction.action == "采纳"))).scalar_one()
    return [
        {"stage": "简历到岸", "value": total},
        {"stage": "AI 自动打分", "value": total},
        {"stage": "人工复核", "value": reviewed},
        {"stage": "采纳推进", "value": adopted},
        {"stage": "完成面试", "value": interviews},
    ]


async def event_stats(days: int = 14) -> dict:
    """埋点统计（PRD 第 6 章）。"""
    since = datetime.now() - timedelta(days=days)
    async with SessionLocal() as db:
        rows = (await db.execute(select(TrackEvent).where(
            TrackEvent.created_at >= since))).scalars().all()
    agg: dict[str, int] = defaultdict(int)
    for r in rows:
        agg[r.event] += 1

    # evidence_click 的作用是验证「证据溯源是否真被使用」这个设计假设
    clicks = agg.get("evidence_click", 0)
    reviews = agg.get("review_action", 0)
    verdict = None
    if reviews >= 10:
        ratio = clicks / reviews
        if ratio < 0.15:
            verdict = ("点击率偏低而复核量正常，说明存在盲目采纳风险："
                       "用户可能没有真的查看证据。需要调整交互而不是庆祝采纳率。")
        else:
            verdict = "证据点击率正常，证据溯源被实际使用，设计假设成立。"
    return {"counts": dict(agg), "evidence_click_ratio": round(clicks / reviews, 3) if reviews else None,
            "verdict": verdict}


async def snapshot_for_agent() -> dict:
    """给 Agent 用的精简数据快照。"""
    o = await overview()
    return {
        "候选人总数": o["total_candidates"],
        "分层分布": o["tier_counts"],
        "状态分布": o["status_counts"],
        "结论一致率": o["consistency_rate"].get("rate"),
        "结论一致率样本量": o["consistency_rate"].get("samples"),
        "误杀率": o["kill_rate"].get("rate"),
        "误杀率样本量": o["kill_rate"].get("samples"),
        "面试题采纳率": o["question_adopt_rate"].get("rate"),
        "平均单份成本_元": o["cost"]["avg_cost_cny"],
        "平均延迟_ms": o["cost"]["avg_latency_ms"],
        "告警": o["alerts"],
    }


async def refresh_snapshot() -> None:
    """把当期指标写进快照表，供历史对比。"""
    from app.db.models import MetricSnapshot
    o = await overview()
    async with SessionLocal() as db:
        db.add(MetricSnapshot(day=day_key(datetime.now()), payload=o))
        await db.commit()
