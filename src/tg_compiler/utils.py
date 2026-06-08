from __future__ import annotations
import re

# Tags whose content is also dangerous (strip the tag AND everything inside)
_BLOCK_TAGS = re.compile(
    r'<(script|style|iframe|object|embed|form)[^>]*>.*?</\1>',
    re.IGNORECASE | re.DOTALL,
)
# Void/self-closing dangerous tags — strip the tag only
_VOID_TAGS = re.compile(
    r'<(meta|link|base|input)[^>]*/?>',
    re.IGNORECASE,
)


def strip_dangerous_html(text: str) -> str:
    if not text:
        return text
    text = _BLOCK_TAGS.sub('', text)
    text = _VOID_TAGS.sub('', text)
    return text

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
