# -*- coding: utf-8 -*-
"""把 docs/ 下的 Markdown 转成 .docx，供墨刀导入。

为什么需要它：墨刀能导入 .docx 与 .md，但 .md 导入后表格与代码块会散架，
而 .docx 的表格、标题层级都能保留。所以正式版走 docx。

支持：标题、段落、粗体/行内代码、无序/有序列表、表格、引用、代码块、
以及 ```mermaid 代码块（渲染成 PNG 后嵌入）。

用法：
    python scripts/md_to_docx.py            # 转换全部
    python scripts/md_to_docx.py MRD.md     # 只转指定文件
"""
from __future__ import annotations

import base64
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = DOCS / "墨刀导入"

# 中文字体：不显式设置的话，Word 会用西文字体渲染汉字，观感很差
FONT_CN = "微软雅黑"
FONT_MONO = "Consolas"

MMDC_DIR = Path(tempfile.gettempdir()) / "mmd4"
_mermaid_cache: dict[str, Path] = {}


# ---------------------------------------------------------------- mermaid 渲染

def render_mermaid(code: str, out_png: Path) -> bool:
    """用 mermaid-cli 把图渲染成 PNG。失败返回 False（留占位文字，不中断转换）。"""
    if code in _mermaid_cache:
        shutil.copy(_mermaid_cache[code], out_png)
        return True
    mmdc = MMDC_DIR / "node_modules" / ".bin" / "mmdc.cmd"
    if not mmdc.exists():
        mmdc = MMDC_DIR / "node_modules" / ".bin" / "mmdc"
    if not mmdc.exists():
        return False
    src = out_png.with_suffix(".mmd")
    src.write_text(code, encoding="utf-8")
    try:
        r = subprocess.run(
            [str(mmdc), "-i", str(src), "-o", str(out_png), "-b", "white", "-s", "2"],
            cwd=MMDC_DIR, capture_output=True, timeout=180,
            shell=(mmdc.suffix == ".cmd"),
        )
        ok = r.returncode == 0 and out_png.exists()
    except Exception:
        ok = False
    if ok:
        _mermaid_cache[code] = out_png
    src.unlink(missing_ok=True)
    return ok


# ---------------------------------------------------------------- 行内格式

def clean(text: str) -> str:
    """剔除 XML 不允许的控制字符。

    Markdown 里偶尔会混进不可见控制字符（如 ），python-docx 写 XML 时会直接抛错。
    与其让整份文档转换失败，不如在这里静默清掉 —— 它们本来就不该出现在正文里。
    """
    return "".join(c for c in text if c == "	" or ord(c) >= 32)


def add_runs(par, text: str) -> None:
    """处理 [链接](地址)、**粗体**、`行内代码`、*斜体*。

    链接必须还原 —— 直接把 `[MRD](MRD.md)` 原样写进 Word，
    看起来就是一段坏掉的标记，而这是要给别人看的交付物。
    """
    text = clean(text)
    # 链接规则排在最前，因为它的括号里可能还含其它行内标记
    parts = re.split(r"(\[[^\]]+\]\([^)]+\)|\*\*.+?\*\*|`[^`]+`|\*[^*\n]+?\*)", text)
    for p in parts:
        if not p:
            continue
        m = re.fullmatch(r"\[([^\]]+)\]\(([^)]*)\)", p)
        if m:
            label, url = m.group(1), m.group(2)
            r = par.add_run(label)
            r.font.color.rgb = RGBColor(0x1A, 0x56, 0xB8)
            if url.startswith(("http://", "https://")):
                r.underline = True
                _hyperlink(par, url, r)
            continue
        if p.startswith("**") and p.endswith("**") and len(p) > 4:
            r = par.add_run(p[2:-2]); r.bold = True
        elif p.startswith("`") and p.endswith("`") and len(p) > 2:
            r = par.add_run(p[1:-1]); r.font.name = FONT_MONO
            r.font.size = Pt(9.5)
            r.font.color.rgb = RGBColor(0xB0, 0x30, 0x60)
        elif p.startswith("*") and p.endswith("*") and len(p) > 2:
            r = par.add_run(p[1:-1]); r.italic = True
        else:
            par.add_run(p)


# 页面可用高度（A4 纵向去掉上下边距后约为 9.7in），留出标题与说明的空间
MAX_IMG_H = 6.6
MAX_IMG_W = 6.1


def _png_size(path: Path) -> tuple[int, int]:
    """读 PNG 的宽高（IHDR 头，固定偏移），避免为一个尺寸引入 Pillow 依赖。"""
    import struct
    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    return struct.unpack(">II", head[16:24])


def add_bounded_picture(doc: Document, png: Path) -> None:
    """按「宽不超 6.1in、高不超 8.6in」等比缩放插入图片。

    只限宽不限高是不行的：竖长的流程图会高到超过一页，Word 会把它整体推到下一页，
    结果标题孤零零留在上一页 —— 看上去就像图没生成。
    """
    w_px, h_px = _png_size(png)
    if not w_px or not h_px:
        doc.add_picture(str(png), width=Inches(MAX_IMG_W))
        return
    # 以 96dpi 折算图片的自然英寸尺寸
    nat_w, nat_h = w_px / 96, h_px / 96
    scale = min(MAX_IMG_W / nat_w, MAX_IMG_H / nat_h, 1.0)
    doc.add_picture(str(png), width=Inches(nat_w * scale), height=Inches(nat_h * scale))


def _hyperlink(par, url: str, run) -> None:
    """给已创建的 run 挂上真实超链接关系（python-docx 没有原生 API）。"""
    try:
        r_id = par.part.relate_to(
            url,
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
            is_external=True,
        )
    except Exception:
        return
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    par._p.replace(run._r, link)
    link.append(run._r)


def set_cn_font(doc: Document) -> None:
    """给正文样式挂上中文字体，否则汉字会走西文字体回落。"""
    st = doc.styles["Normal"]
    st.font.name = FONT_CN
    st.font.size = Pt(10.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)


def shade(cell, hexcolor: str) -> None:
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")
    el.set(qn("w:fill"), hexcolor)
    cell._tc.get_or_add_tcPr().append(el)


# ---------------------------------------------------------------- 主转换

def convert(md_path: Path, img_dir: Path) -> tuple[Path, int, int]:
    md = md_path.read_text(encoding="utf-8")
    doc = Document()
    set_cn_font(doc)

    # 页边距收窄，表格才放得下
    for s in doc.sections:
        s.left_margin = s.right_margin = Inches(0.7)
        s.top_margin = s.bottom_margin = Inches(0.7)

    lines = md.split("\n")
    i = 0
    n_img = n_tbl = 0
    pending_list: list[str] = []

    def flush_list() -> None:
        nonlocal pending_list
        for item in pending_list:
            ind = 0
            while item.startswith("  "):
                ind += 1
                item = item[2:]
            p = doc.add_paragraph(style="List Bullet" if ind == 0 else "List Bullet 2")
            p.paragraph_format.space_after = Pt(2)
            add_runs(p, item)
        pending_list = []

    while i < len(lines):
        ln = lines[i]
        s = ln.strip()

        # ---- mermaid / 代码块 ----
        if s.startswith("```"):
            lang = s[3:].strip().lower()
            j = i + 1
            buf: list[str] = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                buf.append(lines[j]); j += 1
            body = "\n".join(buf)
            if lang == "mermaid":
                flush_list()
                png = img_dir / f"m{img_dir.name}_{n_img}.png"
                if render_mermaid(body, png):
                    add_bounded_picture(doc, png)
                    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                    n_img += 1
                else:                      # 渲染失败：留原文，不静默丢图
                    p = doc.add_paragraph()
                    r = p.add_run("[流程图，建议在 GitHub 或编辑器查看]")
                    r.italic = True
                    r.font.size = Pt(9)
                    r.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
                    p2 = doc.add_paragraph()
                    r2 = p2.add_run(body)
                    r2.font.name = FONT_MONO
                    r2.font.size = Pt(7.5)
            elif lang in ("", "text"):
                flush_list()
                p = doc.add_paragraph()
                r = p.add_run(body)
                r.font.name = FONT_MONO
                r.font.size = Pt(9)
            else:
                flush_list()
                p = doc.add_paragraph()
                r = p.add_run(body)
                r.font.name = FONT_MONO
                r.font.size = Pt(8.5)
                r.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
            i = j + 1
            continue

        # ---- 表格 ----
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            flush_list()
            rows: list[list[str]] = []
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                raw = lines[j].strip().strip("|")
                cells = [c.strip() for c in re.split(r"(?<!\\)\|", raw)]
                if not re.match(r"^[\s:|-]+$", raw):
                    rows.append(cells)
                j += 1
            if rows:
                cols = max(len(r) for r in rows)
                t = doc.add_table(rows=0, cols=cols)
                t.style = "Light Grid Accent 1"
                t.alignment = WD_TABLE_ALIGNMENT.CENTER
                for ri, row in enumerate(rows):
                    cells = t.add_row().cells
                    for ci in range(cols):
                        txt = row[ci] if ci < len(row) else ""
                        txt = txt.replace("\\|", "|")
                        cell = cells[ci]
                        cell.text = ""
                        par = cell.paragraphs[0]
                        par.paragraph_format.space_after = Pt(1)
                        add_runs(par, txt)
                        for r in par.runs:
                            r.font.size = Pt(9)
                        if ri == 0:
                            shade(cell, "EDF2F7")
                            for r in par.runs:
                                r.bold = True
                n_tbl += 1
                doc.add_paragraph().paragraph_format.space_after = Pt(3)
            i = j
            continue

        # ---- 标题 ----
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush_list()
            lvl = len(m.group(1))
            h = doc.add_heading(level=min(lvl, 4))
            add_runs(h, m.group(2))
            for r in h.runs:
                r.font.name = FONT_CN
                r.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)
            i += 1
            continue

        # ---- 分隔线 ----
        if re.match(r"^-{3,}$", s) or re.match(r"^\*{3,}$", s):
            flush_list()
            p = doc.add_paragraph()
            pPr = p._p.get_or_add_pPr()
            b = OxmlElement("w:pBdr"); bot = OxmlElement("w:bottom")
            bot.set(qn("w:val"), "single"); bot.set(qn("w:sz"), "6")
            bot.set(qn("w:color"), "CFD8E3")
            b.append(bot); pPr.append(b)
            i += 1
            continue

        # ---- 引用 ----
        if s.startswith(">"):
            flush_list()
            body = re.sub(r"^>\s?", "", s)
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.25)
            pPr = p._p.get_or_add_pPr()
            b = OxmlElement("w:pBdr"); left = OxmlElement("w:left")
            left.set(qn("w:val"), "single"); left.set(qn("w:sz"), "18")
            left.set(qn("w:color"), "8FAADC"); left.set(qn("w:space"), "8")
            b.append(left); pPr.append(b)
            add_runs(p, body)
            for r in p.runs:
                r.font.size = Pt(9.5)
                r.font.color.rgb = RGBColor(0x44, 0x4E, 0x5A)
            i += 1
            continue

        # ---- 列表 ----
        m = re.match(r"^(\s*)[-*+]\s+(.*)$", ln)
        if m:
            pending_list.append(m.group(1) + m.group(2))
            i += 1
            continue
        m = re.match(r"^(\s*)\d+\.\s+(.*)$", ln)
        if m:
            flush_list()
            p = doc.add_paragraph(style="List Number")
            p.paragraph_format.space_after = Pt(2)
            add_runs(p, m.group(2))
            i += 1
            continue

        # ---- 空行 ----
        if not s:
            flush_list()
            i += 1
            continue

        # ---- 普通段落 ----
        flush_list()
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.line_spacing = 1.25
        add_runs(p, s)
        i += 1

    flush_list()
    dst = OUT / md_path.relative_to(DOCS).with_suffix(".docx")
    dst.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dst)
    return dst, n_img, n_tbl


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    img_root = Path(tempfile.gettempdir()) / "_modao_img"
    shutil.rmtree(img_root, ignore_errors=True)
    img_root.mkdir(parents=True, exist_ok=True)

    if len(sys.argv) > 1:
        targets = [DOCS / a for a in sys.argv[1:]]
    else:
        targets = sorted(
            p for p in DOCS.rglob("*.md")
            if OUT not in p.parents and p.name != "README.md"
        )

    tot_i = tot_t = 0
    for k, md in enumerate(targets):
        if not md.exists():
            print(f"  跳过（不存在）：{md}"); continue
        d = img_root / f"f{k:02d}"; d.mkdir(exist_ok=True)
        dst, ni, nt = convert(md, d)
        tot_i += ni; tot_t += nt
        print(f"  ✓ {md.relative_to(DOCS)}  →  {dst.relative_to(DOCS)}  "
              f"（图 {ni}，表 {nt}）")
    shutil.rmtree(img_root, ignore_errors=True)
    print(f"\n共 {len(targets)} 个文档，嵌入流程图 {tot_i} 张，表格 {tot_t} 个")
    print(f"输出目录：{OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
