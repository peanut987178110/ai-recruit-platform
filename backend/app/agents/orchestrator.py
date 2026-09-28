"""Agent 编排。

两个层次：
1. 对话智能体（ReAct 风格）：用户在对话框里描述需求，Agent 自己选择并串联技能。
   用 LangGraph 的 create_react_agent，工具即上面的 Skill。
2. 固定流水线：简历筛选这类流程步骤确定、顺序不能乱的场景，用显式图编排，
   不交给模型自由发挥 —— 顺序错了会浪费大量 token 且结果不可复现。

关键设计：Agent 的每一个会改变候选人状态的动作（采纳/否决/捞回），
都不由 Agent 直接执行，而是生成「待确认动作」交给人在界面点击。
这是 PRD 6.1「否决权永远在人」在智能体架构上的落地。
"""
from __future__ import annotations

import json
import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from app.agents.skills import register_all_skills
from app.agents.skills.base import SKILL_REGISTRY, get_tools
from app.core.config import settings
from app.llm.registry import registry

# 会改变候选人状态、必须由人确认的技能
HUMAN_GATE_SKILLS = {"pool_rescue"}

AGENT_SYSTEM = """你是「AI 招聘与人才发展平台」的智能助手，服务于招聘 HR、业务面试官与带教人。

你的能力边界（严格遵守）：
- 你只做辅助：把信息结构化、给出带证据的建议、把不确定的交给人工。
- **你不做终局决策**。你不能替 HR 拒绝候选人，不能替面试官打分，不能替带教人给终评分数。
  任何会改变候选人状态或影响转正结论的操作，你都只能生成「待人工确认的动作」，由人在界面上点击生效。
- 涉及自动拒信、情绪推断、性格判断、终面录用决策的请求，直接拒绝并说明原因：
  这类决策责任归属不清或科学性存疑，平台不提供。

工作方式：
- 面对「帮我筛简历」「这个候选人怎么样」这类请求，先调用相应技能拿到带证据的结果，
  再把结论讲清楚：命中了什么、缺失了什么、置信度如何、需要人判断什么。
- 讲结论时必须带上证据来源，不要只给一个分数。用户抗拒的从来不是 AI 的结论，
  而是无法验证的结论。
- 数字与事实优先。不确定就说不确定，不要编造候选人的经历。
- 回答简洁、用中文、面向业务人员（他们不是工程师），不要输出 JSON 原始结构。
"""


class AgentState(TypedDict):
    messages: Annotated[list, operator.add]
    pending_actions: list[dict[str, Any]]
    steps: list[str]


def _build_chat_agent():
    """构建对话智能体。用 LangGraph 的预置 ReAct 图。"""
    from langgraph.prebuilt import create_react_agent

    register_all_skills()
    tools = get_tools()
    tier, _ = registry.pick("medium")

    # 用网关的 Anthropic 兼容接口
    from langchain_openai import ChatOpenAI
    # 网关同时兼容 OpenAI 语义，这里走 OpenAI 兼容端点更稳（支持 function calling）
    llm = ChatOpenAI(
        model=tier,
        base_url=settings.anthropic_base_url.rstrip("/") + "/v1",
        api_key=settings.anthropic_auth_token or "not-set",
        temperature=0.0,
        timeout=120,
        max_retries=1,
        default_headers={"anthropic-version": "2023-06-01"},
    )
    return create_react_agent(llm, tools, prompt=AGENT_SYSTEM)


_chat_agent = None


def get_chat_agent():
    global _chat_agent
    if _chat_agent is None:
        _chat_agent = _build_chat_agent()
    return _chat_agent


async def chat(message: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """跑一轮对话。返回回答与 Agent 调用的技能轨迹。

    模型未配置时降级为规则应答，明确告知用户能力受限，而不是假装能用。
    """
    register_all_skills()
    if not settings.llm_enabled:
        return {
            "reply": "当前未配置模型网关，智能体对话不可用。\n\n"
                     "平台的其他功能（简历筛选、面试出题、培训考核）仍可正常使用，"
                     "但会走降级路径并在界面标注。\n\n"
                     "如需启用智能体，请在 backend/.env 中配置模型网关地址与密钥后重启服务。",
            "steps": [], "degraded": True, "pending_actions": [],
        }

    msgs: list[Any] = [SystemMessage(content=AGENT_SYSTEM)]
    for h in (history or [])[-8:]:
        if h.get("role") == "user":
            msgs.append(HumanMessage(content=h.get("content", "")))
        elif h.get("role") == "assistant":
            msgs.append(AIMessage(content=h.get("content", "")))
    msgs.append(HumanMessage(content=message))

    try:
        agent = get_chat_agent()
        result = await agent.ainvoke({"messages": msgs})
    except Exception as e:  # noqa: BLE001
        return {
            "reply": f"智能体执行失败：{type(e).__name__}。\n\n"
                     f"你可以直接使用左侧的各个功能页完成对应任务。",
            "steps": [], "degraded": True, "pending_actions": [],
            "error": str(e)[:300],
        }

    out_msgs = result.get("messages", [])
    steps: list[str] = []
    pending: list[dict[str, Any]] = []

    for m in out_msgs:
        calls = getattr(m, "tool_calls", None) or []
        for c in calls:
            name = c.get("name", "")
            args = c.get("args", {})
            label = SKILL_REGISTRY.get(name).label if name in SKILL_REGISTRY else name
            steps.append(f"调用技能：{label}")
            if name in HUMAN_GATE_SKILLS:
                pending.append({"skill": name, "label": label, "args": args,
                                "note": "该操作会改变候选人状态，需人工确认后生效"})

    reply = ""
    for m in reversed(out_msgs):
        if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None):
            reply = m.content if isinstance(m.content, str) else str(m.content)
            break

    return {
        "reply": reply or "（智能体未返回内容）",
        "steps": steps,
        "pending_actions": pending,
        "degraded": False,
    }


# ---------------- 固定流水线：简历筛选 ----------------

class ScreenState(TypedDict):
    candidate_id: int
    resume_text: str
    parsed: dict
    meta_parse: dict
    score: dict
    meta_score: dict
    tier: str
    logs: Annotated[list[str], operator.add]


async def _node_parse(state: ScreenState) -> dict:
    from app.services.resume_service import parse_resume
    parsed, meta = await parse_resume(state["resume_text"], candidate_id=state["candidate_id"])
    return {
        "parsed": parsed.model_dump(),
        "meta_parse": meta,
        "logs": [f"解析完成（{'模型' if meta.get('path') == 'model' else '关键词兜底'}）"],
    }


async def _node_score(state: ScreenState) -> dict:
    from app.services.scoring_service import ability_to_dict, score_candidate
    import json as _json
    from sqlalchemy import select

    from app.db.models import AbilityItem, AbilityModel, ConfigItem, Position
    from app.db.session import SessionLocal

    async with SessionLocal() as db:
        cand = None
        from app.db.models import Candidate
        cand = (await db.execute(select(Candidate).where(
            Candidate.id == state["candidate_id"]))).scalar_one_or_none()
        if not cand:
            return {"score": {}, "meta_score": {"error": "候选人不存在"}, "logs": ["打分失败"]}

        pos = (await db.execute(select(Position).where(
            Position.id == cand.position_id))).scalar_one_or_none()
        model = (await db.execute(select(AbilityModel).where(
            AbilityModel.position_id == cand.position_id,
            AbilityModel.active == True))).scalars().first()  # noqa: E712
        items = []
        if model:
            rows = (await db.execute(select(AbilityItem).where(
                AbilityItem.model_id == model.id).order_by(AbilityItem.sort))).scalars().all()
            items = [ability_to_dict(r) for r in rows]

        cfg = {c.key: c.value.get("v") for c in
               (await db.execute(select(ConfigItem))).scalars().all()}

    if not model or not items:
        return {"score": {}, "meta_score": {"error": "岗位未绑定启用的能力模型"},
                "logs": ["未找到启用的能力模型，打分跳过"]}

    res = await score_candidate(
        candidate_id=state["candidate_id"], raw_text=state["resume_text"],
        parsed=state["parsed"], position_name=pos.name if pos else "",
        items=items, model_version=model.version,
        high_conf=float(cfg.get("threshold.high_confidence", 0.85)),
        high_score=int(cfg.get("threshold.high_score", 75)),
        mid_score=int(cfg.get("threshold.mid_score", 45)),
    )

    from app.services.scoring_service import decide_tier
    # 证据体在 res.evidence 里，不在 res.result 里。必须显式合并进 sc，
    # 否则下游落库时取 sc["evidence"] 永远是空的，界面会显示「有得分但无证据」，
    # 这违反「无证据不给分」的产品硬约束。
    sc = res.result.model_dump() if res.result else {}
    if sc:
        sc["evidence"] = [e.model_dump() for e in res.evidence]
    tier = decide_tier(
        sc.get("total", 0), sc.get("confidence", 0), sc.get("veto_hit", False),
        float(cfg.get("threshold.high_confidence", 0.85)),
        int(cfg.get("threshold.high_score", 75)),
        int(cfg.get("threshold.mid_score", 45)),
    ) if sc else ""

    return {
        "score": sc, "tier": tier,
        "meta_score": res.meta.model_dump() | {"ok": res.ok, "error": res.error},
        "logs": [f"打分完成：总分 {sc.get('total', 0)}，分档 {tier}，"
                 f"证据 {len(sc.get('evidence', []))} 条"],
    }


def _route_after_score(state: ScreenState) -> str:
    if not state.get("score") or state.get("meta_score", {}).get("error"):
        return "human"
    return "save"


async def _node_save(state: ScreenState) -> dict:
    from sqlalchemy import select

    from app.db.models import Candidate, DecisionLog, ResumeText, ScoreRecord
    from app.db.session import SessionLocal
    from app.services.scoring_service import derive_risk_tags

    async with SessionLocal() as db:
        cand = (await db.execute(select(Candidate).where(
            Candidate.id == state["candidate_id"]))).scalar_one_or_none()
        if not cand:
            return {"logs": ["候选人不存在"]}

        rt = (await db.execute(select(ResumeText).where(
            ResumeText.candidate_id == cand.id))).scalar_one_or_none()
        if rt is None:
            rt = ResumeText(candidate_id=cand.id)
            db.add(rt)
        rt.raw_text = state["resume_text"]
        rt.parsed = state["parsed"]
        rt.parse_meta = state["meta_parse"]
        rt.low_confidence_fields = state["meta_parse"].get("low_confidence_fields", [])

        sc = state["score"]
        db.add(ScoreRecord(
            candidate_id=cand.id, model_version=state["meta_score"].get("prompt_version", "v1"),
            total=sc.get("total", 0), confidence=sc.get("confidence", 0),
            items=sc.get("ability_scores", []), evidence=sc.get("evidence", []),
            risk_tags=[t for t in sc.get("risk_tags", [])],
            veto_hit=sc.get("veto_hit", False), veto_reason=sc.get("veto_reason", ""),
            summary=sc.get("summary", ""), ai_meta=state["meta_score"],
        ))

        cand.total_score = sc.get("total", 0)
        cand.confidence = sc.get("confidence", 0)
        cand.model_version = state["meta_score"].get("prompt_version", "v1")
        cand.risk_tags = sc.get("risk_tags", [])
        cand.tier = state["tier"]
        cand.status = {"高分档": "待安排面试", "中间档": "待复核",
                       "低分档": "待定池"}.get(state["tier"], "待复核")
        if cand.status == "待定池":
            from app.db.models import now as _now
            cand.pool_enter_at = _now()
        db.add(DecisionLog(
            kind="ai", actor="system", ability="简历筛选流水线",
            candidate_id=cand.id, summary=f"自动解析打分完成，分档 {state['tier']}",
            detail={"tier": state["tier"], "total": sc.get("total", 0)},
            model=state["meta_score"].get("model", ""),
            prompt_version=state["meta_score"].get("prompt_version", ""),
            confidence=sc.get("confidence", 0),
            latency_ms=state["meta_score"].get("latency_ms", 0),
            degrade=state["meta_score"].get("degrade", "none"),
        ))
        await db.commit()
    return {"logs": [f"已落库，状态更新为 {state['tier']}"]}


async def _node_human(state: ScreenState) -> dict:
    """自动流程失败时的兜底。转人工处理并写明原因。

    注意这里置为「解析异常」而不是「待复核」：没有打分记录的候选人如果标成待复核，
    界面看起来像是可以复核的，但 HR 点进去会发现没有任何判断依据。
    「解析异常」在状态机里有明确的可执行动作（补录 / 重新上传），语义才对。
    """
    from sqlalchemy import select

    from app.db.models import Candidate, DecisionLog, ResumeText
    from app.db.session import SessionLocal

    err = (state.get("meta_score") or {}).get("error", "") or "自动打分未产出结果"
    async with SessionLocal() as db:
        cand = (await db.execute(select(Candidate).where(
            Candidate.id == state["candidate_id"]))).scalar_one_or_none()
        if cand:
            # 已有打分记录的（例如后置校验不通过但仍落了分）留在待复核，
            # 完全没有依据的才转解析异常
            has_score = bool((state.get("score") or {}).get("ability_scores"))
            cand.status = "待复核" if has_score else "解析异常"
            rt = (await db.execute(select(ResumeText).where(
                ResumeText.candidate_id == cand.id))).scalar_one_or_none()
            if rt and not rt.parse_error:
                rt.parse_error = f"自动流程未完成：{err[:200]}"
            db.add(DecisionLog(
                kind="ai", actor="system", ability="简历筛选流水线",
                candidate_id=cand.id,
                summary=f"自动流程未完成，已转人工（{cand.status}）：{err[:120]}",
                degrade="human", degrade_reason=err[:200],
            ))
            await db.commit()
    return {"logs": [f"自动流程未完成，已转人工处理：{err[:80]}"]}


def build_screen_graph():
    g = StateGraph(ScreenState)
    g.add_node("parse", _node_parse)
    g.add_node("score", _node_score)
    g.add_node("save", _node_save)
    g.add_node("human", _node_human)
    g.set_entry_point("parse")
    g.add_edge("parse", "score")
    g.add_conditional_edges("score", _route_after_score, {"save": "save", "human": "human"})
    g.add_edge("save", END)
    g.add_edge("human", END)
    return g.compile()


_screen_graph = None


def get_screen_graph():
    global _screen_graph
    if _screen_graph is None:
        _screen_graph = build_screen_graph()
    return _screen_graph


async def run_screen_pipeline(candidate_id: int, resume_text: str) -> dict[str, Any]:
    """跑一次完整的「解析 -> 打分 -> 落库」流水线。"""
    graph = get_screen_graph()
    out = await graph.ainvoke({"candidate_id": candidate_id, "resume_text": resume_text,
                               "logs": []})
    return {
        "candidate_id": candidate_id,
        "tier": out.get("tier", ""),
        "score": out.get("score", {}),
        "logs": out.get("logs", []),
        "meta_parse": out.get("meta_parse", {}),
        "meta_score": out.get("meta_score", {}),
    }
