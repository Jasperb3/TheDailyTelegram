import pytest
from pydantic import ValidationError
from tg_compiler.analyzer import PostAnalysis, parse_analysis_fallback, build_messages, _clean_image_insights
from tg_compiler.utils import clean_entities


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


def test_build_messages_text_only(sample_post):
    sample_post.media_paths = []
    messages = build_messages(sample_post, system_prompt="You are an analyst.")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_entity_containing_key_entities_is_filtered():
    entities = ['key_entities,["IRGC Aerospace Force","Hamas"]', "IRGC Aerospace Force", "Hamas"]
    result = clean_entities(entities)
    assert "IRGC Aerospace Force" in result
    assert "Hamas" in result
    assert not any("key_entities" in e for e in result)


def test_entity_containing_bare_false_is_filtered():
    entities = ["false", "Israel", "true", "Pentagon"]
    result = clean_entities(entities)
    assert "Israel" in result
    assert "Pentagon" in result
    assert "false" not in result
    assert "true" not in result


def test_image_description_with_json_artifact_returns_none():
    assert _clean_image_insights(".json(post_analysis){image_description: null}") is None


def test_build_messages_truncates_long_text(sample_post):
    sample_post.text = "x" * 5000
    sample_post.media_paths = []
    messages = build_messages(sample_post, system_prompt="You are an analyst.")
    user_content = messages[1]["content"]
    text_part = next(p for p in user_content if p["type"] == "text")
    assert text_part["text"].count("x") == 3000
