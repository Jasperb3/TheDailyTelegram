import pytest
from pydantic import ValidationError
from tg_compiler.config import AppConfig, load_config

MINIMAL_YAML = """
telegram:
  api_id: 111
  api_hash: "abc"
  channels:
    - slug: "test"
      username: "@testchan"
lmstudio:
  model: "gemma-3-4b-it"
"""


def test_load_minimal_config(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(MINIMAL_YAML)
    cfg = load_config(str(f))
    assert cfg.telegram.api_id == 111
    assert cfg.telegram.channels[0].slug == "test"
    assert cfg.lmstudio.model == "gemma-3-4b-it"
    assert cfg.triage.keyword_boost == 0.5
    assert cfg.storage.retention_days == 30


def test_missing_api_id_raises(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text("telegram:\n  api_hash: x\n  channels: []\nlmstudio:\n  model: x\n")
    with pytest.raises(ValidationError):
        load_config(str(bad))


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_API_ID", "999")
    monkeypatch.setenv("TG_API_HASH", "envhash")
    f = tmp_path / "config.yaml"
    f.write_text(MINIMAL_YAML)
    cfg = load_config(str(f), env_override=True)
    assert cfg.telegram.api_id == 999
    assert cfg.telegram.api_hash == "envhash"


def test_synthesis_post_limit_rejected(tmp_path):
    """synthesis_post_limit was removed; config must reject it."""
    yaml_with_old_field = MINIMAL_YAML + "\ngeneration:\n  synthesis_post_limit: 20\n"
    f = tmp_path / "config.yaml"
    f.write_text(yaml_with_old_field)
    with pytest.raises(ValidationError):
        load_config(str(f))
