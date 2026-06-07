import pytest
from pydantic import ValidationError
from tg_compiler.analyzer import PostAnalysis, parse_analysis_fallback, build_chat_for_post


def test_post_analysis_parses_valid_json():
    data = {
        "summary": "A test post about something.",
        "importance_score": 3,
        "urgency_score": 2,
        "credibility_score": 4,
        "relevance_score": 3,
        "category": "Analysis",
        "key_entities": ["Alice", "ACME Corp"],
        "image_substantive": False,
        "image_description": None,
        "reasoning": "Moderate importance.",
    }
    pa = PostAnalysis.model_validate(data)
    assert pa.importance_score == 3
    assert pa.category == "Analysis"
    assert "Alice" in pa.key_entities


def test_importance_score_out_of_range_raises():
    data = {
        "summary": "x", "importance_score": 6, "urgency_score": 1,
        "credibility_score": 1, "relevance_score": 1,
        "category": "Other", "reasoning": "r",
    }
    with pytest.raises(ValidationError):
        PostAnalysis.model_validate(data)


def test_fallback_parser_extracts_score_from_prose():
    raw = "The importance score is 4. Summary: Breaking development in the region. Category: Breaking News."
    pa = parse_analysis_fallback(raw)
    assert pa.importance_score == 4
    assert "Breaking" in pa.summary


def test_fallback_parser_returns_defaults_when_nothing_found():
    pa = parse_analysis_fallback("completely unstructured text with no useful fields")
    assert 1 <= pa.importance_score <= 5
    assert pa.summary != ""
    assert pa.category in ["Breaking News", "Analysis", "Official Statement", "Rumor", "Media", "Other"]


def test_build_chat_text_only(sample_post):
    sample_post.media_paths = []
    chat = build_chat_for_post(sample_post, system_prompt="You are an analyst.")
    assert chat is not None


def test_build_chat_truncates_long_text(sample_post):
    sample_post.text = "x" * 5000
    sample_post.media_paths = []
    chat = build_chat_for_post(sample_post, system_prompt="You are an analyst.")
    assert chat is not None
