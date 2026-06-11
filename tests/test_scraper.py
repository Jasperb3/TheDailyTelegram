from pathlib import Path
from tg_compiler.scraper import media_path_for


def test_media_path_structure(tmp_path):
    path = media_path_for(
        base_dir=str(tmp_path),
        channel_slug="news",
        date_str="2026-06-07",
        message_id=42,
        ext="jpg",
    )
    assert path == str(tmp_path / "news" / "2026-06-07" / "42.jpg")


def test_media_path_creates_directories(tmp_path):
    path = media_path_for(
        base_dir=str(tmp_path),
        channel_slug="intel",
        date_str="2026-06-07",
        message_id=99,
        ext="png",
    )
    assert Path(path).parent.exists()


def test_different_channels_dont_collide(tmp_path):
    p1 = media_path_for(str(tmp_path), "chan_a", "2026-06-07", 1, "jpg")
    p2 = media_path_for(str(tmp_path), "chan_b", "2026-06-07", 1, "jpg")
    assert p1 != p2


import pytest
from tg_compiler.scraper import Scraper
from tg_compiler.config import AppConfig, TelegramConfig, LMStudioConfig, ChannelConfig


@pytest.fixture
def scraper_config(tmp_path):
    return AppConfig(
        telegram=TelegramConfig(
            api_id=1, api_hash="x", session_name=str(tmp_path / "session"),
            channels=[ChannelConfig(slug="bad_chan", username="@bad_chan")],
        ),
        lmstudio=LMStudioConfig(model="m"),
    )


async def test_scrape_channel_returns_empty_on_get_entity_failure(db, scraper_config, monkeypatch):
    channel_cfg = scraper_config.telegram.channels[0]
    scraper = Scraper(scraper_config, db)

    async def fake_get_entity(entity):
        raise ValueError("UsernameNotOccupiedError")

    monkeypatch.setattr(scraper._client, "get_entity", fake_get_entity)

    posts = await scraper.scrape_channel(channel_cfg)
    assert posts == []


async def test_scrape_channel_does_not_cap_iter_messages_limit(db, scraper_config, monkeypatch):
    channel_cfg = scraper_config.telegram.channels[0]
    scraper = Scraper(scraper_config, db)

    class FakeEntity:
        id = 12345

    async def fake_get_entity(entity):
        return FakeEntity()

    captured_kwargs = {}

    def fake_iter_messages(entity, **kwargs):
        captured_kwargs.update(kwargs)

        async def _empty_gen():
            return
            yield

        return _empty_gen()

    monkeypatch.setattr(scraper._client, "get_entity", fake_get_entity)
    monkeypatch.setattr(scraper._client, "iter_messages", fake_iter_messages)

    await scraper.scrape_channel(channel_cfg)

    assert captured_kwargs["limit"] is None
