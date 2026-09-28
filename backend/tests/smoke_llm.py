"""冒烟测试：验证网关连通、自动选型、三段结构校验、降级标记。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel  # noqa: E402

from app.core.schemas import AIResult  # noqa: E402
from app.llm.client import llm  # noqa: E402
from app.llm.registry import registry  # noqa: E402


class Demo(BaseModel):
    skill: str
    years: int
    summary: str


async def main() -> None:
    print("=" * 60)
    models = registry.load(force=True)
    print(f"[1] 网关可选文本模型: {len(models)} 个")

    print("[2] 自动选型结果：")
    for tier in ("small", "medium", "large"):
        mid, reason = registry.pick(tier)  # type: ignore[arg-type]
        print(f"    {tier:8s} -> {mid:36s} ({reason})")

    print("[3] 小模型真实调用测试：")
    res: AIResult[Demo] = await llm.complete_json(
        ability="smoke_test",
        prompt_id="smoke",
        prompt_version="v1",
        system="你是结构化抽取器，只输出 JSON，不要任何解释文字。",
        user='从这句话里抽字段：「候选人张伟有 8 年 Java 后端经验，擅长分布式系统。」'
             '输出 {"skill": "...", "years": 数字, "summary": "..."}',
        schema=Demo,
        tier="small",
        max_tokens=300,
    )
    if res.ok and res.result:
        print(f"    OK  结果体 = {res.result.model_dump()}")
    else:
        print(f"    FAIL {res.error}")
    m = res.meta
    print(f"    元信息: model={m.model} latency={m.latency_ms}ms "
          f"tokens={m.tokens_in}/{m.tokens_out} cost=¥{m.cost_cny} degrade={m.degrade.value}")

    print("[4] 大模型真实调用测试（打分档）：")
    res2 = await llm.complete_json(
        ability="smoke_large",
        prompt_id="smoke",
        prompt_version="v1",
        system="你是招聘评估助手，只输出 JSON。",
        user='判断候选人是否匹配岗位。输出 {"skill": "总体评价", "years": 0, "summary": "一句话结论"}',
        schema=Demo,
        tier="large",
        max_tokens=300,
    )
    print(f"    ok={res2.ok} model={res2.meta.model} latency={res2.meta.latency_ms}ms"
          f" cost=¥{res2.meta.cost_cny}")

    print("[5] 用量统计：")
    print("   ", llm.usage_stats())


if __name__ == "__main__":
    asyncio.run(main())
