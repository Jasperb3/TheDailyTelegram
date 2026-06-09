"""Tests for synthesiser JSON validation and PDF merge."""
import pytest
from tg_compiler.synthesiser import _validate_intel, _prepend_pdf


# ---------------------------------------------------------------------------
# _validate_intel
# ---------------------------------------------------------------------------

def _good_intel() -> dict:
    return {
        "situation_summary": "Tensions remain elevated across the region.",
        "key_themes": [{"theme": "Escalation", "detail": "Fighting has intensified."}],
        "signals_and_warnings": [{"signal": "Airspace closure", "assessment": "Watch for further closures."}],
        "named_actors": [{"actor": "PM Netanyahu", "role": "Israeli PM", "activity": "Ordered strikes."}],
    }


def test_validate_intel_valid_passes():
    assert _validate_intel(_good_intel()) is None


def test_validate_intel_missing_key_fails():
    data = _good_intel()
    del data["situation_summary"]
    assert _validate_intel(data) is not None


def test_validate_intel_empty_situation_summary_fails():
    data = _good_intel()
    data["situation_summary"] = "   "
    assert _validate_intel(data) is not None


@pytest.mark.parametrize("key", ["key_themes", "signals_and_warnings", "named_actors"])
def test_validate_intel_empty_list_fails(key):
    data = _good_intel()
    data[key] = []
    assert _validate_intel(data) is not None


def test_validate_intel_key_themes_missing_subkey():
    data = _good_intel()
    data["key_themes"] = [{"theme": "X"}]  # missing 'detail'
    assert _validate_intel(data) is not None


def test_validate_intel_signals_missing_subkey():
    data = _good_intel()
    data["signals_and_warnings"] = [{"signal": "X"}]  # missing 'assessment'
    assert _validate_intel(data) is not None


def test_validate_intel_named_actors_missing_subkey():
    data = _good_intel()
    data["named_actors"] = [{"actor": "X", "role": "Y"}]  # missing 'activity'
    assert _validate_intel(data) is not None


# ---------------------------------------------------------------------------
# _prepend_pdf
# ---------------------------------------------------------------------------

def test_prepend_pdf_merges_pages(tmp_path):
    from pypdf import PdfWriter, PdfReader

    def make_pdf(path, n_pages=1):
        writer = PdfWriter()
        for _ in range(n_pages):
            writer.add_blank_page(width=612, height=792)
        with open(path, "wb") as f:
            writer.write(f)

    front = tmp_path / "front.pdf"
    briefing = tmp_path / "briefing.pdf"
    make_pdf(front, n_pages=1)
    make_pdf(briefing, n_pages=2)

    _prepend_pdf(front, briefing)

    result = PdfReader(str(briefing))
    assert len(result.pages) == 3  # 1 front + 2 briefing


def test_prepend_pdf_no_tmp_file_left(tmp_path):
    from pypdf import PdfWriter

    def make_pdf(path):
        w = PdfWriter()
        w.add_blank_page(width=612, height=792)
        with open(path, "wb") as f:
            w.write(f)

    front = tmp_path / "front.pdf"
    briefing = tmp_path / "briefing.pdf"
    make_pdf(front)
    make_pdf(briefing)

    _prepend_pdf(front, briefing)

    tmp_files = list(tmp_path.glob("*.tmp.pdf"))
    assert tmp_files == [], f"Unexpected temp files: {tmp_files}"


# ---------------------------------------------------------------------------
# _triaged_to_dicts
# ---------------------------------------------------------------------------

def test_triaged_to_dicts_converts_correctly():
    from datetime import datetime, timezone
    from tg_compiler.db import PostRecord, AnalysisRecord
    from tg_compiler.triage import TriagedPost
    from tg_compiler.synthesiser import _triaged_to_dicts

    post = PostRecord(
        channel_id=1,
        channel_name="chan_a",
        message_id=10,
        timestamp=datetime(2026, 6, 9, 10, 30, tzinfo=timezone.utc),
        text="original text",
        media_paths=[],
        has_images=False,
        raw_json="{}",
    )
    analysis = AnalysisRecord(
        post_id=1,
        title="Big Event",
        summary="A notable development occurred.",
        importance_score=4,
        urgency_score=3,
        credibility_score=5,
        relevance_score=4,
        category="Military",
        key_entities=["Russia", "Ukraine"],
        model_used="test",
        threat_level="HIGH",
    )
    item = TriagedPost(post=post, analysis=analysis, composite_score=3.9)
    result = _triaged_to_dicts([item])

    assert len(result) == 1
    d = result[0]
    assert d["title"] == "Big Event"
    assert d["summary"] == "A notable development occurred."
    assert d["category"] == "Military"
    assert d["threat_level"] == "HIGH"
    assert d["composite_score"] == 3.9
    assert d["channel_slug"] == "chan_a"
    assert d["timestamp"] == "2026-06-09T10:30:00+00:00"
    assert d["entities"] == ["Russia", "Ukraine"]


def test_triaged_to_dicts_empty_list():
    from tg_compiler.synthesiser import _triaged_to_dicts
    assert _triaged_to_dicts([]) == []


def test_triaged_to_dicts_missing_title_defaults_empty():
    from datetime import datetime, timezone
    from tg_compiler.db import PostRecord, AnalysisRecord
    from tg_compiler.triage import TriagedPost
    from tg_compiler.synthesiser import _triaged_to_dicts

    post = PostRecord(
        channel_id=2, channel_name="chan_b", message_id=99,
        timestamp=datetime(2026, 6, 9, tzinfo=timezone.utc),
        text="", media_paths=[], has_images=False, raw_json="{}",
    )
    analysis = AnalysisRecord(
        post_id=2, title="", summary="Some summary.",
        importance_score=2, urgency_score=2, credibility_score=2, relevance_score=2,
        category="Other", key_entities=[], model_used="test",
    )
    item = TriagedPost(post=post, analysis=analysis, composite_score=2.0)
    result = _triaged_to_dicts([item])
    assert result[0]["title"] == ""


# ---------------------------------------------------------------------------
# run_analysis signature
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_analysis_accepts_main_items_kwarg():
    """run_analysis must accept main_items=[] without error (empty → early exit)."""
    from datetime import date
    from tg_compiler.synthesiser import run_analysis
    from tg_compiler.config import AppConfig

    cfg = AppConfig.model_validate({
        "telegram": {"api_id": 1, "api_hash": "x", "channels": []},
        "lmstudio": {"model": "test"},
    })
    # Empty main_items — should log error and return without crashing
    await run_analysis(cfg, date(2026, 6, 9), main_items=[])
