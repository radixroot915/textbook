"""
PDF export for compiled textbooks.

Tries backends in order:
  1. weasyprint (HTML → PDF, best typography) — needs: pip install weasyprint
  2. fpdf2 (pure Python) — needs: pip install fpdf2
  3. reportlab — needs: pip install reportlab

Falls back gracefully if none are installed.
"""

import re
import os
import logging

log = logging.getLogger(__name__)

# Matches standard markdown images: ![alt text](path/to/image.jpg)
_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')


def export_pdf(md_path: str, images_dir: str = None) -> str | None:
    """Convert a markdown file to PDF. Returns the PDF path, or None on failure."""
    if not os.path.exists(md_path):
        return None

    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    if images_dir is None:
        images_dir = os.path.dirname(md_path)

    pdf_path = re.sub(r'\.md$', '.pdf', md_path)

    for backend in (_try_weasyprint, _try_fpdf2, _try_reportlab):
        try:
            result = backend(md_text, pdf_path, images_dir)
            if result:
                log.info(f"[PDF] Exported: {pdf_path}")
                return pdf_path
        except Exception as e:
            log.debug(f"[PDF] backend {backend.__name__} failed: {e}")

    log.warning("[PDF] No PDF backend available — install weasyprint, fpdf2, or reportlab")
    return None


# ---------------------------------------------------------------------------
# Backend 1: weasyprint

def _try_weasyprint(md_text: str, pdf_path: str, images_dir: str = "") -> bool:
    import weasyprint
    import markdown as md_lib

    def _replace_image(m):
        alt, src = m.group(1), m.group(2)
        full = src if os.path.isabs(src) else os.path.join(images_dir, src)
        if not os.path.exists(full):
            return f"*[Image not found: {alt}]*"
        return (
            f'<figure style="margin:1.5em auto;text-align:center;max-width:80%;">'
            f'<img src="file:///{full.replace(chr(92), "/")}" alt="{alt}" '
            f'style="max-width:100%;height:auto;border:1px solid #ddd;padding:4px;"/>'
            f'<figcaption style="font-size:0.9em;color:#555;margin-top:0.5em;'
            f'font-style:italic;">{alt}</figcaption></figure>'
        )

    md_text = _IMAGE_RE.sub(_replace_image, md_text)
    html_body = md_lib.markdown(md_text, extensions=["tables", "fenced_code"])
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  @page {{ margin: 2cm; }}
  body {{ font-family: Georgia, serif; font-size: 11pt; line-height: 1.55;
          color: #1a1a1a; max-width: 100%; }}
  h1 {{ font-size: 22pt; color: #111; margin-top: 0; page-break-after: avoid; }}
  h2 {{ font-size: 16pt; color: #222; border-bottom: 1px solid #ccc;
        padding-bottom: 4px; margin-top: 1.8em; page-break-after: avoid; }}
  h3 {{ font-size: 13pt; color: #333; margin-top: 1.2em; page-break-after: avoid; }}
  p  {{ margin: 0.5em 0 0.8em; text-align: justify; orphans: 3; widows: 3; }}
  pre, code {{ font-family: Consolas, monospace; font-size: 9pt;
               background: #f5f5f5; padding: 2px 4px; border-radius: 3px; }}
  pre {{ padding: 8px 12px; overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 10pt; }}
  th, td {{ border: 1px solid #bbb; padding: 5px 8px; text-align: left; }}
  th {{ background: #eee; font-weight: bold; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 1.5em 0; }}
  li {{ margin: 0.3em 0; }}
  em {{ font-style: italic; }}
  strong {{ font-weight: bold; }}
</style>
</head>
<body>{html_body}</body>
</html>"""

    weasyprint.HTML(string=html).write_pdf(pdf_path)
    return os.path.exists(pdf_path)


# ---------------------------------------------------------------------------
# Backend 2: fpdf2

def _try_fpdf2(md_text: str, pdf_path: str, images_dir: str = "") -> bool:
    from fpdf import FPDF, XPos, YPos

    class TextbookPDF(FPDF):
        def __init__(self):
            super().__init__()
            self.set_auto_page_break(auto=True, margin=20)
            self.add_page()
            self.set_margins(20, 20, 20)

        def figure(self, image_path: str, caption: str):
            """Embed image centered with caption below. Skips gracefully if missing."""
            if not image_path or not os.path.exists(image_path):
                self.set_font("Helvetica", "I", 9)
                self.set_text_color(160, 160, 160)
                self.multi_cell(0, 5, f"[Image: {caption}]",
                                new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                self.ln(2)
                return
            # Scale image to fit within usable width (max 120mm wide, keep aspect)
            max_w = min(120, self.epw * 0.75)
            self.ln(3)
            x_center = (self.w - max_w) / 2
            self.image(image_path, x=x_center, w=max_w, keep_aspect_ratio=True)
            self.ln(2)
            # Caption: centered, small, grey italic
            self.set_font("Helvetica", "I", 9)
            self.set_text_color(100, 100, 100)
            self.multi_cell(0, 4, _clean(caption),
                            new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
            self.ln(4)

        def h1(self, text):
            self.set_font("Helvetica", "B", 20)
            self.set_text_color(20, 20, 20)
            self.ln(4)
            self.multi_cell(0, 10, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.ln(4)

        def h2(self, text):
            self.set_font("Helvetica", "B", 14)
            self.set_text_color(40, 40, 40)
            self.ln(6)
            self.multi_cell(0, 8, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_draw_color(180, 180, 180)
            self.line(self.get_x(), self.get_y(), self.w - 20, self.get_y())
            self.ln(3)

        def h3(self, text):
            self.set_font("Helvetica", "B", 11)
            self.set_text_color(60, 60, 60)
            self.ln(4)
            self.multi_cell(0, 7, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.ln(2)

        def body(self, text):
            self.set_font("Helvetica", "", 10)
            self.set_text_color(30, 30, 30)
            self.multi_cell(0, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.ln(2)

        def bullet(self, text):
            self.set_font("Helvetica", "", 10)
            self.set_text_color(30, 30, 30)
            self.set_x(self.get_x() + 6)
            self.multi_cell(0, 6, f"-  {text}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        def body_with_citations(self, text):
            import re as _re
            # Split on citation markers, render body text normal and citations italic+small
            parts = _re.split(r'(\[-> Tool Library:[^\]]+\])', text)
            self.set_x(20)
            for part in parts:
                if part.startswith("[-> Tool Library:"):
                    label = _re.sub(r'\[-> Tool Library:\s*', '', part).rstrip(']')
                    self.set_font("Helvetica", "I", 8)
                    self.set_text_color(120, 120, 120)
                    self.write(5, f" → {_clean(label)}")
                elif part:
                    self.set_font("Helvetica", "", 10)
                    self.set_text_color(30, 30, 30)
                    self.write(6, _clean(part))
            self.ln(8)

        def hr(self):
            self.ln(3)
            self.set_draw_color(200, 200, 200)
            self.line(20, self.get_y(), self.w - 20, self.get_y())
            self.ln(3)

    pdf = TextbookPDF()

    for line in md_text.splitlines():
        stripped = line.rstrip()

        # Image: ![alt text](path)
        img_match = _IMAGE_RE.match(stripped)
        if img_match:
            alt, src = img_match.group(1), img_match.group(2)
            full = src if os.path.isabs(src) else os.path.join(images_dir, src)
            pdf.figure(full, alt)
        elif stripped.startswith("# ") and not stripped.startswith("## "):
            pdf.h1(_clean(stripped[2:]))
        elif stripped.startswith("## "):
            pdf.h2(_clean(stripped[3:]))
        elif stripped.startswith("### "):
            pdf.h3(_clean(stripped[4:]))
        elif stripped.startswith("#### "):
            pdf.h3(_clean(stripped[5:]))
        elif stripped.startswith("---"):
            pdf.hr()
        elif stripped.startswith("- ") or stripped.startswith("* "):
            pdf.bullet(_clean(stripped[2:]))
        elif stripped == "":
            pdf.ln(2)
        else:
            text = _clean(stripped)
            if text:
                if "[-> Tool Library:" in text:
                    pdf.body_with_citations(text)
                else:
                    pdf.body(text)

    pdf.output(pdf_path)
    return os.path.exists(pdf_path)


# ---------------------------------------------------------------------------
# Backend 3: reportlab

def _try_reportlab(md_text: str, pdf_path: str, images_dir: str = "") -> bool:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    )

    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        leftMargin=2.5*cm, rightMargin=2.5*cm,
        topMargin=2.5*cm, bottomMargin=2.5*cm,
    )
    styles = getSampleStyleSheet()

    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=18, spaceAfter=10)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=14, spaceAfter=6, spaceBefore=14)
    h3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=11, spaceAfter=4, spaceBefore=8)
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=14, spaceAfter=6)
    bullet_style = ParagraphStyle("Bullet", parent=body, leftIndent=14, bulletIndent=6)

    story = []
    for line in md_text.splitlines():
        stripped = line.rstrip()

        if stripped.startswith("# ") and not stripped.startswith("## "):
            story.append(Paragraph(_rl_clean(stripped[2:]), h1))
        elif stripped.startswith("## "):
            story.append(Paragraph(_rl_clean(stripped[3:]), h2))
        elif stripped.startswith("### ") or stripped.startswith("#### "):
            story.append(Paragraph(_rl_clean(re.sub(r'^#{3,6}\s+', '', stripped)), h3))
        elif stripped.startswith("---"):
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey, spaceAfter=4))
        elif stripped.startswith("- ") or stripped.startswith("* "):
            story.append(Paragraph(f"• {_rl_clean(stripped[2:])}", bullet_style))
        elif stripped == "":
            story.append(Spacer(1, 6))
        else:
            text = _rl_clean(stripped)
            if text:
                story.append(Paragraph(text, body))

    doc.build(story)
    return os.path.exists(pdf_path)


# ---------------------------------------------------------------------------
# Helpers

def _clean(text: str) -> str:
    """Strip markdown inline syntax and sanitize to latin-1 for Helvetica."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Known replacements first
    text = (text
        .replace('—', '--').replace('–', '-')
        .replace('‘', "'").replace('’', "'")
        .replace('“', '"').replace('”', '"')
        .replace('…', '...').replace('±', '+/-')
        .replace('•', '-').replace(' ', ' ')
        .replace('→', '->').replace('←', '<-')
        .replace('×', 'x').replace('÷', '/')
        .replace('°', 'deg').replace('≥', '>=')
        .replace('≤', '<=').replace('≠', '!=')
    )
    # Drop anything still outside latin-1
    text = text.encode('latin-1', errors='replace').decode('latin-1')
    return text.strip()


def _rl_clean(text: str) -> str:
    """Escape for ReportLab Paragraph while preserving bold/italic."""
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    return text.strip()
