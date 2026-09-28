# -*- coding: utf-8 -*-
"""给所有 .bat 加上纯 ASCII 引导头，让它们在中文 Windows（代码页 936）下也能正确显示中文。

问题：脚本在 936 下启动，读到中间的 `chcp 65001` 时中途换编码，cmd 之后按新编码
重新定位文件位置，把中文行切碎、当成命令执行。实测报错形如：

    'dist，才需要' is not recognized as an internal or external command
    Python: can't open file '...\\虚拟环境并安装后端依赖': [Errno 2] No such file

注意两点（均为实测结论）：
  1. 在同一进程里 `chcp` 后再 `call` 自身**无效** —— 批处理解析器在进程启动时
     就固定了代码页，只有**新起一个 cmd 进程**才会生效。
  2. 本脚本必须**按字节处理**，不能用文本模式读写。文本模式会把
     `backend\\requirements.txt` 里的 `\\r` 当成回车符，写出
     `backend` + CR + `equirements.txt`，破坏安装脚本（曾经真的踩过）。

引导头必须全部是 ASCII 字节，否则它自己就会被切碎。脚本会自检，不满足就中止。
"""
from __future__ import annotations

import sys
from pathlib import Path

CRLF = b"\r\n"

PREAMBLE = CRLF.join([
    b"@echo off",
    b"REM ============================================================",
    b"REM ASCII-only bootstrap -- keep every byte above the marker line",
    b"REM below pure ASCII. cmd.exe mis-parses multi-byte UTF-8 lines when",
    b"REM the code page changes while the script is already running",
    b"REM (Chinese Windows starts at code page 936). So: switch the code",
    b"REM page, then re-enter this file in a NEW cmd process, which then",
    b"REM decodes the whole file as UTF-8 from the first byte.",
    b"REM A same-process `call` does NOT work -- the parser snapshots its",
    b"REM code page at process start. Verified by experiment.",
    b"REM ============================================================",
    b'if not "%~1"=="__utf8" (',
    b"    chcp 65001 >nul",
    b'    cmd /d /c call "%~f0" __utf8',
    b"    exit /b %errorlevel%",
    b")",
    b"REM ==================== end of bootstrap =====================",
    b"",
])

MARKER = b"end of bootstrap"
MARKER_LINE = b"REM ==================== end of bootstrap ====================="
SKIP_FIRST = {b"@echo off", b"chcp 65001 >nul"}


def wrap(raw: bytes) -> bytes:
    """按字节加引导头，不做任何文本解码，避免误吃反斜杠转义。"""
    lines = raw.split(CRLF)
    # 去掉正文开头重复的 @echo off / chcp（引导头已处理）
    while lines and lines[0].strip().lower() in SKIP_FIRST:
        lines.pop(0)

    out = PREAMBLE + CRLF.join(lines)
    if not out.endswith(CRLF):
        out += CRLF

    # 自检 1：标记行之前必须全 ASCII
    head = out[: out.index(MARKER)]
    bad = [i for i, b in enumerate(head) if b >= 128]
    if bad:
        raise SystemExit(f"[中止] 引导头含非 ASCII 字节，位置 {bad[:3]}")

    # 自检 2：不能有裸 CR（那是文件被文本模式破坏的迹象）
    bare = [i for i in range(len(out) - 1) if out[i] == 0x0D and out[i + 1] != 0x0A]
    if bare:
        raise SystemExit(f"[中止] 存在裸 CR，位置 {bare[:3]}")

    return out


def main() -> int:
    targets = [Path("install.bat")] + sorted(Path("scripts").glob("*.bat"))
    for p in targets:
        raw = p.read_bytes()
        if MARKER_LINE in raw:
            print(f"  跳过 {p}（已含引导头）")
            continue
        new = wrap(raw)
        p.write_bytes(new)
        crlf = new.count(CRLF)
        assert new.count(b"\n") == crlf, f"{p} 存在裸 LF"
        assert b"backend\\requirements.txt" in new or p.name != "install.bat", \
            "requirements.txt 路径被破坏"
        print(f"  已加固 {p}  ({len(new)} 字节, CRLF {crlf} 处)")
    print("完成：所有 .bat 已可在 936 代码页下正确运行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
