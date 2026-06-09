from __future__ import annotations
import logging
from pathlib import Path
from datetime import date, datetime

from jinja2 import Environment, FileSystemLoader

from tg_compiler.triage import BriefingContent
from tg_compiler.utils import clean_entities

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


_THREAT_BADGES = {
    "CRITICAL": '<span class="badge badge-critical">CRITICAL</span>',
    "HIGH":     '<span class="badge badge-high">HIGH</span>',
    "MODERATE": '<span class="badge badge-moderate">MODERATE</span>',
    "LOW":      '<span class="badge badge-low">LOW</span>',
}


def _threat_badge(threat_level: str) -> str:
    return _THREAT_BADGES.get(threat_level, "🟡 MODERATE")


def render_markdown(content: BriefingContent) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)
    env.globals["threat_badge"] = _threat_badge
    env.filters["clean_entities"] = clean_entities
    tmpl = env.get_template("briefing.md.j2")
    return tmpl.render(content=content)


def generate_briefing(
    content: BriefingContent,
    output_dir: str,
    pdf: bool = False,
) -> Path:
    date_str = content.date.isoformat()
    date_dir = Path(output_dir) / date_str
    date_dir.mkdir(parents=True, exist_ok=True)
    md_text = render_markdown(content)

    md_path = date_dir / f"briefing_{date_str}.md"
    md_path.write_text(md_text)
    log.info("Markdown briefing saved to %s", md_path)

    if pdf:
        return _render_pdf(md_text, date_dir, date_str)
    return md_path


def _render_pdf(md_text: str, out: Path, date_str: str) -> Path:
    from markdown_pdf import MarkdownPdf, Section

    css_path = TEMPLATES_DIR / "briefing.css"
    user_css = css_path.read_text() if css_path.exists() else None

    ts = datetime.now().strftime("%H%M%S")
    pdf_obj = MarkdownPdf(toc_level=0)
    pdf_obj.meta["title"] = f"The Daily Telegram {date_str}"
    pdf_obj.add_section(Section(md_text), user_css=user_css)
    pdf_path = out / f"TheDailyTelegram_{date_str}_{ts}.pdf"
    pdf_obj.save(str(pdf_path))
    log.info("PDF briefing saved to %s", pdf_path)
    return pdf_path
