# -*- coding: utf-8 -*-
"""面试安排、候选人邀请与业务线字典的接口测试。

覆盖三个此前的缺陷：
  1. 安排面试时无法指定面试官，缺省静默挂到第一个面试官名下；
  2. 业务线是写死在前端的列表，无法增删，且可随意填写导致隔离失效；
  3. 视频面试没有任何入口，候选人和面试官都不知道从哪里进。

运行前后端须在 127.0.0.1:8000。测试自己创建、自己清理，不留下数据。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

import httpx

BASE = "http://127.0.0.1:8000/api"
c = httpx.Client(timeout=60)
fails = 0
_TOK: dict[str, dict] = {}


def hdr(uid: str) -> dict:
    if uid not in _TOK:
        r = c.post(f"{BASE}/auth/login", json={"userid": uid, "password": "123456"})
        _TOK[uid] = {"Authorization": "Bearer " + r.json()["token"]}
    return _TOK[uid]


def check(cond: bool, msg: str, detail: str = "") -> None:
    global fails
    if not cond:
        fails += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}" + (f"  {detail}" if detail else ""))


def when(days: int = 2, hour: int = 10) -> str:
    d = (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=0)
    return d.strftime("%Y-%m-%dT%H:%M")


def main() -> int:
    print("=" * 70)
    print("  面试安排 · 候选人邀请 · 业务线字典")
    print("=" * 70)

    # ---------- 1. 业务线字典 ----------
    print("\n【1】业务线字典")
    pub = c.get(f"{BASE}/business-lines")
    check(pub.status_code == 200, "业务线列表免登录可读（注册页要用）")
    names = [b["name"] for b in pub.json()]
    check({"电商业务线", "供应链业务线"} <= set(names), "默认业务线已就位", " / ".join(names))
    check(c.get(f"{BASE}/business-lines/manage", headers=hdr("interviewer1")).status_code == 403,
          "面试官不能管理业务线")

    r = c.post(f"{BASE}/business-lines", headers=hdr("admin"), json={"name": "测试临时线"})
    check(r.status_code == 200, "超管可新增业务线")
    lid = r.json().get("id")
    check(c.post(f"{BASE}/business-lines", headers=hdr("admin"),
                 json={"name": "测试临时线"}).status_code == 400, "重名被拒")

    r = c.post(f"{BASE}/auth/users", headers=hdr("admin"), json={
        "userid": "t_line_user", "name": "业务线测试", "password": "123456",
        "role": "用人经理", "business_line": "测试临时线"})
    check(r.status_code == 200, "可选新增的业务线建号")
    check(c.post(f"{BASE}/auth/users", headers=hdr("admin"), json={
        "userid": "t_bad", "name": "x", "password": "123456", "role": "招聘HR",
        "business_line": "不存在的线"}).status_code == 400, "不在字典里的业务线被拒")

    r = c.put(f"{BASE}/business-lines/{lid}", headers=hdr("admin"), json={"name": "测试改名线"})
    check(r.status_code == 200, "改名成功", "；".join(r.json().get("changes", [])))
    users = c.get(f"{BASE}/auth/users", headers=hdr("admin")).json()["users"]
    u = next(x for x in users if x["userid"] == "t_line_user")
    check(u["business_line"] == "测试改名线", "改名级联到账号（隔离不断）")

    r = c.put(f"{BASE}/auth/users/t_line_user", headers=hdr("admin"), json={"business_line": "职能线"})
    check(r.status_code == 200 and r.json()["user"]["business_line"] == "职能线", "可编辑账号业务线")
    c.put(f"{BASE}/auth/users/t_line_user", headers=hdr("admin"), json={"business_line": "测试改名线"})

    r = c.delete(f"{BASE}/business-lines/{lid}", headers=hdr("admin"))
    check(r.status_code == 400 and "迁移" in r.json()["detail"], "在用的业务线不能直接删")
    r = c.delete(f"{BASE}/business-lines/{lid}", params={"migrate_to": "通用"}, headers=hdr("admin"))
    check(r.status_code == 200 and r.json()["moved"] >= 1, "指定迁移目标后可删", f"迁移 {r.json().get('moved')} 条")
    users = c.get(f"{BASE}/auth/users", headers=hdr("admin")).json()["users"]
    check(next(x for x in users if x["userid"] == "t_line_user")["business_line"] == "通用",
          "被删业务线的数据已迁移")
    c.delete(f"{BASE}/auth/users/t_line_user", headers=hdr("admin"))

    # ---------- 2. 指派面试官 ----------
    print("\n【2】安排面试与指派面试官")
    items = c.get(f"{BASE}/candidates", headers=hdr("hr1")).json()["items"]
    cand = next((x for x in items if x["status"] in ("待安排面试", "待复核")
                 and not x["name"].startswith("未识别")), None)
    if not cand:
        print("  没有可安排的候选人，跳过（先运行 test_integration.py 导入样例简历）")
        return 1
    cid = cand["id"]

    iv = c.get(f"{BASE}/interview/interviewers", params={"candidate_id": cid}, headers=hdr("hr1")).json()
    people = iv["items"]
    check(len(people) >= 2, f"可指派 {len(people)} 人", " / ".join(f"{p['name']}·{p['match']}" for p in people[:4]))
    names = [p["name"] for p in people]
    check(len(set(names)) == len(names) or "业务面试官" not in names,
          "面试官显示名可区分（不再都叫「业务面试官」）")
    check(people[0]["match"] == "同业务线", "同业务线的排在最前", people[0]["name"])
    check(c.get(f"{BASE}/interview/interviewers", headers=hdr("interviewer1")).status_code == 403,
          "面试官不能指派他人")

    i1 = next(p for p in people if p["userid"] == "interviewer1")
    i2 = next(p for p in people if p["userid"] == "interviewer2")
    base = {"candidate_id": cid, "round_name": "终面", "plan_minutes": 45, "scheduled_at": when()}

    r = c.post(f"{BASE}/interview/schedules", headers=hdr("hr1"), json=base)
    check(r.status_code == 400 and "面试官" in r.json()["detail"], "不指定面试官被拒（不再静默缺省）")
    r = c.post(f"{BASE}/interview/schedules", headers=hdr("hr1"), json={**base, "interviewer_id": i1["id"]})
    check(r.status_code == 400 and "会议链接" in r.json()["detail"], "视频面试必须填会议链接")
    r = c.post(f"{BASE}/interview/schedules", headers=hdr("hr1"), json={
        **base, "interviewer_id": i1["id"], "meeting_url": "javascript:alert(document.cookie)"})
    check(r.status_code == 400, "拒绝 javascript: 链接（会渲染到候选人页面）")
    r = c.post(f"{BASE}/interview/schedules", headers=hdr("hr1"), json={
        **base, "interviewer_id": i1["id"], "mode": "现场"})
    check(r.status_code == 400 and "地点" in r.json()["detail"], "现场面试必须填地点")
    r = c.post(f"{BASE}/interview/schedules", headers=hdr("hr1"),
               json={**base, "interviewer_id": i1["id"], "scheduled_at": when(days=-5)})
    check(r.status_code == 400, "面试时间早于昨天被拒")

    r = c.post(f"{BASE}/interview/schedules", headers=hdr("hr1"), json={
        **base, "interviewer_id": i1["id"], "mode": "视频",
        "meeting_url": "https://meeting.tencent.com/dm/test-sched", "meeting_code": "111 222 333"})
    check(r.status_code == 200, "安排视频面试成功")
    sid, token = r.json()["schedule_id"], r.json()["invite_token"]
    check(len(token) >= 40, "生成随机邀请令牌", f"{len(token)} 位")

    r = c.post(f"{BASE}/interview/schedules", headers=hdr("hr1"), json={
        **base, "interviewer_id": i2["id"], "mode": "电话"})
    check(r.status_code == 400 and "重复" in r.json()["detail"], "同一轮次不能重复安排")

    check(c.post(f"{BASE}/interview/schedules", headers=hdr("interviewer1"),
                 json=base).status_code == 403, "面试官无权安排面试")

    mine = c.get(f"{BASE}/interview/schedules", headers=hdr("interviewer1")).json()
    row = next((s for s in mine if s["id"] == sid), None)
    check(row is not None, "被指派的面试官能看到这场")
    check(row and row["meeting_url"].startswith("https://"), "面试官拿得到会议链接（用自己账号登录后入会）")
    check(row and not row["invite_token"], "面试官拿不到候选人令牌（不能替候选人点同意）")
    check(all(s["id"] != sid for s in c.get(f"{BASE}/interview/schedules",
                                          headers=hdr("interviewer2")).json()),
          "未被指派的面试官看不到")

    # 改派
    r = c.put(f"{BASE}/interview/schedules/{sid}", headers=hdr("hr1"), json={"interviewer_id": i2["id"]})
    check(r.status_code == 200 and r.json()["changes"], "改派成功", "；".join(r.json().get("changes", [])))
    check(any(s["id"] == sid for s in c.get(f"{BASE}/interview/schedules",
                                          headers=hdr("interviewer2")).json()), "改派后新面试官可见")
    check(all(s["id"] != sid for s in c.get(f"{BASE}/interview/schedules",
                                          headers=hdr("interviewer1")).json()), "改派后原面试官不可见")

    # ---------- 3. 候选人邀请 ----------
    print("\n【3】候选人邀请（免登录）")
    r = c.get(f"{BASE}/public/invite/{token}")
    check(r.status_code == 200, "候选人无需账号即可打开")
    d = r.json()
    check(d["mode"] == "视频" and d["meeting_url"].startswith("https://"), "拿得到视频会议入口")
    leaked = [k for k in ("interviewer", "interviewer_id", "questions", "total_score",
                          "resume_text", "phone", "email", "tier") if k in d]
    check(not leaked, "不泄露面试官、题目、评分、简历与联系方式", str(leaked or ""))
    check(c.get(f"{BASE}/public/invite/{'A' * 43}").status_code == 404, "伪造令牌 404")
    check(c.get(f"{BASE}/public/invite/short").status_code == 404, "过短令牌 404")

    r = c.post(f"{BASE}/public/invite/{token}/consent", json={"agree": "yes"})
    check(r.status_code == 400, "同意参数必须是布尔值")
    r = c.post(f"{BASE}/public/invite/{token}/consent", json={"agree": False})
    check(r.json().get("consent_status") == "不同意", "候选人可选择不同意")
    # 候选人明确不同意时，面试官不能在报告页勾选覆盖
    r = c.post(f"{BASE}/interview/{sid}/report", headers=hdr("hr1"),
               json={"transcript": "面试官：请介绍一下。候选人：好的。", "consent_recorded": True})
    check(r.status_code == 400 and "不同意" in r.json().get("detail", ""),
          "候选人不同意时，面试官不能勾选覆盖")
    r = c.post(f"{BASE}/public/invite/{token}/consent", json={"agree": True})
    check(r.json().get("consent_status") == "同意", "开始前可改为同意")
    row = next(s for s in c.get(f"{BASE}/interview/schedules", headers=hdr("hr1")).json() if s["id"] == sid)
    check(row["consent_status"] == "同意" and row["consent_recorded"], "HR 与面试官端同步看到")

    r = c.post(f"{BASE}/interview/schedules/{sid}/invite/reset", headers=hdr("hr1"))
    new_token = r.json()["invite_token"]
    check(new_token != token, "可重新生成邀请链接")
    check(c.get(f"{BASE}/public/invite/{token}").status_code == 404, "旧链接立即失效")
    check(c.get(f"{BASE}/public/invite/{new_token}").status_code == 200, "新链接可用")

    # ---------- 4. 取消 ----------
    print("\n【4】取消面试")
    check(c.post(f"{BASE}/interview/schedules/{sid}/cancel", headers=hdr("hr1"),
                 json={}).status_code == 400, "取消必须填原因")
    r = c.post(f"{BASE}/interview/schedules/{sid}/cancel", headers=hdr("hr1"), json={"reason": "自动化测试清理"})
    check(r.status_code == 200, "取消成功")
    check(c.get(f"{BASE}/public/invite/{new_token}").status_code == 404, "取消后候选人链接失效")
    check(all(s["id"] != sid for s in c.get(f"{BASE}/interview/schedules", headers=hdr("hr1")).json()),
          "默认列表不再显示已取消场次")
    check(any(s["id"] == sid for s in c.get(f"{BASE}/interview/schedules", params={"status": "已取消"},
                                          headers=hdr("hr1")).json()), "按「已取消」可查到")

    print("\n" + "=" * 70)
    print(f"  测试完成：{'全部通过' if not fails else f'{fails} 项未通过'}")
    print("=" * 70)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
