"""Agent 技能（Skill）定义。

技能 = 一个可被 Agent 调用、也可被 REST 接口直接调用的能力单元。
每个技能自带：名称、描述（给模型看的路由依据）、参数 schema、执行函数。

这种设计的好处是同一份能力有两个入口：
- 人机交互：前端点按钮 -> REST 接口 -> 直接执行技能
- 智能体：用户在对话框里说一句话 -> Agent 自己判断该调哪个技能

技能内部再调 LLM 完成子任务，因此技能是「能力编排层」，不是「模型包装层」。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


@dataclass
class SkillResult:
    ok: bool
    summary: str
    data: dict[str, Any]
    meta: dict[str, Any]
    error: str = ""


@dataclass
class Skill:
    name: str
    label: str
    description: str
    args_schema: type[BaseModel]
    handler: Callable[..., Awaitable[SkillResult]]
    category: str = "通用"
    needs_human: bool = True       # 是否保留人工确认环节

    def as_tool(self) -> StructuredTool:
        async def _run(**kwargs: Any) -> str:
            res = await self.handler(**kwargs)
            import json
            return json.dumps({
                "ok": res.ok, "summary": res.summary,
                "data": res.data, "error": res.error,
            }, ensure_ascii=False)[:6000]

        return StructuredTool.from_function(
            coroutine=_run,
            name=self.name,
            description=self.description,
            args_schema=self.args_schema,
        )


# ---------------- 各技能的参数 schema ----------------

class ScreenResumeArgs(BaseModel):
    candidate_id: int = Field(description="候选人 ID")
    refresh: bool = Field(default=False, description="是否重新解析打分，忽略已有结果")


class GenerateQuestionsArgs(BaseModel):
    candidate_id: int = Field(description="候选人 ID")
    plan_minutes: int = Field(default=30, description="面试时长预算，30 或 60 分钟")


class AnalyzeJDArgs(BaseModel):
    jd_text: str = Field(description="职位描述原文")
    seq: str = Field(default="技术", description="岗位序列：技术/产品/运营/职能")


class GenerateTrainingArgs(BaseModel):
    position_id: int = Field(description="岗位 ID")
    with_exam: bool = Field(default=True, description="是否同时生成考核题库")


class JudgeExamArgs(BaseModel):
    plan_id: int = Field(description="培训方案 ID")
    trainee_name: str = Field(description="新人姓名")
    answers: dict[str, str] = Field(default_factory=dict,
                                    description="题号到作答内容的映射")


class BuildModelArgs(BaseModel):
    position_name: str = Field(description="岗位名称")
    seq: str = Field(default="技术", description="岗位序列")
    business_line: str = Field(default="通用", description="所属业务线")
    from_jd: str = Field(default="", description="可选的 JD 原文，用于提炼能力项")


class QueryDataArgs(BaseModel):
    question: str = Field(description="关于招聘数据的问题，例如「各岗位分层占比如何」")


class KnowledgeSearchArgs(BaseModel):
    query: str = Field(description="检索关键词，例如「订单幂等设计规范」")
    top_k: int = Field(default=5, description="返回条数")


class ExplainDecisionArgs(BaseModel):
    candidate_id: int = Field(description="候选人 ID")


class PoolRescueArgs(BaseModel):
    candidate_id: int = Field(description="待定池中的候选人 ID")
    reason: str = Field(default="", description="捞回原因")


SKILL_REGISTRY: dict[str, Skill] = {}


def register(target: type[Skill] | Skill) -> Skill:
    """注册技能。既可作为类装饰器（自动实例化），也可直接传入实例。"""
    skill = target() if isinstance(target, type) else target
    SKILL_REGISTRY[skill.name] = skill
    return skill


def get_tools(names: list[str] | None = None) -> list[StructuredTool]:
    src = SKILL_REGISTRY.values() if not names else [SKILL_REGISTRY[n] for n in names if n in SKILL_REGISTRY]
    return [s.as_tool() for s in src]


def describe_skills() -> list[dict[str, Any]]:
    return [{
        "name": s.name, "label": s.label, "description": s.description,
        "category": s.category, "needs_human": s.needs_human,
        "args": [{"name": k, "type": str(v.annotation), "desc": v.description}
                 for k, v in s.args_schema.model_fields.items()],
    } for s in SKILL_REGISTRY.values()]
