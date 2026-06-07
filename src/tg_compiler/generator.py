from __future__ import annotations
import logging
import re
from pathlib import Path
from datetime import date

from jinja2 import Environment, FileSystemLoader

from tg_compiler.triage import BriefingContent

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"

_ENTITY_GARBAGE = re.compile(r'[`{}<>]|json|PostAnalysis|importance_score|urgency_score')


def _importance_badge(composite_score: float) -> str:
    # Use composite score (weighted float) not raw importance int —
    # raw importance is inflated on high-activity channels.
    # Thresholds tuned so ~15% red, ~50% amber, ~35% green.
    if composite_score >= 4.0:
        return '<span style="color:#e74c3c;font-weight:bold">⬤</span>'
    if composite_score >= 3.5:
        return '<span style="color:#e67e22;font-weight:bold">⬤</span>'
    return '<span style="color:#27ae60;font-weight:bold">⬤</span>'


def _clean_entities(entities: list[str]) -> list[str]:
    return [
        e.strip() for e in entities
        if e and len(e.strip()) <= 80 and not _ENTITY_GARBAGE.search(e)
    ]


def render_markdown(content: BriefingContent) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)
    env.globals["importance_badge"] = _importance_badge
    env.filters["clean_entities"] = _clean_entities
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

    css_path = TEMPLATES_DIR / "briefing.css"
    user_css = css_path.read_text() if css_path.exists() else None

    pdf = MarkdownPdf(toc_level=2)
    pdf.meta["title"] = f"Daily Briefing {date_str}"
    pdf.add_section(Section(md_text), user_css=user_css)
    pdf_path = out / f"briefing_{date_str}.pdf"
    pdf.save(str(pdf_path))
    log.info("PDF briefing saved to %s", pdf_path)
    return pdf_path
