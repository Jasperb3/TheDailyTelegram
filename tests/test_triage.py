from datetime import datetime, date, timezone, timedelta
from tg_compiler.db import PostRecord, AnalysisRecord
from tg_compiler.triage import triage, TriagedPost, BriefingContent, _jaccard
from tg_compiler.config import TriageConfig


def make_pair(
    importance=3, urgency=3, credibility=3, relevance=3,
    category="Analysis", text="test post", msg_id=1,
    summary="A test post summary.", title="", channel_name="test",
    timestamp=None,
):
    ts = timestamp or datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    post = PostRecord(
        id=msg_id, channel_id=1, channel_name=channel_name, message_id=msg_id,
        timestamp=ts,
        text=text, media_paths=[], has_images=False, raw_json="{}",
    )
    analysis = AnalysisRecord(
        id=msg_id, post_id=msg_id, summary=summary, title=title,
        importance_score=importance, urgency_score=urgency,
        credibility_score=credibility, relevance_score=relevance,
        category=category, key_entities=[], model_used="test",
    )
    return post, analysis


def test_composite_score_formula():
    post, analysis = make_pair(importance=5, urgency=4, credibility=3, relevance=2)
    config = TriageConfig(keywords=[], keyword_boost=0.5, min_composite_score=0.0)
    result = triage([(post, analysis)], config)
    expected = 0.4 * 5 + 0.3 * 4 + 0.2 * 3 + 0.1 * 2
    assert abs(result.main_items[0].composite_score - expected) < 0.001


def test_keyword_boost_applied():
    post, analysis = make_pair(importance=2, text="breaking news: urgent launch event")
    config = TriageConfig(keywords=["breaking", "urgent"], keyword_boost=1.0, min_composite_score=0.0)
    result = triage([(post, analysis)], config)
    base = 0.4 * 2 + 0.3 * 3 + 0.2 * 3 + 0.1 * 3
    assert result.main_items[0].composite_score > base


def test_keyword_boost_capped_at_five():
    post, analysis = make_pair(importance=5, urgency=5, credibility=5, relevance=5, text="urgent")
    config = TriageConfig(keywords=["urgent"], keyword_boost=2.0, min_composite_score=0.0)
    result = triage([(post, analysis)], config)
    assert result.main_items[0].composite_score <= 5.0


def test_below_threshold_goes_to_appendix():
    post, analysis = make_pair(importance=1, urgency=1, credibility=1, relevance=1)
    config = TriageConfig(min_composite_score=3.0)
    result = triage([(post, analysis)], config)
    assert len(result.main_items) == 0
    assert len(result.appendix_items) == 1


def test_sorted_by_score_descending():
    pairs = [make_pair(importance=i, msg_id=i) for i in range(1, 5)]
    config = TriageConfig(min_composite_score=0.0)
    result = triage(pairs, config)
    scores = [t.composite_score for t in result.main_items]
    assert scores == sorted(scores, reverse=True)


def test_briefing_content_collects_channel_names():
    pairs = [make_pair()]
    config = TriageConfig(min_composite_score=0.0)
    result = triage(pairs, config)
    assert isinstance(result, BriefingContent)
    assert "test" in result.channel_names


def test_keyword_boost_applies_only_once():
    # Text matches both keywords; only one boost should be applied
    post, analysis = make_pair(importance=2, text="breaking urgent event")
    config = TriageConfig(keywords=["breaking", "urgent"], keyword_boost=1.0, min_composite_score=0.0)
    result = triage([(post, analysis)], config)
    base = 0.4 * 2 + 0.3 * 3 + 0.2 * 3 + 0.1 * 3
    # Exactly one boost: base + 1.0
    assert abs(result.main_items[0].composite_score - (base + 1.0)) < 0.001


def test_jaccard_similarity():
    assert _jaccard("Israeli airstrikes hit Dahiyeh Beirut", "Israeli airstrikes struck Dahiyeh Beirut Lebanon") > 0.3
    assert _jaccard("the weather is fine today", "completely unrelated topic about rockets") < 0.1


def test_dedup_removes_similar_post():
    # Two nearly-identical summaries within 1 hour → only the higher-scored one kept
    base_ts = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    summary = "Israeli airstrikes were reported in the Dahiyeh suburb of Beirut Lebanon"
    p1, a1 = make_pair(importance=5, msg_id=1, summary=summary, timestamp=base_ts)
    p2, a2 = make_pair(importance=3, msg_id=2,
                       summary="Israeli airstrikes reported in Dahiyeh suburb Beirut",
                       timestamp=base_ts + timedelta(minutes=30))
    config = TriageConfig(min_composite_score=0.0)
    result = triage([(p1, a1), (p2, a2)], config)
    total = len(result.main_items) + len(result.appendix_items)
    assert total == 1
    # The higher-scored post wins
    kept = (result.main_items + result.appendix_items)[0]
    assert kept.post.message_id == 1


def test_dedup_keeps_dissimilar_posts():
    base_ts = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    p1, a1 = make_pair(msg_id=1,
                       summary="Israeli airstrikes hit Dahiyeh suburb of Beirut",
                       timestamp=base_ts)
    p2, a2 = make_pair(msg_id=2,
                       summary="Ceasefire negotiations resumed in Qatar between warring parties",
                       timestamp=base_ts + timedelta(minutes=30))
    config = TriageConfig(min_composite_score=0.0)
    result = triage([(p1, a1), (p2, a2)], config)
    total = len(result.main_items) + len(result.appendix_items)
    assert total == 2


def test_dedup_ignores_outside_time_window():
    # Same summary text but >2 hours apart → both kept (different events)
    summary = "Israeli airstrikes hit Dahiyeh suburb of Beirut Lebanon"
    p1, a1 = make_pair(msg_id=1, summary=summary,
                       timestamp=datetime(2026, 6, 7, 8, 0, tzinfo=timezone.utc))
    p2, a2 = make_pair(msg_id=2, summary=summary,
                       timestamp=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc))
    config = TriageConfig(min_composite_score=0.0)
    result = triage([(p1, a1), (p2, a2)], config)
    total = len(result.main_items) + len(result.appendix_items)
    assert total == 2


def test_max_main_items_cap():
    # 10 high-score posts with dissimilar summaries, max_main_items=3 → 3 in main, 7 in appendix
    summaries = [
        "Airstrikes hit Beirut overnight causing significant damage",
        "Ceasefire negotiations collapsed after delegates walked out",
        "President signed emergency decree expanding military powers",
        "Floods displaced thousands across southern provinces",
        "Opposition leader arrested on espionage charges",
        "Oil pipeline sabotaged near border crossing",
        "Evacuation ordered for coastal settlements ahead of storm",
        "Diplomatic envoy expelled following spy scandal",
        "Rebel forces captured strategic bridge over river",
        "Parliament dissolved after vote of no confidence passed",
    ]
    pairs = [make_pair(importance=5, msg_id=i, summary=summaries[i]) for i in range(10)]
    config = TriageConfig(min_composite_score=0.0, max_main_items=3)
    result = triage(pairs, config)
    assert len(result.main_items) == 3
    assert len(result.appendix_items) == 7


def test_category_counts_populated():
    pairs = [
        make_pair(msg_id=1, summary="Breaking event in the northern region", category="Breaking News"),
        make_pair(msg_id=2, summary="Analysis of the diplomatic situation", category="Analysis"),
        make_pair(msg_id=3, summary="Official statement from government sources", category="Breaking News"),
    ]
    config = TriageConfig(min_composite_score=0.0)
    result = triage(pairs, config)
    assert result.category_counts["Breaking News"] == 2
    assert result.category_counts["Analysis"] == 1


def test_severity_tiebreaker_critical_over_high():
    # Both posts have the same composite score (5.0 via keyword boost); CRITICAL should rank first.
    base_ts = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    p1, a1 = make_pair(importance=5, urgency=5, credibility=5, relevance=5,
                       msg_id=1, summary="Critical event requiring immediate attention", timestamp=base_ts)
    a1.threat_level = "HIGH"
    p2, a2 = make_pair(importance=5, urgency=5, credibility=5, relevance=5,
                       msg_id=2, summary="Another event of critical severity level found", timestamp=base_ts)
    a2.threat_level = "CRITICAL"
    config = TriageConfig(min_composite_score=0.0)
    result = triage([(p1, a1), (p2, a2)], config)
    assert result.main_items[0].post.message_id == 2  # CRITICAL outranks HIGH


def test_severity_tiebreaker_high_over_moderate():
    # Both posts have the same composite score; HIGH should rank above MODERATE.
    base_ts = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    p1, a1 = make_pair(importance=5, urgency=5, credibility=5, relevance=5,
                       msg_id=1, summary="Moderate situation developing in the region", timestamp=base_ts)
    a1.threat_level = "MODERATE"
    p2, a2 = make_pair(importance=5, urgency=5, credibility=5, relevance=5,
                       msg_id=2, summary="High severity incident reported at border crossing", timestamp=base_ts)
    a2.threat_level = "HIGH"
    config = TriageConfig(min_composite_score=0.0)
    result = triage([(p1, a1), (p2, a2)], config)
    assert result.main_items[0].post.message_id == 2  # HIGH outranks MODERATE


def test_dedup_entity_overlap():
    # Same entities, same time window → duplicate
    base_ts = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    p1, a1 = make_pair(importance=5, msg_id=1,
                       summary="Unrelated text here with no word overlap whatsoever",
                       timestamp=base_ts)
    a1.key_entities = ["Israel", "Beirut", "Hezbollah", "Lebanon", "IDF"]
    p2, a2 = make_pair(importance=3, msg_id=2,
                       summary="Completely different phrasing and words in this one",
                       timestamp=base_ts + timedelta(minutes=45))
    a2.key_entities = ["Israel", "Beirut", "Hezbollah", "Lebanon", "strikes"]
    config = TriageConfig(min_composite_score=0.0)
    result = triage([(p1, a1), (p2, a2)], config)
    total = len(result.main_items) + len(result.appendix_items)
    assert total == 1
