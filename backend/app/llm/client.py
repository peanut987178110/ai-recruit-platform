"""LLM 调用客户端。

封装三件事，这三件事是 PRD 4.3 与 4.2 的落地点：
1. 三级降级：重试 -> 换模型 -> 转人工，且降级状态必须回传给界面（禁止静默降级）。
2. 超时阈值：同步能力 20s、异步能力 10min，超时按失败处理并走降级路径。
3. 三段结构强校验：结果体字段缺失、枚举越界、证据无法定位，一律判为失败。

同时记录 token 与成本，供 P-11 看板的成本监控使用。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import ModelTier, settings
from app.core.schemas import AIResult, DegradeLevel, MetaInfo
from app.llm.registry import registry

T = TypeVar("T", bound=BaseModel)

# 粗略单价（元 / 千 token），仅用于成本看板估算，不做财务依据
_PRICE = {
    "small": (0.001, 0.004),
    "medium": (0.008, 0.024),
    "large": (0.03, 0.12),
}


class LLMError(Exception):
    pass


def _extract_json(text: str) -> Any:
    """模型有时会把 JSON 包在解释文字或代码块里，这里做容错抽取。

    重点处理「被 max_tokens 截断」的情况：结尾的代码块围栏、未闭合的括号
    都不应该让我们直接放弃 —— 先尝试补全再解析，补不上才报错。
    """
    if not text:
        raise LLMError("空响应")
    text = text.strip()

    # 代码块围栏可能只有开头没有结尾（被截断），所以两种形式都要试
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    else:
        opening = re.search(r"```(?:json)?\s*(.+)$", text, re.S)
        if opening:
            text = opening.group(1).strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    # 直接找最外层的 {...} 或 [...]
    for opener, closer in (("{", "}"), ("[", "]")):
        i = text.find(opener)
        if i < 0:
            continue
        j = text.rfind(closer)
        if j > i:
            try:
                return json.loads(text[i:j + 1])
            except Exception:
                pass
        # 截断补救：补齐未闭合的括号与引号，再试一次
        candidate = text[i:]
        try:
            return json.loads(_repair_truncated(candidate, opener, closer))
        except Exception:
            continue
    raise LLMError("响应中未找到合法 JSON")


def _repair_truncated(s: str, opener: str, closer: str) -> str:
    """修复被截断的 JSON。

    截断有两种常见形态，都要能处理：
      1. 断在结构边界——最后一个元素是完整的，只是外层括号没闭合；
      2. 断在字符串中间——例如 `"reason": "独立负责仓储人` 这样引号都没闭合。
    第二种更常见，单靠补括号修不好，必须先丢弃这段残值。
    """
    body = _cut_to_complete(s)

    if not body:
        raise LLMError("无法修复截断的 JSON")

    # 若仍处于未闭合字符串中，丢掉最后一个不完整的字段
    if _unclosed_quote(body):
        cut = body.rfind('"')
        body = body[:cut] if cut > 0 else body
        # 去掉尾部残留的 `"key":` 形式
        body = re.sub(r',?\s*"[^"]*"\s*:\s*$', "", body)
        body = re.sub(r",\s*$", "", body)

    # 补齐仍未闭合的括号
    opens = body.count("{") - body.count("}")
    arrs = body.count("[") - body.count("]")
    body += "]" * max(0, arrs) + "}" * max(0, opens)

    # 去掉可能产生的悬空逗号
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    return body


def _cut_to_complete(s: str) -> str:
    """回退到最后一个结构完整的位置。"""
    depth = 0
    in_str = False
    esc = False
    last_safe = 0
    for idx, ch in enumerate(s):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                last_safe = idx + 1
    if last_safe:
        return s[:last_safe]

    # 没有完整的外层结构：截到最后一个完整的数组元素或对象成员
    cut = s.rfind("},")
    if cut > 0:
        return s[:cut + 1]
    cut = s.rfind('"')
    return s[:cut] if cut > 0 else s


def _unclosed_quote(s: str) -> bool:
    """判断字符串扫描结束时是否停在一个未闭合的引号内。"""
    in_str = False
    esc = False
    for ch in s:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
    return in_str


class LLMClient:
    """对网关的薄封装。走 Anthropic Messages 协议，因此换网关不用改调用方。"""

    def __init__(self) -> None:
        self._usage: list[dict[str, Any]] = []

    # ---------- 底层请求 ----------
    async def _once(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
        images: list[str] | None = None,
    ) -> tuple[str, dict[str, int]]:
        if not settings.llm_enabled:
            raise LLMError("未配置模型网关")

        content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        for img in images or []:
            # img 形如 "data:image/png;base64,xxx"
            if "," in img:
                media, b64 = img.split(",", 1)
                mt = media.split(";")[0].replace("data:", "") or "image/png"
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mt, "data": b64},
                })

        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        url = settings.anthropic_base_url.rstrip("/") + "/v1/messages"
        headers = {
            "x-api-key": settings.anthropic_auth_token,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=timeout, verify=False) as c:
            r = await c.post(url, json=payload, headers=headers)
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
            data = r.json()

        text = ""
        for blk in data.get("content", []):
            if blk.get("type") == "text":
                text += blk.get("text", "")
        usage = data.get("usage", {}) or {}
        stop = data.get("stop_reason", "")
        if stop == "max_tokens":
            # 输出被 token 上限截断。这个信息必须带出去，否则会以
            # 「JSON 解析失败」的面目出现，排查时看不出真正原因。
            raise LLMError(
                f"输出被 max_tokens({max_tokens}) 截断，结果不完整。"
                f"该能力需要更高的 max_tokens 或更小的输出范围。"
            )
        return text, {
            "in": int(usage.get("input_tokens", 0)),
            "out": int(usage.get("output_tokens", 0)),
        }

    # ---------- 对外主入口 ----------
    async def complete(
        self,
        *,
        ability: str,
        prompt_id: str,
        prompt_version: str,
        system: str,
        user: str,
        tier: ModelTier = "medium",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        is_async: bool = False,
        images: list[str] | None = None,
        timeout_override: int | None = None,
    ) -> tuple[str, MetaInfo]:
        """返回 (原始文本, 元信息)。降级过程记录在元信息里，供界面明示。"""
        meta = MetaInfo(
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            model_version=prompt_version,
        )
        started = time.time()

        if not settings.llm_enabled:
            meta.degrade = DegradeLevel.HUMAN
            meta.degrade_reason = "未配置模型网关，已转人工路径"
            meta.latency_ms = int((time.time() - started) * 1000)
            raise LLMError(meta.degrade_reason)

        model, _reason = registry.pick(tier)
        meta.model = model
        # 按能力覆盖优先，其次按同步/异步档位
        timeout = (
            timeout_override
            or settings.llm_timeout_overrides.get(ability)
            or (settings.llm_timeout_async if is_async else settings.llm_timeout_sync)
        )

        # 一级降级：重试（网络或超时，最多 2 次）
        last_err = ""
        for attempt in range(settings.llm_max_retries + 1):
            try:
                text, usage = await self._once(
                    model, system, user, temperature, max_tokens, timeout, images
                )
                meta.tokens_in, meta.tokens_out = usage["in"], usage["out"]
                pin, pout = _PRICE.get(tier, _PRICE["medium"])
                meta.cost_cny = round(usage["in"] / 1000 * pin + usage["out"] / 1000 * pout, 5)
                if attempt > 0:
                    meta.degrade = DegradeLevel.RETRY
                    meta.degrade_reason = f"重试 {attempt} 次后成功"
                meta.latency_ms = int((time.time() - started) * 1000)
                self._record(ability, meta)
                return text, meta
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                if attempt < settings.llm_max_retries:
                    await asyncio.sleep(0.6 * (attempt + 1))

        # 二级降级：换备用模型
        fallback = settings.model_fallback_large if tier == "large" else settings.model_fallback_medium
        if fallback and fallback != model:
            try:
                text, usage = await self._once(
                    fallback, system, user, temperature, max_tokens, timeout, images
                )
                meta.model = fallback
                meta.tokens_in, meta.tokens_out = usage["in"], usage["out"]
                meta.degrade = DegradeLevel.SWITCH_MODEL
                meta.degrade_reason = f"主模型不可用，已切换备用模型（{last_err[:80]}）"
                meta.latency_ms = int((time.time() - started) * 1000)
                self._record(ability, meta)
                return text, meta
            except Exception as e:  # noqa: BLE001
                last_err = f"{last_err} | fallback: {e}"

        # 三级降级：转人工
        meta.degrade = DegradeLevel.HUMAN
        meta.degrade_reason = f"连续失败，转人工。原因：{last_err[:160]}"
        meta.latency_ms = int((time.time() - started) * 1000)
        self._record(ability, meta)
        raise LLMError(meta.degrade_reason)

    async def complete_json(
        self,
        *,
        ability: str,
        prompt_id: str,
        prompt_version: str,
        system: str,
        user: str,
        schema: type[T],
        tier: ModelTier = "medium",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        is_async: bool = False,
        images: list[str] | None = None,
        require_evidence: bool = False,
        bump_retry: bool = True,
    ) -> AIResult[T]:
        """调用并做三段结构强校验。校验不过即判为失败并走降级，不把残缺结果渲染给用户。"""
        try:
            text, meta = await self.complete(
                ability=ability, prompt_id=prompt_id, prompt_version=prompt_version,
                system=system, user=user, tier=tier, temperature=temperature,
                max_tokens=max_tokens, is_async=is_async, images=images,
            )
        except LLMError as e:
            return AIResult.failure(ability, str(e), MetaInfo(
                prompt_id=prompt_id, prompt_version=prompt_version,
                degrade=DegradeLevel.HUMAN, degrade_reason=str(e),
            ))

        try:
            raw = _extract_json(text)
        except LLMError as e:
            # 输出不完整或不合规。最常见的原因是 max_tokens 不够导致被截断，
            # 而截断位置又正好落在修复不了的边界上。
            # 这类失败是概率性的：同样的输入重跑一次往往就正常。
            # 因此这里先自动用更高的上限重试一次，避免把「偶发截断」
            # 变成「这位候选人打分失败、直接转人工」。
            if bump_retry and not is_async:
                retry_tokens = min(max_tokens * 2, 32000)
                retry_meta = MetaInfo(prompt_id=prompt_id, prompt_version=prompt_version)
                try:
                    # 注意：这里不能传 timeout_override —— 它属于 complete() 的参数，
                    # complete_json() 没有这个形参。让 complete() 按能力解析超时即可。
                    text2, meta2 = await self.complete(
                        ability=ability, prompt_id=prompt_id, prompt_version=prompt_version,
                        system=system, user=user, tier=tier, temperature=temperature,
                        max_tokens=retry_tokens, is_async=is_async, images=images,
                    )
                    raw = _extract_json(text2)
                    meta2.degrade = DegradeLevel.RETRY
                    meta2.degrade_reason = (
                        f"输出不完整（{str(e)[:60]}），已用 {retry_tokens} token 上限重试成功"
                    )
                    meta = meta2
                except Exception as e2:  # noqa: BLE001
                    return AIResult.failure(
                        ability,
                        f"输出不合法且重试仍失败（{str(e)[:60]} / {str(e2)[:60]}）。"
                        f"该能力的 max_tokens={max_tokens}，可能需要继续调大。",
                        meta,
                    )
            else:
                # 模型返回了非 JSON 内容：判为结果体结构校验失败并走降级，
                # 不能让异常冒泡成 500 —— 那是把「模型输出不合规」误报成「服务故障」。
                return AIResult.failure(
                    ability, f"模型输出不是合法 JSON（{e}）：{text[:160]}", meta
                )

        # 元信息段：模型可能自带版本号，以代码配置的为准
        if isinstance(raw, dict) and "meta" in raw and isinstance(raw["meta"], dict):
            raw = {k: v for k, v in raw.items() if k != "meta"}

        result_field = raw
        if isinstance(raw, dict) and "result" in raw:
            result_field = raw["result"]
            evidence_raw = raw.get("evidence", [])
        else:
            evidence_raw = raw.get("evidence", []) if isinstance(raw, dict) else []

        try:
            parsed = schema.model_validate(result_field)
        except ValidationError as e:
            return AIResult.failure(
                ability, f"结果体 schema 校验失败：{e.errors()[:3]}", meta
            )

        from app.core.schemas import Evidence
        evidence: list[Evidence] = []
        for ev in evidence_raw if isinstance(evidence_raw, list) else []:
            try:
                evidence.append(Evidence.model_validate(ev))
            except ValidationError:
                continue

        if require_evidence and not evidence:
            # 打分类能力证据体必须非空（PRD 4.2）
            return AIResult.failure(ability, "打分类能力返回证据体为空，判为失败", meta)

        return AIResult(ok=True, ability=ability, result=parsed, evidence=evidence, meta=meta)

    # ---------- 成本与用量 ----------
    def _record(self, ability: str, meta: MetaInfo) -> None:
        self._usage.append({"ability": ability, **meta.model_dump()})
        if len(self._usage) > 2000:
            self._usage = self._usage[-1000:]

    def usage_stats(self) -> dict[str, Any]:
        if not self._usage:
            return {"calls": 0, "cost_cny": 0.0, "avg_latency_ms": 0, "degraded": 0, "by_ability": {}}
        by: dict[str, dict[str, Any]] = {}
        for u in self._usage:
            b = by.setdefault(u["ability"], {"calls": 0, "cost": 0.0, "latency": 0, "degraded": 0})
            b["calls"] += 1
            b["cost"] += u.get("cost_cny", 0) or 0
            b["latency"] += u.get("latency_ms", 0) or 0
            if u.get("degrade") not in (None, "none"):
                b["degraded"] += 1
        total_cost = sum(u.get("cost_cny", 0) or 0 for u in self._usage)
        return {
            "calls": len(self._usage),
            "cost_cny": round(total_cost, 4),
            "avg_cost_cny": round(total_cost / len(self._usage), 5),
            "avg_latency_ms": int(sum(u.get("latency_ms", 0) or 0 for u in self._usage) / len(self._usage)),
            "degraded": sum(1 for u in self._usage if u.get("degrade") not in (None, "none")),
            "by_ability": {k: {**v, "cost": round(v["cost"], 4)} for k, v in by.items()},
        }


llm = LLMClient()
