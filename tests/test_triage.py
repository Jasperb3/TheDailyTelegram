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
    ts = timestamp or datetime.now(timezone.utc)
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
    # Same summary text but beyond the 6h summary-similarity window → both kept (different events)
    summary = "Israeli airstrikes hit Dahiyeh suburb of Beirut Lebanon"
    p1, a1 = make_pair(msg_id=1, summary=summary,
                       timestamp=datetime(2026, 6, 7, 2, 0, tzinfo=timezone.utc))
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
    assert len(result.main_items) == 2  # both posts survive dedup
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
    assert len(result.main_items) == 2  # both posts survive dedup
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


def test_dedup_24h_entity_cluster_collapses_duplicate():
    # Two posts sharing 4+ entities with a 4-hour gap → deduplicated by 24h rule
    base_ts = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    p1, a1 = make_pair(importance=5, msg_id=1,
                       summary="Unrelated wording that shares no words with the next post",
                       timestamp=base_ts)
    a1.key_entities = ["Israel", "Iran", "IRGC", "Tel Aviv", "ballistic missile"]
    p2, a2 = make_pair(importance=3, msg_id=2,
                       summary="Completely different phrasing about an entirely other matter",
                       timestamp=base_ts + timedelta(hours=4))
    a2.key_entities = ["Israel", "Iran", "IRGC", "Tel Aviv", "drone strike"]
    # dedup_window_secs=3600 (1h) ensures the 4h gap is outside the primary window,
    # so only the entity-cluster pass can trigger the dedup.
    config = TriageConfig(min_composite_score=0.0, dedup_window_secs=3600)
    result = triage([(p1, a1), (p2, a2)], config)
    total = len(result.main_items) + len(result.appendix_items)
    assert total == 1
    kept = (result.main_items + result.appendix_items)[0]
    assert kept.post.message_id == 1  # higher-scored post wins


def test_null_score_excluded_from_main_and_appendix():
    # A post with a zero importance score (corrupt record) must be excluded entirely.
    post, analysis = make_pair(importance=0, summary="A valid summary that is long enough to pass")
    config = TriageConfig(min_composite_score=0.0)
    result = triage([(post, analysis)], config)
    assert len(result.main_items) == 0
    assert len(result.appendix_items) == 0


def test_none_score_excluded_from_main_and_appendix():
    # A post with a None score (missing column from old DB migration) must be excluded entirely.
    post, analysis = make_pair(summary="A valid summary that is long enough to pass")
    analysis.urgency_score = None
    config = TriageConfig(min_composite_score=0.0)
    result = triage([(post, analysis)], config)
    assert len(result.main_items) == 0
    assert len(result.appendix_items) == 0


def test_dedup_24h_entity_cluster_not_triggered_with_only_3_entities():
    # Two posts sharing only 3 entities with a 3-hour gap → NOT collapsed
    # (gap > 2h so old rule doesn't apply; new rule requires 4 shared entities)
    base_ts = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    p1, a1 = make_pair(importance=5, msg_id=1,
                       summary="Unrelated wording that shares no words with the next post",
                       timestamp=base_ts)
    a1.key_entities = ["Israel", "Iran", "IRGC", "Tel Aviv"]
    p2, a2 = make_pair(importance=3, msg_id=2,
                       summary="Completely different phrasing about an entirely other matter",
                       timestamp=base_ts + timedelta(hours=3))
    a2.key_entities = ["Israel", "Iran", "IRGC", "Hezbollah"]
    config = TriageConfig(min_composite_score=0.0)
    result = triage([(p1, a1), (p2, a2)], config)
    total = len(result.main_items) + len(result.appendix_items)
    assert total == 2


def test_skipped_category_excluded_from_main_and_appendix():
    post, analysis = make_pair(category="Skipped", summary="", importance=None, urgency=None,
                                credibility=None, relevance=None)
    config = TriageConfig(keywords=[], keyword_boost=0.5, min_composite_score=0.0)
    result = triage([(post, analysis)], config)
    assert result.main_items == []
    assert result.appendix_items == []


def test_dedup_extends_to_6h_summary_window():
    # Real-world regression: 2h19m apart, similar wording, just past the old 2h cutoff.
    p1, a1 = make_pair(
        msg_id=1, importance=5,
        title="Iran launches missiles at U.S. targets",
        summary="Reports indicate Iran launched missiles targeting US bases overnight.",
        timestamp=datetime(2026, 6, 7, 2, 45, tzinfo=timezone.utc),
    )
    p2, a2 = make_pair(
        msg_id=2, importance=3,
        title="Iran launches ballistic missiles at US military sites",
        summary="Iran launched ballistic missiles at US military sites overnight.",
        timestamp=datetime(2026, 6, 7, 5, 4, tzinfo=timezone.utc),
    )
    config = TriageConfig(min_composite_score=0.0)
    result = triage([(p1, a1), (p2, a2)], config)
    total = len(result.main_items) + len(result.appendix_items)
    assert total == 1
    kept = (result.main_items + result.appendix_items)[0]
    assert kept.post.message_id == 1


def test_recency_decay_demotes_older_post():
    now = datetime.now(timezone.utc)
    recent_post, recent_analysis = make_pair(msg_id=1, importance=4, urgency=4, credibility=4, relevance=4,
                                              timestamp=now - timedelta(hours=1),
                                              summary="A recent breaking development overseas")
    old_post, old_analysis = make_pair(msg_id=2, importance=4, urgency=4, credibility=4, relevance=4,
                                        timestamp=now - timedelta(hours=26),
                                        summary="An older unrelated development happened yesterday")
    config = TriageConfig(min_composite_score=0.0)
    result = triage([(recent_post, recent_analysis), (old_post, old_analysis)], config)
    by_id = {t.post.message_id: t.composite_score for t in result.main_items}
    assert by_id[1] > by_id[2]


def test_recency_floor_caps_decay():
    now = datetime.now(timezone.utc)
    post, analysis = make_pair(importance=4, urgency=4, credibility=4, relevance=4,
                                timestamp=now - timedelta(days=10))
    config = TriageConfig(min_composite_score=0.0, recency_half_life_hours=12.0, recency_floor=0.6)
    result = triage([(post, analysis)], config)
    base = 0.4 * 4 + 0.3 * 4 + 0.2 * 4 + 0.1 * 4
    assert abs(result.main_items[0].composite_score - base * 0.6) < 0.001


def test_executive_items_includes_all_critical_regardless_of_score():
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
    pairs = []
    # 10 high-scoring HIGH-severity items fill the executive summary
    for i in range(10):
        post, analysis = make_pair(msg_id=i, importance=5, urgency=5, credibility=5, relevance=5,
                                    summary=summaries[i])
        analysis.threat_level = "HIGH"
        pairs.append((post, analysis))
    # One CRITICAL item with a low composite score
    crit_post, crit_analysis = make_pair(msg_id=99, importance=2, urgency=2, credibility=2, relevance=2,
                                          summary="A critical low-scored but urgent unrelated alert")
    crit_analysis.threat_level = "CRITICAL"
    pairs.append((crit_post, crit_analysis))

    config = TriageConfig(min_composite_score=0.0, max_main_items=20)
    result = triage(pairs, config)
    assert len(result.executive_items) == 10
    assert any(t.post.message_id == 99 for t in result.executive_items)
