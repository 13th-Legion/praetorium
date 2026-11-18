#!/usr/bin/env python3
"""Regenerate a TRADOC static PDF from its HTML partial template.

Requires WeasyPrint (pip install weasyprint). Renders the partial body
(e.g. app/templates/tradoc/cof.html) inside a print-friendly standalone
document and writes the PDF.

Usage:
    gen_tradoc_pdf.py <template.html> <output.pdf>

Example:
    gen_tradoc_pdf.py app/templates/tradoc/cof.html app/static/tradoc/course-of-fire.pdf
"""
import re
import sys

from weasyprint import HTML

PRINT_CSS = """
@page { size: Letter; margin: 18mm 16mm 18mm 16mm; }
body { font-family: 'DejaVu Sans', sans-serif; font-size: 10.5pt; line-height: 1.45; color: #111; }
.tradoc-content { max-width: 100%; }
.header { text-align: center; border-bottom: 2px solid #333; padding-bottom: 12px; margin-bottom: 20px; }
.header h1 { font-size: 20pt; margin: 0 0 4px; }
.header .sub { font-size: 12pt; margin: 0; color: #333; }
.header .ref { font-size: 9pt; margin: 4px 0 0; color: #555; }
h2 { font-size: 13pt; margin: 20px 0 6px; border-bottom: 1px solid #ccc; padding-bottom: 2px; }
h3 { font-size: 11.5pt; margin: 14px 0 4px; }
h4 { font-size: 10.5pt; margin: 12px 0 4px; }
p { margin: 5px 0; }
ul, ol { margin: 6px 0 6px 20px; padding: 0; }
li { margin: 3px 0; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; }
th, td { border: 1px solid #999; padding: 4px 7px; text-align: left; vertical-align: top; }
th { background: #eee; font-weight: bold; }
.ammo-summary table { width: auto; min-width: 60%; }
.note { background: #f5f5f5; border-left: 3px solid #666; padding: 6px 10px; margin: 8px 0; }
.warning { background: #fdf0e0; border-left: 3px solid #c60; padding: 6px 10px; margin: 8px 0; }
.legal { background: #eef4fa; border-left: 3px solid #357; padding: 6px 10px; margin: 8px 0; font-size: 9.5pt; }
.stage { margin: 10px 0; }
.footer { margin-top: 24px; padding-top: 8px; border-top: 1px solid #999; text-align: center; font-size: 9pt; color: #555; }
code { font-family: 'DejaVu Sans Mono', monospace; font-size: 9.5pt; }
strong { font-weight: bold; }
"""


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    template, out = sys.argv[1], sys.argv[2]
    src = open(template, encoding="utf-8").read()
    src = re.sub(r"\{#.*?#\}", "", src, flags=re.DOTALL)
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>" + PRINT_CSS + "</style></head><body>" + src + "</body></html>"
    )
    HTML(string=html).write_pdf(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
