from datetime import date, datetime, timezone

from tg_compiler.db import PostRecord, AnalysisRecord
from tg_compiler.trends import compute_trends


def _pair(msg_id, day, category, entities):
    post = PostRecord(
        channel_id=1, channel_name="chan", message_id=msg_id,
        timestamp=datetime(2026, 6, day, 12, 0, tzinfo=timezone.utc),
        text="x", media_paths=[], has_images=False, raw_json="{}",
    )
    analysis = AnalysisRecord(
        post_id=msg_id, summary="s", importance_score=3, urgency_score=2,
        credibility_score=4, relevance_score=3, category=category,
        key_entities=entities, model_used="test",
    )
    return post, analysis


def test_entity_and_category_deltas():
    history = [
        _pair(1, 9, "Military", ["Bahrain", "Iran"]),
        _pair(2, 9, "Military", ["Bahrain"]),
        _pair(3, 8, "Military", ["Iran"]),
        _pair(4, 7, "Analysis", ["Iran"]),
    ]
    trends = compute_trends(history, date(2026, 6, 9))

    by_entity = {d["entity"]: d for d in trends["entity_deltas"]}
    assert by_entity["bahrain"] == {"entity": "bahrain", "today_count": 2, "prior_count": 0}
    assert by_entity["iran"] == {"entity": "iran", "today_count": 1, "prior_count": 2}

    by_category = {d["category"]: d for d in trends["category_deltas"]}
    assert by_category["military".title()] == {"category": "Military", "today_count": 2, "prior_count": 1}


def test_emerging_entities_unseen_in_prior_days():
    history = [
        _pair(1, 9, "Military", ["Bahrain"]),
        _pair(2, 8, "Military", ["Iran"]),
    ]
    trends = compute_trends(history, date(2026, 6, 9))
    assert trends["emerging_entities"] == ["bahrain"]


def test_entity_alias_normalization_merges_history():
    history = [
        _pair(1, 9, "Military", ["U.S."]),
        _pair(2, 8, "Military", ["United States"]),
    ]
    trends = compute_trends(history, date(2026, 6, 9))

    assert trends["emerging_entities"] == []
    by_entity = {d["entity"]: d for d in trends["entity_deltas"]}
    assert by_entity["united states"] == {"entity": "united states", "today_count": 1, "prior_count": 1}


def test_no_history_returns_empty_results():
    trends = compute_trends([], date(2026, 6, 9))
    assert trends == {"entity_deltas": [], "category_deltas": [], "emerging_entities": []}
