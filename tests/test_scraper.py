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
