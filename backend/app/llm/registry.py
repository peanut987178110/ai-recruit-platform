"""模型注册表与自动选型。

运行时会向网关拉取可用模型列表，然后按「能力档位 -> 候选模型」打分挑选，
而不是把模型名硬编码在代码里。这样网关换一批模型时，平台不需要改代码。

选型考虑三件事：
1. 档位匹配：解析/格式化走小模型，打分/出题走大模型（PRD 5.2 成本约束）。
2. 可用性：候选模型必须在网关实际返回的列表里，否则降级到次优候选。
3. 成本与能力平衡：同档位内优先选「够用且便宜」的，用户可在 P-12 手动锁定。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

import httpx

from app.core.config import ModelTier, settings


@dataclass
class ModelInfo:
    id: str
    tier_affinity: dict[str, float] = field(default_factory=dict)
    context: int = 0
    note: str = ""

    @property
    def vendor(self) -> str:
        mid = self.id.lower()
        for key, name in (
            ("claude", "Anthropic"), ("gpt", "OpenAI"), ("gemini", "Google"),
            ("qwen", "阿里通义"), ("deepseek", "DeepSeek"), ("glm", "智谱"),
            ("kimi", "月之暗面"), ("minimax", "MiniMax"), ("grok", "xAI"),
            ("doubao", "字节豆包"), ("hunyuan", "腾讯混元"),
        ):
            if key in mid:
                return name
        return "其他"


# 模型家族特征：只描述「这个家族在哪个档位更合适」，不写死具体版本号。
# 新版本出现时，同一家族的打分自动继承，无需改代码。
_FAMILY_RULES: list[tuple[str, dict[str, float]]] = [
    # 旗舰推理档：适合打分、出题、主观判卷这类需要理解力的活儿
    (r"^claude-opus|opus-4-[6-9]|opus-5", {"large": 1.0, "medium": 0.75, "small": 0.25}),
    (r"^claude-sonnet", {"medium": 1.0, "large": 0.85, "small": 0.3}),
    (r"^claude-haiku", {"small": 1.0, "medium": 0.6, "large": 0.1}),
    (r"gpt-6|gpt-5\.6|gpt-5\.5-pro|gpt-5\.4-pro", {"large": 1.0, "medium": 0.7, "small": 0.2}),
    (r"gpt-5\.3|gpt-5\.4$|gpt-5\.1$|^gpt-5$", {"large": 0.9, "medium": 1.0, "small": 0.3}),
    (r"gpt-5(\.\d)?-mini|gpt-4\.1-nano|gpt-4o-mini", {"small": 1.0, "medium": 0.55, "large": 0.1}),
    (r"^gpt-4\.1$|^gpt-4o$", {"medium": 0.9, "small": 0.5, "large": 0.4}),
    (r"gemini-3\.\d+-pro|gemini-3-pro", {"large": 1.0, "medium": 0.8, "small": 0.25}),
    (r"gemini-3\.[5-9]-flash|gemini-3-1-flash|gemini-3\.1-flash", {"medium": 1.0, "small": 0.7, "large": 0.3}),
    (r"gemini-3(\.\d)?-flash", {"small": 1.0, "medium": 0.7, "large": 0.15}),
    (r"qwen3\.8-max|qwen3\.7-max|qwen3-max", {"large": 1.0, "medium": 0.8, "small": 0.2}),
    (r"qwen3\.8-flash|qwen3-vl-flash", {"small": 1.0, "medium": 0.6, "large": 0.1}),
    (r"qwen3-vl", {"vision": 1.0, "medium": 0.5, "small": 0.3}),
    (r"deepseek-v4-pro|deepseek-reasoner", {"large": 1.0, "medium": 0.8, "small": 0.2}),
    (r"deepseek-v4\b|deepseek-chat", {"medium": 1.0, "small": 0.6, "large": 0.6}),
    (r"deepseek.*flash", {"small": 1.0, "medium": 0.6, "large": 0.1}),
    (r"glm-5\.3$|glm-5\.2", {"medium": 1.0, "large": 0.8, "small": 0.4}),
    (r"glm-.*flash", {"small": 1.0, "medium": 0.5, "large": 0.1}),
    (r"kimi-k3|kimi-k2\.7-code|kimi-k2\.6", {"medium": 1.0, "large": 0.8, "small": 0.3}),
    (r"minimax-m2\.7|minimax/m2", {"medium": 1.0, "large": 0.7, "small": 0.4}),
    (r"grok-4\.6|grok-4\.5", {"large": 1.0, "medium": 0.8, "small": 0.2}),
    (r"doubao-seed.*pro", {"medium": 0.9, "large": 0.7, "small": 0.3}),
]

# 明确排除：图像、视频、语音、3D 等非文本模型
_EXCLUDE = re.compile(
    r"image|video|speech|tts|asr|veo|kling|jimeng|vidu|meshy|tripo|hunyuan-(uv|generate|remesh|retexture)"
    r"|youchuan|seedance|seedream|generate-001|embedding|rerank|moderation",
    re.I,
)


def _score_for(model_id: str) -> dict[str, float]:
    mid = model_id.lower()
    for pattern, affinity in _FAMILY_RULES:
        if re.search(pattern, mid):
            return dict(affinity)
    return {"medium": 0.4, "small": 0.2, "large": 0.2}


class ModelRegistry:
    """拉取并缓存网关模型列表，提供按档位自动选型。"""

    def __init__(self) -> None:
        self._all: list[ModelInfo] = []
        self._loaded = False
        self._overrides: dict[str, str] = {}   # tier -> 用户手动锁定的模型 id

    # ---------- 拉取 ----------
    def load(self, force: bool = False) -> list[ModelInfo]:
        if self._loaded and not force:
            return self._all
        models: list[ModelInfo] = []
        if settings.llm_enabled:
            try:
                url = settings.anthropic_base_url.rstrip("/") + "/v1/models"
                with httpx.Client(timeout=15, verify=False) as c:
                    r = c.get(url, headers={
                        "x-api-key": settings.anthropic_auth_token,
                        "anthropic-version": "2023-06-01",
                    })
                    r.raise_for_status()
                    data = r.json()
                for m in data.get("data", []):
                    mid = m.get("id", "")
                    if not mid or _EXCLUDE.search(mid):
                        continue
                    models.append(ModelInfo(id=mid, tier_affinity=_score_for(mid)))
            except Exception:
                models = []
        self._all = models
        self._loaded = True
        return models

    def available(self, tier: ModelTier, limit: int = 12) -> list[ModelInfo]:
        models = self.load()
        scored = [(m, m.tier_affinity.get(tier, 0.0)) for m in models]
        scored = [(m, s) for m, s in scored if s > 0]
        scored.sort(key=lambda x: (-x[1], len(x[0].id)))
        return [m for m, _ in scored[:limit]]

    def pick(self, tier: ModelTier) -> tuple[str, str]:
        """返回 (model_id, 选型理由)。未命中任何候选时回退到配置默认值。"""
        if tier in self._overrides:
            return self._overrides[tier], "后台配置锁定"

        configured = {
            "small": settings.model_route_small,
            "medium": settings.model_route_medium,
            "large": settings.model_route_large,
            "vision": settings.model_route_vision,
            "embedding": settings.model_route_embedding,
        }[tier]

        models = self.load()
        if not models:
            return configured, "网关未返回模型列表，使用内置默认值"

        ids = {m.id for m in models}
        if configured in ids:
            return configured, "内置默认模型，网关已确认可用"

        cands = self.available(tier, limit=1)
        if cands:
            return cands[0].id, f"内置默认模型 {configured} 不在网关列表中，按档位自动改选"
        return configured, "无可用候选，使用内置默认值（可能失败）"

    def set_override(self, tier: ModelTier, model_id: str | None) -> None:
        if model_id:
            self._overrides[tier] = model_id
        else:
            self._overrides.pop(tier, None)

    def snapshot(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for tier in ("small", "medium", "large", "vision", "embedding"):
            model, _ = self.pick(tier)  # type: ignore[arg-type]
            out[tier] = model
        return out


registry = ModelRegistry()
