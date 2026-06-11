from __future__ import annotations
import html
import os
import re


def escape_html(text: str) -> str:
    """Escape &, < and > in LLM-derived text so it can't inject markup into rendered output."""
    if not text:
        return text
    return html.escape(text, quote=False)


def secure_file(path: str) -> None:
    """Restrict a sensitive file to owner read/write only, if it exists."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

_ENTITY_GARBAGE = re.compile(
    r'[`{}<>\[\]]|json|PostAnalysis|importance_score|urgency_score'
    r'|title|summary|category|image_substantive|post_id|credibility|relevance|reasoning'
    r'|key_entities|threat_level|image_description|\.json\('
    r'|\bfalse\b|\btrue\b|\bnull\b'
    r'|\bCRITICAL\b|\bHIGH\b|\bMODERATE\b|\bLOW\b'
    r'|Breaking News|Official Statement|Breaking news|Official statement'
)


def clean_entities(entities: list[str]) -> list[str]:
    return [
        e.strip() for e in entities
        if e
        and len(e.strip()) >= 2
        and len(e.strip()) <= 80
        and not re.fullmatch(r'\d+', e.strip())
        and not _ENTITY_GARBAGE.search(e)
    ]
