"""提示词运行时管理（PRD 4.4）。

职责：
- 按 ability 解析当前生效的提示词版本（含灰度分流）。
- 渲染 user 模板，并把渲染后的完整提示词落进决策日志，保证可追溯。
- 提供离线回归卡点的判定入口：核心指标下降超过 2 个百分点即阻止上线。

提示词从数据库读，数据库没有则回落到代码里的种子版本 —— 这样冷启动不用先跑一遍初始化。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.prompts.library import SEED_PROMPTS


@dataclass
class ResolvedPrompt:
    prompt_id: str
    version: str
    system_prompt: str
    user_prompt: str
    tier: str
    is_canary: bool = False


# 按 ability 名找 prompt_id
_BY_ABILITY: dict[str, str] = {}
for _p in SEED_PROMPTS:
    _BY_ABILITY.setdefault(_p["ability"], _p["prompt_id"])


def prompt_id_for(ability: str) -> str:
    return _BY_ABILITY.get(ability, ability)


class PromptManager:
    def __init__(self) -> None:
        # prompt_id -> list[dict]，按 version 倒序
        self._versions: dict[str, list[dict]] = {}
        self._loaded = False

    async def load(self) -> int:
        from sqlalchemy import select

        from app.db.models import PromptVersion
        from app.db.session import SessionLocal

        async with SessionLocal() as db:
            rows = (await db.execute(select(PromptVersion))).scalars().all()
            table: dict[str, list[dict]] = {}
            for r in rows:
                table.setdefault(r.prompt_id, []).append({
                    "version": r.version, "system_prompt": r.system_prompt,
                    "user_template": r.user_template, "tier": r.tier,
                    "active": r.active, "canary_version": r.canary_version,
                    "canary_ratio": r.canary_ratio, "note": r.note,
                })
        for v in table.values():
            v.sort(key=lambda x: x["version"], reverse=True)
        self._versions = table
        self._loaded = True
        return sum(len(v) for v in table.values())

    def _pick_version(self, prompt_id: str, route_key: str) -> tuple[dict, bool]:
        """返回 (版本记录, 是否灰度命中)。route_key 用于稳定分流，同一候选人保持同一版本。"""
        rows = self._versions.get(prompt_id)
        if not rows:
            seed = next((p for p in SEED_PROMPTS if p["prompt_id"] == prompt_id), None)
            if not seed:
                raise KeyError(f"未找到提示词：{prompt_id}")
            return {
                "version": seed["version"], "system_prompt": seed["system_prompt"],
                "user_template": seed["user_template"], "tier": seed["tier"],
                "active": True, "canary_version": "", "canary_ratio": 0.0,
            }, False

        active = next((r for r in rows if r["active"]), rows[0])

        # 灰度期同一能力可并行两个版本，按流量比例分配
        if active.get("canary_version") and active.get("canary_ratio", 0) > 0:
            canary = next((r for r in rows if r["version"] == active["canary_version"]), None)
            if canary and self._bucket(route_key) < active["canary_ratio"]:
                return canary, True
        return active, False

    @staticmethod
    def _bucket(key: str) -> float:
        """稳定哈希分桶，保证同一 key 每次都落在同一侧。"""
        h = hashlib.md5(key.encode("utf-8")).hexdigest()
        return int(h[:8], 16) / 0xFFFFFFFF

    async def resolve(
        self,
        ability: str,
        variables: dict[str, str],
        route_key: str = "",
    ) -> ResolvedPrompt:
        prompt_id = prompt_id_for(ability)
        ver, canary = self._pick_version(prompt_id, route_key or ability + str(variables)[:80])
        user = ver["user_template"]
        for k, v in variables.items():
            user = user.replace("{" + k + "}", str(v))
        return ResolvedPrompt(
            prompt_id=prompt_id,
            version=ver["version"],
            system_prompt=ver["system_prompt"],
            user_prompt=user,
            tier=ver.get("tier", "medium") or "medium",
            is_canary=canary,
        )

    # ---------- 回归卡点 ----------
    @staticmethod
    def regression_blocked(baseline: dict[str, float], current: dict[str, float],
                           tolerance: float = 0.02) -> tuple[bool, list[str]]:
        """离线回归判定：任一核心指标下降超过 tolerance 即阻止上线。

        PRD 4.4：机制由发布流程强制卡点，非人工自觉。
        """
        blocked: list[str] = []
        for k, base in baseline.items():
            cur = current.get(k)
            if cur is None:
                continue
            # 一致率/准确率类越高越好；延迟类在别处校验
            if base - cur > tolerance:
                blocked.append(f"{k}: {base:.3f} -> {cur:.3f}（下降 {base - cur:.3f}）")
        return bool(blocked), blocked

    def list_prompts(self) -> list[dict]:
        out = []
        for pid, rows in self._versions.items():
            for r in rows:
                out.append({"prompt_id": pid, **r})
        if not out:
            for p in SEED_PROMPTS:
                out.append({
                    "prompt_id": p["prompt_id"], "version": p["version"],
                    "system_prompt": p["system_prompt"][:200] + "…",
                    "tier": p["tier"], "active": p["active"], "note": p["note"],
                    "canary_version": "", "canary_ratio": 0.0, "source": "代码种子",
                })
        return out


prompts = PromptManager()
