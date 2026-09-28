"""认证与密码安全。

不引入额外依赖：密码哈希用标准库的 PBKDF2-HMAC-SHA256，
令牌用 HMAC 签名的自包含结构。这样打包到别的机器上不需要多装任何东西。

安全上的取舍：
- 密码加盐哈希，永不明文落库，也不可逆。
- 令牌带过期时间与签名，篡改后校验必然失败。
- 校验用 compare_digest 做常数时间比较，避免时序侧信道。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from app.core.config import settings

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 200_000


# ---------------- 密码 ----------------

def hash_password(password: str) -> str:
    """返回 `算法$盐$哈希` 形式的字符串，可安全入库。"""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_b64e(salt)}${_b64e(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码。任何格式异常都返回 False，不抛异常。"""
    try:
        algo, salt_b64, hash_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        salt = _b64d(salt_b64)
        expected = _b64d(hash_b64)
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return hmac.compare_digest(dk, expected)


def password_strength_issue(password: str) -> str:
    """返回不满足要求的原因；满足则返回空字符串。

    演示平台不强制复杂度，只挡住明显不安全的短密码 —— 否则会妨碍体验。
    """
    if not password or len(password) < 6:
        return "密码至少 6 位"
    if len(password) > 128:
        return "密码过长（最多 128 位）"
    return ""


# ---------------- 令牌 ----------------

def make_token(userid: str, role: str, ttl_seconds: int | None = None) -> tuple[str, int]:
    """签发令牌。返回 (token, 过期时间戳)。

    结构：base64(payload).base64(签名)，payload 里带过期时间。
    无状态，不需要额外的会话表；代价是无法主动吊销单个令牌 ——
    对演示平台这个取舍可以接受，用户被停用后 15 分钟内令牌自然失效。
    """
    ttl = ttl_seconds if ttl_seconds is not None else settings.token_ttl_hours * 3600
    exp = int(time.time()) + ttl
    payload = {"s": userid, "r": role, "e": exp, "n": secrets.token_hex(4)}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(settings.secret_key.encode("utf-8"), body.encode("ascii"),
                   hashlib.sha256).digest()
    return f"{body}.{_b64e(sig)}", exp


def parse_token(token: str) -> dict[str, Any] | None:
    """校验并解析令牌。签名不对、格式不对、已过期都返回 None。"""
    try:
        body, sig_b64 = token.split(".")
        expected = hmac.new(settings.secret_key.encode("utf-8"), body.encode("ascii"),
                            hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(sig_b64)):
            return None
        payload = json.loads(_b64d(body).decode("utf-8"))
        if int(payload.get("e", 0)) < int(time.time()):
            return None
        return payload
    except Exception:  # noqa: BLE001
        return None


# ---------------- 内部工具 ----------------

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)
