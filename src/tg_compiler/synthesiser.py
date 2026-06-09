from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from openai import OpenAI

from tg_compiler.config import AppConfig
from tg_compiler.db import Database
from tg_compiler.utils import strip_dangerous_html

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"

_SYNTHESIS_SYSTEM = (
    "You are a senior intelligence analyst producing a daily assessment. "
    "You will receive a structured list of the highest-priority intelligence reports "
    "from a 24-hour monitoring period. Produce a concise, structured assessment. "
    "Return ONLY valid JSON. No preamble, no markdown fences, no commentary outside the JSON object."
)

_SYNTHESIS_INSTRUCTIONS = """
Based on these intelligence reports, produce a JSON object with exactly these four keys:

"situation_summary": A 3-5 sentence executive overview of the overall geopolitical situation. Write as an intelligence analyst, not a journalist. Be direct and specific — name actors, locations, and developments.

"key_themes": An array of 3-5 objects. Each object: {"theme": "<short title>", "detail": "<2-3 sentences explaining what events are connected and why the pattern matters>"}

"signals_and_warnings": An array of 3-5 objects. Each object: {"signal": "<short title>", "assessment": "<what could develop next and what observable indicators to watch for>"}

"named_actors": An array of the 4-6 most significant actors from today's reporting. Each object: {"actor": "<name>", "role": "<one short phrase>", "activity": "<1-2 sentences on what they did today and its significance>"}
"""


def _validate_intel(data: dict) -> str | None:
    """Return an error string if validation fails, or None if valid."""
    required_keys = {"situation_summary", "key_themes", "signals_and_warnings", "named_actors"}
    missing = required_keys - data.keys()
    if missing:
        return f"missing keys: {missing}"
    if not isinstance(data["situation_summary"], str) or not data["situation_summary"].strip():
        return "situation_summary is empty"
    for key in ("key_themes", "signals_and_warnings", "named_actors"):
        val = data[key]
        if not isinstance(val, list) or not val:
            return f"{key} is empty or not a list"
    for item in data["key_themes"]:
        if "theme" not in item or "detail" not in item:
            return "key_themes item missing 'theme' or 'detail'"
    for item in data["signals_and_warnings"]:
        if "signal" not in item or "assessment" not in item:
            return "signals_and_warnings item missing 'signal' or 'assessment'"
    for item in data["named_actors"]:
        if "actor" not in item or "role" not in item or "activity" not in item:
            return "named_actors item missing required sub-key"
    return None


async def synthesise(config: AppConfig, posts: list[dict]) -> dict | None:
    cfg = config.lmstudio
    try:
        client = OpenAI(
            base_url=f"http://{cfg.server_host}:{cfg.server_port}/v1",
            api_key=cfg.api_token or "lm-studio",
        )
    except Exception as e:
        log.error("LM Studio not reachable — cannot generate intelligence front page: %s", e)
        return None

    posts_json = json.dumps(posts, ensure_ascii=False, default=str)
    user_message = f"{posts_json}\n{_SYNTHESIS_INSTRUCTIONS}"

    try:
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=cfg.model,
                messages=[
                    {"role": "system", "content": _SYNTHESIS_SYSTEM},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.2,
                max_tokens=3000,
            )
        )
    except Exception as e:
        log.error("LM Studio not reachable — cannot generate intelligence front page: %s", e)
        return None

    raw = response.choices[0].message.content or ""
    log.debug("Synthesis raw response: %s", raw)

    # Strip markdown fences if the model wrapped the JSON despite instructions
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as e:
        log.error("Intelligence synthesis failed: JSON parse error: %s", e)
        return None

    error = _validate_intel(data)
    if error:
        log.error("Intelligence synthesis failed: %s", error)
        return None

    return data


def _sanitize_intel(intel: dict) -> dict:
    s = strip_dangerous_html
    return {
        "situation_summary": s(intel["situation_summary"]),
        "key_themes": [
            {"theme": s(i["theme"]), "detail": s(i["detail"])}
            for i in intel["key_themes"]
        ],
        "signals_and_warnings": [
            {"signal": s(i["signal"]), "assessment": s(i["assessment"])}
            for i in intel["signals_and_warnings"]
        ],
        "named_actors": [
            {"actor": s(i["actor"]), "role": s(i["role"]), "activity": s(i["activity"])}
            for i in intel["named_actors"]
        ],
    }


def _render_front_page_md(intel: dict, target_date: date) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)
    tmpl = env.get_template("intel_front_page.md.j2")
    clean = _sanitize_intel(intel)
    return tmpl.render(
        date=target_date.strftime("%A, %-d %B %Y"),
        situation_summary=clean["situation_summary"],
        key_themes=clean["key_themes"],
        signals_and_warnings=clean["signals_and_warnings"],
        named_actors=clean["named_actors"],
    )


def _md_to_pdf(md_text: str, date_str: str, date_dir: Path) -> Path:
    from markdown_pdf import MarkdownPdf, Section

    css_path = TEMPLATES_DIR / "briefing.css"
    user_css = css_path.read_text() if css_path.exists() else None

    pdf_obj = MarkdownPdf(toc_level=0)
    pdf_obj.meta["title"] = f"The Daily Telegram — Intelligence Assessment {date_str}"
    pdf_obj.add_section(Section(md_text), user_css=user_css)
    pdf_path = date_dir / f"intel_front_{date_str}.pdf"
    pdf_obj.save(str(pdf_path))
    return pdf_path


def _prepend_pdf(front_page_path: Path, briefing_path: Path) -> None:
    from pypdf import PdfWriter, PdfReader

    writer = PdfWriter()
    for page in PdfReader(str(front_page_path)).pages:
        writer.add_page(page)
    for page in PdfReader(str(briefing_path)).pages:
        writer.add_page(page)

    fd, tmp_path = tempfile.mkstemp(dir=briefing_path.parent, suffix=".tmp.pdf")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as f:
            writer.write(f)
        os.replace(tmp_path, briefing_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


async def run_analysis(config: AppConfig, target_date: date) -> None:
    date_str = target_date.isoformat()
    date_dir = Path(config.generation.output_dir) / date_str

    # Find the most recent TheDailyTelegram PDF in the date subdirectory
    pdfs = sorted(date_dir.glob("TheDailyTelegram_*.pdf")) if date_dir.exists() else []
    if not pdfs:
        log.error("No briefing found for %s. Run --batch first.", date_str)
        return
    briefing_path = pdfs[-1]  # latest by timestamped filename

    db = Database(config.storage.db_path)
    db.init_schema()

    posts = db.get_top_posts_for_date(date_str, limit=config.triage.max_main_items)
    if not posts:
        log.error("No analysed posts found for %s — cannot generate intelligence front page", date_str)
        return

    log.info("Synthesising intelligence assessment from %d posts…", len(posts))
    intel = await synthesise(config, posts)
    if intel is None:
        return

    md = _render_front_page_md(intel, target_date)
    front_page_pdf = _md_to_pdf(md, date_str, date_dir)

    try:
        _prepend_pdf(front_page_pdf, briefing_path)
    finally:
        front_page_pdf.unlink(missing_ok=True)

    log.info("Intelligence front page prepended → %s", briefing_path)
