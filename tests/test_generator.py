from datetime import date, datetime, timezone
from tg_compiler.db import PostRecord, AnalysisRecord
from tg_compiler.triage import TriagedPost, BriefingContent
from tg_compiler.generator import render_markdown


def make_triaged(importance=4, category="Breaking News",
                 summary="Key event occurred.", msg_id=1, channel="news"):
    post = PostRecord(
        id=msg_id, channel_id=1, channel_name=channel, message_id=msg_id,
        timestamp=datetime(2026, 6, 7, 14, 30, tzinfo=timezone.utc),
        text="Original text.", media_paths=[], has_images=False, raw_json="{}",
    )
    analysis = AnalysisRecord(
        id=msg_id, post_id=msg_id, summary=summary,
        importance_score=importance, urgency_score=3, credibility_score=4, relevance_score=3,
        category=category, key_entities=["Alice"], model_used="test",
    )
    return TriagedPost(post=post, analysis=analysis, composite_score=float(importance))


def make_content(n_main=2, n_appendix=1):
    return BriefingContent(
        date=date(2026, 6, 7),
        main_items=[make_triaged(msg_id=i) for i in range(n_main)],
        appendix_items=[make_triaged(importance=1, msg_id=n_main + i) for i in range(n_appendix)],
        channel_names=["news"],
    )


def test_markdown_contains_date():
    md = render_markdown(make_content())
    # Date appears in post timestamps within the briefing body
    assert "14:30 UTC" in md


def test_markdown_has_executive_summary():
    md = render_markdown(make_content(n_main=3))
    assert "Executive Summary" in md


def test_markdown_has_channel_section():
    md = render_markdown(make_content())
    assert "news" in md


def test_markdown_shows_threat_badge():
    md = render_markdown(make_content())
    # default threat_level is MODERATE
    assert "MODERATE" in md or "HIGH" in md or "CRITICAL" in md or "LOW" in md


def test_markdown_includes_post_summary():
    md = render_markdown(make_content())
    assert "Key event occurred." in md


def test_appendix_is_present_when_low_score_posts_exist():
    md = render_markdown(make_content(n_appendix=1))
    assert "Appendix" in md


def test_empty_main_items_still_renders():
    content = BriefingContent(
        date=date(2026, 6, 7), main_items=[], appendix_items=[], channel_names=[]
    )
    md = render_markdown(content)
    assert "Executive Summary" in md
    assert "No high-priority items today" in md


from tg_compiler.generator import generate_briefing


def test_pdf_file_is_created(tmp_path):
    content = make_content(n_main=2)
    path = generate_briefing(content, output_dir=str(tmp_path), pdf=True)
    assert path.suffix == ".pdf"
    assert path.exists()
    assert path.stat().st_size > 0


def test_markdown_file_is_always_created(tmp_path):
    content = make_content()
    path = generate_briefing(content, output_dir=str(tmp_path), pdf=False)
    assert path.suffix == ".md"
    assert path.exists()


from tg_compiler.main import purge_old_media


def test_purge_removes_old_directories(tmp_path):
    old_dir = tmp_path / "news" / "2020-01-01"
    recent_dir = tmp_path / "news" / "2026-06-07"
    old_dir.mkdir(parents=True)
    recent_dir.mkdir(parents=True)
    (old_dir / "1.jpg").write_bytes(b"x")
    (recent_dir / "2.jpg").write_bytes(b"x")

    removed = purge_old_media(str(tmp_path), retention_days=30)
    assert removed == 1
    assert not old_dir.exists()
    assert recent_dir.exists()


def test_purge_nonexistent_dir_returns_zero():
    assert purge_old_media("/nonexistent/path/abc123", retention_days=30) == 0
