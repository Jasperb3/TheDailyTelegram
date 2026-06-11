from __future__ import annotations
from collections import Counter
from datetime import date

from tg_compiler.db import PostRecord, AnalysisRecord
from tg_compiler.triage import _normalize_entity

TREND_WINDOW_DAYS = 7
TOP_N_ENTITIES = 10


def compute_trends(history: list[tuple[PostRecord, AnalysisRecord]], target_date: date) -> dict:
    """Split a multi-day history into today vs prior days and compute
    entity/category mention deltas plus entities first seen today."""
    today_entities: Counter[str] = Counter()
    today_categories: Counter[str] = Counter()
    prior_entities: Counter[str] = Counter()
    prior_categories: Counter[str] = Counter()

    for post, analysis in history:
        is_today = post.timestamp.date() == target_date
        entity_bucket = today_entities if is_today else prior_entities
        category_bucket = today_categories if is_today else prior_categories

        for entity in {_normalize_entity(e) for e in analysis.key_entities if e}:
            entity_bucket[entity] += 1
        if analysis.category:
            category_bucket[analysis.category] += 1

    entity_deltas = [
        {
            "entity": entity,
            "today_count": today_count,
            "prior_count": prior_entities.get(entity, 0),
        }
        for entity, today_count in today_entities.items()
    ]
    entity_deltas.sort(key=lambda d: d["today_count"] - d["prior_count"], reverse=True)
    entity_deltas = entity_deltas[:TOP_N_ENTITIES]

    category_deltas = [
        {
            "category": category,
            "today_count": today_count,
            "prior_count": prior_categories.get(category, 0),
        }
        for category, today_count in today_categories.items()
    ]
    category_deltas.sort(key=lambda d: d["today_count"] - d["prior_count"], reverse=True)

    emerging_entities = sorted(
        entity for entity, today_count in today_entities.items()
        if today_count > 0 and prior_entities.get(entity, 0) == 0
    )

    return {
        "entity_deltas": entity_deltas,
        "category_deltas": category_deltas,
        "emerging_entities": emerging_entities,
    }
