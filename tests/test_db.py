from tg_compiler.db import AnalysisRecord


def test_insert_and_fetch_post(db, sample_post):
    post_id = db.insert_post(sample_post)
    assert post_id > 0
    fetched = db.get_post(post_id)
    assert fetched.message_id == 42
    assert fetched.text == "Hello world"
    assert fetched.channel_id == 100


def test_duplicate_post_is_ignored(db, sample_post):
    db.insert_post(sample_post)
    second_id = db.insert_post(sample_post)
    assert second_id is None


def test_get_last_seen_id_defaults_to_zero(db):
    assert db.get_last_seen_id(channel_id=999) == 0


def test_set_and_get_last_seen_id(db):
    db.set_last_seen_id(channel_id=100, message_id=500)
    assert db.get_last_seen_id(channel_id=100) == 500


def test_get_unanalysed_posts(db, sample_post):
    post_id = db.insert_post(sample_post)
    unanalysed = db.get_unanalysed_posts()
    assert any(p.id == post_id for p in unanalysed)


def test_insert_analysis_and_joined_query(db, sample_post):
    post_id = db.insert_post(sample_post)
    rec = AnalysisRecord(
        post_id=post_id,
        summary="Test summary",
        importance_score=3,
        urgency_score=2,
        credibility_score=4,
        relevance_score=3,
        category="Analysis",
        key_entities=["Alice"],
        model_used="test-model",
    )
    a_id = db.insert_analysis(rec)
    assert a_id > 0

    results = db.get_days_posts_with_analyses("2026-06-07")
    assert len(results) == 1
    post, analysis = results[0]
    assert post.message_id == 42
    assert analysis.importance_score == 3
    assert "Alice" in analysis.key_entities
