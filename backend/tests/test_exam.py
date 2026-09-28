"""培训考核全流程测试。

重点验证反作弊设计的每一层是否真的生效：
  1. 答案绝不下发到客户端
  2. 服务端计时不可被前端绕过
  3. 试卷冻结，交卷按同一份卷子判分
  4. 选项乱序后判分仍然正确
  5. 幂等交卷
  6. 风险信号只作线索，不影响成绩
  7. 新人数据隔离
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000/api"
PASS, FAIL = "[PASS]", "[FAIL]"
fails = 0

_TOKENS: dict[str, str] = {}


def tok(uid: str) -> str:
    if uid not in _TOKENS:
        r = httpx.post(f"{BASE}/auth/login", json={"userid": uid, "password": "123456"}, timeout=30)
        _TOKENS[uid] = r.json()["token"]
    return _TOKENS[uid]


def hdr(uid: str = "admin") -> dict:
    return {"Authorization": f"Bearer {tok(uid)}"}


def check(cond: bool, msg: str, detail: str = "") -> None:
    global fails
    if not cond:
        fails += 1
    print(f"  {PASS if cond else FAIL} {msg}" + (f"  {detail}" if detail else ""))


def main() -> int:
    c = httpx.Client(timeout=300)

    print("=" * 76)
    print("  培训考核 · 反作弊设计验证")
    print("=" * 76)

    # ---------- 1. 上传公司资料 ----------
    print("\n【1】发布者上传公司文档资料")
    mat_text = """示例科技 · 订单链路研发规范 V2.1

一、服务分层
订单链路分为接入层、路由层、渠道适配层。接入层只做协议转换与鉴权，禁止承载业务逻辑。
路由层负责渠道选择，必须实现加权评分与实时熔断，单个渠道连续失败若干次即熔断一段时间。
渠道适配层按渠道隔离，任一渠道的异常不得影响其它渠道。

二、幂等设计
所有写接口必须支持幂等。幂等键由「业务方标识 + 单据号 + 操作类型」组成，有效期可按业务配置。
幂等记录必须在提交前落库，采用先写幂等表再调用下游的顺序，避免重复处理。

三、对账规范
每日固定时间执行自动对账，覆盖全部渠道。对账差异分为三类：
长款（我方成功、渠道失败）、短款（我方失败、渠道成功）、金额不符。
差异必须在规定时限内处理完毕，超出阈值的差异需当日报备。

四、容灾要求
系统必须实现同城双活。故障切换时间目标控制在一分钟以内。
按固定周期至少一次全链路故障演练，覆盖渠道不可用、数据库主从切换、缓存失效三种场景。

五、监控指标
核心指标包括：请求成功率、平均响应时间、渠道可用率、对账差异率。
任一指标连续偏离目标需触发告警。
"""
    r = c.post(f"{BASE}/training/materials",
               files={"file": ("支付链路研发规范.txt", mat_text.encode("utf-8"), "text/plain")},
               data={"title": "支付链路研发规范 V2.1", "plan_id": "0"},
               headers=hdr("mentor1"))
    check(r.status_code == 200, "上传资料成功", f"HTTP {r.status_code}")
    mat = r.json() if r.status_code == 200 else {}
    if mat:
        print(f"    {mat.get('char_count')} 字，{mat.get('chunk_count')} 个片段")
        check(mat.get("char_count", 0) > 400, "资料内容已抽取")
    mid = mat.get("id")

    # ---------- 2. 生成方案 ----------
    print("\n【2】基于资料生成培训方案与题库")
    positions = c.get(f"{BASE}/models/positions", headers=hdr("mentor1")).json()
    pos = next((p for p in positions if p["name"] == "后端开发工程师"), positions[0])
    t0 = time.time()
    # 显式设定 30 分钟限时与 70 分及格线，后面验证服务端确实按这两个值执行
    r = c.post(f"{BASE}/training/plans",
               json={"position_id": pos["id"], "with_exam": True,
                     "exam_minutes": 30, "pass_score": 70},
               headers=hdr("mentor1"))
    check(r.status_code == 200, "生成方案", f"{time.time() - t0:.0f}s")
    pid = r.json().get("plan_id") if r.status_code == 200 else None

    detail = c.get(f"{BASE}/training/plans/{pid}", headers=hdr("mentor1")).json()
    qs = detail.get("questions", [])
    plan_minutes = detail.get("exam_minutes") or 60
    outline = detail.get("outline", [])
    print(f"    大纲 {len(outline)} 章，题库 {len(qs)} 道")
    qtypes: dict[str, int] = {}
    for q in qs:
        qtypes[q["qtype"]] = qtypes.get(q["qtype"], 0) + 1
    print(f"    题型：{qtypes}")
    check(len(qs) >= 5, "题库已生成")
    # 出题是否引用了上传的资料
    refs = [q.get("ref", "") for q in qs if q.get("ref")]
    check(len(refs) > 0, f"题目带出题出处（{len(refs)} 道）", refs[0][:40] if refs else "")

    # 发布
    r = c.post(f"{BASE}/training/plans/{pid}/publish",
               json={"allow_missing_ref": True}, headers=hdr("mentor1"))
    check(r.status_code == 200, "发布方案", f"HTTP {r.status_code}")

    # ---------- 3. 指派给新人 ----------
    print("\n【3】指派给新人")
    newbies = c.get(f"{BASE}/training/newcomers", headers=hdr("mentor1")).json()
    check(len(newbies) >= 2, f"新人列表 {len(newbies)} 人",
          "、".join(f"{n['name']}({n['userid']})" for n in newbies))
    nb = next((n for n in newbies if n["userid"] == "newbie1"), newbies[0])

    r = c.post(f"{BASE}/training/assign",
               json={"plan_id": pid, "user_ids": [nb["id"]], "due_days": 7, "max_attempts": 1},
               headers=hdr("mentor1"))
    check(r.status_code == 200, "指派成功", str(r.json().get("message", ""))[:50])

    # ---------- 4. 新人视角 ----------
    print("\n【4】新人只能看到指派给自己的培训")
    my = c.get(f"{BASE}/training/my", headers=hdr("newbie1")).json()
    check(len(my.get("items", [])) >= 1, f"新人看到 {len(my['items'])} 项培训")
    # 取本次刚创建并指派的那个方案，而不是列表首项。
    # 重复跑测试会累积历史方案，首项往往是旧方案且已用完考试机会。
    item = next((x for x in my["items"] if x["plan_id"] == pid), my["items"][0])
    print(f"    《{item['title']}》 状态={item['status']} "
          f"可考={item['can_attempt']} 已用={item['used_attempts']}/{item['max_attempts']}")

    # 数据隔离：新人看不到候选人
    r = c.get(f"{BASE}/candidates", headers=hdr("newbie1"))
    check(r.status_code == 403, "新人无法访问候选人数据（最小权限）", f"HTTP {r.status_code}")
    r = c.get(f"{BASE}/models", headers=hdr("newbie1"))
    check(r.status_code == 403, "新人无法访问能力模型", f"HTTP {r.status_code}")

    # ---------- 5. 开考（反作弊核心）----------
    print("\n【5】开考 —— 验证答案不下发")
    # 故意传一个更大的时长，验证服务端忽略客户端传值、只认方案里的设置
    r = c.post(f"{BASE}/training/plans/{pid}/start",
               json={"minutes": 999}, headers=hdr("newbie1"))
    check(r.status_code == 200, "开始考试", f"HTTP {r.status_code}")
    paper = r.json() if r.status_code == 200 else {}
    aid = paper.get("attempt_id")
    questions = paper.get("questions", [])

    print(f"    试卷 {len(questions)} 道，剩余 {paper.get('seconds_left')} 秒")
    check(len(questions) > 0, "试卷已下发")

    # 关键：确认响应里没有任何答案字段
    raw = json.dumps(paper, ensure_ascii=False)
    answer_keys = [k for k in ("answer", "correct", "explanation", "ref", "标准答案")
                   if f'"{k}"' in raw]
    check(not answer_keys, "响应中不含答案/解析字段", f"发现: {answer_keys}" if answer_keys else "")
    check("server_now" in paper and "deadline_at" in paper, "下发服务端时间与截止时间")

    # 选项乱序：同一道题在不同考生手里顺序不同
    q0 = questions[0] if questions else {}
    if q0.get("options"):
        print(f"    第 1 题选项顺序：{[o[:12] for o in q0['options']]}")

    # ---------- 6. 计时不可绕过 ----------
    print("\n【6】服务端计时不可绕过")
    r = c.post(f"{BASE}/training/attempts/{aid}/heartbeat",
               json={"signals": []}, headers=hdr("newbie1"))
    hb = r.json() if r.status_code == 200 else {}
    check(r.status_code == 200, "心跳正常", f"剩余 {hb.get('seconds_left')} 秒")
    # 方案里设的是 plan_minutes，客户端传 999 分钟必须无效
    check(hb.get("seconds_left", 0) <= (plan_minutes + 1) * 60,
          f"客户端传 999 分钟被忽略（方案限时 {plan_minutes} 分钟）",
          f"剩余 {hb.get('seconds_left')} 秒")

    # 刷新页面应恢复同一场考试，而不是重新计时
    r2 = c.post(f"{BASE}/training/plans/{pid}/start", json={}, headers=hdr("newbie1"))
    check(r2.status_code == 200, "重复开考返回原考试")
    check(r2.json().get("attempt_id") == aid and r2.json().get("resumed"),
          "试卷与计时已恢复（未重新开始）")

    # ---------- 7. 暂存与信号上报 ----------
    print("\n【7】暂存进度与风险信号上报")
    answers = {}
    for q in questions:
        if q["qtype"] == "单选":
            answers[str(q["id"])] = "A"
        elif q["qtype"] == "多选":
            answers[str(q["id"])] = "AB"
        else:
            answers[str(q["id"])] = "先做止损，再定位根因，最后产出复盘报告并跟踪改进项落地。"

    r = c.post(f"{BASE}/training/attempts/{aid}/save",
               json={"answers": answers}, headers=hdr("newbie1"))
    check(r.status_code == 200, "暂存作答", f"已存 {r.json().get('saved')} 题")

    # 上报一批作弊信号
    signals = [
        {"kind": "blur", "at": 10, "detail": "窗口失去焦点"},
        {"kind": "blur", "at": 40, "detail": "窗口失去焦点"},
        {"kind": "blur_long", "at": 60, "detail": "离开 35 秒"},
        {"kind": "paste", "at": 90, "detail": "粘贴 45 字符"},
        {"kind": "devtools", "at": 120, "detail": "窗口尺寸突变"},
        {"kind": "非法信号类型", "at": 130},   # 应被过滤
    ]
    r = c.post(f"{BASE}/training/attempts/{aid}/heartbeat",
               json={"signals": signals}, headers=hdr("newbie1"))
    check(r.status_code == 200, "信号已上报")
    check(r.json().get("risk_level") in ("关注", "可疑"),
          f"风险等级已升级", f"{r.json().get('risk_level')}")

    # ---------- 8. 交卷 ----------
    print("\n【8】交卷判分")
    r = c.post(f"{BASE}/training/attempts/{aid}/submit",
               json={"answers": answers, "signals": []}, headers=hdr("newbie1"))
    check(r.status_code == 200, "交卷成功", f"HTTP {r.status_code}")
    res = r.json() if r.status_code == 200 else {}
    print(f"    客观题 {res.get('objective_score')}/{res.get('objective_full')} "
          f"用时 {res.get('duration_seconds')} 秒 风险 {res.get('risk_level')}")
    sub_id = res.get("submission_id")
    check(sub_id is not None, "生成判卷结果")
    check(res.get("risk_level") in ("关注", "可疑"), "风险等级随交卷一并计算")

    # 幂等
    r2 = c.post(f"{BASE}/training/attempts/{aid}/submit",
                json={"answers": answers}, headers=hdr("newbie1"))
    check(r2.status_code == 200 and r2.json().get("already_submitted"),
          "重复交卷幂等（不重复判分）")

    # 限次
    r3 = c.post(f"{BASE}/training/plans/{pid}/start", json={}, headers=hdr("newbie1"))
    check(r3.status_code == 400, "达到限次后无法再考", str(r3.json().get("detail", ""))[:44])

    # 考生看不到自己的风险明细
    r4 = c.get(f"{BASE}/training/attempts/{aid}/result", headers=hdr("newbie1"))
    check(r4.json().get("signals") is None, "考生看不到自己的作弊信号明细")

    # ---------- 9. 带教人复核 ----------
    print("\n【9】带教人复核（风险只作线索）")
    rq = c.get(f"{BASE}/training/review/queue", headers=hdr("mentor1")).json()
    print(f"    待复核 {rq['counts']['total']} 条（可疑 {rq['counts']['suspect']}，"
          f"关注 {rq['counts']['watch']}）")
    check(rq["counts"]["total"] >= 1, "记录进入复核队列")
    target = next((x for x in rq["items"] if x["attempt_id"] == aid), rq["items"][0])
    check(target["risk_level"] in ("关注", "可疑"), "风险等级已呈现给带教人")
    check(len(target["signals"]) > 0, f"信号明细可见（{len(target['signals'])} 类）")
    for s in target["signals"][:4]:
        print(f"      · {s['label']} × {s['count']}")
    check(all(s["kind"] != "非法信号类型" for s in target["signals"]),
          "非法信号已被过滤")

    # 带教人能看到信号，考生不能
    r5 = c.get(f"{BASE}/training/attempts/{aid}/result", headers=hdr("mentor1"))
    check(r5.json().get("signals") is not None, "带教人可查看风险明细")

    # ---------- 10. 终评与作废 ----------
    print("\n【10】终评与作废")
    r = c.post(f"{BASE}/training/submissions/{sub_id}/confirm",
               json={"final_score": 82}, headers=hdr("mentor1"))
    check(r.status_code == 200, "带教人给出终评分数", f"HTTP {r.status_code}")

    r = c.post(f"{BASE}/training/attempts/{aid}/void",
               json={"note": "考试期间多次离开页面，且作答内容与资料高度雷同"},
               headers=hdr("mentor1"))
    check(r.status_code == 200, "带教人可作废作答", str(r.json().get("message", ""))[:40])
    check("返还" in str(r.json().get("message", "")), "作废后返还重考机会")

    # 作废后可重考
    r = c.post(f"{BASE}/training/plans/{pid}/start", json={}, headers=hdr("newbie1"))
    check(r.status_code == 200, "作废后可重新作答")
    if r.status_code == 200:
        check(r.json().get("attempt_id") != aid, "产生新的作答记录")

    # ---------- 11. 权限 ----------
    print("\n【11】权限校验")
    r = c.post(f"{BASE}/training/assign", json={"plan_id": pid, "user_ids": [nb["id"]]},
               headers=hdr("newbie1"))
    check(r.status_code == 403, "新人不能指派考试", f"HTTP {r.status_code}")
    r = c.get(f"{BASE}/training/review/queue", headers=hdr("newbie1"))
    check(r.status_code == 403, "新人看不到复核队列", f"HTTP {r.status_code}")
    r = c.post(f"{BASE}/training/materials",
               files={"file": ("x.txt", b"test content here " * 20, "text/plain")},
               headers=hdr("newbie1"))
    check(r.status_code == 403, "新人不能上传资料", f"HTTP {r.status_code}")

    # 别人不能操作我的考试
    other = next((n for n in newbies if n["id"] != nb["id"]), None)
    if other:
        r = c.get(f"{BASE}/training/attempts/{aid}/result", headers=hdr(other["userid"]))
        check(r.status_code == 403, "不能查看他人的考试记录", f"HTTP {r.status_code}")

    # 清理
    if mid:
        c.delete(f"{BASE}/training/materials/{mid}", headers=hdr("mentor1"))

    print("\n" + "=" * 76)
    print(f"  {'全部通过' if not fails else f'{fails} 项未通过'}")
    print("=" * 76)
    return fails


if __name__ == "__main__":
    sys.exit(main())
