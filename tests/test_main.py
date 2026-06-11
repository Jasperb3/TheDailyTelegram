from datetime import datetime, timezone

import pytest
from tg_compiler.config import AppConfig, TelegramConfig, LMStudioConfig, ChannelConfig
from tg_compiler import main as main_module
from tg_compiler.main import _parse_since, purge_old_media


# ---------------------------------------------------------------------------
# _parse_since
# ---------------------------------------------------------------------------

def test_parse_since_time_of_day_today():
    result = _parse_since("00:00")
    now = datetime.now(timezone.utc)
    assert (result.year, result.month, result.day) == (now.year, now.month, now.day)
    assert result.hour == 0
    assert result.minute == 0
    assert result.tzinfo == timezone.utc


def test_parse_since_date_only():
    result = _parse_since("2026-06-01")
    assert result == datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)


def test_parse_since_date_and_time():
    result = _parse_since("2026-06-01T08:30")
    assert result == datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc)


def test_parse_since_invalid_raises_system_exit():
    with pytest.raises(SystemExit):
        _parse_since("not-a-date")


# ---------------------------------------------------------------------------
# purge_old_media
# ---------------------------------------------------------------------------

def test_purge_old_media_removes_old_dirs(tmp_path):
    old_dir = tmp_path / "chan" / "2020-01-01"
    new_dir = tmp_path / "chan" / "2099-01-01"
    other_dir = tmp_path / "chan" / "not-a-date"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    other_dir.mkdir(parents=True)

    removed = purge_old_media(str(tmp_path), retention_days=30)

    assert removed == 1
    assert not old_dir.exists()
    assert new_dir.exists()
    assert other_dir.exists()


def test_purge_old_media_missing_base_dir_returns_zero(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert purge_old_media(str(missing), retention_days=30) == 0


@pytest.fixture
def batch_config(tmp_path):
    return AppConfig(
        telegram=TelegramConfig(
            api_id=1, api_hash="x", session_name=str(tmp_path / "session"),
            channels=[
                ChannelConfig(slug="chan_a", username="@chan_a"),
                ChannelConfig(slug="chan_b", username="@chan_b"),
                ChannelConfig(slug="chan_c", username="@chan_c"),
            ],
        ),
        lmstudio=LMStudioConfig(model="m"),
    )


async def test_run_batch_continues_after_one_channel_fails(tmp_path, batch_config, monkeypatch):
    batch_config.storage.db_path = str(tmp_path / "db.sqlite")

    scraped_channels = []

    class FakeScraper:
        def __init__(self, config, db):
            self.channel_map = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def scrape_channel(self, channel_cfg):
            if channel_cfg.slug == "chan_b":
                raise RuntimeError("boom")
            scraped_channels.append(channel_cfg.slug)
            return []

    class FakeAnalyzer:
        def __init__(self, config, db):
            pass

        async def process_unanalysed(self, channel_map=None):
            return 0, 0

    async def fake_generate_daily_briefing(config, today, db, **kwargs):
        from tg_compiler.triage import BriefingContent
        return "fake.pdf", BriefingContent(date=today, main_items=[], appendix_items=[])

    async def fake_run_analysis(config, today, main_items=None):
        return None

    monkeypatch.setattr(main_module, "Scraper", FakeScraper)
    monkeypatch.setattr("tg_compiler.analyzer.Analyzer", FakeAnalyzer)
    monkeypatch.setattr(main_module, "generate_daily_briefing", fake_generate_daily_briefing)
    monkeypatch.setattr("tg_compiler.synthesiser.run_analysis", fake_run_analysis)

    await main_module.run_batch(batch_config)

    assert scraped_channels == ["chan_a", "chan_c"]


@pytest.fixture
def daemon_config(tmp_path):
    return AppConfig(
        telegram=TelegramConfig(
            api_id=1, api_hash="x", session_name=str(tmp_path / "session"),
            channels=[ChannelConfig(slug="chan_a", username="@chan_a")],
        ),
        lmstudio=LMStudioConfig(model="m"),
    )


async def test_run_daemon_logs_scheduler_crash(tmp_path, daemon_config, monkeypatch, caplog):
    import asyncio
    import logging
    import telethon

    daemon_config.storage.db_path = str(tmp_path / "db.sqlite")

    class FakeEntity:
        id = 1

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self):
            return None

        async def get_entity(self, identifier):
            return FakeEntity()

        def on(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

        async def run_until_disconnected(self):
            await asyncio.sleep(0.05)

        async def disconnect(self):
            return None

    async def fake_schedule_daily_generation(config):
        raise RuntimeError("scheduler boom")

    monkeypatch.setattr(telethon, "TelegramClient", FakeClient)
    monkeypatch.setattr(main_module, "schedule_daily_generation", fake_schedule_daily_generation)

    with caplog.at_level(logging.ERROR):
        await main_module.run_daemon(daemon_config)

    assert any("Daily generation scheduler crashed" in r.message for r in caplog.records)
