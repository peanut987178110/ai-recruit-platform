"""考试组卷、计时与反作弊。

反作弊的设计原则，也是这个模块最需要讲清楚的部分：

**分三层，且只有第一层是真正的防线。**

1. 服务端强制（无法绕过）
   - 正确答案永不下发到客户端，判分只在服务端做
   - 开考即冻结试卷，交卷按同一份卷子判分
   - 倒计时以服务端时间为准，前端显示只是参考
   - 随机组卷 + 选项乱序 + 每人不同顺序
   - 交卷幂等、限次，过期不可再交

2. 客户端信号采集（可绕过，仅作线索）
   - 切屏/失焦次数与时长、粘贴、复制、右键、开发者工具、多标签页
   这些**挡不住有心作弊的人** —— 拔网线、用第二台设备都能绕过。
   因此只记录、只汇总成风险分，绝不据此自动判负。

3. 人工复核（最终判定）
   风险分高的记录连同证据一起呈现给带教人，由人决定是否作废重考。
   这符合平台一贯的「AI 给证据、人做决策」，也避免误伤：
   切屏可能是因为弹窗、输入法、甚至系统通知。

把第二层当第三层用（自动判作弊）是最常见的错误设计 —— 它既拦不住真作弊，
又会冤枉正常人。所以 risk_level 只影响「是否进入人工复核队列」，
不影响成绩本身。
"""
from __future__ import annotations

import random
import re
from datetime import datetime, timedelta

# ---------------- 反作弊信号 ----------------

# 每个信号的权重。数值是经验值，衡量的是「这个行为有多异常」，
# 不是「有多可疑」—— 权重高的信号更罕见，而非更确定。
SIGNAL_WEIGHTS: dict[str, float] = {
    "blur": 1.0,             # 页面失焦（切到别的窗口）
    "blur_long": 3.0,        # 单次失焦超过 20 秒
    "paste": 4.0,            # 粘贴
    "copy": 2.0,             # 复制题干
    "contextmenu": 2.0,      # 右键菜单
    "devtools": 6.0,         # 疑似打开开发者工具
    "visibility": 1.0,       # 切到别的标签页
    "multi_tab": 5.0,        # 同一账号多标签页同时考试
    "fast_submit": 3.0,      # 交卷时间异常短
    "no_focus_session": 2.0,  # 整场考试从未获得焦点
    "print": 4.0,            # 打印（含打印为 PDF）
    "resize_abnormal": 1.0,  # 窗口尺寸异常（可能是分屏抄答案）
}

SIGNAL_LABEL: dict[str, str] = {
    "blur": "切出考试页面",
    "blur_long": "长时间离开考试页面",
    "paste": "使用粘贴",
    "copy": "复制内容",
    "contextmenu": "使用右键菜单",
    "devtools": "疑似打开开发者工具",
    "visibility": "切换到其它标签页",
    "multi_tab": "同一账号多窗口作答",
    "fast_submit": "交卷速度异常",
    "no_focus_session": "整场未获得焦点",
    "print": "尝试打印",
    "resize_abnormal": "窗口尺寸异常",
}

# 风险等级阈值。刻意定得保守 —— 宁可多转几条人工复核，
# 也不要因为一次弹窗就把人标成作弊。
RISK_WATCH = 5.0
RISK_SUSPECT = 15.0


def compute_risk(signals: list[dict], duration_seconds: int, total_questions: int) -> tuple[float, str]:
    """把信号列表汇总成风险分与等级。

    去重加权：同一类信号反复出现会累计，但单类上限为权重的 3 倍 ——
    否则一个人切屏 50 次会直接判可疑，而实际上可能只是他的输入法一直抢焦点。
    """
    score = 0.0
    per_kind: dict[str, int] = {}
    for s in signals or []:
        kind = str(s.get("kind", ""))
        if not kind:
            continue
        per_kind[kind] = per_kind.get(kind, 0) + 1

    for kind, count in per_kind.items():
        w = SIGNAL_WEIGHTS.get(kind, 1.0)
        score += w * min(count, 3)

    # 时长信号：每题平均不足 20 秒基本不可能认真读题
    if total_questions > 0 and duration_seconds > 0:
        per_q = duration_seconds / total_questions
        if per_q < 15:
            score += SIGNAL_WEIGHTS["fast_submit"] * 2
        elif per_q < 25:
            score += SIGNAL_WEIGHTS["fast_submit"]

    if score >= RISK_SUSPECT:
        return round(score, 1), "可疑"
    if score >= RISK_WATCH:
        return round(score, 1), "关注"
    return round(score, 1), "正常"


def normalize_signals(raw: list[dict]) -> list[dict]:
    """清洗前端上报的信号。

    前端可以被篡改，所以只接受白名单内的类型，并限制条数 ——
    否则有人可以塞一万条 blur 把别人的风险分刷爆（如果将来支持互相上报的话），
    或者塞非法字段污染存储。
    """
    out: list[dict] = []
    for s in (raw or [])[:200]:
        kind = str(s.get("kind", ""))
        if kind not in SIGNAL_WEIGHTS:
            continue
        try:
            at = int(s.get("at", 0))
        except (TypeError, ValueError):
            at = 0
        extra = s.get("detail")
        out.append({
            "kind": kind,
            "label": SIGNAL_LABEL.get(kind, kind),
            "at": max(0, min(at, 24 * 3600)),
            "detail": str(extra)[:120] if extra else "",
        })
    return out


def summarize_signals(signals: list[dict]) -> list[dict]:
    """按类型归并，供带教人快速看清发生了什么。"""
    agg: dict[str, dict] = {}
    for s in signals or []:
        k = s.get("kind", "")
        if k not in agg:
            agg[k] = {"kind": k, "label": s.get("label") or SIGNAL_LABEL.get(k, k),
                      "count": 0, "first_at": s.get("at", 0), "samples": []}
        agg[k]["count"] += 1
        if len(agg[k]["samples"]) < 3 and s.get("detail"):
            agg[k]["samples"].append(s["detail"])
    return sorted(agg.values(), key=lambda x: -SIGNAL_WEIGHTS.get(x["kind"], 0) * x["count"])


# ---------------- 组卷 ----------------

def build_paper(
    questions: list[dict],
    counts: dict[str, int] | None = None,
    seed: int | None = None,
) -> tuple[list[str], dict[str, list[int]]]:
    """随机组卷并生成选项乱序映射。

    返回 (题目 id 有序列表, {题目id: 选项新顺序})。

    三个反抄袭措施：
    - 从题库随机抽取：不同人拿到的题目集合不同
    - 题目顺序随机：防止对题号抄答案
    - 选项顺序随机：防止「第三题选 C」这种跨人抄袭
    """
    rng = random.Random(seed)

    if not counts:
        # 默认按题型取：单选多选全取，主观题各取一半
        counts = {}
        for q in questions:
            counts[q.get("qtype", "单选")] = counts.get(q.get("qtype", "单选"), 0) + 1

    picked: list[dict] = []
    for qtype, n in counts.items():
        pool = [q for q in questions if q.get("qtype") == qtype]
        rng.shuffle(pool)
        # 题目数量不足就全取，不报错 —— 组卷失败比少几道题更糟
        picked.extend(pool[:max(1, n)] if pool else [])

    rng.shuffle(picked)
    paper = [str(q["id"]) for q in picked]

    option_order: dict[str, list[int]] = {}
    for q in picked:
        opts = q.get("options") or []
        if not opts:
            continue
        order = list(range(len(opts)))
        rng.shuffle(order)
        option_order[str(q["id"])] = order

    return paper, option_order


def shuffle_options(options: list[str], order: list[int]) -> list[str]:
    """按给定顺序重排选项。"""
    if not options or not order:
        return list(options or [])
    out = []
    for idx in order:
        if 0 <= idx < len(options):
            out.append(options[idx])
    return out


def letter_of(index: int) -> str:
    """0 -> A，1 -> B，…"""
    return chr(ord("A") + index) if 0 <= index < 26 else ""


def remap_answer(answer: str, order: list[int]) -> str:
    """把题库里的原始答案（按原选项顺序）换算成本次乱序后的答案字母。

    例：原答案 B（索引 1），乱序后原索引 1 落在新位置 2 → 返回 "C"。
    """
    if not answer or not order:
        return answer
    std = {c for c in str(answer).upper() if c.isalpha()}
    originals = {ord(c) - ord("A") for c in std}
    mapped = {letter_of(order.index(i)) for i in originals if i in order}
    return "".join(sorted(x for x in mapped if x))


def unmap_answer(answer: str, order: list[int]) -> str:
    """把考生提交的乱序字母换算回题库原始顺序，用于判分。

    与 remap_answer 互为逆运算。判分必须走这一步，
    否则乱序后按原答案比对会全错。
    """
    if not answer or not order:
        return answer
    std = {c for c in str(answer).upper() if c.isalpha()}
    picked = {ord(c) - ord("A") for c in std}
    originals = {order[i] for i in picked if 0 <= i < len(order)}
    return "".join(sorted(letter_of(i) for i in originals))


def strip_answers(questions: list[dict]) -> list[dict]:
    """剔除答案与解析，只留考生需要的字段。

    这是整个反作弊体系里最重要的一行 —— 只要答案随题目下发，
    其余措施全部形同虚设（打开开发者工具就能看到）。
    """
    out = []
    for q in questions:
        out.append({
            "id": q.get("id"),
            "qtype": q.get("qtype", "单选"),
            "stem": q.get("stem", ""),
            "options": q.get("options") or [],
            "difficulty": q.get("difficulty", "基础"),
            "ability_name": q.get("ability_name", ""),
            "full_score": q.get("full_score", 10.0),
        })
    return out


def grade_objective(qtype: str, std_answer: str, given: str, full_score: float) -> tuple[float, str, list[str], list[str]]:
    """客观题判分。返回 (得分, 说明, 命中项, 缺失项)。

    单选：完全匹配即得分。
    多选：全对得满分，漏选得半分，错选不得分（规则在卷首说明，验收 A4-2）。
    """
    std = {c for c in str(std_answer or "").upper() if c.isalpha()}
    got = {c for c in str(given or "").upper() if c.isalpha()}

    if not std:
        return 0.0, "该题无标准答案，待人工处理", [], []
    if not got:
        return 0.0, "未作答", [], sorted(std)

    if qtype == "单选":
        ok = got == std
        return (full_score if ok else 0.0,
                "回答正确" if ok else f"正确答案为 {''.join(sorted(std))}",
                sorted(got & std) if ok else [],
                [] if ok else sorted(std))

    # 多选
    wrong = got - std
    missed = std - got
    if wrong:
        return 0.0, f"存在错选（{''.join(sorted(wrong))}），不得分", sorted(got & std), sorted(std)
    if not missed:
        return full_score, "全部选对", sorted(std), []
    return full_score / 2, f"漏选 {''.join(sorted(missed))}，得半分", sorted(got & std), sorted(missed)


# ---------------- 其它 ----------------

_ANSWER_LEAK = re.compile(r"(答案|正确答案|参考答案|answer)\s*[:：]?\s*[A-Da-d]{1,4}")


def material_quality_warning(text: str) -> str:
    """检查上传的资料是否适合出题。

    资料里直接写着「答案：B」会让 AI 生成一批毫无区分度的题，
    提前提示比事后返工便宜。这里只提示，不阻止上传。
    """
    if len(text.strip()) < 200:
        return "资料内容较短（不足 200 字），可能不足以生成足够的题目。"
    hits = _ANSWER_LEAK.findall(text)
    if len(hits) >= 3:
        return (f"资料中检测到 {len(hits)} 处「答案：X」形式的表述。"
                f"AI 出题会优先避开这类内容，但建议先清理，否则题目质量会受影响。")
    return ""


def default_deadline(minutes: int = 60) -> datetime:
    return datetime.now() + timedelta(minutes=minutes)
