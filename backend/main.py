"""AI 招聘与人才发展平台 · 后端服务。

启动顺序：
1. 建表
2. 灌种子数据（用户、能力模型、配置、知识库、提示词、历史样本）
3. 加载知识库索引与提示词库
4. 启动候选人调度器（待定池到期归档）
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.api.v1 import (  # noqa: E402
    agent_api, auth_api, board_api, business_line_api, candidates_api, exam_api,
    interview_api, invite_api, models_api, training_api,
)
from app.core.config import settings  # noqa: E402
from app.db.session import init_db  # noqa: E402
from app.llm.registry import registry  # noqa: E402
from app.prompts.manager import prompts  # noqa: E402

_tasks: list[asyncio.Task] = []


async def _pool_scheduler() -> None:
    """后台维护任务，每 5 分钟一轮：

    1. 待定池到期自动归档（R-07）。归档不发送任何对外通知，不等于拒绝。
    2. 兜底扫描：把卡在「待解析 / 待打分」的候选人重新入队。
       并发写库冲突、进程重启都可能让某条流水线中断，不处理的话这些记录会
       永远停在中间状态，而用户在界面上看不到任何错误。
    """
    from datetime import datetime, timedelta

    from sqlalchemy import select

    from app.db.models import Candidate, DecisionLog, ResumeText
    from app.db.session import SessionLocal

    while True:
        try:
            revivable: list[tuple[int, str]] = []
            async with SessionLocal() as db:
                rows = (await db.execute(select(Candidate).where(
                    Candidate.status == "待定池"))).scalars().all()
                expired = []
                remind = []
                for c in rows:
                    left = c.pool_days_left
                    if left is None:
                        continue
                    if left <= 0:
                        c.status = "已归档"
                        c.archived_at = datetime.now()
                        expired.append(c)
                    elif left <= 1:
                        remind.append(c)
                for c in expired:
                    db.add(DecisionLog(
                        kind="ai", actor="system", ability="待定池归档",
                        candidate_id=c.id,
                        summary=f"候选人 {c.name} 待定池停留满 {c.pool_days} 天，已自动归档。"
                                f"归档不触发任何对外通知，不等于拒绝。",
                    ))
                # 到期前一天向负责 HR 发送汇总提醒
                for c in remind:
                    db.add(DecisionLog(
                        kind="ai", actor="system", ability="待定池提醒",
                        candidate_id=c.id,
                        summary=f"候选人 {c.name} 待定池剩余 {c.pool_days_left} 天，"
                                f"明日到期归档，请及时决定是否捞回。",
                    ))

                # 兜底只捞「有简历正文、且 5 分钟没更新」的记录，
                # 避免把正在处理中的任务重复入队。
                stuck = (await db.execute(select(Candidate).where(
                    Candidate.status.in_(["待解析", "待打分"]),
                    Candidate.updated_at < datetime.now() - timedelta(minutes=5),
                ))).scalars().all()
                for c in stuck:
                    rt = (await db.execute(select(ResumeText).where(
                        ResumeText.candidate_id == c.id))).scalar_one_or_none()
                    if rt and rt.raw_text:
                        revivable.append((c.id, rt.raw_text))

                if expired or remind:
                    await db.commit()

            for cid, text in revivable:
                print(f"[兜底] 重新入队候选人 {cid}（此前卡在中间状态）")
                asyncio.create_task(_rerun_pipeline(cid, text))
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(300)


async def _rerun_pipeline(candidate_id: int, text: str) -> None:
    from app.api.v1.candidates_api import _run_pipeline
    try:
        await _run_pipeline(candidate_id, text)
    except Exception:  # noqa: BLE001
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    from app.db.seed import ensure_seeded
    await ensure_seeded()

    from app.rag.retriever import load_knowledge_from_db
    chunks = await load_knowledge_from_db()

    n_prompts = await prompts.load()

    registry.load(force=True)

    task = asyncio.create_task(_pool_scheduler())
    _tasks.append(task)

    print("=" * 66)
    print("  AI 招聘与人才发展平台 · 后端已启动")
    print("=" * 66)
    print(f"  模型网关      : {'已连接' if settings.llm_enabled else '未配置（走降级模式）'}")
    if settings.llm_enabled:
        route = registry.snapshot()
        print(f"  自动选型      : 小={route.get('small')}")
        print(f"                  中={route.get('medium')}")
        print(f"                  大={route.get('large')}")
    print(f"  知识库索引    : {chunks} 个片段")
    print(f"  提示词版本    : {n_prompts} 条")
    print(f"  接口文档      : http://127.0.0.1:8000/docs")
    print("=" * 66)
    yield

    for t in _tasks:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t


app = FastAPI(
    title="AI 招聘与人才发展平台",
    description=(
        "以岗位能力模型为统一底座，在简历筛选、面试提问、培训考核三个环节"
        "提供带证据的判断建议，人保留最终决策权。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins + ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth_api.router, models_api.router, candidates_api.router,
          interview_api.router, training_api.router, exam_api.router,
          board_api.router, agent_api.router, business_line_api.router,
          invite_api.router):
    app.include_router(r, prefix="/api")


# ---------- 托管前端构建产物 ----------
# 前端构建后由后端一并提供，这样运行时只需要 Python 一个进程：
# 解压 → 安装依赖 → 双击一个脚本 → 打开浏览器，不需要另外开 Node 服务。
# 开发时仍可用 npm run dev，走 Vite 代理访问后端。
# main.py 在 backend/ 下，构建产物在项目根的 frontend/dist
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(str(_DIST / "index.html"))

    @app.get("/favicon.ico")
    async def favicon():
        f = _DIST / "favicon.ico"
        if f.exists():
            return FileResponse(str(f))
        return Response(status_code=204)
else:
    @app.get("/")
    async def root():
        """前端尚未构建时给出说明，而不是一个 404。"""
        return {
            "name": "AI 招聘与人才发展平台",
            "version": "1.0.0",
            "docs": "/docs",
            "frontend": "未构建。请先运行 install.bat，或在 frontend 目录执行 npm run build。",
            "modules": ["M1 岗位能力模型", "M2 简历筛选", "M3 面试助手",
                        "M4 培训考核", "M5 数据回流与效果看板"],
        }


@app.get("/api/health")
async def health():
    return {"status": "ok", "llm": settings.llm_enabled}


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception):  # noqa: ANN001
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {str(exc)[:300]}"},
    )
