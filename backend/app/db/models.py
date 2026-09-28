"""数据库模型。

组织原则：
- 业务实体（能力模型 / 候选人 / 面试 / 培训）各自成表，通过 id 关联。
- 所有 AI 产出都附 meta 字段（模型版本、提示词版本、置信度、是否降级），
  这是 PRD 4.2「把版本号写进输出」的落库形式。
- 日志表只追加不修改，满足 PRD 5.5「日志本身不可编辑，仅可追加」。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> datetime:
    return datetime.now()


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


# ==================== 平台层：用户与权限 ====================


class User(Base, TimestampMixin):
    """角色与数据可见范围见 PRD 1.4。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    userid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32), index=True)
    # 招聘HR / HR负责人 / 用人经理 / 业务面试官 / 带教人 / 法务审计 / 系统管理员
    business_line: Mapped[str] = mapped_column(String(64), default="", index=True)
    department: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str] = mapped_column(String(128), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # ---------- 账号 ----------
    # 加盐哈希，永不存明文。空字符串表示该账号还不能登录（仅作为数据归属占位）。
    password_hash: Mapped[str] = mapped_column(String(256), default="")
    # 由谁创建。admin 初始账号为 "system"
    created_by: Mapped[str] = mapped_column(String(64), default="")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def can_login(self) -> bool:
        return bool(self.password_hash) and self.active

    @property
    def can_view_resume(self) -> bool:
        """系统管理员可配置参数但不可查看简历正文（PRD 1.4 与 5.4）。"""
        return self.role != "系统管理员"

    @property
    def mask_contact(self) -> bool:
        """法务审计全量只读且不含联系方式（PRD 1.4）。"""
        return self.role == "法务审计"

    @property
    def is_super(self) -> bool:
        """初始超管账号：可管理其它账号，不受「仅 HR 负责人可注册」限制。"""
        return self.userid == "admin"


# ==================== M1 岗位能力模型 ====================


class Position(Base, TimestampMixin):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[str] = mapped_column(String(32), default="技术")   # 技术/产品/运营/职能
    level_range: Mapped[str] = mapped_column(String(32), default="")
    business_line: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(16), default="在招")  # 在招/已关闭
    headcount: Mapped[int] = mapped_column(Integer, default=1)
    jd_text: Mapped[str] = mapped_column(Text, default="")

    models: Mapped[list["AbilityModel"]] = relationship(back_populates="position")


class AbilityModel(Base, TimestampMixin):
    """能力模型：某岗位录用标准的结构化表达（PRD 3.1.2）。"""

    __tablename__ = "ability_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    version: Mapped[str] = mapped_column(String(16), default="v1")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")

    position: Mapped[Position] = relationship(
        back_populates="models", lazy="selectin")
    items: Mapped[list["AbilityItem"]] = relationship(
        back_populates="model", cascade="all, delete-orphan",
        order_by="AbilityItem.sort", lazy="selectin",
    )

    @property
    def total_weight(self) -> int:
        return sum(i.weight for i in self.items)

    @property
    def veto_count(self) -> int:
        return sum(1 for i in self.items if i.is_veto)


class AbilityItem(Base, TimestampMixin):
    __tablename__ = "ability_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("ability_models.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    weight: Mapped[int] = mapped_column(Integer, default=1)       # 1-10 整数，系统换算为百分比
    evidence_types: Mapped[list] = mapped_column(JSON, default=list)
    # 项目经历/任职履历/技能标签/量化成果/教育背景
    criteria: Mapped[str] = mapped_column(Text, default="")        # 判定说明，200 字以内
    is_veto: Mapped[bool] = mapped_column(Boolean, default=False)  # 否决项上限 3 个
    # 否决项极性：positive = 必须具备（未命中即归零）；negative = 必须没有（命中即归零）。
    # 这个字段是必需的：像「简历真实性存疑」这类负面条款，如果按「未命中即归零」处理，
    # 会把简历真实的候选人全部打成 0 分，是非对称的严重错误。
    veto_polarity: Mapped[str] = mapped_column(String(16), default="positive")
    sort: Mapped[int] = mapped_column(Integer, default=0)

    model: Mapped[AbilityModel] = relationship(back_populates="items")


# ==================== M2 简历筛选 ====================


class Candidate(Base, TimestampMixin):
    """候选人主状态机（PRD 2.2）。每个状态的进入条件与可执行动作必须唯一。"""

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    hr_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)

    # 待解析 / 解析异常 / 待打分 / 待安排面试 / 待复核 / 待定池 / 已归档
    status: Mapped[str] = mapped_column(String(24), default="待解析", index=True)
    tier: Mapped[str] = mapped_column(String(16), default="", index=True)  # 高分档/中间档/低分档

    phone: Mapped[str] = mapped_column(String(32), default="")
    email: Mapped[str] = mapped_column(String(128), default="")
    source: Mapped[str] = mapped_column(String(32), default="ATS推送")      # ATS推送/手工导入
    file_path: Mapped[str] = mapped_column(String(512), default="")
    file_type: Mapped[str] = mapped_column(String(16), default="text")

    total_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    model_version: Mapped[str] = mapped_column(String(16), default="")     # 打分时的模型版本
    risk_tags: Mapped[list] = mapped_column(JSON, default=list)

    pool_enter_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pool_days: Mapped[int] = mapped_column(Integer, default=7)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    need_confirm: Mapped[bool] = mapped_column(Boolean, default=False)      # 低置信字段待确认
    injection_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    doubt_tag: Mapped[bool] = mapped_column(Boolean, default=False)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    resume: Mapped["ResumeText"] = relationship(
        back_populates="candidate", uselist=False, cascade="all, delete-orphan",
        lazy="selectin",
    )
    scores: Mapped[list["ScoreRecord"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", lazy="selectin",
    )
    position: Mapped["Position"] = relationship(lazy="selectin")

    @property
    def pool_days_left(self) -> int | None:
        if self.status != "待定池" or not self.pool_enter_at:
            return None
        elapsed = (now() - self.pool_enter_at).days
        return max(0, self.pool_days - elapsed)

    @property
    def pool_expire_at(self) -> datetime | None:
        if self.status != "待定池" or not self.pool_enter_at:
            return None
        return self.pool_enter_at + timedelta(days=self.pool_days)


class ResumeText(Base):
    """简历原文与结构化解析结果。院校名称脱敏后单独存储，不进打分链路。"""

    __tablename__ = "resume_texts"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    parsed: Mapped[dict] = mapped_column(JSON, default=dict)
    low_confidence_fields: Mapped[list] = mapped_column(JSON, default=list)
    parse_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    parse_error: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    candidate: Mapped[Candidate] = relationship(back_populates="resume")


class ScoreRecord(Base):
    """打分结果。历史打分记录保留当时使用的模型版本（验收 A1-4）。"""

    __tablename__ = "score_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    model_id: Mapped[int | None] = mapped_column(ForeignKey("ability_models.id"), nullable=True)
    model_version: Mapped[str] = mapped_column(String(16), default="v1")
    total: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    items: Mapped[list] = mapped_column(JSON, default=list)     # AbilityScore 列表
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    risk_tags: Mapped[list] = mapped_column(JSON, default=list)
    veto_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    veto_reason: Mapped[str] = mapped_column(String(256), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    ai_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)

    candidate: Mapped[Candidate] = relationship(back_populates="scores")


class ReviewAction(Base):
    """人工复核动作。否决原因写入回流样本池（验收 A2-8）。"""

    __tablename__ = "review_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(16), index=True)   # 采纳/否决/标记疑问/捞回/退回
    reason: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    model_version: Mapped[str] = mapped_column(String(16), default="")
    stay_seconds: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


# ==================== M3 面试助手 ====================


class InterviewSchedule(Base, TimestampMixin):
    __tablename__ = "interview_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    round_name: Mapped[str] = mapped_column(String(32), default="初面")
    interviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    plan_minutes: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(16), default="待面试")  # 待面试/已完成
    consent_recorded: Mapped[bool] = mapped_column(Boolean, default=False)  # 录音需候选人明示同意


class QuestionRecord(Base):
    """面试题与面试官操作。操作行为即采纳率的统计来源（PRD 3.3.2）。"""

    __tablename__ = "question_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("interview_schedules.id"), index=True)
    layer: Mapped[str] = mapped_column(String(32), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    ability_id: Mapped[str] = mapped_column(String(32), default="")
    ability_name: Mapped[str] = mapped_column(String(64), default="")
    basis: Mapped[str] = mapped_column(Text, default="")
    anchors: Mapped[dict] = mapped_column(JSON, default=dict)
    duration_min: Mapped[int] = mapped_column(Integer, default=3)
    probe: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(String(16), default="待定")   # 采纳/删除/编辑/待定
    edited_content: Mapped[str] = mapped_column(Text, default="")
    ai_meta: Mapped[dict] = mapped_column(JSON, default=dict)


class InterviewReport(Base):
    __tablename__ = "interview_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("interview_schedules.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    transcript: Mapped[str] = mapped_column(Text, default="")
    by_ability: Mapped[list] = mapped_column(JSON, default=list)
    uncovered: Mapped[list] = mapped_column(JSON, default=list)
    suggestions: Mapped[list] = mapped_column(JSON, default=list)
    # 面试官评分：1-5 分逐项，AI 不预填（验收 A3-6）
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    conclusion: Mapped[str] = mapped_column(Text, default="")
    human_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


# ==================== M4 培训考核 ====================


class TrainingPlan(Base, TimestampMixin):
    __tablename__ = "training_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    outline: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="草稿")  # 草稿/已发布
    mentor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    ai_meta: Mapped[dict] = mapped_column(JSON, default=dict)

    # ---------- 时间与考试规则（由发布者设定，服务端强制执行） ----------
    # 培训周期：开始前新人看得到大纲但不能开考；结束后不能再开考
    start_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 考试时长由方案决定，不再让考生自己填 —— 否则考生可以给自己加时间
    exam_minutes: Mapped[int] = mapped_column(Integer, default=60)
    pass_score: Mapped[int] = mapped_column(Integer, default=60)


class ExamQuestionRecord(Base):
    __tablename__ = "exam_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("training_plans.id"), index=True)
    qtype: Mapped[str] = mapped_column(String(16), default="单选")
    stem: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[list] = mapped_column(JSON, default=list)
    answer: Mapped[str] = mapped_column(Text, default="")
    explanation: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[str] = mapped_column(String(16), default="基础")
    ability_id: Mapped[str] = mapped_column(String(32), default="")
    ability_name: Mapped[str] = mapped_column(String(64), default="")
    ref: Mapped[str] = mapped_column(String(256), default="")
    need_manual_answer: Mapped[bool] = mapped_column(Boolean, default=False)


class ExamSubmission(Base):
    """新人答题与判卷。主观题 AI 只给要点覆盖分析，终评权在人手里（PRD 3.4.2）。"""

    __tablename__ = "exam_submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("training_plans.id"), index=True)
    trainee_name: Mapped[str] = mapped_column(String(64), default="")
    # 答题人账号。与 trainee_name 并存：前者是身份，后者是展示名
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    judge: Mapped[dict] = mapped_column(JSON, default=dict)
    objective_score: Mapped[float] = mapped_column(Float, default=0.0)
    objective_full: Mapped[float] = mapped_column(Float, default=0.0)
    radar: Mapped[dict] = mapped_column(JSON, default=dict)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)   # 由带教人给出
    mentor_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


# ==================== M5 数据回流与看板 ====================


class ReflowSample(Base):
    """回流样本池：HR 采纳/否决、捞回、试用期表现，用于权重复盘。"""

    __tablename__ = "reflow_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    # review / rescue / interview_score / exam / probation
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    model_version: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class TrackEvent(Base):
    """埋点事件（PRD 第 6 章）。只埋能驱动决策的事件。"""

    __tablename__ = "track_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event: Mapped[str] = mapped_column(String(48), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


# ==================== 平台层：配置、日志、知识库、提示词 ====================


class ConfigItem(Base, TimestampMixin):
    """阈值与参数（P-12）。调整即时生效不需发版，每次变更记录操作人与前后值。"""

    __tablename__ = "config_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    label: Mapped[str] = mapped_column(String(128), default="")
    group: Mapped[str] = mapped_column(String(32), default="threshold")
    description: Mapped[str] = mapped_column(Text, default="")


class DecisionLog(Base):
    """AI 决策日志（PRD 5.5）。只追加，不可编辑。"""

    __tablename__ = "decision_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), default="ai", index=True)
    # ai / human / config / access / injection
    actor: Mapped[str] = mapped_column(String(64), default="system")
    ability: Mapped[str] = mapped_column(String(48), default="", index=True)
    candidate_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str] = mapped_column(String(16), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    degrade: Mapped[str] = mapped_column(String(16), default="none")
    # 降级原因：审计时最常需要看的字段（哪一次调用降级了、为什么），单列存便于筛选
    degrade_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class KnowledgeDoc(Base, TimestampMixin):
    """知识库文档。只索引位置不复制正文，避免版本不一致（PRD 3.4.1）。"""

    __tablename__ = "knowledge_docs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(64), default="SOP", index=True)
    business_line: Mapped[str] = mapped_column(String(64), default="", index=True)
    source_path: Mapped[str] = mapped_column(String(512), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    chunks: Mapped[list] = mapped_column(JSON, default=list)
    owner: Mapped[str] = mapped_column(String(64), default="")


class PromptVersion(Base, TimestampMixin):
    """提示词库。独立于代码管理，按能力拆分，支持按能力粒度独立回滚（PRD 4.4）。"""

    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("prompt_id", "version", name="uq_prompt_ver"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(16), default="v1")
    ability: Mapped[str] = mapped_column(String(48), default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    user_template: Mapped[str] = mapped_column(Text, default="")
    tier: Mapped[str] = mapped_column(String(16), default="medium")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")

    # 灰度：同一能力可并行两个版本，按流量比例分配（PRD 4.4）
    canary_version: Mapped[str] = mapped_column(String(16), default="")
    canary_ratio: Mapped[float] = mapped_column(Float, default=0.0)


class MetricSnapshot(Base):
    """指标快照，供效果看板与归因。标注模型版本与阈值变更点（PRD 3.5.2）。"""

    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[str] = mapped_column(String(16), index=True)
    position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hr_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class SampleCase(Base):
    """试点/演示用的历史样本（模拟 ATS 历史投递记录）。"""

    __tablename__ = "sample_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_seq: Mapped[str] = mapped_column(String(32), default="技术")
    position_name: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(64), default="")
    resume_text: Mapped[str] = mapped_column(Text, default="")
    label: Mapped[str] = mapped_column(String(16), default="")   # pass / reject，用作一致率真值
    is_edge_case: Mapped[bool] = mapped_column(Boolean, default=False)


class ExamAttempt(Base, TimestampMixin):
    """一次考试作答（在场记录）。

    与 ExamSubmission 的区别：Attempt 在**开考那一刻**就创建，用来冻结试卷、
    记录计时与作弊信号；Submission 是交卷判卷后的结果。分开是为了让
    「正在考试」这个状态有地方存 —— 否则刷新页面就会丢掉计时与已选答案。
    """

    __tablename__ = "exam_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("training_plans.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # 进行中 / 已交卷 / 已过期 / 已作废（带教人判定作弊）
    status: Mapped[str] = mapped_column(String(16), default="进行中", index=True)

    # 本次试卷：从题库随机抽取并打乱顺序后的题目 id 列表
    paper: Mapped[list] = mapped_column(JSON, default=list)
    # 选项乱序映射：{题目id: [原选项索引的新顺序]}，防止照抄邻座答案
    option_order: Mapped[dict] = mapped_column(JSON, default=dict)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)

    # 作答进度：{题目id: 答案}，用于刷新后恢复
    draft_answers: Mapped[dict] = mapped_column(JSON, default=dict)

    # 反作弊信号：由前端上报，仅作线索，不作为判定依据
    cheat_signals: Mapped[list] = mapped_column(JSON, default=list)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_level: Mapped[str] = mapped_column(String(16), default="正常")  # 正常/关注/可疑
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    submission_id: Mapped[int | None] = mapped_column(
        ForeignKey("exam_submissions.id"), nullable=True)


class ExamAssignment(Base, TimestampMixin):
    """把培训方案指派给具体新人。

    新人只能看到指派给自己的考核 —— 否则所有在库试卷对所有人开放，
    既无法控制节奏，也谈不上「谁的考试」。
    """

    __tablename__ = "exam_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("training_plans.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 待开始 / 进行中 / 已完成 / 已通过 / 已过期
    status: Mapped[str] = mapped_column(String(16), default="待开始", index=True)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str] = mapped_column(Text, default="")


class TrainingMaterial(Base, TimestampMixin):
    """发布者上传的公司文档资料，作为 AI 出题的语料来源。

    与 KnowledgeDoc 的关系：KnowledgeDoc 是全局知识库（SOP、合规），跨岗位共用；
    TrainingMaterial 是**某一份培训方案专属**的资料，由发布者上传，生成大纲与
    题库时优先检索它。分开的好处是换一份资料生成新方案时不会污染全局知识库。
    """

    __tablename__ = "training_materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("training_plans.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(256), default="")
    file_type: Mapped[str] = mapped_column(String(16), default="text")
    content: Mapped[str] = mapped_column(Text, default="")
    chunks: Mapped[list] = mapped_column(JSON, default=list)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


def utc_ts() -> float:
    return time.time()
