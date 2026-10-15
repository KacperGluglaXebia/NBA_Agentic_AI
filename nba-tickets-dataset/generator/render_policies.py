"""Render the HoopsPass policy documents (knowledge/policies/*.md) to PDF.

The PDFs are the source documents of the Knowledge Assistant and of the RAG index in
notebook 03. The Markdown files stay the source of truth; re-run this after editing them.

    uv run --with fpdf2 python generator/render_policies.py
"""
import argparse
import re
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]


class PolicyPDF(FPDF):
    def __init__(self, doc_id: str):
        super().__init__(format="A4")
        self.doc_id = doc_id
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(20, 18, 20)

    def header(self):
        self.set_font("Helvetica", "", 8)
        self.set_text_color(120)
        self.cell(0, 6, f"HoopsPass Inc. (fictional) - {self.doc_id}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0)

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(120)
        self.cell(0, 6, f"Page {self.page_no()} - synthetic document for the HoopsPass demo", align="C")


def render(md_path: Path, out_dir: Path) -> Path:
    text = md_path.read_text(encoding="utf-8")
    doc_id = (re.search(r"Document (HP-POL-\d+)", text) or [None, md_path.stem])[1]
    pdf = PolicyPDF(doc_id)
    pdf.add_page()
    table: list[list[str]] = []

    def flush_table():
        if not table:
            return
        pdf.set_font("Helvetica", "", 9)
        with pdf.table(first_row_as_headings=True, line_height=5.5) as t:
            for cells in table:
                row = t.row()
                for c in cells:
                    row.cell(c)
        pdf.ln(3)
        table.clear()

    for line in text.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not all(set(c) <= set("-: ") for c in cells):
                table.append(cells)
            continue
        flush_table()
        if line.startswith("# "):
            pdf.set_font("Helvetica", "B", 16)
            pdf.multi_cell(0, 8, line[2:], new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif line.startswith("## "):
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 12)
            pdf.multi_cell(0, 7, line[3:], new_x="LMARGIN", new_y="NEXT")
        elif line.startswith("Document HP-POL"):
            pdf.set_font("Helvetica", "I", 9)
            pdf.multi_cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        elif line.strip():
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 5.2, line, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1.5)
    flush_table()
    out = out_dir / f"{md_path.stem}.pdf"
    pdf.output(str(out))
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--src", default=str(ROOT / "knowledge" / "policies"))
    p.add_argument("--out", default=str(ROOT / "knowledge" / "pdf"))
    a = p.parse_args()
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for md in sorted(Path(a.src).glob("*.md")):
        print(render(md, out_dir).relative_to(ROOT))
