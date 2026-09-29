# -*- coding: utf-8 -*-
"""打包成两种分发包：离线包 与 GitHub 包。

为什么要一个脚本出两个包：两份交付物必须来自同一份代码。若各自维护一套，
第一次改代码就会漂移，而漂移出来的差异往往正是安全相关的（比如某个包忘了
排除密钥）。

  offline  —— 含预构建的 frontend/dist，目标机器只需 Python；
              含全部文档截图；不含任何密钥、数据库、上传文件。
  github   —— 纯源码，用于发布到公开仓库；额外套用净化规则
              （关闭认证旁路、泛化示例公司名），并且会校验每条规则都命中。

关于「校验命中」：净化最典型的失败方式是原文变了、替换静默失效，
而构建照常成功，于是不该发的东西照样发布。所以这里每条替换都断言必须命中，
不命中就整体报错中止。

用法：
    python scripts/pack.py            # 生成两个包
    python scripts/pack.py offline    # 只生成离线包
    python scripts/pack.py github     # 只生成 GitHub 包
产物：
    dist_pack/ai-recruit-platform-offline-<日期>.zip
    dist_pack/ai-recruit-platform-github-<日期>.zip
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------- 通用排除 ----------

EXCLUDE_DIRS = {
    ".venv", "node_modules", "__pycache__", ".git", ".idea", ".vscode",
    "dist_pack", ".pytest_cache", "echarts", ".mypy_cache", ".ruff_cache",
    "_pdfout", "_tmp", "_scratch",
}

# 任何包里都不该出现的东西：运行期数据与密钥
SECRET_FILES = {
    "app.db", "secret.key", ".env",
    "server.log", "server.out.log", "server.err.log", "dev.log",
    ".DS_Store", "Thumbs.db",
}
SECRET_SUFFIX = {".pyc", ".pyo", ".log", ".tmp", ".db-wal", ".db-shm"}

# GitHub 包额外排除：开发过程产物，不属于仓库内容
GITHUB_EXTRA_FILES = set()
GITHUB_EXTRA_GLOBS = [
    "docs/diag-*.png",           # 调试期截图
    "frontend/projectdocsshot-*.png",   # 路径拼接 bug 产生的重复图
    "backend/_*",                # 临时补丁脚本
    "frontend/_*",
]

# 需要保留的目录本身（即使为空），否则解压后后端会因目录不存在而报错
KEEP_EMPTY = {"backend/data/uploads", "backend/data/samples", "backend/data/knowledge"}


# ---------- 净化规则（仅 GitHub 包） ----------
#
# 每条是 (文件, 原文, 替换后, 说明)。原文必须唯一命中，否则构建失败。

def config_patch_header_auth() -> tuple[str, str, str]:
    """关闭 X-User-Id 认证旁路。

    离线包保留开启（方便本地调试），但公开仓库里默认开启等于送人一个后门：
    任何拿到代码的人发一个 X-User-Id: admin 就能无密码以超管登录。
    """
    old = (
        "    # 是否允许用 X-User-Id 请求头直接指定身份。\n"
        "    # 这是给自动化测试与本地调试用的后门，生产环境必须关掉。\n"
        "    allow_header_auth: bool = True"
    )
    new = (
        "    # 是否允许用 X-User-Id 请求头直接指定身份。\n"
        "    # 这是给自动化测试与本地调试用的后门，默认关闭。\n"
        "    # 需要时用环境变量打开：APP_ALLOW_HEADER_AUTH=true\n"
        "    allow_header_auth: bool = False"
    )
    return "backend/app/core/config.py", old, new, "关闭 X-User-Id 认证旁路"


# GitHub 包的净化规则表
def github_sanitize_rules() -> list[tuple[str, str, str, str]]:
    return [config_patch_header_auth()]


# ---------- 收集文件 ----------

def _is_excluded(rel: Path, profile: str) -> bool:
    parts = set(rel.parts)
    if parts & EXCLUDE_DIRS:
        return True
    if rel.name in SECRET_FILES or rel.name in GITHUB_EXTRA_FILES:
        return True
    if rel.suffix in SECRET_SUFFIX:
        return True
    # 上传的简历与公司资料可能含真实数据，任何包都不带
    if "data/uploads" in rel.as_posix() and rel.is_file():
        return True
    # 数据库的任何副本都不带：app.db.bak、app.db.bak-before-xxx、app.db.old……
    # 只按「app.db」精确匹配会漏掉手工备份，而备份里是完整的候选人数据。
    if rel.name.startswith("app.db") or ".db." in rel.name or rel.suffix in (".bak", ".sqlite", ".sqlite3"):
        return True
    if profile == "github":
        posix = rel.as_posix()
        for pat in GITHUB_EXTRA_GLOBS:
            if Path(posix).match(pat) or rel.match(pat):
                return True
    return False


def collect(profile: str) -> list[tuple[Path, str]]:
    """返回 [(本地绝对路径, 包内相对路径)]。"""
    items: list[tuple[Path, str]] = []
    for p in ROOT.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if _is_excluded(rel, profile):
            continue
        # 离线包带构建好的前端；GitHub 包不带（dist 已在 .gitignore 中）
        if profile == "github" and rel.parts[:2] == ("frontend", "dist"):
            continue
        items.append((p, rel.as_posix()))
    return items


def apply_sanitize(profile: str, staging: Path) -> None:
    """在暂存副本上套用净化规则。任一规则未命中即抛错。"""
    if profile != "github":
        return
    print("\n  套用 GitHub 净化规则：")
    for rel, old, new, desc in github_sanitize_rules():
        f = staging / rel
        if not f.exists():
            raise SystemExit(f"  [中止] 净化目标不存在：{rel}")
        text = f.read_text(encoding="utf-8")
        n = text.count(old)
        if n != 1:
            raise SystemExit(
                f"  [中止] 净化规则未命中（{rel}）：\n"
                f"         期望恰好 1 处，实际 {n} 处。\n"
                f"         原文可能已被改动。请更新 scripts/pack.py 里的规则，\n"
                f"         否则该修的内容会原样发布出去。")
        f.write_text(text.replace(old, new, 1), encoding="utf-8")
        print(f"    ✓ {desc}  ({rel})")


def run_sanitize_selfcheck() -> None:
    """构建前先验证净化规则在当前源码上能命中，避免打包到一半才失败。"""
    print("\n  自检净化规则：")
    for rel, old, _new, desc in github_sanitize_rules():
        f = ROOT / rel
        if not f.exists():
            raise SystemExit(f"  [中止] 规则指向的文件不存在：{rel}")
        n = f.read_text(encoding="utf-8").count(old)
        if n != 1:
            raise SystemExit(
                f"  [中止] 规则「{desc}」在 {rel} 中命中 {n} 处（应为 1）。\n"
                f"         源码可能已改。请更新 scripts/pack.py 中的规则。")
        print(f"    ✓ {desc}")


# ---------- 构建 ----------

def build(profile: str, stamp: str) -> Path:
    label = "离线包" if profile == "offline" else "GitHub 包"
    print("\n" + "=" * 64)
    print(f"  构建{label}（{profile}）")
    print("=" * 64)

    files = collect(profile)
    total_raw = sum(f.stat().st_size for f, _ in files)
    print(f"\n  文件 {len(files)} 个，原始 {total_raw / 1024 / 1024:.1f} MB")

    out_dir = ROOT / "dist_pack"
    out_dir.mkdir(exist_ok=True)
    zip_path = out_dir / f"ai-recruit-platform-{profile}-{stamp}.zip"

    # 净化在临时副本上做，绝不改工作区的源文件
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        for src, rel in files:
            dst = staging / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        apply_sanitize(profile, staging)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as z:
            existing = {rel for _s, rel in files}
            for d in KEEP_EMPTY:
                name = f"{d}/.gitkeep"
                if name not in existing:
                    z.writestr(name, "")
            for src, rel in files:
                # 净化过的文件从暂存区取，其余直接从工作区取
                real = staging / rel
                try:
                    z.write(real if profile == "github" else src, rel)
                except (OSError, PermissionError) as e:
                    print(f"    跳过 {rel}: {type(e).__name__}")

    size = zip_path.stat().st_size
    print(f"\n  产物：{zip_path.name}")
    print(f"  大小：{size / 1024 / 1024:.1f} MB（压缩率 {size / total_raw * 100:.0f}%）")
    return zip_path


def verify_package(zip_path: Path) -> list[str]:
    """解包检查：确认没有任何密钥类文件混进去。返回问题列表。"""
    problems: list[str] = []
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            base = Path(n).name
            low = n.lower()
            if base in SECRET_FILES or base == "secret.key":
                problems.append(f"含敏感文件：{n}")
            if base.startswith("app.db") or ".db." in base or base.endswith((".bak", ".sqlite", ".sqlite3")):
                problems.append(f"含数据库副本：{n}")
            if "/uploads/" in low and not n.endswith(".gitkeep"):
                problems.append(f"含上传文件：{n}")
            if base.endswith((".log", ".pyc")):
                problems.append(f"含临时文件：{n}")
    return problems


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    profiles = args if args else ["offline", "github"]
    for prof in profiles:
        if prof not in ("offline", "github"):
            print(f"  未知的包类型：{prof}（可选 offline / github）")
            return 2

    print("=" * 64)
    print("  打包 AI 招聘与人才发展平台")
    print("=" * 64)

    if "github" in profiles:
        run_sanitize_selfcheck()

    stamp = datetime.now().strftime("%Y%m%d")
    built: list[Path] = []
    for prof in profiles:
        built.append(build(prof, stamp))

    print("\n" + "=" * 64)
    print("  校验产物")
    print("=" * 64)
    failed = False
    for zp in built:
        problems = verify_package(zp)
        if problems:
            failed = True
            print(f"\n  [失败] {zp.name}")
            for p in problems[:10]:
                print(f"    ✗ {p}")
        else:
            print(f"\n  ✓ {zp.name} —— 无密钥、无数据库、无上传文件")

    if failed:
        print("\n  有产物未通过校验，请修正后重试。")
        return 1

    print("\n" + "=" * 64)
    print("  完成")
    print("=" * 64)
    if "offline" in profiles:
        print("  离线包：解压 → 双击 install.bat → 双击 scripts\\start-all.bat")
    if "github" in profiles:
        print("  GitHub 包：解压 → git init → git add -A → 提交并推送")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
