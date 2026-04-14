"""Convert synthetic markdown documents to PDF using reportlab.

Uses Windows Malgun Gothic (malgun.ttf) for Korean rendering. Intentionally
minimal — this is only for producing realistic-looking source PDFs for the
ingest pipeline to re-parse. Not a general markdown renderer.

Usage (from repo root, PowerShell):
    .\.venv\Scripts\python scripts\md_to_pdf.py synthetic\regulation_v1.md
    .\.venv\Scripts\python scripts\md_to_pdf.py synthetic\regulation_v1.md --out synthetic\regulation_v1.pdf
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

MALGUN = Path(r"C:\Windows\Fonts\malgun.ttf")
MALGUN_BOLD = Path(r"C:\Windows\Fonts\malgunbd.ttf")


def register_fonts() -> tuple[str, str]:
    if not MALGUN.exists():
        print(f"ERROR: {MALGUN} not found — this script assumes Windows", file=sys.stderr)
        sys.exit(2)
    pdfmetrics.registerFont(TTFont("Malgun", str(MALGUN)))
    bold_name = "Malgun"
    if MALGUN_BOLD.exists():
        pdfmetrics.registerFont(TTFont("MalgunBold", str(MALGUN_BOLD)))
        bold_name = "MalgunBold"
    return "Malgun", bold_name


def build_styles(regular: str, bold: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["BodyText"]
    return {
        "h1": ParagraphStyle(
            "H1", parent=base, fontName=bold, fontSize=20, leading=26,
            spaceBefore=14, spaceAfter=10, alignment=1,
        ),
        "h2": ParagraphStyle(
            "H2", parent=base, fontName=bold, fontSize=15, leading=20,
            spaceBefore=16, spaceAfter=8,
        ),
        "h3": ParagraphStyle(
            "H3", parent=base, fontName=bold, fontSize=12, leading=17,
            spaceBefore=10, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "Body", parent=base, fontName=regular, fontSize=10.5, leading=16,
            spaceAfter=4,
        ),
        "meta": ParagraphStyle(
            "Meta", parent=base, fontName=regular, fontSize=9, leading=13,
            textColor="#555555", spaceAfter=2,
        ),
    }


def md_to_flowables(md_text: str, styles: dict[str, ParagraphStyle]) -> list:
    """Very small subset of markdown: # / ## / ### headings, --- hr, - list, paragraphs."""
    flowables: list = []
    lines = md_text.splitlines()
    buf: list[str] = []

    def flush_para(style_key: str = "body") -> None:
        if buf:
            text = " ".join(s.strip() for s in buf).strip()
            if text:
                flowables.append(Paragraph(escape_xml(text), styles[style_key]))
            buf.clear()

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            continue
        if line.startswith("# "):
            flush_para()
            flowables.append(Paragraph(escape_xml(line[2:].strip()), styles["h1"]))
            continue
        if line.startswith("## "):
            flush_para()
            flowables.append(Spacer(1, 6))
            flowables.append(Paragraph(escape_xml(line[3:].strip()), styles["h2"]))
            continue
        if line.startswith("### "):
            flush_para()
            flowables.append(Paragraph(escape_xml(line[4:].strip()), styles["h3"]))
            continue
        if line.strip() == "---":
            flush_para()
            flowables.append(Spacer(1, 8))
            continue
        if line.startswith("- "):
            flush_para()
            flowables.append(Paragraph("• " + escape_xml(line[2:].strip()), styles["body"]))
            continue
        if re.match(r"^\d+\.\s", line):
            flush_para()
            flowables.append(Paragraph(escape_xml(line.strip()), styles["body"]))
            continue
        buf.append(line)

    flush_para()
    return flowables


def escape_xml(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="markdown file path")
    ap.add_argument("--out", help="output PDF path (default: same stem + .pdf)")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"ERROR: input not found: {src}", file=sys.stderr)
        sys.exit(2)
    dst = Path(args.out) if args.out else src.with_suffix(".pdf")

    regular, bold = register_fonts()
    styles = build_styles(regular, bold)

    md_text = src.read_text(encoding="utf-8")
    flowables = md_to_flowables(md_text, styles)

    doc = SimpleDocTemplate(
        str(dst), pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm,
        topMargin=22 * mm, bottomMargin=22 * mm,
        title=src.stem,
    )
    doc.build(flowables)
    print(f"wrote {dst} ({dst.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
