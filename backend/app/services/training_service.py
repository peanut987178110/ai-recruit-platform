"""培训方案与考核判卷（R-11、R-15）。

关键约束：主观题 AI 不给终评分数，只给要点覆盖分析。
培训考核结果可能影响转正评估，终评权必须在人手里（PRD 3.4.2）。
"""
from __future__ import annotations

import json

from app.core.schemas import ExamQuestion, ExamResult, JudgeItem, JudgeResult, TrainingPlanResult
from app.llm.client import llm
from app.prompts.manager import prompts
from app.rag.retriever import kb, search_knowledge

TYPE_MIX = {"基础": 0.4, "进阶": 0.4, "综合": 0.2}
QPTS = ["单选", "多选", "情景判断", "案例分析"]

FULL_SCORE = {"单选": 10.0, "多选": 10.0, "情景判断": 10.0, "案例分析": 20.0}



# 不适合出培训章节的能力项特征。
# 「学历本科及以上」是准入硬门槛、「简历真实性存疑」是排除条款 ——
# 这两类没有可培训的内容：你不能教一个人「变得更真实」，
# 也不能给已经在职的人培训学历。把它们编进大纲只会占学时、稀释重点。
_NON_TRAINABLE = (
    "学历", "学位", "本科及以上", "硕士", "博士", "大专",
    "真实性", "存疑", "造假", "虚假", "夸大", "包装",
)


def is_trainable(item: dict) -> bool:
    """该能力项是否适合作为培训内容。"""
    if item.get("is_veto"):
        # 负面否决项（排除条款）本质是筛查条件，不是能力
        if (item.get("veto_polarity") or "positive") == "negative":
            return False
    name = str(item.get("name", ""))
    return not any(k in name for k in _NON_TRAINABLE)


async def gen_outline(*, position_name: str, items: list[dict],
                      business_line: str,
                      extra_context: str = "") -> tuple[TrainingPlanResult, dict]:
    """生成培训大纲。章节与能力项一一对应，无孤立章节（验收 A4-1）。

    extra_context 是发布者上传的公司资料 —— 有它时优先引用，
    因为它比通用 SOP 更贴合这家公司的实际做法。
    """
    # 只把可培训的能力项交给模型 —— 门槛类（学历）与排除条款类（真实性存疑）
    # 编进大纲没有意义：你没法给在职的人培训学历，也没法培训"变得更真实"。
    trainable = [i for i in items if is_trainable(i)]
    if not trainable:
        trainable = items          # 全都是门槛项时退回全集，至少别生成空大纲

    ability_lines = "\n".join(
        f"- ability_id={i['id']}｜{i['name']}｜判定说明：{i.get('criteria', '')}"
        for i in trainable
    )
    hits = await search_knowledge(
        f"{position_name} {' '.join(i['name'] for i in trainable[:4])}",
        top_k=6, business_line=business_line,
    )
    docs = kb.format_for_prompt(hits, max_chars=2600)
    if extra_context:
        # 公司资料优先展示，并明确要求优先引用它
        docs = ("【公司内部资料｜优先依据】\n" + extra_context
                + "\n\n【通用知识库｜补充参考】\n" + docs)

    p = await prompts.resolve("培训大纲生成", {
        "position": position_name, "abilities": ability_lines, "docs": docs,
    }, route_key=f"outline-{position_name}")

    res = await llm.complete_json(
        ability="培训大纲生成", prompt_id=p.prompt_id, prompt_version=p.version,
        system=p.system_prompt, user=p.user_prompt,
        schema=TrainingPlanResult, tier="medium", max_tokens=10000, is_async=True,
    )

    if not res.ok or not res.result:
        # 降级：按能力项直出大纲骨架，人工补充
        plan = TrainingPlanResult(draft=True)
        for i in items:
            plan.outline.append({
                "chapter": f"第 {len(plan.outline) + 1} 章　{i['name']}",
                "ability_id": str(i["id"]), "ability_name": i["name"],
                "hours": 2.0, "material_ref": "待人工关联知识库文档",
            })
        from app.core.schemas import TrainingOutlineItem
        plan.outline = [TrainingOutlineItem(**o) for o in plan.outline]
        meta = res.meta.model_dump()
        meta["degraded"] = True
        meta["degrade_reason"] = res.error or "大纲生成失败，已降级为能力项骨架"
        return plan, meta

    plan = res.result
    item_map = {str(i["id"]): i["name"] for i in trainable}

    # 后置校验：剔除无对应能力项的孤立章节，并回填名称
    valid, seen = [], set()
    for o in plan.outline:
        if o.ability_id and o.ability_id not in item_map:
            # 模型给了不存在的能力项 id：尝试按名称匹配
            matched = next((k for k, v in item_map.items() if v == o.ability_name), None)
            if not matched:
                continue
            o.ability_id = matched
        if not o.ability_name:
            o.ability_name = item_map.get(o.ability_id, "")
        if not o.ability_name:
            continue
        if o.ability_id in seen:
            continue
        seen.add(o.ability_id)
        o.chapter = o.chapter or f"第 {len(valid) + 1} 章　{o.ability_name}"
        valid.append(o)

    # 补齐缺失的可培训项章节，保证一一对应。
    # 注意只补可培训的 —— 门槛类与排除条款类不该出现在培训大纲里。
    trainable_map = {str(i["id"]): i["name"] for i in trainable}
    for k, name in trainable_map.items():
        if k not in seen:
            from app.core.schemas import TrainingOutlineItem
            valid.append(TrainingOutlineItem(
                chapter=f"第 {len(valid) + 1} 章　{name}", ability_id=k, ability_name=name,
                hours=2.0, material_ref="待人工关联知识库文档",
            ))
    plan.outline = valid
    plan.draft = True
    return plan, res.meta.model_dump()


async def gen_exam(*, position_name: str, items: list[dict], business_line: str,
                   counts: dict[str, int] | None = None,
                   extra_context: str = "") -> tuple[ExamResult, dict]:
    """生成考核题库。每道题的答案必须可溯源（PRD 3.4.1）。"""
    counts = counts or {"单选": 4, "多选": 2, "情景判断": 2, "案例分析": 1}
    # 与大纲一致：门槛类与排除条款类不出题。
    # 这类项本来就无法通过「学习」获得，拿去考人只会制造噪音。
    items = [i for i in items if is_trainable(i)] or items
    ability_lines = "\n".join(
        f"- ability_id={i['id']}｜{i['name']}｜判定说明：{i.get('criteria', '')}"
        for i in items
    )
    hits = await search_knowledge(f"{position_name} SOP 规范 流程", top_k=8,
                                  business_line=business_line)
    docs = kb.format_for_prompt(hits, max_chars=4200)
    if extra_context:
        docs = ("【公司内部资料｜出题依据优先取自此处】\n" + extra_context
                + "\n\n【通用知识库｜补充参考】\n" + docs)
    p = await prompts.resolve("题库生成", {
        "position": position_name, "abilities": ability_lines,
        "docs": docs, "mix": json.dumps(counts, ensure_ascii=False),
    }, route_key=f"exam-{position_name}")

    res = await llm.complete_json(
        ability="题库生成", prompt_id=p.prompt_id, prompt_version=p.version,
        system=p.system_prompt, user=p.user_prompt,
        schema=ExamResult, tier="medium", max_tokens=12000, is_async=True,
    )

    if not res.ok or not res.result:
        meta = res.meta.model_dump()
        meta["degraded"] = True
        meta["degrade_reason"] = res.error or "题库生成失败，可从知识库手动选题"
        return ExamResult(draft=True), meta

    exam = res.result
    item_map = {str(i["id"]): i["name"] for i in items}
    cleaned: list[ExamQuestion] = []
    for q in exam.questions:
        if q.qtype not in QPTS:
            continue
        if not q.stem.strip():
            continue
        if not q.ability_name:
            q.ability_name = item_map.get(q.ability_id, "")
        # 多选题必须有至少两个正确选项；单选题必须有答案
        if q.qtype == "单选" and (not q.options or not q.answer):
            continue
        if q.qtype == "多选" and len(q.options) < 3:
            continue
        q.ref = (q.ref or "").strip()
        cleaned.append(q)
    exam.questions = cleaned
    exam.draft = True
    return exam, res.meta.model_dump()


def judge_objective(q: ExamQuestion, answer: str) -> JudgeItem:
    """客观题规则自动判（PRD 3.4.2）。

    单选：完全匹配即得分。
    多选：全对得满分，漏选得半分，错选不得分。
    """
    if q.qtype == "单选":
        correct = (answer or "").strip().upper()[:1] == (q.answer or "").strip().upper()[:1]
        return JudgeItem(
            question_id=str(q.id), qtype=q.qtype, full_score=FULL_SCORE["单选"],
            score=FULL_SCORE["单选"] if correct else 0.0, method="规则自动判",
            hit_points=["选择正确"] if correct else [],
            miss_points=[] if correct else [f"正确答案为 {q.answer}"],
            comment="",
        )

    if q.qtype == "多选":
        std = {c for c in (q.answer or "").upper() if c.isalpha()}
        got = {c for c in (answer or "").upper() if c.isalpha()}
        wrong = got - std
        missed = std - got
        if wrong:
            score, note, hit, miss = 0.0, "存在错选，不得分", list(got & std), sorted(wrong | missed)
        elif not missed:
            score, note, hit, miss = FULL_SCORE["多选"], "全部选对", sorted(std), []
        else:
            score = FULL_SCORE["多选"] / 2
            note, hit, miss = "漏选得半分", sorted(got & std), sorted(missed)
        return JudgeItem(
            question_id=str(q.id), qtype=q.qtype, full_score=FULL_SCORE["多选"],
            score=score, method="规则自动判", hit_points=hit, miss_points=miss, comment=note,
        )

    return JudgeItem(question_id=str(q.id), qtype=q.qtype,
                     full_score=FULL_SCORE.get(q.qtype, 10.0), method="待人工")


async def judge_subjective(*, qa_pairs: list[dict]) -> tuple[list[JudgeItem], dict]:
    """主观题：AI 只给要点覆盖分析，不给终评分数。"""
    if not qa_pairs:
        return [], {}
    payload = json.dumps(qa_pairs, ensure_ascii=False)[:9000]
    p = await prompts.resolve("主观题判卷", {"questions": payload, "answers": payload},
                              route_key=f"judge-{len(qa_pairs)}")
    res = await llm.complete_json(
        ability="主观题判卷", prompt_id=p.prompt_id, prompt_version=p.version,
        system=p.system_prompt, user=p.user_prompt,
        schema=JudgeResult, tier="medium", max_tokens=4000, is_async=False,
    )
    if not res.ok or not res.result:
        # 降级为仅人工判卷（PRD 4.1）
        items = [JudgeItem(question_id=str(p_["id"]), qtype=p_.get("qtype", "案例分析"),
                           full_score=FULL_SCORE.get(p_.get("qtype", "案例分析"), 10.0),
                           method="仅人工判卷",
                           comment="要点分析服务不可用，请带教人手动判卷")
                 for p_ in qa_pairs]
        meta = res.meta.model_dump()
        meta["degraded"] = True
        meta["degrade_reason"] = res.error or "主观题判卷失败，降级为仅人工判卷"
        return items, meta

    out = []
    for it in res.result.items:
        # 强制清空任何分数，只保留要点覆盖分析
        it.score = 0.0
        it.method = "AI 要点覆盖分析（终评由带教人给出）"
        out.append(it)
    return out, res.meta.model_dump()


def build_radar(items: list[dict], judge_items: list[JudgeItem],
                question_map: dict[str, dict]) -> dict[str, float]:
    """按能力项汇总得分，生成能力雷达图（R-15）。维度即能力模型的能力项。"""
    agg: dict[str, list[float]] = {i["name"]: [] for i in items}
    id2name = {str(i["id"]): i["name"] for i in items}
    for ji in judge_items:
        q = question_map.get(ji.question_id)
        if not q:
            continue
        name = q.get("ability_name") or id2name.get(str(q.get("ability_id")), "")
        if not name:
            continue
        agg.setdefault(name, [])
        if ji.full_score > 0:
            agg[name].append(round(ji.score / ji.full_score * 100, 1))
    return {k: (round(sum(v) / len(v), 1) if v else 0.0) for k, v in agg.items()}
