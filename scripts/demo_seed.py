"""一键灌入演示数据。

跑完后工作台会呈现完整的分层流转状态，便于直接观察各页面：
  高分档 -> 待安排面试
  中间档 -> 待复核
  低分档 -> 待定池
  解析异常 -> 异常队列

用法：
    F:\\project\\.venv\\Scripts\\python.exe scripts\\demo_seed.py
"""
from __future__ import annotations

import asyncio
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8000/api"
HDR: dict[str, str] = {}   # 登录后填入 Authorization


def login(userid: str = "admin", password: str = "123456") -> dict:
    """用账号登录并返回鉴权头。脚本需要 HR 权限来导入与复核。"""
    global HDR
    r = httpx.post(f"{BASE}/auth/login", json={"userid": userid, "password": password}, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"登录失败（{r.status_code}）：{r.json().get('detail', r.text[:120])}")
    HDR = {"Authorization": f"Bearer {r.json()['token']}"}
    return r.json()["user"]


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app.services.sample_data import SAMPLE_RESUMES

    c = httpx.Client(timeout=300)
    me = login()
    print(f"已登录：{me['name']}（{me['role']}）")
    print()

    # 先清掉旧的演示数据，避免重复叠加
    try:
        c.post(f"{BASE}/system/reset-demo", headers=HDR)
        print("已清空旧数据")
    except Exception:
        pass

    positions = c.get(f"{BASE}/models/positions", headers=HDR).json()
    by_name = {p["name"]: p["id"] for p in positions}

    print(f"准备灌入 {len(SAMPLE_RESUMES)} 份简历...\n")
    created = []
    for s in SAMPLE_RESUMES:
        pos_id = by_name.get(s["position_name"])
        if not pos_id:
            continue
        fname = f"{s['name']}-{s['position_name']}.txt"
        try:
            r = c.post(
                f"{BASE}/candidates/import",
                files={"file": (fname, io.BytesIO(s["resume_text"].encode("utf-8")), "text/plain")},
                data={"position_id": str(pos_id), "name": s["name"], "source": "ATS推送"},
                headers=HDR,
            ).json()
            created.append((r.get("candidate_id"), s["name"], s["position_name"]))
            print(f"  已接收  {s['name']:5s}  {s['position_name']}")
        except Exception as e:  # noqa: BLE001
            print(f"  失败    {s['name']}: {type(e).__name__}")

    print(f"\n共 {len(created)} 份，等待后台解析打分...")
    # 等待条件：状态脱离「待解析/待打分」即为处理完（成功或明确失败都算完）
    deadline = time.time() + 420
    while time.time() < deadline:
        time.sleep(4)
        states = []
        for cid, _n, _p in created:
            d = c.get(f"{BASE}/candidates/{cid}", headers=HDR).json()
            states.append(d.get("status"))
        done = sum(1 for s in states if s not in ("待解析", "待打分"))
        left = [s for s in states if s in ("待解析", "待打分")]
        print(f"  进度 {done}/{len(created)}" + (f"  处理中 {len(left)}" if left else "  完成   "), end="\r")
        if done == len(created):
            break
    print()

    print("\n打分结果：")
    print(f"  {'姓名':<8}{'岗位':<18}{'总分':>7}  {'分档':<8}{'状态':<12}{'证据':>5}")
    print("  " + "-" * 62)
    for cid, _n, _p in created:
        d = c.get(f"{BASE}/candidates/{cid}", headers=HDR).json()
        ev = sum(len(a.get("evidence", [])) for a in d.get("abilities", []))
        print(f"  {d['name']:<8}{d['position_name']:<18}{d['total_score']:>7}  "
              f"{d['tier'] or '—':<8}{d['status']:<12}{ev:>5}")

    # 把中间档的采纳掉几个，让「面试日程」有内容。
    # 注意：必须重新查询当前状态，不能用等待循环之前算出的列表 ——
    # 那时部分候选人还没跑完流水线，拿旧数据会把未打分的记录推进面试。
    print("\n为部分候选人安排面试...")
    interview_ready, skipped = [], []
    for cid, name, _p in created:
        d = c.get(f"{BASE}/candidates/{cid}", headers=HDR).json()
        if d.get("status") == "待复核" and d.get("score"):
            interview_ready.append(cid)
        elif d.get("status") in ("待解析", "待打分"):
            skipped.append(name)

    scheduled = 0
    for cid in interview_ready[:3]:
        r = c.post(f"{BASE}/candidates/{cid}/review", json={"action": "采纳"}, headers=HDR)
        if r.status_code != 200:
            print(f"  采纳失败 #{cid}: {r.json().get('detail', '')[:50]}")
            continue
        c.post(f"{BASE}/interview/schedules",
               json={"candidate_id": cid, "round_name": "初面", "plan_minutes": 30}, headers=HDR)
        scheduled += 1
    print(f"  已安排 {scheduled} 场面试")
    if skipped:
        print(f"  [注意] 以下候选人仍在处理中，未安排面试：{'、'.join(skipped)}")

    # 造一条解析异常，便于观察兜底矩阵
    try:
        c.post(f"{BASE}/candidates/import",
               files={"file": ("损坏文件.pdf", io.BytesIO(b"\x00\x01\x02\x03not a pdf"), "application/pdf")},
               data={"position_id": str(list(by_name.values())[0]), "name": "示例-文件损坏"},
               headers=HDR)
        print("  已生成 1 条解析异常记录")
    except Exception:
        pass

    ov = c.get(f"{BASE}/board/overview", headers=HDR).json()
    print("\n当前看板概览：")
    print(f"  候选人总数 {ov['total_candidates']}")
    print(f"  分层分布   {ov['tier_counts']}")
    print(f"  状态分布   {ov['status_counts']}")
    print(f"  平均成本   ¥{ov['cost']['avg_cost_cny']}/份")

    print("\n完成。打开 http://127.0.0.1:5173 查看。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
