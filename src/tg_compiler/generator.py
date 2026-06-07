from __future__ import annotations
import logging
from pathlib import Path
from datetime import date

from jinja2 import Environment, FileSystemLoader

from tg_compiler.triage import BriefingContent

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


def _importance_badge(score: int) -> str:
    if score >= 4:
        return "🔴"
    if score >= 3:
        return "🟡"
    return "🟢"


def render_markdown(content: BriefingContent) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    env.globals["importance_badge"] = _importance_badge
    tmpl = env.get_template("briefing.md.j2")
    return tmpl.render(content=content)


def generate_briefing(
    content: BriefingContent,
    output_dir: str,
    pdf: bool = False,
) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    date_str = content.date.isoformat()
    md_text = render_markdown(content)

    md_path = out / f"briefing_{date_str}.md"
    md_path.write_text(md_text)
    log.info("Markdown briefing saved to %s", md_path)

    if pdf:
        return _render_pdf(md_text, out, date_str)
    return md_path


def _render_pdf(md_text: str, out: Path, date_str: str) -> Path:
    from markdown_pdf import MarkdownPdf, Section

    pdf = MarkdownPdf(toc_level=2)
    pdf.meta["title"] = f"Daily Briefing {date_str}"
    pdf.add_section(Section(md_text))
    pdf_path = out / f"briefing_{date_str}.pdf"
    pdf.save(str(pdf_path))
    log.info("PDF briefing saved to %s", pdf_path)
    return pdf_path
