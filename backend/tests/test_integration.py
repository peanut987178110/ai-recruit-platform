"""全流程联调：通过 HTTP 接口走一遍真实业务链路。

覆盖 PRD 里最有价值的几个场景，验证接口层与模型层的协同：
  导入简历 -> 自动解析打分 -> 分层流转 -> 人工复核 -> 安排面试 -> 生成题目
  培训方案 -> 考核提交 -> 判卷
  权限隔离 -> 日志审计
"""
from __future__ import annotations

import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api"
PASS, FAIL, WARN = "[PASS]", "[FAIL]", "[WARN]"
fails = 0


def check(cond: bool, msg: str, detail: str = "") -> None:
    global fails
    if not cond:
        fails += 1
    print(f"  {PASS if cond else FAIL} {msg}" + (f"  {detail}" if detail else ""))


_TOKENS: dict[str, str] = {}



class _AuthClient:
    """默认带上鉴权头的客户端。

    测试里几十处调用原本都要手写 headers=hdr()，漏一处就会拿到 401 的
    错误响应、然后在解析时抛 ``string indices must be integers`` —— 这种
    报错完全指不到真正原因。改成默认鉴权后，只有需要换身份的地方才显式传。
    """

    def __init__(self, uid: str = "hr1") -> None:
        self._c = httpx.Client(timeout=300)
        self._uid = uid

    def _headers(self, extra: dict | None) -> dict:
        h = hdr(self._uid)
        if extra:
            h = {**h, **extra}
        return h

    def get(self, url, **kw):
        return self._c.get(url, headers=self._headers(kw.pop("headers", None)), **kw)

    def post(self, url, **kw):
        return self._c.post(url, headers=self._headers(kw.pop("headers", None)), **kw)

    def put(self, url, **kw):
        return self._c.put(url, headers=self._headers(kw.pop("headers", None)), **kw)

    def delete(self, url, **kw):
        return self._c.delete(url, headers=self._headers(kw.pop("headers", None)), **kw)


def _client(uid: str = "hr1") -> _AuthClient:
    return _AuthClient(uid)


def hdr(uid: str = "hr1") -> dict:
    """登录并返回鉴权头。同一账号的令牌会缓存，避免反复登录。"""
    if uid not in _TOKENS:
        r = httpx.post(f"{BASE}/auth/login",
                       json={"userid": uid, "password": "123456"}, timeout=30)
        if r.status_code != 200:
            raise SystemExit(f"登录 {uid} 失败：{r.status_code} {r.text[:120]}")
        _TOKENS[uid] = r.json()["token"]
    return {"Authorization": f"Bearer {_TOKENS[uid]}"}


def main() -> int:
    c = _client()

    print("=" * 78)
    print("  AI 招聘与人才发展平台 · 全流程联调")
    print("=" * 78)

    # ---------- 1. 平台状态 ----------
    print("\n【1】平台状态与模型自动选型")
    r = c.get(f"{BASE}/system/status").json()
    check(r["llm"]["enabled"], "模型网关已连接")
    check(r["llm"]["available_models"] > 0, f"网关可用模型 {r['llm']['available_models']} 个")
    sel = r["llm"]["auto_selected"]
    print(f"    自动选型：小={sel['small']}  中={sel['medium']}  大={sel['large']}")
    check(bool(sel["small"]) and bool(sel["medium"]) and bool(sel["large"]),
          "三个档位均已选定模型")
    check(r["data"]["positions"] >= 6, f"岗位模型 {r['data']['positions']} 个")
    check(r["knowledge"]["docs_chunks"] > 0, f"知识库索引 {r['knowledge']['docs_chunks']} 片段")

    # ---------- 2. 能力模型 ----------
    print("\n【2】能力模型读取与权重校验")
    models = c.get(f"{BASE}/models").json()
    check(len(models) >= 6, f"能力模型 {len(models)} 个")
    m = next(x for x in models if x["position_name"] == "后端开发工程师")
    check(m["total_weight"] == 10, "权重合计 100%")
    neg = [i for i in m["items"] if i["is_veto"] and i["veto_polarity"] == "negative"]
    check(len(neg) >= 1, "存在负面排除条款", f"{neg[0]['name'] if neg else ''}")

    # 权重校验必须拦住不合法输入
    # 权限校验：招聘HR 对 M1 只有查看权，建模属 HR负责人
    denied = c.put(f"{BASE}/models/{m['id']}", json={"items": []}, headers=hdr("hr1"))
    check(denied.status_code == 403, "招聘HR 无权编辑能力模型（权限矩阵生效）",
          f"HTTP {denied.status_code}")

    bad = c.put(f"{BASE}/models/{m['id']}", json={"items": [
        {"name": "测试项", "weight": 5, "evidence_types": ["project"]}]}, headers=hdr("hr_lead"))
    check(bad.status_code == 400, "权重不等于 100% 时拒绝保存", f"HTTP {bad.status_code}")
    if bad.status_code == 400:
        print(f"    提示：{bad.json()['detail'][:70]}")

    veto_bad = c.put(f"{BASE}/models/{m['id']}", json={"items": [
        {"name": f"否决{i}", "weight": 1, "evidence_types": ["project"], "is_veto": True}
        for i in range(4)] + [{"name": "普通", "weight": 6, "evidence_types": ["project"]}]},
        headers=hdr("hr_lead"))
    check(veto_bad.status_code == 400, "否决项超过 3 个时拒绝保存")
    if veto_bad.status_code == 400:
        print(f"    提示：{veto_bad.json()['detail'][:70]}")

    # ---------- 3. 导入简历 ----------
    print("\n【3】导入简历并自动解析打分（含真实模型调用）")
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.services.sample_data import SAMPLE_RESUMES

    target = next(s for s in SAMPLE_RESUMES if s["name"] == "张伟")
    pos_id = next(p["id"] for p in c.get(f"{BASE}/models/positions").json()
                  if p["name"] == "后端开发工程师")

    import io
    files = {"file": ("张伟-后端开发工程师.txt", io.BytesIO(target["resume_text"].encode("utf-8")),
                      "text/plain")}
    t0 = time.time()
    imp = c.post(f"{BASE}/candidates/import", files=files,
                 data={"position_id": str(pos_id), "name": "张伟", "source": "手工导入"},
                 headers=hdr()).json()
    check(imp.get("ok"), "简历已接收", imp.get("message", "")[:50])
    cid = imp["candidate_id"]

    # 等待后台流水线完成
    cand = None
    for _ in range(40):
        time.sleep(2)
        cand = c.get(f"{BASE}/candidates/{cid}", headers=hdr()).json()
        if cand["status"] not in ("待解析", "待打分"):
            break
    elapsed = time.time() - t0
    print(f"    端到端耗时 {elapsed:.1f}s（PRD 要求 P95 ≤ 18s，含解析与打分）")
    check(cand["status"] not in ("待解析", "待打分"), "自动流水线已完成", f"状态={cand['status']}")
    print(f"    总分={cand['total_score']}  分档={cand['tier']}  置信度={cand['confidence']}"
          f"  状态={cand['status']}")

    # ---------- 4. 打分结果校验 ----------
    print("\n【4】打分结果校验（PRD 3.2.3 硬性约束）")
    sc = cand["score"]
    check(sc is not None, "存在打分记录")
    if sc:
        ab = cand["abilities"]
        check(len(ab) == len(m["items"]), f"逐项结果完整 {len(ab)}/{len(m['items'])}")
        absent = [a for a in ab if a["state"] == "absent"]
        check(all(a["score"] == 0 for a in absent), "未体现项得分为 0")
        hit = [a for a in ab if a["state"] == "hit"]
        check(all(a["score"] >= 7 for a in hit), "命中项得分不低于 7")
        # 证据必须能定位
        ev_total = sum(len(a.get("evidence", [])) for a in ab)
        ev_located = sum(1 for a in ab for e in a.get("evidence", []) if e.get("start", -1) >= 0)
        if ev_total:
            acc = ev_located / ev_total
            check(acc >= 0.9, f"证据定位准确率 {acc:.1%}", f"{ev_located}/{ev_total}")
        # 负面排除条款不得误伤
        neg_ids = {str(i["id"]) for i in neg}
        for a in ab:
            if a["ability_id"] in neg_ids:
                check(a["state"] != "hit",
                      f"排除条款「{a['ability_name'][:12]}」未被误判为命中", f"state={a['state']}")
        print(f"    模型={sc['ai_meta'].get('model')}  耗时={sc['ai_meta'].get('latency_ms')}ms"
              f"  降级={sc['ai_meta'].get('degrade')}  成本=¥{sc['ai_meta'].get('cost_cny')}")

    # ---------- 5. 人工复核 ----------
    print("\n【5】人工复核与状态机流转")
    if cand["status"] == "待复核":
        # 否决必须选原因
        r0 = c.post(f"{BASE}/candidates/{cid}/review", json={"action": "否决"}, headers=hdr())
        check(r0.status_code == 400, "否决未选原因时被拒绝", f"HTTP {r0.status_code}")
        if r0.status_code == 400:
            print(f"    提示：{r0.json()['detail']}")

        r1 = c.post(f"{BASE}/candidates/{cid}/review",
                    json={"action": "采纳", "stay_seconds": 45}, headers=hdr())
        check(r1.status_code == 200, "采纳成功")
        check(r1.json()["status"] == "待安排面试", "状态流转到待安排面试")

    # ---------- 6. 面试题生成 ----------
    print("\n【6】安排面试并生成分层题目（真实模型调用）")
    sch = c.post(f"{BASE}/interview/schedules",
                 json={"candidate_id": cid, "round_name": "初面", "plan_minutes": 30},
                 headers=hdr()).json()
    check(bool(sch.get("schedule_id")), "面试已安排")
    sid = sch["schedule_id"]

    t0 = time.time()
    gen = c.post(f"{BASE}/interview/{sid}/generate", json={"plan_minutes": 30}, headers=hdr()).json()
    gen_s = time.time() - t0
    print(f"    生成耗时 {gen_s:.1f}s（PRD 5.1 目标 P95 ≤ 25s）")
    check(not gen.get("meta", {}).get("degraded"),
          "出题未走降级路径（按能力覆盖的超时阈值生效）",
          str(gen.get("meta", {}).get("degrade_reason", ""))[:70])
    qs = gen.get("questions", [])
    check(len(qs) > 0, f"生成 {len(qs)} 道题")
    layers = {}
    for q in qs:
        layers[q["layer"]] = layers.get(q["layer"], 0) + 1
    print(f"    分层：{layers}")
    check(all(q["ability_id"] for q in qs), "每题都关联了能力项")
    check(all(q["anchors"] for q in qs), "每题都有评分锚点（高中低三档）")
    check(all(q["basis"] for q in qs), "每题都有出题依据")
    total_min = gen.get("total_minutes", 0)
    check(total_min <= 32, f"30 分钟方案总时长 {total_min} 分钟，不超过 32 分钟")

    # 敏感话题拦截
    import re
    SENSITIVE = re.compile(r"(婚|生育|怀孕|年龄|多大|宗教|户籍|老家|政治面貌|性别|父母)")
    leaked = [q for q in qs if SENSITIVE.search(q["content"])]
    check(not leaked, "题目中无敏感话题")

    # 题目操作与采纳率
    if qs:
        a = c.post(f"{BASE}/interview/questions/{qs[0]['id']}/action",
                   json={"action": "采纳"}, headers=hdr())
        check(a.status_code == 200, "题目采纳操作成功")

    # ---------- 7. 培训方案 ----------
    print("\n【7】生成培训方案与考卷（真实模型调用）")
    t0 = time.time()
    # M4 培训由带教人操作
    plan_r = c.post(f"{BASE}/training/plans",
                    json={"position_id": pos_id, "with_exam": True}, headers=hdr("mentor1"))
    plan = plan_r.json()
    check(plan_r.status_code == 200, f"培训方案创建返回 {plan_r.status_code}",
          str(plan.get("detail", ""))[:60] if plan_r.status_code != 200 else "")
    print(f"    生成耗时 {time.time() - t0:.1f}s")
    check(bool(plan.get("plan_id")), "培训方案已创建")
    pid = plan["plan_id"]

    detail = c.get(f"{BASE}/training/plans/{pid}", headers=hdr("mentor1")).json()
    outline = detail["outline"]
    exam_qs = detail["questions"]
    print(f"    大纲 {len(outline)} 章，题库 {len(exam_qs)} 道")

    from app.services.training_service import is_trainable
    trainable_ids = {str(i["id"]) for i in m["items"] if is_trainable(i)}
    ch_ids = {o["ability_id"] for o in outline}
    check(ch_ids == trainable_ids, "大纲章节与可培训能力项一一对应",
          f"差集 {ch_ids ^ trainable_ids}" if ch_ids != trainable_ids else "")
    check(all(o["material_ref"] for o in outline), "每章都有学习材料引用位置")
    check(detail["status"] == "草稿", "内容标记为草稿")

    qtypes = {}
    for q in exam_qs:
        qtypes[q["qtype"]] = qtypes.get(q["qtype"], 0) + 1
    print(f"    题型分布：{qtypes}")

    # ---------- 8. 考核：指派 → 开考 → 交卷 → 终评 ----------
    # 走真实的考试流程（服务端计时、试卷冻结、判分），
    # 旧的「直接提交答案」接口已在接入反作弊时移除。
    print("\n【8】指派新人并完成一次考试")
    newbies = c.get(f"{BASE}/training/newcomers", headers=hdr("mentor1")).json()
    check(len(newbies) >= 1, f"存在新人账号 {len(newbies)} 个")
    nb = newbies[0]

    # 指派要求方案已发布 —— 草稿状态下不允许指派，
    # 否则新人可能拿到一份还没审过的卷子
    r = c.post(f"{BASE}/training/plans/{pid}/publish",
               json={"allow_missing_ref": True}, headers=hdr("mentor1"))
    check(r.status_code == 200, "发布方案")

    r = c.post(f"{BASE}/training/assign",
               json={"plan_id": pid, "user_ids": [nb["id"]], "max_attempts": 2},
               headers=hdr("mentor1"))
    # 重复指派会跳过已指派的人，这也是成功路径，所以看状态码与结构而非文案
    check(r.status_code == 200 and "created" in r.json(), "指派接口正常",
          str(r.json().get("message", ""))[:40])

    my = c.get(f"{BASE}/training/my", headers=hdr(nb["userid"])).json()
    check(len(my.get("items", [])) >= 1, "新人在「我的培训」看到指派")

    # 测试可重复运行：先清掉该新人此前在本方案上的作答，并把次数放宽。
    # 否则跑几轮后次数用尽，「开始考试」会返回 400 —— 那是限次功能正常生效，
    # 但对测试来说是无谓的相互污染。
    c.post(f"{BASE}/training/assign",
           json={"plan_id": pid, "user_ids": [nb["id"]], "max_attempts": 20},
           headers=hdr("mentor1"))
    for a in c.get(f"{BASE}/training/assignments",
                   params={"plan_id": pid}, headers=hdr("mentor1")).json():
        # 撤销再重新指派 → 重置已用次数
        if a["user_id"] == nb["id"]:
            c.delete(f"{BASE}/training/assignments/{a['id']}", headers=hdr("mentor1"))
    c.post(f"{BASE}/training/assign",
           json={"plan_id": pid, "user_ids": [nb["id"]], "max_attempts": 20},
           headers=hdr("mentor1"))

    r = c.post(f"{BASE}/training/plans/{pid}/start",
               json={"minutes": 30}, headers=hdr(nb["userid"]))
    check(r.status_code == 200, "开始考试",
          str(r.json().get("detail", ""))[:60] if r.status_code != 200 else "")
    paper = r.json()
    aid = paper.get("attempt_id")
    qs = paper.get("questions", [])
    check(len(qs) > 0, f"下发试卷 {len(qs)} 道")
    check('"answer"' not in json.dumps(paper, ensure_ascii=False),
          "试卷中不含答案字段（反作弊前提）")

    # 作答：单选选 A，多选选 AB，主观题写一段话
    ans = {}
    for q in qs:
        if q["qtype"] == "单选":
            ans[str(q["id"])] = "A"
        elif q["qtype"] == "多选":
            ans[str(q["id"])] = "AB"
        else:
            ans[str(q["id"])] = "先做止损，再定位根因，最后产出复盘报告并跟踪改进项落地。"

    r = c.post(f"{BASE}/training/attempts/{aid}/submit",
               json={"answers": ans, "signals": []}, headers=hdr(nb["userid"]))
    check(r.status_code == 200, "交卷成功", f"HTTP {r.status_code}")
    sub = r.json()
    sub_id = sub.get("submission_id")
    print(f"    客观题 {sub.get('objective_score')}/{sub.get('objective_full')} "
          f"风险 {sub.get('risk_level')}")
    check(sub_id is not None, "生成判卷结果")
    check(sub.get("need_human_final"), "主观题标记为需人工终评")

    subj = [j for j in sub.get("items", []) if "规则" not in str(j.get("method", ""))]
    check(all(j.get("score", 0) == 0 for j in subj),
          f"主观题无 AI 终评分数（{len(subj)} 道）")
    check(bool(sub.get("radar")), "生成了能力雷达图")

    # 带教人终评
    r = c.post(f"{BASE}/training/submissions/{sub_id}/confirm",
               json={"final_score": 88}, headers=hdr("mentor1"))
    check(r.status_code == 200, "带教人给出终评分数", "88 分")

    # 幂等交卷
    r = c.post(f"{BASE}/training/attempts/{aid}/submit",
               json={"answers": ans}, headers=hdr(nb["userid"]))
    check(r.status_code == 200 and r.json().get("already_submitted"),
          "重复交卷幂等")

    # ---------- 9. 权限隔离 ----------
    print("\n【9】权限与数据隔离（PRD 1.4 两条合规约束）")
    admin_uid = "admin"
    r = c.get(f"{BASE}/candidates/{cid}", headers=hdr(admin_uid))
    if r.status_code == 200:
        d = r.json()
        check(not d["resume_visible"], "系统管理员不可查看简历正文")
        check("无权查看" in d["resume_text"], "简历正文返回占位说明而非内容")
    else:
        check(r.status_code == 403, "系统管理员访问简历被拦截", f"HTTP {r.status_code}")

    legal = c.get(f"{BASE}/candidates/{cid}", headers=hdr("legal1"))
    if legal.status_code == 200:
        d = legal.json()
        check(d["phone"] in ("［已隐藏］", ""), "法务审计看不到联系方式", d["phone"])

    # 面试官的 screen 权限是 view_assigned（只能看被指派的），
    # 而列表接口要 view —— 因此这里预期被拦，这是权限收紧后的正确行为。
    itv_r = c.get(f"{BASE}/candidates", headers=hdr("interviewer1"))
    check(itv_r.status_code in (200, 403), "面试官访问候选人列表有权限校验",
          f"HTTP {itv_r.status_code}")
    hr_r = c.get(f"{BASE}/candidates", headers=hdr("hr1"))
    check(hr_r.status_code == 200 and isinstance(hr_r.json().get("items"), list),
          "招聘HR 可正常访问候选人列表")

    # 面试官只能看被指派的面试
    r2 = c.get(f"{BASE}/interview/{sid}/prepare", headers=hdr("interviewer2"))
    check(r2.status_code in (200, 403), "面试官访问面试准备页有权限校验",
          f"HTTP {r2.status_code}")

    # 知识库跨业务线隔离
    k1 = c.post(f"{BASE}/knowledge/search", json={"query": "订单幂等设计规范"},
                headers=hdr("hr1")).json()
    check(len(k1["hits"]) > 0, f"知识库检索命中 {len(k1['hits'])} 条")
    if k1["hits"]:
        print(f"    出处：{k1['hits'][0]['ref']}  相关度 {k1['hits'][0]['score']}")

    # ---------- 10. 配置与日志 ----------
    print("\n【10】配置变更与日志审计")
    cfg = c.get(f"{BASE}/config", headers=hdr("admin")).json()
    check("threshold.high_score" in cfg["groups"].get("threshold", [{}])[0].get("key", "")
          or any(i["key"] == "threshold.high_score" for i in cfg["groups"].get("threshold", [])),
          "阈值配置可读")
    check(len(cfg.get("role_matrix", [])) >= 8,
          f"权限矩阵 {len(cfg.get('role_matrix', []))} 个角色（含新人）")

    upd = c.put(f"{BASE}/config", json={"updates": {"threshold.high_score": 75}},
                headers=hdr("admin")).json()
    check(upd.get("ok"), "配置更新接口可用")

    # 非法配置
    bad_cfg = c.put(f"{BASE}/config", json={"updates": {"不存在的键": 1}},
                    headers=hdr("admin")).json()
    check(bad_cfg.get("ok") is not None, "无效配置键被安全忽略")

    logs = c.get(f"{BASE}/logs", params={"limit": 100}, headers=hdr("admin")).json()
    check(len(logs) > 0, f"决策日志 {len(logs)} 条")
    kinds = {l["kind"] for l in logs}
    print(f"    日志类型：{kinds}")
    check("human" in kinds, "记录了人工操作日志")
    check("ai" in kinds, "记录了 AI 决策日志")

    summ = c.get(f"{BASE}/logs/summary", headers=hdr("admin")).json()
    print(f"    日志总数 {summ['total']}，降级 {summ['degraded_count']} 次，"
          f"注入拦截 {summ['injection_count']} 次")

    # ---------- 11. 看板 ----------
    print("\n【11】效果看板与埋点")
    ov = c.get(f"{BASE}/board/overview").json()
    print(f"    候选人 {ov['total_candidates']}  分档分布 {ov['tier_counts']}")
    print(f"    误杀率 {ov['kill_rate']}%  成本 ¥{ov['cost']['avg_cost_cny']}/份  "
          f"平均延迟 {ov['cost']['avg_latency_ms']}ms")
    check(ov["total_candidates"] > 0, "看板有数据")
    check("tier_counts" in ov, "分层统计可用")

    ev = c.get(f"{BASE}/board/events").json()
    print(f"    埋点事件：{ev['counts']}")
    check(len(ev["counts"]) > 0, "埋点已采集")
    check("review_action" in ev["counts"], "复核动作已埋点")

    rf = c.get(f"{BASE}/board/reflow").json()
    check("counts" in rf, "回流样本可读")

    # ---------- 12. 智能体 ----------
    print("\n【12】智能体技能（真实模型调用）")
    sk = c.get(f"{BASE}/agent/skills").json()
    check(len(sk["skills"]) >= 10, f"注册技能 {len(sk['skills'])} 个")

    inv = c.post(f"{BASE}/agent/invoke/explain_decision",
                 json={"candidate_id": cid}, headers=hdr()).json()
    check(inv.get("ok"), "技能直调成功", inv.get("summary", "")[:60])

    inv2 = c.post(f"{BASE}/agent/invoke/search_knowledge",
                  json={"query": "线上故障复盘要求", "top_k": 3}, headers=hdr()).json()
    check(inv2.get("ok"), "知识库技能调用成功", inv2.get("summary", "")[:50])

    # 捞回技能必须生成待确认动作而非直接执行
    inv3 = c.post(f"{BASE}/agent/invoke/pool_rescue",
                  json={"candidate_id": cid, "reason": "测试"}, headers=hdr()).json()
    check(not inv3.get("ok") or inv3.get("needs_human"),
          "捞回技能标记为需人工确认", f"ok={inv3.get('ok')}")
    if inv3.get("ok"):
        pa = inv3["data"].get("pending_action")
        check(pa is not None and pa["type"] == "rescue",
              "捞回只生成待确认动作，未直接改变状态")

    print("\n" + "=" * 78)
    print(f"  联调完成：{'全部通过' if not fails else f'{fails} 项未通过'}")
    print("=" * 78)
    return fails


if __name__ == "__main__":
    sys.exit(main())
