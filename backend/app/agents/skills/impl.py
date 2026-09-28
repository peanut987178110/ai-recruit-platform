"""技能实现与注册。

每个技能既是 REST 接口的实现，也是 Agent 可调用的工具。
handler 统一返回 SkillResult，便于两个入口共用同一份返回结构。
"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from app.agents.skills.base import (
    AnalyzeJDArgs, BuildModelArgs, ExplainDecisionArgs, GenerateQuestionsArgs,
    GenerateTrainingArgs, JudgeExamArgs, KnowledgeSearchArgs, PoolRescueArgs,
    QueryDataArgs, ScreenResumeArgs, Skill, SkillResult, register,
)


class BuildReportArgs(BaseModel):
    schedule_id: int = Field(description="面试日程 ID")
    transcript: str = Field(description="面试录音转写后的逐字稿全文")
from app.core.schemas import AIResult
from app.llm.client import llm
from app.prompts.manager import prompts
from app.rag.retriever import kb


def _ok(summary: str, data: dict, meta: dict | None = None) -> SkillResult:
    return SkillResult(ok=True, summary=summary, data=data, meta=meta or {})


def _fail(summary: str, error: str, meta: dict | None = None) -> SkillResult:
    return SkillResult(ok=False, summary=summary, data={}, meta=meta or {}, error=error)


# ---------------- 简历筛选 ----------------


@register
class ScreenResumeSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="screen_resume",
            label="简历筛选",
            description="对指定候选人执行「解析 -> 打分 -> 分层」全流程，"
                        "返回总分、逐项得分、证据位置与风险提示。当用户问"
                        "「这个候选人怎么样」「帮我筛一下这份简历」时调用。",
            args_schema=ScreenResumeArgs,
            handler=self.run,
            category="M2 简历筛选",
        )

    async def run(self, candidate_id: int, refresh: bool = False) -> SkillResult:
        from sqlalchemy import select

        from app.agents.orchestrator import run_screen_pipeline
        from app.db.models import Candidate, ResumeText
        from app.db.session import SessionLocal

        async with SessionLocal() as db:
            cand = (await db.execute(select(Candidate).where(
                Candidate.id == candidate_id))).scalar_one_or_none()
            if not cand:
                return _fail("候选人不存在", f"未找到 id={candidate_id} 的候选人")
            rt = (await db.execute(select(ResumeText).where(
                ResumeText.candidate_id == candidate_id))).scalar_one_or_none()
            text = rt.raw_text if rt else ""
            name = cand.name

        if not text:
            return _fail("缺少简历原文", "该候选人没有可用的简历文本，请先上传简历")

        out = await run_screen_pipeline(candidate_id, text)
        sc = out.get("score") or {}
        if not sc:
            return _fail("自动流程未完成", out.get("meta_score", {}).get("error", "已转人工"))

        hits = [i for i in sc.get("ability_scores", []) if i.get("state") == "hit"]
        absent = [i for i in sc.get("ability_scores", []) if i.get("state") == "absent"]
        summary = (
            f"{name}：总分 {sc.get('total')}，分档 {out.get('tier')}，"
            f"置信度 {sc.get('confidence')}。命中 {len(hits)} 项，未体现 {len(absent)} 项。"
            + (f" 触发否决项：{sc.get('veto_reason')}" if sc.get("veto_hit") else "")
        )
        return _ok(summary, out, out.get("meta_score", {}))


# ---------------- 面试题生成 ----------------


@register
class GenQuestionsSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="generate_questions",
            label="生成面试题",
            description="为指定候选人生成分层面试题（基础考察/项目深挖/压力追问/真实性验证），"
                        "每题带评分锚点与出题依据。当用户问「这个候选人该问什么」"
                        "「帮我准备面试题」时调用。",
            args_schema=GenerateQuestionsArgs,
            handler=self.run,
            category="M3 面试助手",
        )

    async def run(self, candidate_id: int, plan_minutes: int = 30) -> SkillResult:
        from sqlalchemy import select

        from app.db.models import (
            AbilityItem, AbilityModel, Candidate, Position, ResumeText, ScoreRecord,
        )
        from app.db.session import SessionLocal
        from app.services.interview_service import generate_questions
        from app.services.scoring_service import ability_to_dict

        async with SessionLocal() as db:
            cand = (await db.execute(select(Candidate).where(
                Candidate.id == candidate_id))).scalar_one_or_none()
            if not cand:
                return _fail("候选人不存在", f"未找到 id={candidate_id}")
            pos = (await db.execute(select(Position).where(
                Position.id == cand.position_id))).scalar_one_or_none()
            model = (await db.execute(select(AbilityModel).where(
                AbilityModel.position_id == cand.position_id,
                AbilityModel.active == True))).scalars().first()  # noqa: E712
            if not model:
                return _fail("岗位未绑定能力模型", "请先在 M1 为该岗位配置并启用能力模型")
            items = [ability_to_dict(r) for r in (await db.execute(select(AbilityItem).where(
                AbilityItem.model_id == model.id).order_by(AbilityItem.sort))).scalars().all()]
            sr = (await db.execute(select(ScoreRecord).where(
                ScoreRecord.candidate_id == candidate_id
            ).order_by(ScoreRecord.id.desc()))).scalars().first()
            score_items = sr.items if sr else []
            rt = (await db.execute(select(ResumeText).where(
                ResumeText.candidate_id == candidate_id))).scalar_one_or_none()
            parsed = rt.parsed if rt else {}
            name = cand.name

        gen, meta, dropped = await generate_questions(
            candidate_id=candidate_id, position_name=pos.name if pos else "",
            items=items, score_items=score_items, parsed=parsed, plan_minutes=plan_minutes,
        )
        by_layer: dict[str, int] = {}
        for q in gen.questions:
            by_layer[q.layer] = by_layer.get(q.layer, 0) + 1
        summary = (f"为 {name} 生成 {len(gen.questions)} 道题："
                   + "、".join(f"{k} {v} 道" for k, v in by_layer.items())
                   + f"，总预计 {sum(q.duration_min for q in gen.questions)} 分钟")
        if dropped:
            summary += f"；拦截敏感话题题目 {len(dropped)} 道"
        return _ok(summary, {
            "questions": [q.model_dump() for q in gen.questions],
            "plan_minutes": gen.plan_minutes,
            "blocked": dropped,
        }, meta)


# ---------------- 面试报告 ----------------


@register
class BuildReportSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="build_interview_report",
            label="生成面试评估报告",
            description="根据面试录音转写文本生成结构化评估报告，按能力项归类问答。"
                        "注意：报告不预填面试官评分，评分由面试官本人录入。",
            args_schema=BuildReportArgs,
            handler=self.run,
            category="M3 面试助手",
        )

    async def run(self, schedule_id: int, transcript: str) -> SkillResult:
        from sqlalchemy import select

        from app.db.models import (
            AbilityItem, AbilityModel, Candidate, InterviewReport, InterviewSchedule,
            Position, QuestionRecord, User,
        )
        from app.db.session import SessionLocal
        from app.services.interview_service import build_report

        async with SessionLocal() as db:
            sch = (await db.execute(select(InterviewSchedule).where(
                InterviewSchedule.id == schedule_id))).scalar_one_or_none()
            if not sch:
                return _fail("面试日程不存在", f"未找到 id={schedule_id}")
            cand = (await db.execute(select(Candidate).where(
                Candidate.id == sch.candidate_id))).scalar_one_or_none()
            pos = (await db.execute(select(Position).where(
                Position.id == cand.position_id))).scalar_one_or_none() if cand else None
            itv = (await db.execute(select(User).where(
                User.id == sch.interviewer_id))).scalar_one_or_none() if sch.interviewer_id else None
            model = (await db.execute(select(AbilityModel).where(
                AbilityModel.position_id == cand.position_id,
                AbilityModel.active == True))).scalars().first() if cand else None  # noqa: E712
            items = [{"id": r.id, "name": r.name, "criteria": r.criteria}
                     for r in (await db.execute(select(AbilityItem).where(
                         AbilityItem.model_id == model.id))).scalars().all()] if model else []
            qs = [{"layer": r.layer, "content": r.content, "ability_id": r.ability_id,
                   "ability_name": r.ability_name}
                  for r in (await db.execute(select(QuestionRecord).where(
                      QuestionRecord.schedule_id == schedule_id))).scalars().all()]

        rep, meta = await build_report(
            schedule_id=schedule_id,
            candidate_name=cand.name if cand else "",
            position_name=pos.name if pos else "",
            interviewer=itv.name if itv else "",
            duration_min=sch.plan_minutes, round_name=sch.round_name,
            transcript=transcript, questions=qs, items=items,
        )

        async with SessionLocal() as db:
            row = InterviewReport(
                schedule_id=schedule_id, candidate_id=sch.candidate_id,
                transcript=transcript,
                by_ability=[a.model_dump() for a in rep.by_ability],
                uncovered=rep.uncovered_abilities, suggestions=rep.suggestions,
                ai_meta=meta,
            )
            db.add(row)
            await db.commit()
            rid = row.id

        covered = sum(len(a.qa) for a in rep.by_ability)
        return _ok(
            f"报告已生成：覆盖 {len(rep.by_ability)} 个能力项、{covered} 组问答；"
            f"未覆盖 {len(rep.uncovered_abilities)} 项，已列出供下一轮接续。"
            f"面试官评分需由本人录入。",
            {"report_id": rid, "report": rep.model_dump()}, meta,
        )


# ---------------- 培训与考核 ----------------


@register
class GenTrainingSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="generate_training",
            label="生成培训方案",
            description="为指定岗位生成培训大纲（可选同时生成考核题库）。"
                        "生成内容标记为草稿，须带教人确认后才可发布给新人。",
            args_schema=GenerateTrainingArgs,
            handler=self.run,
            category="M4 培训考核",
        )

    async def run(self, position_id: int, with_exam: bool = True) -> SkillResult:
        from sqlalchemy import select

        from app.db.models import AbilityItem, AbilityModel, Position, TrainingPlan
        from app.db.session import SessionLocal
        from app.services.training_service import gen_exam, gen_outline

        async with SessionLocal() as db:
            pos = (await db.execute(select(Position).where(
                Position.id == position_id))).scalar_one_or_none()
            if not pos:
                return _fail("岗位不存在", f"未找到 id={position_id}")
            model = (await db.execute(select(AbilityModel).where(
                AbilityModel.position_id == position_id,
                AbilityModel.active == True))).scalars().first()  # noqa: E712
            if not model:
                return _fail("岗位未绑定能力模型", "请先配置并启用能力模型")
            items = [{"id": r.id, "name": r.name, "criteria": r.criteria}
                     for r in (await db.execute(select(AbilityItem).where(
                         AbilityItem.model_id == model.id).order_by(AbilityItem.sort))).scalars().all()]
            pname, bl = pos.name, pos.business_line

        plan, meta = await gen_outline(position_name=pname, items=items, business_line=bl)
        data: dict = {"outline": [o.model_dump() for o in plan.outline], "draft": True}

        if with_exam:
            exam, emeta = await gen_exam(position_name=pname, items=items, business_line=bl)
            data["exam"] = [q.model_dump() for q in exam.questions]
            meta = {**meta, "exam_meta": emeta}

        async with SessionLocal() as db:
            row = TrainingPlan(
                position_id=position_id, title=f"{pname} 新人培训方案",
                outline=[o.model_dump() for o in plan.outline], status="草稿",
                ai_meta=meta,
            )
            db.add(row)
            await db.flush()
            if with_exam:
                from app.db.models import ExamQuestionRecord
                for q in data.get("exam", []):
                    db.add(ExamQuestionRecord(
                        plan_id=row.id, qtype=q["qtype"], stem=q["stem"],
                        options=q.get("options", []), answer=q.get("answer", ""),
                        explanation=q.get("explanation", ""), difficulty=q.get("difficulty", "基础"),
                        ability_id=q.get("ability_id", ""), ability_name=q.get("ability_name", ""),
                        ref=q.get("ref", ""), need_manual_answer=not bool(q.get("ref")),
                    ))
            await db.commit()
            data["plan_id"] = row.id

        n_exam = len(data.get("exam", []))
        return _ok(
            f"已生成培训大纲 {len(plan.outline)} 章" + (f"、考核题 {n_exam} 道" if with_exam else "")
            + "。内容为草稿状态，请带教人审核后再发布。",
            data, meta,
        )


@register
class JudgeExamSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="judge_exam",
            label="考核判卷",
            description="对新人提交的考核作答判卷。客观题按规则自动判，"
                        "主观题只输出要点覆盖分析，不给终评分数 —— 终评由带教人给出。",
            args_schema=JudgeExamArgs,
            handler=self.run,
            category="M4 培训考核",
        )

    async def run(self, plan_id: int, trainee_name: str,
                  answers: dict[str, str] | None = None) -> SkillResult:
        return await _judge_impl(plan_id, trainee_name, answers or {})


async def _judge_impl(plan_id: int, trainee_name: str, answers: dict[str, str]) -> SkillResult:
    from sqlalchemy import select

    from app.db.models import (
        AbilityItem, AbilityModel, ExamQuestionRecord, ExamSubmission, TrainingPlan,
    )
    from app.db.session import SessionLocal
    from app.services.training_service import build_radar, judge_objective, judge_subjective

    async with SessionLocal() as db:
        plan = (await db.execute(select(TrainingPlan).where(
            TrainingPlan.id == plan_id))).scalar_one_or_none()
        if not plan:
            return _fail("培训方案不存在", f"未找到 id={plan_id}")
        qs = (await db.execute(select(ExamQuestionRecord).where(
            ExamQuestionRecord.plan_id == plan_id))).scalars().all()
        model = (await db.execute(select(AbilityModel).where(
            AbilityModel.position_id == plan.position_id,
            AbilityModel.active == True))).scalars().first()  # noqa: E712
        items = [{"id": r.id, "name": r.name}
                 for r in (await db.execute(select(AbilityItem).where(
                     AbilityItem.model_id == model.id))).scalars().all()] if model else []

    judged: list = []
    qmap: dict[str, dict] = {}
    subjective_pairs: list[dict] = []

    for q in qs:
        ans = answers.get(str(q.id), "")
        qmap[str(q.id)] = {"ability_id": q.ability_id, "ability_name": q.ability_name,
                           "qtype": q.qtype}
        if q.qtype in ("单选", "多选"):
            judged.append(judge_objective(q, ans))
        else:
            subjective_pairs.append({
                "id": str(q.id), "qtype": q.qtype, "stem": q.stem,
                "ref_answer": q.answer, "explanation": q.explanation,
                "answer": ans or "（未作答）",
            })

    sub_meta: dict = {}
    if subjective_pairs:
        subs, sub_meta = await judge_subjective(qa_pairs=subjective_pairs)
        judged.extend(subs)

    obj_score = sum(j.score for j in judged if j.qtype in ("单选", "多选"))
    obj_full = sum(j.full_score for j in judged if j.qtype in ("单选", "多选"))
    radar = build_radar(items, judged, qmap)

    async with SessionLocal() as db:
        row = ExamSubmission(
            plan_id=plan_id, trainee_name=trainee_name, answers=answers,
            judge={"items": [j.model_dump() for j in judged]},
            objective_score=obj_score, objective_full=obj_full, radar=radar,
            ai_meta=sub_meta,
        )
        db.add(row)
        await db.commit()
        sid = row.id

    return _ok(
        f"判卷完成：客观题 {obj_score:.0f}/{obj_full:.0f} 分。"
        f"主观题已给出要点覆盖分析，**终评分数需带教人给出**。",
        {
            "submission_id": sid,
            "items": [j.model_dump() for j in judged],
            "objective_score": obj_score, "objective_full": obj_full,
            "radar": radar, "need_human_final": True,
        },
        sub_meta,
    )


# ---------------- 能力模型 ----------------


@register
class AnalyzeJDSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="analyze_jd",
            label="JD 分析",
            description="从职位描述中提炼能力项候选池，区分硬性要求与加分项，"
                        "并给出建模建议。当用户说「照这个 JD 建个模型」「帮我看看这份 JD」时调用。",
            args_schema=AnalyzeJDArgs,
            handler=self.run,
            category="M1 能力模型",
        )

    async def run(self, jd_text: str, seq: str = "技术") -> SkillResult:
        from app.core.schemas import JDAnalysisResult

        p = await prompts.resolve("JD 分析", {"jd_text": jd_text[:6000], "seq": seq},
                                  route_key=jd_text[:60])
        res = await llm.complete_json(
            ability="JD 分析", prompt_id=p.prompt_id, prompt_version=p.version,
            system=p.system_prompt, user=p.user_prompt,
            schema=JDAnalysisResult, tier=p.tier, max_tokens=2500,
        )
        if not res.ok or not res.result:
            return _fail("JD 分析失败", res.error or "模型返回异常", res.meta.model_dump())
        r = res.result
        return _ok(
            f"提炼出 {len(r.ability_candidates)} 个能力项候选，"
            f"硬性要求 {len(r.must_have)} 条。",
            r.model_dump(), res.meta.model_dump(),
        )


@register
class BuildModelSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="build_ability_model",
            label="生成能力模型",
            description="根据岗位名称与 JD 自动生成能力模型草稿（能力项+权重+证据类型+判定说明）。"
                        "生成的模型为草稿，需 HR 在编辑器里调整权重后才能启用。",
            args_schema=BuildModelArgs,
            handler=self.run,
            category="M1 能力模型",
            needs_human=True,
        )

    async def run(self, position_name: str, seq: str = "技术",
                  business_line: str = "通用", from_jd: str = "") -> SkillResult:
        from app.core.schemas import JDAnalysisResult

        src = from_jd or f"岗位：{position_name}，序列：{seq}，业务线：{business_line}。请依据该岗位的通行要求提炼能力项。"
        p = await prompts.resolve("JD 分析", {"jd_text": src[:6000], "seq": seq},
                                  route_key=position_name)
        res = await llm.complete_json(
            ability="JD 分析", prompt_id=p.prompt_id, prompt_version=p.version,
            system=p.system_prompt, user=p.user_prompt,
            schema=JDAnalysisResult, tier=p.tier, max_tokens=2500,
        )
        if not res.ok or not res.result:
            return _fail("模型生成失败", res.error or "模型返回异常", res.meta.model_dump())

        cands = res.result.ability_candidates[:9]
        n = len(cands) or 1
        base, rem = divmod(10, n)
        items = []
        for i, name in enumerate(cands):
            w = base + (1 if i < rem else 0)
            items.append({
                "name": name[:30], "weight": max(1, min(10, w)),
                "evidence_types": ["project", "tenure"],
                "criteria": "需结合简历原文与实际面试表现判断，关注具体事例而非自我评价。",
                "is_veto": i == len(cands) - 1 and n >= 5,
            })
        return _ok(
            f"已生成 {len(items)} 个能力项草稿，权重总和 {sum(i['weight'] for i in items)}。"
            f"请在能力模型编辑器中调整权重至总和 100% 后再启用。",
            {"items": items, "suggestions": res.result.suggestions,
             "must_have": res.result.must_have},
            res.meta.model_dump(),
        )


# ---------------- 数据查询与解释 ----------------


@register
class QueryDataSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="query_recruit_data",
            label="查询招聘数据",
            description="查询平台内的招聘数据统计，例如各岗位分层占比、待办数量、"
                        "待定池情况、成本与延迟等。当用户问「现在有多少待复核」"
                        "「分层占比怎么样」这类问题时调用。",
            args_schema=QueryDataArgs,
            handler=self.run,
            category="M5 数据回流",
        )

    async def run(self, question: str) -> SkillResult:
        from app.services.analytics_service import snapshot_for_agent

        data = await snapshot_for_agent()
        return _ok("已获取平台当前数据概览。", {"question": question, **data})


@register
class ExplainDecisionSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="explain_decision",
            label="解释 AI 结论",
            description="解释某位候选人的 AI 打分结论：为什么是这个分、证据在哪里、"
                        "哪些能力项未体现、使用了哪个模型与提示词版本。"
                        "当用户问「为什么给他这个分」「这个结论怎么来的」时调用。",
            args_schema=ExplainDecisionArgs,
            handler=self.run,
            category="平台层",
        )

    async def run(self, candidate_id: int) -> SkillResult:
        from sqlalchemy import select

        from app.db.models import Candidate, DecisionLog, ResumeText, ScoreRecord
        from app.db.session import SessionLocal

        async with SessionLocal() as db:
            cand = (await db.execute(select(Candidate).where(
                Candidate.id == candidate_id))).scalar_one_or_none()
            if not cand:
                return _fail("候选人不存在", f"未找到 id={candidate_id}")
            sr = (await db.execute(select(ScoreRecord).where(
                ScoreRecord.candidate_id == candidate_id
            ).order_by(ScoreRecord.id.desc()))).scalars().first()
            logs = (await db.execute(select(DecisionLog).where(
                DecisionLog.candidate_id == candidate_id
            ).order_by(DecisionLog.id.desc()).limit(10))).scalars().all()

        if not sr:
            return _fail("暂无打分记录", "该候选人尚未完成自动打分")

        lines = []
        for it in sr.items:
            cn = {"hit": "命中", "partial": "部分命中",
                  "absent": "未体现", "mismatch": "不符合"}.get(it.get("state"), "")
            lines.append({
                "能力项": it.get("ability_name"), "状态": cn,
                "得分": it.get("score"), "权重": it.get("weight"),
                "判断依据": it.get("reason"),
                "证据数": sum(1 for e in sr.evidence if str(e.get("ability_id")) == str(it.get("ability_id"))),
            })
        return _ok(
            f"总分 {sr.total}，置信度 {sr.confidence}，使用模型版本 {sr.model_version}。",
            {
                "candidate": cand.name, "total": sr.total, "confidence": sr.confidence,
                "items": lines, "veto_hit": sr.veto_hit, "veto_reason": sr.veto_reason,
                "risk_tags": sr.risk_tags, "summary": sr.summary,
                "evidence": sr.evidence, "ai_meta": sr.ai_meta,
                "decision_logs": [{"summary": l.summary, "model": l.model,
                                   "prompt_version": l.prompt_version,
                                   "degrade": l.degrade, "at": str(l.created_at)}
                                  for l in logs],
            },
        )


@register
class KnowledgeSearchSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="search_knowledge",
            label="检索知识库",
            description="在内部 SOP、规范、方法论文档中检索内容。"
                        "当用户问「我们的规范是怎么要求的」「XX 流程怎么做」时调用。",
            args_schema=KnowledgeSearchArgs,
            handler=self.run,
            category="知识库",
        )

    async def run(self, query: str, top_k: int = 5) -> SkillResult:
        from app.rag.retriever import search_knowledge
        hits = await search_knowledge(query, top_k=top_k)
        if not hits:
            return _ok("知识库中没有找到相关内容。", {"hits": []})
        return _ok(
            f"在知识库中找到 {len(hits)} 条相关内容，最高相关度 {hits[0]['score']}。",
            {"hits": hits},
        )


@register
class PoolRescueSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="pool_rescue",
            label="待定池捞回",
            description="把待定池中的候选人捞回复核队列。"
                        "【重要】该操作会改变候选人状态，调用后平台只生成待确认动作，"
                        "必须由 HR 在界面上点击确认后才生效。",
            args_schema=PoolRescueArgs,
            handler=self.run,
            category="M2 简历筛选",
            needs_human=True,
        )

    async def run(self, candidate_id: int, reason: str = "") -> SkillResult:
        from sqlalchemy import select

        from app.db.models import Candidate
        from app.db.session import SessionLocal

        async with SessionLocal() as db:
            cand = (await db.execute(select(Candidate).where(
                Candidate.id == candidate_id))).scalar_one_or_none()
            if not cand:
                return _fail("候选人不存在", f"未找到 id={candidate_id}")
            if cand.status != "待定池":
                return _fail("状态不匹配", f"该候选人当前状态为 {cand.status}，不在待定池中")
            name, status, left = cand.name, cand.status, cand.pool_days_left

        return _ok(
            f"已生成待确认动作：把 {name} 从待定池捞回复核队列（剩余 {left} 天）。"
            f"请在候选人工作台点击确认后生效。",
            {
                "pending_action": {
                    "type": "rescue", "candidate_id": candidate_id, "candidate_name": name,
                    "from_status": status, "to_status": "待复核", "reason": reason,
                }
            },
        )


def register_all_skills() -> None:
    """模块导入即完成注册（装饰器在类定义时执行）。"""
    return None
