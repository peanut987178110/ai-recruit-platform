"""匹配打分服务（R-03、R-04）。

三条硬约束在这里落地（PRD 6.1）：
  1. 无证据不给分 —— 模型找不到支撑片段时必须输出 absent，不允许凭常识推测。
  2. 未体现与不符合必须区分 —— 信息缺失不等于能力不足。
  3. 输出层一致性校验 —— 得分与证据不匹配、证据无法在原文定位，一律判为失败转人工。

另外做加权计算与分层判定。注意：权重计算、否决归零、分层阈值这三件事由代码算，
不交给模型，避免模型在算术上出错。
"""
from __future__ import annotations

import re
import time

from app.core.schemas import (
    AbilityScore, AbilityState, AIResult, Evidence, MatchScoreResult, RiskTag,
)
from app.llm.client import llm
from app.prompts.manager import prompts


def ability_to_dict(row) -> dict:
    """把 AbilityItem 数据库行转成打分用的字典。

    集中在一处转换，是为了保证 veto_polarity 这类新增字段不会在某条调用链上被漏掉 ——
    漏掉它会让负面排除条款按正面规则处理，把好候选人系统性打成 0 分。
    """
    return {
        "id": row.id,
        "name": row.name,
        "weight": row.weight,
        "evidence_types": row.evidence_types or [],
        "criteria": row.criteria or "",
        "is_veto": bool(row.is_veto),
        "veto_polarity": getattr(row, "veto_polarity", "positive") or "positive",
        "sort": getattr(row, "sort", 0),
    }


def locate_quote(quote: str, raw_text: str) -> tuple[int, int]:
    """把模型给的 quote 在原文里定位。定位不到返回 (-1, -1)。

    这是输出层校验的核心：定位不到的引用计为不准确（PRD 附录·引用准确率）。
    """
    if not quote or not raw_text:
        return -1, -1
    q = quote.strip()
    if len(q) < 4:
        return -1, -1

    idx = raw_text.find(q)
    if idx >= 0:
        return idx, idx + len(q)

    # 容忍空白差异
    collapsed = re.sub(r"\s+", "", q)
    if len(collapsed) >= 6:
        pattern = r"\s*".join(re.escape(c) for c in collapsed[:40])
        m = re.search(pattern, raw_text)
        if m:
            return m.start(), m.end()

    return -1, -1


# 风险提示的客观事实规则。系统输出客观描述，不做主观归因（PRD 3.2.3）
_YEAR = re.compile(r"(19|20)\d{2}")

# 院校名称识别：证据片段里若含校名，脱敏后再挂到能力项上（PRD 3.2.2）
_SCHOOL_IN_TEXT = re.compile(r"[一-鿿]{2,12}(?:大学|学院|学校|职业技术学院)")


def derive_risk_tags(parsed: dict, raw_text: str) -> list[RiskTag]:
    """从简历结构化数据中提取客观事实型风险提示。"""
    tags: list[RiskTag] = []
    works = parsed.get("works") or []

    # 近三年任职公司数
    recent = 0
    gaps: list[int] = []
    spans: list[tuple[int, int]] = []
    for w in works:
        s = _YEAR.findall(str(w.get("start", "")))
        e = _YEAR.findall(str(w.get("end", ""))) or ([str(__import__("datetime").date.today().year)]
                                                     if "至今" in str(w.get("end", "")) else [])
        if s and e:
            ys, ye = int(s[0] + _two(w.get("start", ""))), int(e[0] + _two(w.get("end", "")))
            spans.append((ys, ye))
    for ys, ye in spans:
        if ys >= 2023:
            recent += 1
    if recent >= 4:
        tags.append(RiskTag.FREQUENT_JOB)
    elif recent >= 4:
        tags.append(RiskTag.FREQUENT_JOB)

    # 履历断档：排序后相邻两段之间超过 6 个月
    spans.sort()
    for i in range(1, len(spans)):
        gap_months = (spans[i][0] - spans[i - 1][1]) * 12
        if gap_months >= 6:
            gaps.append(gap_months)
    if gaps:
        tags.append(RiskTag.GAP_6M)

    # 平均在职不足 1 年
    if len(spans) >= 3:
        total = sum(max(0, ye - ys) for ys, ye in spans)
        if total / len(spans) < 1.0:
            tags.append(RiskTag.AVG_TENURE_1Y)

    # 职级跨越异常：从低到高的跳跃幅度
    titles = [str(w.get("title", "")) for w in works]
    levels = []
    for t in titles:
        m = re.search(r"[Pp](\d)", t)
        if m:
            levels.append(int(m.group(1)))
    if len(levels) >= 2 and (max(levels) - min(levels)) >= 4:
        tags.append(RiskTag.LEVEL_JUMP)

    return tags[:4]


def _two(s: str) -> str:
    m = re.search(r"\d{2}(?=\D*$)", str(s))
    if m:
        return m.group(0)
    return "01"


def compute_total(scores: list[AbilityScore], items: list[dict]) -> tuple[float, bool, str]:
    """按权重加权求和，并处理否决项。

    两条规则来自 PRD 3.2.3 与 3.2.5：

    一、未体现（absent）固定 0 分且**不计入分母** —— 信息缺失不应该拉低总分，
        否则简历写得简略的候选人会被系统性低估。

    二、否决项分两种极性，必须区别对待：
        - positive（必须具备）：state 为 absent 或 mismatch 视为未命中，总分归零。
          例：「学历本科及以上」未达到。
        - negative（必须没有）：**只有 state 为 hit 才视为命中该负面条款**，总分归零。
          例：「简历真实性存疑」只有在模型确实发现造假证据时才归零。
          若按 positive 规则处理，简历真实的候选人会因为「没有造假迹象」被判 absent
          而归零，这是把好候选人系统性打成 0 分的严重错误。

    负面否决项同时是纯门禁：它不参与加权求和（既不加分也不占分母），
    否则「没有造假」这件事会变成一个白送的正向分数。
    """
    weight_map = {str(i["id"]): i.get("weight", 1) for i in items}
    veto_map = {str(i["id"]): i.get("is_veto", False) for i in items}
    polarity_map = {str(i["id"]): (i.get("veto_polarity") or "positive") for i in items}

    veto_hit = False
    veto_reason = ""
    for s in scores:
        if not veto_map.get(s.ability_id):
            continue
        polarity = polarity_map.get(s.ability_id, "positive")
        s.veto_polarity = polarity
        s.is_gate = polarity == "negative"

        if polarity == "positive":
            if s.state in (AbilityState.ABSENT, AbilityState.MISMATCH):
                veto_hit = True
                veto_reason = f"否决项「{s.ability_name}」未命中（{_state_cn(s.state)}）"
        else:  # negative：命中即归零
            if s.state == AbilityState.HIT:
                veto_hit = True
                veto_reason = f"触发排除条款「{s.ability_name}」（{s.reason or '简历存在该问题'}）"
        if veto_hit:
            s.veto_triggered = True
            break

    numerator = 0.0
    denominator = 0
    for s in scores:
        # 负面否决项是门禁，不参与加权
        if polarity_map.get(s.ability_id) == "negative" and veto_map.get(s.ability_id):
            continue
        w = weight_map.get(s.ability_id, s.weight or 1)
        if s.state == AbilityState.ABSENT:
            continue                      # 不计入分母
        denominator += w
        numerator += (s.score / 10.0) * w

    if veto_hit:
        return 0.0, True, veto_reason
    if denominator == 0:
        return 0.0, False, ""
    return round(numerator / denominator * 100, 1), False, ""


def _state_cn(state: AbilityState) -> str:
    return {
        AbilityState.HIT: "命中", AbilityState.PARTIAL: "部分命中",
        AbilityState.ABSENT: "未体现", AbilityState.MISMATCH: "不符合",
    }.get(state, str(state))


def decide_tier(total: float, confidence: float, veto_hit: bool,
                high_conf: float, high_score: int, mid_score: int) -> str:
    """置信度分层（PRD 3.2.5）。

    高分档：置信度达标且总分达标 -> 直接转待安排面试
    中间档：不满足高分档且总分达标 -> 强制人工复核
    低分档：总分不足或触发否决项 -> 转待定池
    """
    if veto_hit:
        return "低分档"
    if confidence >= high_conf and total >= high_score:
        return "高分档"
    if total >= mid_score:
        return "中间档"
    return "低分档"


async def score_candidate(
    *,
    candidate_id: int,
    raw_text: str,
    parsed: dict,
    position_name: str,
    items: list[dict],
    model_version: str,
    high_conf: float,
    high_score: int,
    mid_score: int,
) -> AIResult[MatchScoreResult]:
    """执行匹配打分。"""
    started = time.time()

    abilities_text = "\n".join(
        f"- ability_id={i['id']}｜{i['name']}｜权重 {i.get('weight', 1)}｜"
        f"证据类型 {'/'.join(i.get('evidence_types') or [])}｜"
        f"判定说明：{i.get('criteria', '')}"
        + ("｜【否决项·必须具备】判定为 absent 或 mismatch 时总分归零"
           if i.get("is_veto") and (i.get("veto_polarity") or "positive") == "positive"
           else "｜【排除条款·必须没有】只有在简历中确实找到该问题的证据时才判 hit，"
                "此时总分归零；简历未提及该问题一律判 absent，不视为问题"
           if i.get("is_veto")
           else "")
        for i in items
    )

    parsed_polarity = {str(i["id"]): (i.get("veto_polarity") or "positive") for i in items}
    parsed_veto = {str(i["id"]): bool(i.get("is_veto")) for i in items}

    p = await prompts.resolve("匹配打分", {
        "position": position_name,
        "abilities": abilities_text,
        "resume_text": raw_text[:11000],
    }, route_key=str(candidate_id))

    res = await llm.complete_json(
        ability="匹配打分", prompt_id=p.prompt_id, prompt_version=p.version,
        system=p.system_prompt, user=p.user_prompt,
        # 打分要为每个能力项输出状态、得分、理由，再加证据片段与风险提示。
# 能力项多的岗位（6 项）实测输出常在 4000 至 8000 token，6000 会截断。
        schema=_RawScoreResult, tier="medium", max_tokens=10000, is_async=False,
    )

    if not res.ok or not res.result:
        failed = MatchScoreResult()
        failed.summary = "打分失败，已转人工复核"
        out = AIResult.failure("匹配打分", res.error, res.meta)
        out.result = failed
        return out

    raw = res.result

    # ---- 组装逐项结果 ----
    lst: list[AbilityScore] = []
    item_map = {str(i["id"]): i for i in items}
    for rs in raw.ability_scores:
        item = item_map.get(str(rs.ability_id))
        if not item:
            continue
        try:
            state = AbilityState(rs.state)
        except ValueError:
            state = AbilityState.ABSENT
        # 校验得分区间与状态的一致性
        score = max(0.0, min(10.0, float(rs.score or 0)))
        if state == AbilityState.ABSENT:
            score = 0.0
        elif state == AbilityState.HIT and score < 7:
            score = 7.0     # 命中至少 7 分，防止模型把命中打成低分
        elif state == AbilityState.PARTIAL and not (3 <= score <= 6):
            score = max(3.0, min(6.0, score if score > 0 else 4.0))
        lst.append(AbilityScore(
            ability_id=str(item["id"]), ability_name=item["name"],
            weight=item.get("weight", 1), state=state, score=score,
            reason=rs.reason or "",
            veto_polarity=item.get("veto_polarity") or "positive",
            is_gate=(item.get("is_veto", False)
                     and (item.get("veto_polarity") or "positive") == "negative"),
        ))

    # 补齐模型漏掉的能力项：漏掉的按未体现处理（信息缺失，不拉低总分）
    seen = {s.ability_id for s in lst}
    for i in items:
        if str(i["id"]) not in seen:
            lst.append(AbilityScore(
                ability_id=str(i["id"]), ability_name=i["name"],
                weight=i.get("weight", 1), state=AbilityState.ABSENT,
                score=0.0, reason="模型未返回该项判断，按未体现处理",
                veto_polarity=i.get("veto_polarity") or "positive",
                is_gate=(i.get("is_veto", False)
                         and (i.get("veto_polarity") or "positive") == "negative"),
            ))

    total, veto_hit, veto_reason = compute_total(lst, items)
    result = MatchScoreResult(
        total=total, ability_scores=lst, veto_hit=veto_hit,
        veto_reason=veto_reason, summary=raw.summary or "",
    )

    # ---- 组装证据体并做输出层一致性校验 ----
    evidence: list[Evidence] = []
    for ev in res.evidence:
        start, end = locate_quote(ev.quote, raw_text)
        if start < 0:
            continue    # 定位不到的证据直接丢弃，计为引用不准确
        item = item_map.get(str(ev.ability_id), {})
        # 合规约束（PRD 3.2.2）：院校名称不进打分链路，也不该在证据里回显。
        # 学历项的证据常是含校名的整行，这里脱敏后再挂上去。
        # 注意 start/end 仍指向原文位置（前端据此高亮），quote 只是展示用的脱敏副本，
        # 因此前后端比对高亮时必须用「原文片段」而不是 quote。
        quote = ev.quote
        if _SCHOOL_IN_TEXT.search(quote):
            quote = _SCHOOL_IN_TEXT.sub("［院校已脱敏］", quote)
        evidence.append(Evidence(
            quote=quote, field_source=ev.field_source, start=start, end=end,
            ability_id=str(ev.ability_id), ability_name=item.get("name", ""),
            weight=item.get("weight", 0),
        ))

    # 打分项命中/部分命中却没有任何证据 -> 一致性校验失败，转人工
    scored_states = {AbilityState.HIT, AbilityState.PARTIAL}
    scored_abilities = {s.ability_id for s in lst if s.state in scored_states}
    evidenced = {e.ability_id for e in evidence}
    naked = scored_abilities - evidenced

    # 置信度：模型自评 + 证据完备度折扣。证据越充分，置信度越高
    conf = max(0.0, min(1.0, float(raw.confidence or 0.5)))
    if scored_abilities:
        coverage = len(evidenced & scored_abilities) / len(scored_abilities)
        conf = round(conf * (0.55 + 0.45 * coverage), 3)
    result.confidence = conf
    result.risk_tags = derive_risk_tags(parsed, raw_text)

    # 汇总证据到结果里（前端按能力项取）。
    # 同时写进 result 自身：调用方如果只拿 result（例如流水线落库），
    # 也能拿到证据，不会因为取错数据源而丢掉整条证据链。
    result_evidence = evidence
    result.evidence = [e.model_dump() for e in evidence]

    meta_ok = True
    warn = ""
    if naked and len(naked) >= max(2, len(scored_abilities)):
        # 全部给分项都无证据：判为输出异常
        meta_ok = False
        warn = "得分与证据不匹配：有给分能力项无法在简历原文定位证据，已转人工"

    out = AIResult(ok=meta_ok, ability="匹配打分", result=result,
                   evidence=result_evidence, meta=res.meta)
    if not meta_ok:
        out.error = warn
        out.result.confidence = min(conf, 0.4)
    out.meta.latency_ms = int((time.time() - started) * 1000)
    return out


# 模型输出的中间结构。故意与业务结构分开，因为模型只负责判断，不负责算术。
from pydantic import BaseModel, Field  # noqa: E402


class _RawAbilityScore(BaseModel):
    ability_id: str = ""
    ability_name: str = ""
    state: str = "absent"
    score: float = 0.0
    reason: str = ""


class _RawScoreResult(BaseModel):
    ability_scores: list[_RawAbilityScore] = Field(default_factory=list)
    confidence: float = 0.0
    veto_hit: bool = False
    veto_reason: str = ""
    risk_tags: list[str] = Field(default_factory=list)
    summary: str = ""
