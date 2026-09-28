"""面试题生成与评估报告（R-09、R-10、R-14）。

出题逻辑的核心是「差集」：优先针对简历中未体现或部分命中的能力项出题，
已充分证明的能力项最多生成 1 道确认题，避免把面试时间浪费在已知信息上。

敏感话题拦截做双重保障（PRD 3.3.2）：
  提示词硬约束（在提示词库里）+ 输出后置校验（在这里）
命中则整条丢弃并记录日志。
"""
from __future__ import annotations

import re

from app.core.schemas import (
    AbilityQA, InterviewQuestion, InterviewReportResult, QuestionGenResult,
)
from app.llm.client import llm
from app.prompts.manager import prompts

# 后置校验：敏感话题关键词。提示词已经禁止，这里兜第二层。
_SENSITIVE = [
    re.compile(r"(婚|已婚|未婚|结婚|离婚|恋爱|对象|配偶)"),
    re.compile(r"(生育|生孩子|要孩子|怀孕|孕|备孕|哺乳|产假|二胎|三胎)"),
    re.compile(r"(年龄|多大|几岁|出生|生日|哪年(出生|生))"),
    re.compile(r"(宗教|信仰|信(佛|教|基督|伊斯兰))"),
    re.compile(r"(户籍|户口|老家|籍贯|哪里人|祖籍)"),
    re.compile(r"(政治面貌|党员|党派|入党)"),
    re.compile(r"(性别|男性|女性|男朋友|女朋友)"),
    re.compile(r"(健康状况|病史|疾病|残疾|体检报告)"),
    re.compile(r"(父母|家庭情况|家里|兄弟姐妹|家人做什么)"),
]

LAYER_QUOTA = {
    "基础考察": (2, 3),
    "项目深挖": (3, 5),
    "压力追问": (2, 2),
    "真实性验证": (2, 2),
}


def is_sensitive(text: str) -> tuple[bool, str]:
    for pat in _SENSITIVE:
        m = pat.search(text)
        if m:
            return True, m.group(0)
    return False, ""


def filter_sensitive(questions: list[InterviewQuestion]) -> tuple[list[InterviewQuestion], list[dict]]:
    """整条丢弃命中敏感话题的题目，并返回被丢弃的记录用于写日志。"""
    kept, dropped = [], []
    for q in questions:
        hit, word = is_sensitive(q.content)
        if hit:
            dropped.append({"content": q.content[:120], "matched": word, "layer": q.layer})
            continue
        kept.append(q)
    return kept, dropped


def ensure_layers(questions: list[InterviewQuestion], states: dict[str, str],
                  ability_names: dict[str, str] | None = None,
                  weights: dict[str, int] | None = None) -> list[InterviewQuestion]:
    """补齐题量不足的层级（PRD 4.1：面试题生成失败的降级策略）。

    降级题目也必须关联到具体能力项 —— 没有能力项锚点的题目既无法统计采纳率，
    也无法在评估报告里归类，对面试官是废题。因此这里按「能力缺口 + 权重」
    循环取用，而不是用一次就耗尽的池子。
    """
    ability_names = ability_names or {}
    weights = weights or {}
    by_layer: dict[str, list[InterviewQuestion]] = {}
    for q in questions:
        by_layer.setdefault(q.layer, []).append(q)

    # 缺口能力项按权重降序，权重高的更需要面试验证
    weak = sorted(
        [aid for aid, st in states.items() if st in ("absent", "partial", "mismatch")],
        key=lambda a: -weights.get(a, 0),
    )
    # 缺口项太少时，把已证明的能力项也纳入补齐池 —— 否则所有兜底题会挤在同一项上，
    # 面试官会看到四道题都在验证同一个能力，其余能力得不到覆盖。
    if len(weak) < 2:
        rest = sorted(
            [aid for aid in states if aid not in weak],
            key=lambda a: -weights.get(a, 0),
        )
        weak = weak + rest
    if not weak:
        weak = [""]

    for layer, (lo, _hi) in LAYER_QUOTA.items():
        cur = by_layer.get(layer, [])
        if len(cur) >= lo:
            continue
        used = {q.ability_id for q in cur}
        for k in range(lo - len(cur)):
            # 优先挑本轮还没被用过的能力项，保证覆盖面
            aid = next((a for a in weak if a not in used), weak[k % len(weak)])
            used.add(aid)
            cur.append(InterviewQuestion(
                layer=layer,
                content=_FALLBACK[layer],
                ability_id=aid,
                ability_name=ability_names.get(aid, ""),
                basis="该能力项在简历中未充分体现，使用通用模板确认"
                      if states.get(aid) in ("absent", "partial", "mismatch")
                      else "简历中已有证据，此题用于交叉确认",
                anchors={"高": "有具体事例，能说清过程与结果",
                         "中": "有事例但细节模糊",
                         "低": "无法给出具体事例"},
                duration_min=3,
                probe="能否举一个最近半年内的具体例子？",
            ))
        by_layer[layer] = cur

    out: list[InterviewQuestion] = []
    for layer in LAYER_QUOTA:
        out.extend(by_layer.get(layer, []))
    return out


_FALLBACK = {
    "基础考察": "请介绍一段你最有代表性的工作经历，说明你个人具体负责的部分与团队分工。",
    "项目深挖": "请挑一个你主导的项目，说明当时的目标、你的关键决策，以及最终结果如何衡量。",
    "压力追问": "如果这个项目的时间压缩一半，资源不变，你会砍掉哪些部分？为什么？",
    "真实性验证": "关于你提到的这段经历，能否说明一下你在其中对接了哪些角色，协作方式是怎样的？",
}


def enforce_gap_priority(
    questions: list[InterviewQuestion],
    states: dict[str, str],
    weights: dict[str, int] | None = None,
    gate_ids: set[str] | None = None,
) -> list[InterviewQuestion]:
    """强制「差集出题」约束。这条规则写在提示词里，但模型会违反它 —— 实测中它会
    给两个已证明的能力项各出三道题，把 10 道题全堆在已经验证过的信息上。

    三类能力项要区别对待：

    1. 排除条款（gate，如「简历真实性存疑」）—— **最多 1 道，且只做真实性交叉核验**。
       这类条款是给 HR 判断合规风险的，不是让候选人当面回答「你有没有造假」。
       模型会把它当成普通缺口项猛出题，实测出过 4 道，这在面试现场是不合适的问法。

    2. 普通缺口项（absent / partial / mismatch）—— 优先保留，这是面试要验证的重点。

    3. 已证明项（hit）—— 每项最多 1 道确认题，避免浪费面试时间在已知信息上。
    """
    weights = weights or {}
    gate_ids = gate_ids or set()

    gates = [q for q in questions if q.ability_id in gate_ids]
    others = [q for q in questions if q.ability_id not in gate_ids]
    gaps = [q for q in others if states.get(q.ability_id) in ("absent", "partial", "mismatch")]
    proven = [q for q in others if q not in gaps]

    # 排除条款：只保留 1 道，优先「真实性验证」层（它的性质就是交叉核验）
    gate_kept: list[InterviewQuestion] = []
    if gates:
        gates.sort(key=lambda q: (q.layer != "真实性验证", q.duration_min))
        gate_kept = [gates[0]]

    # 已证明项：每个能力项只留一道，优先「基础考察」（确认题的性质）
    layer_rank = {"基础考察": 0, "真实性验证": 1, "项目深挖": 2, "压力追问": 3}
    seen: dict[str, InterviewQuestion] = {}
    for q in proven:
        cur = seen.get(q.ability_id)
        if cur is None or layer_rank.get(q.layer, 9) < layer_rank.get(cur.layer, 9):
            seen[q.ability_id] = q

    gaps.sort(key=lambda q: -weights.get(q.ability_id, 0))
    return gaps + gate_kept + list(seen.values())


def top_up_from_gaps(
    questions: list[InterviewQuestion],
    states: dict[str, str],
    ability_names: dict[str, str],
    weights: dict[str, int],
    plan_minutes: int,
    gate_ids: set[str] | None = None,
) -> list[InterviewQuestion]:
    """已证明项被限流后，用缺口项的题目把时间预算补回来。

    如果不补，强简历（多数能力已证明）会只剩三四道题，面试时间用不满，
    面试官会以为系统没生成完。
    """
    budget = plan_minutes + 2
    used = sum(q.duration_min for q in questions)
    if used >= budget:
        return questions

    gate_ids = gate_ids or set()
    # 排除条款不参与补齐：它最多一道核验题，不重复追问
    gaps = sorted(
        [a for a, st in states.items()
         if st in ("absent", "partial", "mismatch") and a not in gate_ids],
        key=lambda a: -weights.get(a, 0),
    )
    if not gaps:
        return questions

    layers = list(LAYER_QUOTA.keys())
    i = 0
    while used + 3 <= budget and i < len(gaps) * 2:
        aid = gaps[i % len(gaps)]
        layer = layers[i % len(layers)]
        questions.append(InterviewQuestion(
            layer=layer,
            content=_FALLBACK[layer],
            ability_id=aid,
            ability_name=ability_names.get(aid, ""),
            basis="该能力项在简历中未充分体现，需在面试中补充验证",
            anchors={"高": "有具体事例，能说清过程与结果",
                     "中": "有事例但细节模糊",
                     "低": "无法给出具体事例"},
            duration_min=3,
            probe="能否举一个最近半年内的具体例子？",
        ))
        used += 3
        i += 1
    return questions


def trim_to_minutes(questions: list[InterviewQuestion], plan_minutes: int,
                    gap_ids: set[str] | None = None) -> list[InterviewQuestion]:
    """按时间预算重排取舍（PRD 3.3.2）。

    A3-3 要求 30 分钟方案总预计时长不超过 32 分钟，所以这里留 2 分钟余量。

    取舍优先级：覆盖能力缺口的题 > 其他题。差集出题是本产品的核心设计，
    如果按时间裁剪时把缺口题裁掉、留下泛泛的确认题，这个设计就失效了。
    """
    budget = plan_minutes + 2
    if not questions:
        return []
    gap_ids = gap_ids or set()

    chosen: list[InterviewQuestion] = []
    seen_layer: set[str] = set()

    # 第一轮：每个层级保底一道，优先取缺口题
    for layer in LAYER_QUOTA:
        pool = [q for q in questions if q.layer == layer]
        if not pool:
            continue
        pool.sort(key=lambda q: (q.ability_id not in gap_ids, q.duration_min))
        chosen.append(pool[0])
        seen_layer.add(layer)

    # 第二轮：按「缺口优先、时长短优先」补满预算
    rest = [q for q in questions if q not in chosen]
    rest.sort(key=lambda q: (q.ability_id not in gap_ids, q.duration_min))

    used = sum(q.duration_min for q in chosen)
    for q in rest:
        if used + q.duration_min <= budget:
            chosen.append(q)
            used += q.duration_min

    order = {k: i for i, k in enumerate(LAYER_QUOTA)}
    chosen.sort(key=lambda q: (order.get(q.layer, 9),
                               q.ability_id not in gap_ids))
    return chosen


async def generate_questions(
    *,
    candidate_id: int,
    position_name: str,
    items: list[dict],
    score_items: list[dict],
    parsed: dict,
    plan_minutes: int = 30,
) -> tuple[QuestionGenResult, dict, list[dict]]:
    """生成分层面试题。返回 (结果, 元信息, 被拦截的题目列表)。"""
    states = {str(s.get("ability_id")): s.get("state", "absent") for s in score_items}
    ability_names = {str(i["id"]): i["name"] for i in items}
    weights = {str(i["id"]): int(i.get("weight", 1) or 1) for i in items}
    # 负面否决项（排除条款）不当提问主题，只做一道真实性核验
    gate_ids = {str(i["id"]) for i in items
                if i.get("is_veto") and (i.get("veto_polarity") or "positive") == "negative"}
    ability_lines = []
    for i in items:
        aid = str(i["id"])
        st = states.get(aid, "absent")
        cn = {"hit": "已充分证明", "partial": "部分命中", "absent": "未体现",
              "mismatch": "不符合"}.get(st, st)
        ability_lines.append(
            f"- ability_id={aid}｜{i['name']}｜权重 {i.get('weight', 1)}｜"
            f"简历状态：{cn}｜判定说明：{i.get('criteria', '')}"
        )

    p = await prompts.resolve("面试题生成", {
        "position": position_name,
        "plan_minutes": str(plan_minutes),
        "abilities": "\n".join(ability_lines),
        "resume_json": _brief(parsed),
        "states": "\n".join(f"- {k}: {v}" for k, v in states.items()),
    }, route_key=str(candidate_id))

    res = await llm.complete_json(
        ability="面试题生成", prompt_id=p.prompt_id, prompt_version=p.version,
        system=p.system_prompt, user=p.user_prompt,
        schema=QuestionGenResult, tier="medium", max_tokens=12000,
        # 走异步超时档（10 分钟）：出题要输出多道题带锚点与依据，实测 60 至 90 秒，
        # 远超 PRD 4.3 给「同步能力」定的 20 秒阈值。这个能力在产品形态上本来就是
        # 后台生成 + 进度提示（PRD 2.3：超过 30 秒转后台任务），因此按异步处理。
        is_async=True,
    )

    if not res.ok or not res.result:
        # 降级为按能力项的通用题模板（PRD 4.1）
        gen = QuestionGenResult(plan_minutes=plan_minutes)
        gen.questions = ensure_layers([], states, ability_names, weights)
        gen.questions = trim_to_minutes(gen.questions, plan_minutes,
                                        gap_ids={aid for aid, st in states.items()
                                                 if st in ('absent', 'partial', 'mismatch')})
        meta = res.meta.model_dump()
        meta["degraded"] = True
        meta["degrade_reason"] = res.error or "面试题生成失败，已降级为通用模板"
        return gen, meta, []

    gen = res.result
    # 名称回填（模型可能只给 id）
    item_map = {str(i["id"]): i for i in items}
    for q in gen.questions:
        if not q.ability_name and q.ability_id in item_map:
            q.ability_name = item_map[q.ability_id]["name"]
        q.anchors = {k: str(v)[:200] for k, v in (q.anchors or {}).items()}

    kept, dropped = filter_sensitive(gen.questions)
    gen.questions = ensure_layers(kept, states, ability_names, weights)

    # 强制差集约束：已证明项限流到每项 1 道，再用缺口项把时间预算补回来。
    # 这一步必须放在提示词之外，因为模型会违反提示词里的这条规则。
    gen.questions = enforce_gap_priority(gen.questions, states, weights, gate_ids)
    gen.questions = top_up_from_gaps(gen.questions, states, ability_names, weights,
                                     plan_minutes, gate_ids)
    gen.questions = trim_to_minutes(
        gen.questions, plan_minutes,
        gap_ids={aid for aid, st in states.items() if st in ('absent', 'partial', 'mismatch')},
    )
    gen.plan_minutes = plan_minutes

    # 能力项名称回填（兜底题与模型只给 id 的题都要补）
    for q in gen.questions:
        if not q.ability_name:
            q.ability_name = ability_names.get(q.ability_id, "")

    gap_ids = {aid for aid, st in states.items() if st in ('absent', 'partial', 'mismatch')}
    meta = res.meta.model_dump()
    meta["sensitive_blocked"] = len(dropped)
    meta["gap_focused"] = sum(1 for q in gen.questions if q.ability_id in gap_ids)
    meta["gap_total"] = len(gen.questions)
    return gen, meta, dropped


def _brief(parsed: dict) -> str:
    import json
    keep = {k: parsed.get(k) for k in
            ("name", "works", "projects", "skills", "metrics", "education_level", "intention")}
    return json.dumps(keep, ensure_ascii=False)[:5000]


async def build_report(
    *,
    schedule_id: int,
    candidate_name: str,
    position_name: str,
    interviewer: str,
    duration_min: int,
    round_name: str,
    transcript: str,
    questions: list[dict],
    items: list[dict],
) -> tuple[InterviewReportResult, dict]:
    """由录音转写生成结构化评估报告（R-14）。AI 不预填面试官评分。"""
    ability_lines = "\n".join(
        f"- ability_id={i['id']}｜{i['name']}｜判定说明：{i.get('criteria', '')}"
        for i in items
    )
    q_lines = "\n".join(
        f"- [{q.get('layer')}] {q.get('content')}（考察：{q.get('ability_name') or q.get('ability_id')}）"
        for q in questions[:20]
    )

    p = await prompts.resolve("问答归类", {
        "abilities": ability_lines,
        "questions": q_lines or "（本轮无预生成题目）",
        "transcript": transcript[:14000],
    }, route_key=str(schedule_id))

    res = await llm.complete_json(
        ability="问答归类", prompt_id=p.prompt_id, prompt_version=p.version,
        system=p.system_prompt, user=p.user_prompt,
        schema=InterviewReportResult, tier="medium", max_tokens=10000, is_async=True,
    )

    if not res.ok or not res.result:
        # 降级为仅提供原始逐字稿（PRD 4.1）
        rep = InterviewReportResult(
            basic={"候选人": candidate_name, "岗位": position_name, "面试官": interviewer,
                   "时长": f"{duration_min} 分钟", "面试轮次": round_name},
            transcript=transcript,
            suggestions=["问答归类服务不可用，本报告仅提供原始逐字稿，请面试官手动填写评价"],
        )
        meta = res.meta.model_dump()
        meta["degraded"] = True
        meta["degrade_reason"] = res.error or "问答归类失败，降级为原始逐字稿"
        return rep, meta

    rep = res.result
    rep.basic = {
        "候选人": candidate_name, "岗位": position_name, "面试官": interviewer,
        "时长": f"{duration_min} 分钟", "面试轮次": round_name,
    }
    rep.transcript = transcript
    item_map = {str(i["id"]): i["name"] for i in items}
    for a in rep.by_ability:
        a.ability_name = a.ability_name or item_map.get(str(a.ability_id), "")
    return rep, res.meta.model_dump()
