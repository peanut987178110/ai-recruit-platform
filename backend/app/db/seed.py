"""种子数据：把 PRD 里的岗位能力模型、角色、阈值、知识库灌进库里。

能力模型冷启动按 PRD 6.3 的三路并行方法构造：
  正向拆解 -> 高绩效员工的事后有效信号
  反向挖掘 -> 历史通过/拒绝样本里 HR 实际在意的判断维度
  专家校订 -> 合并去重、确认权重区间
这里落地为 6 条业务线、若干岗位模板，权重和否决项都遵守 PRD 的约束（否决项 <= 3）。
"""
from __future__ import annotations

import random

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    AbilityItem, AbilityModel, ConfigItem, KnowledgeDoc, Position, PromptVersion,
    SampleCase, User,
)
from app.prompts.library import SEED_PROMPTS

# ---------------- 岗位与能力模型定义 ----------------
# evidence_types 取值：project 项目经历 / tenure 任职履历 / skill 技能标签 / metric 量化成果 / education 教育背景

POSITION_SEEDS: list[dict] = [
    {
        "name": "后端开发工程师", "seq": "技术", "level": "P6-P7",
        "business_line": "电商业务线", "headcount": 4,
        "jd": "负责交易链路后端服务设计与开发；要求 5 年以上 Java/Go 经验，有高并发系统实战；"
              "独立负责过完整业务模块；具备复杂问题定位能力。",
        "abilities": [
            ("独立负责过完整业务模块的设计与落地", 3, ["project", "tenure"], "能说清模块边界、自己负责的部分、关键取舍，而非只描述团队做了什么", False, "positive"),
            ("高并发场景的实战经验与量化结果", 2, ["metric", "project"], "有明确的 QPS/延迟/容量数字，并说明优化路径与数据来源", False, "positive"),
            ("复杂线上问题的定位与复盘能力", 2, ["project"], "有具体故障案例，能还原排查过程与根因，而非归因于他人或环境", False, "positive"),
            ("主流后端技术栈的深度使用", 1, ["skill", "project"], "对核心框架能说明选型理由与踩坑，非罗列技术名词", False, "positive"),
            ("学历层次（本科及以上）", 1, ["education"], "本科及以上，作为硬性门槛而非筛选偏好", False, "positive"),
            ("简历真实性存疑", 1, ["project", "metric"], "时间线矛盾、成果与角色明显不匹配、关键项目无法说明细节。仅有据可查时才判命中", True, "negative"),
        ],
    },
    {
        "name": "前端开发工程师", "seq": "技术", "level": "P5-P6",
        "business_line": "电商业务线", "headcount": 2,
        "jd": "负责 C 端页面与组件库建设；要求 3 年以上前端经验，熟悉主流框架与工程化体系。",
        "abilities": [
            ("复杂交互与性能优化的实战经验", 3, ["project", "metric"], "有具体的加载/渲染性能指标前后对比", False, "positive"),
            ("组件库或工程化体系的建设经历", 2, ["project"], "能说明设计动机、覆盖范围与业务方接入情况", False, "positive"),
            ("主流前端框架的深度使用", 2, ["skill", "project"], "能解释框架机制而非只会调用 API", False, "positive"),
            ("技术方案表达清晰度", 1, ["project"], "描述有结构、有取舍逻辑", False, "positive"),
            ("学历层次（本科及以上）", 1, ["education"], "本科及以上", False, "positive"),
            ("作品或代码存在明显抄袭迹象", 1, ["project"], "项目描述与公开项目高度雷同且无法说明细节。仅有据可查时才判命中", True, "negative"),
        ],
    },
    {
        "name": "产品经理", "seq": "产品", "level": "P6-P7",
        "business_line": "电商业务线", "headcount": 2,
        "jd": "负责交易与增长方向产品规划；要求 4 年以上 B 端或交易产品经验，有完整需求到上线闭环。",
        "abilities": [
            ("独立负责过完整产品模块的规划与上线", 3, ["project", "tenure"], "能说明目标、方案取舍、上线后效果", False, "positive"),
            ("用数据驱动决策的习惯", 2, ["metric", "project"], "能给出关键指标定义与验证方式", False, "positive"),
            ("跨部门推动与资源协调能力", 2, ["tenure", "project"], "有推动多方达成一致的具体事例", False, "positive"),
            ("业务理解与行业认知深度", 1, ["project"], "对业务模式有独立判断而非复述结论", False, "positive"),
            ("学历层次（本科及以上）", 1, ["education"], "本科及以上", False, "positive"),
            ("履历存在明显包装迹象", 1, ["project", "tenure"], "职责描述与实际角色差距过大且无法佐证。仅有据可查时才判命中", True, "negative"),
        ],
    },
    {
        "name": "数据分析师", "seq": "技术", "level": "P5-P6",
        "business_line": "供应链业务线", "headcount": 2,
        "jd": "负责供应链经营分析；要求熟练 SQL 与统计方法，有业务分析落地经验。",
        "abilities": [
            ("独立完成业务分析课题并产出决策建议", 3, ["project"], "有从问题定义到结论落地的完整链路", False, "positive"),
            ("量化结果的可信度与可复现性", 2, ["metric"], "口径清晰、样本合理、结论可被复核", False, "positive"),
            ("SQL 与统计方法的实战能力", 2, ["skill", "project"], "能说明复杂查询与统计方法的使用场景", False, "positive"),
            ("业务方沟通与结论呈现", 1, ["tenure"], "能把分析结论翻译成业务语言", False, "positive"),
            ("学历层次（本科及以上）", 1, ["education"], "本科及以上", False, "positive"),
            ("成果数据明显夸大或无法解释来源", 1, ["metric"], "关键数字与业务规模明显不匹配。仅有据可查时才判命中", True, "negative"),
        ],
    },
    {
        "name": "供应链运营专员", "seq": "运营", "level": "P4-P5",
        "business_line": "供应链业务线", "headcount": 3,
        "jd": "负责仓配运营与日常异常处理；要求 2 年以上供应链运营经验，能独立处理跨部门异常。",
        "abilities": [
            ("独立处理复杂运营异常的经历", 3, ["project"], "能还原处理过程与结果", False, "positive"),
            ("流程优化与效率提升的实际成果", 2, ["metric", "project"], "有可量化的效率或成本改善", False, "positive"),
            ("跨部门协同与供应商管理", 2, ["tenure"], "有对接外部供应商或内部多部门的实操", False, "positive"),
            ("数据工具使用能力", 1, ["skill"], "能用 Excel/SQL 做日常分析", False, "positive"),
            ("学历层次（大专及以上）", 1, ["education"], "大专及以上", False, "positive"),
            ("履历时间线存在无法解释的空缺", 1, ["tenure"], "关键时段无说明且与所述经历冲突。仅有据可查时才判命中", True, "negative"),
        ],
    },
    {
        "name": "HRBP", "seq": "职能", "level": "P6",
        "business_line": "职能线", "headcount": 1,
        "jd": "支持业务线人力资源全模块；要求 5 年以上 HR 经验，有组织诊断与人才盘点实操。",
        "abilities": [
            ("独立支撑业务线人力全模块的能力", 3, ["tenure", "project"], "覆盖招聘、绩效、组织发展的实操证据", False, "positive"),
            ("组织诊断与人才盘点项目经历", 2, ["project"], "有项目背景、方法与后续动作", False, "positive"),
            ("业务理解与影响力", 2, ["project", "tenure"], "能说明如何影响业务决策", False, "positive"),
            ("员工关系与冲突处理经验", 1, ["project"], "有具体案例与处理原则", False, "positive"),
            ("学历层次（本科及以上）", 1, ["education"], "本科及以上", False, "positive"),
            ("从业经历存在明显夸大", 1, ["tenure"], "职级与职责描述不匹配且无法佐证。仅有据可查时才判命中", True, "negative"),
        ],
    },
]

# 初始账号与演示角色。
# 第一项是超管账号 admin / 123456，它初始化全部数据、管理其它账号。
# 其余七个是演示角色，密码同样是 123456 —— 这样任何角色都能直接登录体验权限差异。
# 生产环境应当只保留 admin，其余账号由 admin 在界面上创建。
ITEMS = [
    ("系统管理员", "超级管理员", "admin", "", "信息化部", "系统管理员"),
    ("招聘 HR", "张敏", "hr1", "电商业务线", "", "招聘HR"),
    ("HR 负责人", "李国强", "hr_lead", "", "", "HR负责人"),
    ("用人经理", "王海涛", "mgr1", "电商业务线", "交易研发部", "用人经理"),
    ("业务面试官", "陈志远", "interviewer1", "电商业务线", "交易研发部", "业务面试官"),
    ("业务面试官", "刘一帆", "interviewer2", "供应链业务线", "供应链技术部", "业务面试官"),
    ("带教人", "赵鹏", "mentor1", "电商业务线", "交易研发部", "带教人"),
    ("法务审计", "孙雨", "legal1", "", "法务部", "法务审计"),
    # 新人账号：用于体验「学习 → 考试 → 反作弊 → 带教人复核」全流程
    ("新人", "林小雨", "newbie1", "电商业务线", "交易研发部", "新人"),
    ("新人", "郑浩然", "newbie2", "供应链业务线", "供应链技术部", "新人"),
]

# 所有演示账号的初始密码
DEMO_PASSWORD = "123456"

KNOWLEDGE_SEEDS = [
    ("交易链路服务开发规范", "SOP", "电商业务线",
     "一、服务分层：交易链路分为接入层、领域服务层、基础设施层。接入层只做协议转换与鉴权，"
     "不得承载业务逻辑。领域服务层按聚合根划分，单个服务不得跨两个以上聚合根。"
     "二、幂等设计：所有写接口必须支持幂等，幂等键由业务主键加操作类型组成，"
     "幂等窗口不短于 24 小时。三、超时与重试：下游调用超时时间不超过 300ms，重试最多 1 次，"
     "且必须由幂等保障。四、降级：核心链路必须设计降级开关，降级后主流程可继续。"
     "五、压测：新上线接口必须提供压测报告，P99 延迟与容量水位需说明。"),
    ("高并发系统优化实践", "SOP", "电商业务线",
     "一、容量评估：按大促峰值为日常的 8 至 10 倍做容量规划。二、缓存策略："
     "热点数据多级缓存，本地缓存过期时间不超过 5 秒，防止数据不一致。"
     "三、数据库：单表数据量超过 500 万行需评估分库分表，慢查询阈值 100ms。"
     "四、限流：入口限流按租户与接口双维度，限流阈值需压测验证。"
     "五、故障演练：每季度至少一次全链路故障演练，覆盖缓存击穿、数据库主从切换场景。"),
    ("线上故障响应与复盘规范", "SOP", "电商业务线",
     "一、故障分级：P1 影响核心交易，5 分钟内响应；P2 影响部分功能，15 分钟内响应。"
     "二、处理原则：先止损后定位，止损动作需记录时间点。三、复盘要求："
     "P1 故障 3 个工作日内产出复盘报告，包含时间线、根因、改进项与责任人。"
     "四、根因要求：必须定位到系统或流程缺陷，不得以「人为疏忽」作为最终根因。"),
    ("产品需求评审与上线流程", "SOP", "电商业务线",
     "一、需求评审：所有需求需经过产品、研发、测试三方评审，评审通过后才可进入排期。"
     "二、需求文档：必须包含背景、目标、用户故事、验收标准四部分。"
     "三、上线检查：上线前需完成回归测试、灰度方案、回滚预案三项准备工作。"
     "四、数据回收：功能上线后 2 周内需回收核心指标，未达标需给出后续方案。"),
    ("供应链异常处理手册", "SOP", "供应链业务线",
     "一、异常分类：库存异常、时效异常、质量异常、系统异常四类。"
     "二、处理时效：库存异常 30 分钟内响应，时效异常 2 小时内给出方案。"
     "三、升级机制：超过处理时效未解决，自动升级至业务线负责人。"
     "四、记录要求：所有异常需在系统留痕，包含异常描述、处理动作、最终结果。"
     "五、复盘：月度汇总异常类型分布，连续两月占比上升的类型需专项治理。"),
    ("经营分析方法论", "SOP", "供应链业务线",
     "一、问题定义：分析开始前必须明确业务问题与决策场景，禁止无目标取数。"
     "二、口径管理：每个指标必须有唯一口径文档，口径变更需同步所有下游。"
     "三、方法选择：对比分析、结构分析、趋势分析、归因分析四类方法按场景选择。"
     "四、结论要求：结论必须可执行，避免「需要持续关注」这类不可行动表述。"
     "五、复现要求：关键分析需保留取数逻辑，支持他人复现。"),
    ("新人带教与考核规范", "SOP", "通用",
     "一、带教周期：新人入职后 35 天内完成独立上岗评估。"
     "二、带教内容：按岗位能力模型组织，每项能力需有对应的学习材料与实操任务。"
     "三、考核方式：客观题考察知识掌握，情景与案例题考察应用能力。"
     "四、转正评估：考核成绩作为参考，最终结论由带教人与用人经理共同给出。"
     "五、反馈机制：新人每周提交一次学习记录，带教人每周反馈一次。"),
    ("个人信息保护与招聘合规要求", "合规", "通用",
     "一、敏感信息：性别、年龄、婚育状况、籍贯地域、院校层次不得作为筛选依据。"
     "二、自动化决策：不得单独依据自动化决策做出对候选人的不利决定。"
     "三、留痕要求：全链路记录输入、模型版本、输出结论与人工操作，日志保留不少于 3 年。"
     "四、候选人权：候选人可申请删除个人信息，需在 15 个工作日内完成。"
     "五、知情同意：招聘页面需明示使用自动化工具辅助筛选，并说明存在人工复核环节。"),
]


def normalize_weights(raw: list[int], total: int = 10) -> list[int]:
    """把任意相对权重归一化成总和恰为 total 的整数权重，每项落在 1 至 10 之间。

    用最大余数法，保留原始权重的相对大小；同时保证每项至少 1，避免出现
    「某个能力项权重为 0」这种会让它在打分中被静默忽略的情况。
    """
    n = len(raw)
    if n == 0:
        return []
    if total < n:
        # 项数多于总权重时无法保证每项至少 1，退化为均分
        return [1] * n

    s = sum(raw) or n
    scaled = [w / s * total for w in raw]
    out = [max(1, int(x)) for x in scaled]

    # 修正到恰好等于 total：先补差额，再削多余的
    diff = total - sum(out)
    order = sorted(range(n), key=lambda i: scaled[i] - int(scaled[i]), reverse=True)
    while diff > 0:
        for i in order:
            if diff == 0:
                break
            out[i] += 1
            diff -= 1
    while diff < 0:
        for i in sorted(range(n), key=lambda i: out[i], reverse=True):
            if diff == 0 or out[i] <= 1:
                continue
            out[i] -= 1
            diff += 1
        if all(v <= 1 for v in out):
            break
    return out


async def _seed_users(db: AsyncSession) -> None:
    if (await db.execute(select(func.count(User.id)))).scalar_one() > 0:
        return
    from app.core.security import hash_password

    pwd = hash_password(DEMO_PASSWORD)
    for name, uname, uid, bl, dept, role in ITEMS:
        db.add(User(userid=uid, name=name, role=role, business_line=bl, department=dept,
                    email=f"{uid}@example.com", password_hash=pwd,
                    created_by="system" if uid == "admin" else "admin"))
    await db.flush()


async def _seed_positions(db: AsyncSession) -> None:
    if (await db.execute(select(func.count(Position.id)))).scalar_one() > 0:
        return
    for ps in POSITION_SEEDS:
        pos = Position(name=ps["name"], seq=ps["seq"], level_range=ps["level"],
                       business_line=ps["business_line"], headcount=ps["headcount"],
                       jd_text=ps["jd"], status="在招")
        db.add(pos)
        await db.flush()
        model = AbilityModel(position_id=pos.id, version="v1", active=True)
        db.add(model)
        await db.flush()
        # 原始权重是相对值（9/8/7...），归一化到总和 10（即 100%），满足校验规则
        raw_weights = [a[1] for a in ps["abilities"]]
        weights = normalize_weights(raw_weights)
        for i, (ab, w) in enumerate(zip(ps["abilities"], weights)):
            nm, _raw, et, crit, veto = ab[0], ab[1], ab[2], ab[3], ab[4]
            polarity = ab[5] if len(ab) > 5 else "positive"
            db.add(AbilityItem(model_id=model.id, name=nm, weight=w, evidence_types=et,
                               criteria=crit, is_veto=veto, veto_polarity=polarity, sort=i))
    await db.flush()


async def _seed_config(db: AsyncSession) -> None:
    if (await db.execute(select(func.count(ConfigItem.id)))).scalar_one() > 0:
        return
    rows = [
        ("threshold.high_confidence", "高分档置信度阈值", {"v": settings.default_high_confidence},
         "threshold", "置信度不低于该值且总分达标才进高分档"),
        ("threshold.high_score", "高分档总分阈值", {"v": settings.default_high_score},
         "threshold", "总分不低于该值才进高分档"),
        ("threshold.mid_score", "中间档总分下限", {"v": settings.default_mid_score},
         "threshold", "低于该值进低分档；中间档强制人工复核"),
        ("pool.keep_days", "待定池保留天数", {"v": settings.default_pool_days},
         "pool", "到期自动归档，归档不等于拒绝，不触发对外通知"),
        ("pool.warn_days", "待定池标黄剩余天数", {"v": 2}, "pool", "剩余天数小于等于该值标黄提示"),
        ("cost.target_cny", "单份处理成本目标（元）", {"v": settings.default_cost_target},
         "cost", "PRD 5.2 成本约束"),
        ("cost.alert_cny", "单份成本告警线（元）", {"v": settings.default_cost_alert},
         "cost", "连续 3 天超线告警"),
        ("model.route_small", "小模型档位用途", {"v": "解析、格式化、字段抽取"}, "model", "成本最低"),
        ("model.route_medium", "中模型档位用途", {"v": "打分、题目生成、判卷"}, "model", "能力与成本平衡"),
        ("model.route_large", "大模型档位用途", {"v": "复杂归因、报告生成"}, "model", "按需使用，成本最高"),
        ("review.batch_forbid_mid", "中间档禁用批量通过", {"v": True}, "review",
         "产品有意增加操作成本，这部分恰是模型最没把握的样本"),
        ("interview.plan_minutes", "面试默认时长（分钟）", {"v": 30}, "interview", "用于题目按时间预算重排"),
    ]
    for k, label, v, g, desc in rows:
        db.add(ConfigItem(key=k, value=v, label=label, group=g, description=desc))
    await db.flush()


async def _seed_knowledge(db: AsyncSession) -> None:
    if (await db.execute(select(func.count(KnowledgeDoc.id)))).scalar_one() > 0:
        return
    from app.rag.splitter import split_text
    for title, cat, bl, content in KNOWLEDGE_SEEDS:
        chunks = split_text(content)
        db.add(KnowledgeDoc(title=title, category=cat, business_line=bl, content=content,
                            chunks=chunks, owner="HR招聘中心",
                            source_path=f"内部知识库/{cat}/{title}.md"))
    await db.flush()


async def _seed_prompts(db: AsyncSession) -> None:
    if (await db.execute(select(func.count(PromptVersion.id)))).scalar_one() > 0:
        return
    for p in SEED_PROMPTS:
        db.add(PromptVersion(**p))
    await db.flush()


async def _seed_samples(db: AsyncSession) -> None:
    """历史样本：模拟 ATS 历史投递记录，用作试算区与一致率真值。"""
    if (await db.execute(select(func.count(SampleCase.id)))).scalar_one() > 0:
        return
    from app.services.sample_data import SAMPLE_RESUMES
    for s in SAMPLE_RESUMES:
        db.add(SampleCase(**s))
    await db.flush()


async def seed_all(db: AsyncSession) -> None:
    await _seed_users(db)
    await _seed_positions(db)
    await _seed_config(db)
    await _seed_knowledge(db)
    await _seed_prompts(db)
    await _seed_samples(db)
    await db.commit()


async def ensure_seeded() -> None:
    from app.db.session import SessionLocal
    async with SessionLocal() as db:
        await seed_all(db)
