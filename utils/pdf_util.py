from fpdf import FPDF
import os
import re


FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")

# Download these from https://dejavu-fonts.github.io/ and place in utils/fonts/
FONT_REGULAR = os.path.join(FONTS_DIR, "DejaVuSans.ttf")
FONT_BOLD    = os.path.join(FONTS_DIR, "DejaVuSans-Bold.ttf")


def save_to_pdf(text: str, filename: str, folder: str = "memory") -> str:

    os.makedirs(folder, exist_ok=True)
    filepath = os.path.join(folder, filename)

    pdf = FPDF()
    pdf.set_margins(left=20, top=20, right=20)
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20)

    pdf.add_font("DejaVu",      style="",  fname=FONT_REGULAR, uni=True)
    pdf.add_font("DejaVu-Bold", style="",  fname=FONT_BOLD, uni=True)

    for line in text.split("\n"):
        stripped = line.strip()

        # blank line → small vertical gap
        if not stripped:
            pdf.ln(3)
            continue

        # H1  →  # Title
        if stripped.startswith("# ") and not stripped.startswith("## "):
            pdf.set_font("DejaVu-Bold", size=18)
            pdf.multi_cell(0, 10, stripped[2:].strip())
            pdf.ln(2)

        # H2  →  ## Section
        elif stripped.startswith("## ") and not stripped.startswith("### "):
            pdf.ln(3)
            pdf.set_font("DejaVu-Bold", size=14)
            pdf.multi_cell(0, 9, stripped[3:].strip())
            pdf.ln(1)

        # H3  →  ### Subsection
        elif stripped.startswith("### "):
            pdf.ln(2)
            pdf.set_font("DejaVu-Bold", size=12)
            pdf.multi_cell(0, 8, stripped[4:].strip())

        # bullet  →  - item  or  * item
        elif re.match(r"^[-*]\s+", stripped):
            pdf.set_font("DejaVu", size=11)
            content = re.sub(r"^[-*]\s+", "", stripped)
            content = _strip_inline_md(content)
            pdf.set_x(25)
            pdf.multi_cell(0, 7, f"\u2022  {content}")

        # numbered list  →  1. item
        elif re.match(r"^\d+\.\s+", stripped):
            pdf.set_font("DejaVu", size=11)
            content = _strip_inline_md(stripped)
            pdf.set_x(25)
            pdf.multi_cell(0, 7, content)

        # horizontal rule  →  ---
        elif re.match(r"^-{3,}$", stripped):
            pdf.ln(2)
            pdf.set_draw_color(180, 180, 180)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(3)

        # normal paragraph
        else:
            pdf.set_font("DejaVu", size=11)
            pdf.multi_cell(0, 7, _strip_inline_md(stripped))

        pdf.set_x(pdf.l_margin)  # reset indent after each line

    pdf.output(filepath)
    print(f"✅ Saved PDF to {filepath}")
    return filepath


def _strip_inline_md(text: str) -> str:
    """Remove bold/italic markdown markers for plain-text rendering."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)   # **bold**
    text = re.sub(r"\*(.+?)\*",     r"\1", text)   # *italic*
    text = re.sub(r"__(.+?)__",     r"\1", text)   # __bold__
    text = re.sub(r"`(.+?)`",       r"\1", text)   # `code`
    return text