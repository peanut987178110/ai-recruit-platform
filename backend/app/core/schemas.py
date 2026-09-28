"""AI 输出契约（PRD 4.2 节）。

所有模型能力统一返回三段结构：结果体 / 证据体 / 元信息。
研发按此结构做强校验，字段缺失即视为调用失败并走降级，不允许把不完整结果渲染给用户。
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")

# ---------------- 枚举：取值不在约定集合内即判为失败 ----------------


class AbilityState(str, Enum):
    """能力项四态。未体现与不符合必须区分（PRD 3.2.3）。

    未体现 = 简历里没提到，属信息缺失，应面试验证；
    不符合 = 简历里写了但明显不达标，属能力不足。
    两者混为一谈会把表述简略的候选人误判为能力差。
    """

    HIT = "hit"                    # 命中
    PARTIAL = "partial"            # 部分命中
    ABSENT = "absent"              # 未体现（不给分，且不计入分母）
    MISMATCH = "mismatch"          # 不符合


class EvidenceType(str, Enum):
    PROJECT = "project"            # 项目经历
    TENURE = "tenure"              # 任职履历
    SKILL = "skill"                # 技能标签
    METRIC = "metric"              # 量化成果
    EDUCATION = "education"        # 教育背景


class DegradeLevel(str, Enum):
    """降级三级（PRD 4.3）。禁止静默降级，任何降级都要在界面明示。"""

    NONE = "none"
    RETRY = "retry"                # 一级：网络或超时重试，最多 2 次
    SWITCH_MODEL = "switch_model"  # 二级：切备用模型并标记
    HUMAN = "human"                # 三级：转人工


class RiskTag(str, Enum):
    """风险提示只陈述客观事实，不做主观归因（PRD 3.2.3）。

    系统输出「近三年任职 4 家公司」，不输出「稳定性差」。
    """

    GAP_6M = "履历断档超 6 个月"
    AVG_TENURE_1Y = "平均在职不足 1 年"
    LEVEL_JUMP = "职级跨越异常"
    TITLE_INFLATION = "职位名称与职责不匹配"
    FREQUENT_JOB = "近三年任职 4 家公司及以上"


# ---------------- 三段结构 ----------------


class Evidence(BaseModel):
    """证据体：含简历片段原文、字段来源、字符起止位置，供前端高亮定位。"""

    quote: str = Field(description="简历原文片段")
    field_source: str = Field(default="", description="字段来源，如「工作经历[0].职责描述」")
    start: int = Field(default=-1, description="字符起止位置，前端据此高亮")
    end: int = Field(default=-1)
    ability_id: str = ""
    ability_name: str = ""
    weight: int = 0

    @property
    def located(self) -> bool:
        return self.start >= 0 and self.end > self.start


class MetaInfo(BaseModel):
    """元信息：模型版本、提示词版本、置信度、耗时，用于线上归因与回归比对。

    PRD 4.2：版本号必须可追溯到提示词库的具体版本，供回归对比。
    """

    model: str = ""
    model_version: str = ""
    prompt_id: str = ""
    prompt_version: str = ""
    confidence: float = 0.0
    latency_ms: int = 0
    degrade: DegradeLevel = DegradeLevel.NONE
    degrade_reason: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cny: float = 0.0
    mocked: bool = False
    created_at: float = Field(default_factory=time.time)


class AIResult(BaseModel, Generic[T]):
    """统一返回结构：结果体 + 证据体 + 元信息。"""

    ok: bool = True
    ability: str = ""
    result: T | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    meta: MetaInfo = Field(default_factory=MetaInfo)
    error: str = ""

    @classmethod
    def failure(cls, ability: str, error: str, meta: MetaInfo | None = None) -> "AIResult[T]":
        return cls(ok=False, ability=ability, error=error, meta=meta or MetaInfo())


# ---------------- 各能力的业务结果体 ----------------


class ParsedField(BaseModel):
    """简历解析产出的单个结构化字段。"""

    name: str
    value: str = ""
    must: bool = True
    confidence: float = 1.0
    low_confidence: bool = False   # 低置信字段黄底高亮，需人工确认后才可打分
    confirmed: bool = True


class WorkItem(BaseModel):
    company: str = ""
    title: str = ""
    start: str = ""
    end: str = ""
    duty: str = ""


class ProjectItem(BaseModel):
    name: str = ""
    role: str = ""
    period: str = ""
    content: str = ""


class ResumeParseResult(BaseModel):
    """R-02 简历解析结果体。院校名称脱敏后单独存储，不参与打分（PRD 3.2.2）。"""

    name: str = ""
    phone: str = ""
    email: str = ""
    works: list[WorkItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    education_level: str = ""      # 仅学历层次与专业，参与硬性门槛判断
    education_major: str = ""
    school_masked: str = ""        # 院校名称脱敏后单独存储，不进打分链路
    intention: str = ""
    raw_text: str = ""
    injection_hits: list[str] = Field(default_factory=list)


class AbilityScore(BaseModel):
    """R-03 单项能力打分。"""

    ability_id: str
    ability_name: str
    weight: int = 1
    state: AbilityState = AbilityState.ABSENT
    score: float = 0.0             # 0-10，仅命中与部分命中给分
    reason: str = ""
    veto_triggered: bool = False
    veto_polarity: str = "positive"  # positive 必须具备 / negative 必须没有
    is_gate: bool = False            # 负面否决项为纯门禁：不参与加权，只会归零


class MatchScoreResult(BaseModel):
    """R-03 匹配打分结果体。"""

    total: float = 0.0
    ability_scores: list[AbilityScore] = Field(default_factory=list)
    risk_tags: list[RiskTag] = Field(default_factory=list)
    confidence: float = 0.0
    veto_hit: bool = False
    veto_reason: str = ""
    summary: str = ""
    # 证据同时挂在结果体上，避免调用方只取 result 时丢掉证据链。
    # 与 AIResult.evidence 是同一份数据，两者都要有。
    evidence: list[dict] = Field(default_factory=list)

    @field_validator("total")
    @classmethod
    def _clamp_total(cls, v: float) -> float:
        return max(0.0, min(100.0, round(v, 1)))


class InterviewQuestion(BaseModel):
    """R-09/R-10 面试题。"""

    layer: str = ""                # 基础考察 / 项目深挖 / 压力追问 / 真实性验证
    content: str = ""
    ability_id: str = ""
    ability_name: str = ""
    basis: str = ""                # 出题依据，引用简历原文片段或标注该能力项未体现
    anchors: dict[str, str] = Field(default_factory=dict)  # 高中低三档答案特征描述
    duration_min: int = 3
    probe: str = ""                # 追问建议
    sensitive_blocked: bool = False


class QuestionGenResult(BaseModel):
    questions: list[InterviewQuestion] = Field(default_factory=list)
    plan_minutes: int = 30


class QAItem(BaseModel):
    question: str = ""
    answer_summary: str = ""
    anchor_match: str = ""


class AbilityQA(BaseModel):
    ability_id: str = ""
    ability_name: str = ""
    qa: list[QAItem] = Field(default_factory=list)


class InterviewReportResult(BaseModel):
    """R-14 面试评估报告。AI 不预填面试官评分（PRD 3.3.3）。"""

    basic: dict[str, Any] = Field(default_factory=dict)
    by_ability: list[AbilityQA] = Field(default_factory=list)
    uncovered_abilities: list[str] = Field(default_factory=list)
    transcript: str = ""
    suggestions: list[str] = Field(default_factory=list)  # 明确标注为参考


class TrainingOutlineItem(BaseModel):
    chapter: str = ""
    ability_id: str = ""
    ability_name: str = ""
    hours: float = 1.0
    material_ref: str = ""         # 指向内部文档原文位置，不复制正文


class TrainingPlanResult(BaseModel):
    outline: list[TrainingOutlineItem] = Field(default_factory=list)
    draft: bool = True             # 一律标记为草稿，须带教人确认后才可发布


class ExamQuestion(BaseModel):
    qtype: str = ""                # 单选 / 多选 / 情景判断 / 案例分析
    stem: str = ""
    options: list[str] = Field(default_factory=list)
    answer: str = ""
    explanation: str = ""
    difficulty: str = "基础"       # 基础 / 进阶 / 综合
    ability_id: str = ""
    ability_name: str = ""
    ref: str = ""                  # 知识库出处，无出处则标记待人工补充


class ExamResult(BaseModel):
    questions: list[ExamQuestion] = Field(default_factory=list)
    draft: bool = True


class JudgeItem(BaseModel):
    question_id: str = ""
    qtype: str = ""
    score: float = 0.0
    full_score: float = 10.0
    method: str = ""               # 规则自动判 / AI 评分加人工确认 / AI 建议加人工终评
    hit_points: list[str] = Field(default_factory=list)
    miss_points: list[str] = Field(default_factory=list)
    comment: str = ""


class JudgeResult(BaseModel):
    """R-11 判卷结果。主观题 AI 不给终评分数，只给要点覆盖分析。"""

    items: list[JudgeItem] = Field(default_factory=list)
    objective_score: float = 0.0
    objective_full: float = 0.0
    radar: dict[str, float] = Field(default_factory=dict)
    need_human_final: bool = False


class JDAnalysisResult(BaseModel):
    must_have: list[str] = Field(default_factory=list)
    nice_have: list[str] = Field(default_factory=list)
    ability_candidates: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
