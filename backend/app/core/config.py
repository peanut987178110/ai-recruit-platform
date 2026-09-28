"""全局配置。

设计要点：
- 网关地址与密钥从环境变量读取，与 Claude Code 共用一份，避免明文落盘。
- 所有模型名都是「逻辑档位 -> 具体模型 id」的映射，运行时可被 P-12 后台配置覆盖。
- 分层阈值、留存天数等业务参数有默认值，但真实生效值来自数据库配置表。
"""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]  # backend/
PROJECT_ROOT = BASE_DIR.parent

# 模型路由档位：产品侧只关心「这一档用大模型还是小模型」，不关心具体厂商
ModelTier = Literal["small", "medium", "large", "vision", "embedding"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    app_name: str = "AI 招聘与人才发展平台"
    app_version: str = "1.0.0"
    debug: bool = True

    # ---------- LLM 网关（兼容 Anthropic Messages 语义）----------
    # 留空即走 mock 模式，界面上的能力仍可完整演示（结果标记为「模拟」）
    anthropic_base_url: str = Field(default="", alias="ANTHROPIC_BASE_URL")
    anthropic_auth_token: str = Field(default="", alias="ANTHROPIC_AUTH_TOKEN")
    llm_timeout_sync: int = 20          # PRD 4.3 超时阈值：同步能力 20s
    llm_timeout_async: int = 600        # PRD 4.3 超时阈值：异步能力 10min
    llm_max_retries: int = 2            # 一级降级：重试最多 2 次

    # 按能力覆盖超时。PRD 4.3 给同步能力定的 20 秒，是按「短输出」假设写的；
    # 实测出题、打分这类要输出大量结构化内容的调用会跑到 60 至 120 秒，
    # 一刀切会把正常调用误判为超时并触发降级。因此按能力的实际输出量分别设置。
    llm_timeout_overrides: dict[str, int] = {
        "匹配打分": 180,
        "面试题生成": 300,
        "题库生成": 300,
        "培训大纲生成": 240,
        "主观题判卷": 180,
        "问答归类": 240,
        "JD 分析": 120,
        "简历解析": 180,
    }

    # ---------- 模型路由默认表（P-12 可覆盖）----------
    # 分级路由：解析/格式化用小模型，打分/出题用大模型（PRD 5.2 成本约束）
    model_route_small: str = "claude-haiku-4-5-20251001"
    model_route_medium: str = "claude-sonnet-5"
    model_route_large: str = "claude-opus-5"
    model_route_vision: str = "qwen3-vl-plus"
    model_route_embedding: str = "text-embedding-3-large"
    # 备用模型：二级降级时切换（PRD 4.3）
    model_fallback_medium: str = "claude-sonnet-4-6"
    model_fallback_large: str = "claude-opus-4-8"

    # ---------- 存储 ----------
    database_url: str = f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'app.db').as_posix()}"
    upload_dir: Path = BASE_DIR / "data" / "uploads"
    knowledge_dir: Path = BASE_DIR / "data" / "knowledge"
    sample_dir: Path = BASE_DIR / "data" / "samples"

    # ---------- 业务默认参数（首次启动写入配置表，之后以库内为准）----------
    default_high_confidence: float = 0.85
    default_high_score: int = 75
    default_mid_score: int = 45
    default_pool_days: int = 7           # 待定池保留天数
    default_cost_alert: float = 0.25     # 单份成本告警线（元）
    default_cost_target: float = 0.16    # 单份成本目标（元）

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # ---------- 认证 ----------
    # 令牌签名密钥。首次启动会生成一个随机值写进 data/secret.key 并复用，
    # 因此打包到别的机器上也能直接用；换机器想强制所有登录失效就删掉那个文件。
    secret_key: str = ""
    token_ttl_hours: int = 12

    # 是否允许用 X-User-Id 请求头直接指定身份。
    # 这是给自动化测试与本地调试用的后门，默认关闭。
    # 需要时用环境变量打开：APP_ALLOW_HEADER_AUTH=true
    allow_header_auth: bool = False

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_base_url and self.anthropic_auth_token)


def reload_settings() -> Settings:
    """清掉缓存并重新读取，用于界面保存网关配置后立即生效。"""
    get_settings.cache_clear()
    return get_settings()


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # 环境变量兜底：pydantic-settings 的 alias 已能读到，这里再兜一层
    if not s.anthropic_base_url:
        s.anthropic_base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if not s.anthropic_auth_token:
        s.anthropic_auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    for p in (s.upload_dir, s.knowledge_dir, s.sample_dir):
        Path(p).mkdir(parents=True, exist_ok=True)
    s.secret_key = s.secret_key or _load_or_create_secret()
    return s


def _load_or_create_secret() -> str:
    """令牌密钥：优先环境变量，其次本地文件，最后新建。

    放在 data/ 下而不是写进代码，是为了让「压缩包解压即可用」成立 ——
    每台机器首次启动自动生成自己的密钥，不需要手工配置。
    """
    env = os.environ.get("APP_SECRET_KEY", "")
    if env:
        return env
    key_file = Path(BASE_DIR) / "data" / "secret.key"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        v = key_file.read_text(encoding="utf-8").strip()
        if v:
            return v
    v = secrets.token_urlsafe(48)
    key_file.write_text(v, encoding="utf-8")
    return v


settings = get_settings()
