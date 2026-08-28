"""Markdown-ish text to PDF.

The agents produce client-facing markdown; this renders it well enough to hand
over. PDF generation is deliberately non-fatal: if the DejaVu fonts are missing we
degrade to a built-in font rather than failing a pipeline run that has already
spent real money on model calls.
"""

from __future__ import annotations

import re
from pathlib import Path

from fpdf import FPDF

from core.logging import get_logger

logger = get_logger(__name__)

FONTS_DIR = Path(__file__).parent / "fonts"
FONT_REGULAR = FONTS_DIR / "DejaVuSans.ttf"
FONT_BOLD = FONTS_DIR / "DejaVuSans-Bold.ttf"

_UNICODE_FAMILY = "DejaVu"
_FALLBACK_FAMILY = "Helvetica"
_MONO_FAMILY = "Courier"

_BULLET = re.compile(r"^[-*]\s+")
_NUMBERED = re.compile(r"^\d+\.\s+")
_RULE = re.compile(r"^([-*_])\1{2,}$")
_TABLE_ROW = re.compile(r"^\|.*\|$")
_TABLE_DIVIDER = re.compile(r"^\|[\s:|-]+\|$")


def _register_fonts(pdf: FPDF) -> str:
    """Return the family name to use, preferring the Unicode-capable one."""
    if FONT_REGULAR.is_file() and FONT_BOLD.is_file():
        pdf.add_font(_UNICODE_FAMILY, style="", fname=str(FONT_REGULAR))
        pdf.add_font(_UNICODE_FAMILY, style="B", fname=str(FONT_BOLD))
        return _UNICODE_FAMILY

    logger.warning(
        "DejaVu fonts not found in %s; falling back to %s and dropping non-Latin characters. "
        "Download them from https://dejavu-fonts.github.io/",
        FONTS_DIR,
        _FALLBACK_FAMILY,
    )
    return _FALLBACK_FAMILY


def strip_inline_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"\1 (\2)", text)
    return text


def _encodable(text: str, family: str) -> str:
    """Core PDF fonts are Latin-1 only; drop what they cannot represent."""
    if family != _FALLBACK_FAMILY:
        return text
    return text.encode("latin-1", errors="replace").decode("latin-1")


def save_to_pdf(text: str, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    pdf = FPDF()
    pdf.set_margins(left=20, top=20, right=20)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    family = _register_fonts(pdf)
    in_code_block = False

    for raw_line in (text or "").split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            pdf.ln(2)
            continue

        if in_code_block:
            pdf.set_font(_MONO_FAMILY, size=9)
            pdf.multi_cell(0, 5, _encodable(line or " ", _MONO_FAMILY))
            pdf.set_x(pdf.l_margin)
            continue

        if not stripped:
            pdf.ln(3)
            continue

        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            content = strip_inline_markdown(stripped.lstrip("#").strip())
            sizes = {1: 18, 2: 14, 3: 12}
            heights = {1: 10, 2: 9, 3: 8}
            size = sizes.get(level, 11)
            pdf.ln(3 if level > 1 else 0)
            pdf.set_font(family, style="B", size=size)
            pdf.multi_cell(0, heights.get(level, 7), _encodable(content, family))
            pdf.ln(1)

        elif _RULE.match(stripped):
            pdf.ln(2)
            pdf.set_draw_color(180, 180, 180)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(3)

        elif _TABLE_DIVIDER.match(stripped):
            continue

        elif _TABLE_ROW.match(stripped):
            cells = [strip_inline_markdown(c.strip()) for c in stripped.strip("|").split("|")]
            pdf.set_font(family, size=10)
            pdf.multi_cell(0, 6, _encodable("  |  ".join(cells), family))

        elif _BULLET.match(stripped):
            content = strip_inline_markdown(_BULLET.sub("", stripped))
            pdf.set_font(family, size=11)
            pdf.set_x(25)
            pdf.multi_cell(0, 7, _encodable(f"\u2022  {content}" if family == _UNICODE_FAMILY else f"-  {content}", family))

        elif _NUMBERED.match(stripped):
            pdf.set_font(family, size=11)
            pdf.set_x(25)
            pdf.multi_cell(0, 7, _encodable(strip_inline_markdown(stripped), family))

        else:
            pdf.set_font(family, size=11)
            pdf.multi_cell(0, 7, _encodable(strip_inline_markdown(stripped), family))

        pdf.set_x(pdf.l_margin)

    pdf.output(str(path))
    logger.info("Saved PDF artifact: %s", path.name)
    return path


def try_save_to_pdf(text: str, path: Path) -> Path | None:
    """Best-effort PDF generation. A rendering failure must not fail a run."""
    try:
        return save_to_pdf(text, path)
    except Exception:
        logger.exception("PDF generation failed for %s; continuing without it", Path(path).name)
        return None
