from __future__ import annotations
import argparse
import asyncio
import logging
import os
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader

load_dotenv()

from tg_compiler.config import load_config, AppConfig, ChannelConfig
from tg_compiler.db import Database, PostRecord
from tg_compiler.scraper import Scraper

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


async def generate_daily_briefing(config: AppConfig, target_date: date, db: Database) -> str:
    from tg_compiler.triage import triage as do_triage
    from tg_compiler.generator import generate_briefing

    pairs = db.get_days_posts_with_analyses(target_date.isoformat())
    content = do_triage(pairs, config.triage, today=target_date)
    content.channel_links = {
        ch.slug: ch.username.lstrip("@")
        for ch in config.telegram.channels
        if ch.username
    }
    path = generate_briefing(content, config.generation.output_dir, pdf=True)
    log.info("Briefing generated: %s", path)
    return path


async def run_batch(config: AppConfig) -> None:
    from tg_compiler.analyzer import Analyzer
    from tg_compiler.synthesiser import run_analysis

    db = Database(config.storage.db_path)
    db.init_schema()
    today = date.today()

    from tqdm import tqdm
    from tqdm.contrib.logging import logging_redirect_tqdm
    async with Scraper(config, db) as scraper:
        with logging_redirect_tqdm():
            for channel_cfg in tqdm(config.telegram.channels, desc="Scraping channels", unit="channel"):
                posts = await scraper.scrape_channel(channel_cfg)
                log.info("Scraped %d new posts from %s", len(posts), channel_cfg.slug)
        channel_map = scraper.channel_map

    analyzer = Analyzer(config, db)
    count = await analyzer.process_unanalysed(channel_map)
    log.info("Analysed %d posts", count)

    await generate_daily_briefing(config, today, db)
    await run_analysis(config, today)


def purge_old_media(media_dir: str, retention_days: int) -> int:
    cutoff = datetime.now() - timedelta(days=retention_days)
    base = Path(media_dir)
    if not base.exists():
        return 0
    removed = 0
    for date_dir in base.rglob("????-??-??"):
        if date_dir.is_dir():
            try:
                dir_date = datetime.strptime(date_dir.name, "%Y-%m-%d")
                if dir_date < cutoff:
                    shutil.rmtree(date_dir)
                    removed += 1
            except ValueError:
                pass
    return removed


async def schedule_daily_generation(config: AppConfig) -> None:
    h, m = map(int, config.generation.generate_at.split(":"))
    while True:
        now = datetime.now()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        today = date.today()
        db = Database(config.storage.db_path)
        db.init_schema()
        await generate_daily_briefing(config, today, db)

        removed = purge_old_media(config.storage.media_dir, config.storage.retention_days)
        log.info("Daily briefing complete. Purged %d old media directories.", removed)


async def run_daemon(config: AppConfig) -> None:
    from telethon import TelegramClient, events
    from tg_compiler.analyzer import Analyzer
    from tg_compiler.scraper import media_path_for
    from tg_compiler.db import AnalysisRecord

    db = Database(config.storage.db_path)
    db.init_schema()
    analyzer = Analyzer(config, db)

    client = TelegramClient(
        config.telegram.session_name,
        config.telegram.api_id,
        config.telegram.api_hash,
    )
    await client.start()
    try:
        channel_entities = []
        channel_cfg_by_id: dict[int, ChannelConfig] = {}
        for ch in config.telegram.channels:
            identifier = ch.username or ch.id
            if not identifier:
                raise ValueError(f"Channel config has neither username nor id: {ch!r}")
            entity = await client.get_entity(identifier)
            channel_entities.append(entity)
            channel_cfg_by_id[entity.id] = ch

        @client.on(events.NewMessage(chats=channel_entities))
        async def handle_new_message(event):
            msg = event.message
            channel_id = event.chat_id
            channel_cfg = channel_cfg_by_id.get(channel_id)
            if channel_cfg is None:
                log.warning("Received message from unmapped channel %s — skipping", channel_id)
                return
            text = msg.text or ""
            ts = msg.date
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            media_paths: list[str] = []
            has_video = bool(msg.video or msg.gif)
            if msg.photo:
                dest = media_path_for(
                    config.storage.media_dir, channel_cfg.slug,
                    ts.strftime("%Y-%m-%d"), msg.id, "jpg",
                )
                try:
                    await client.download_media(msg, file=dest)
                    media_paths.append(dest)
                except Exception as e:
                    log.warning("Daemon: media download failed for %s: %s", msg.id, e)

            record = PostRecord(
                channel_id=channel_id,
                channel_name=channel_cfg.slug,
                message_id=msg.id,
                timestamp=ts,
                text=text,
                media_paths=media_paths,
                has_images=bool(media_paths),
                has_video=has_video,
                raw_json="{}",
            )
            post_id = db.insert_post(record)
            if post_id is not None:
                record.id = post_id
                try:
                    analysis = await analyzer.analyze_post(record, channel_cfg)
                    db.insert_analysis(AnalysisRecord(
                        post_id=post_id,
                        summary=analysis.summary,
                        importance_score=analysis.importance_score,
                        urgency_score=analysis.urgency_score,
                        credibility_score=analysis.credibility_score,
                        relevance_score=analysis.relevance_score,
                        category=analysis.category,
                        key_entities=analysis.key_entities,
                        image_insights=analysis.image_description,
                        model_used=config.lmstudio.model,
                        threat_level=analysis.threat_level,
                    ))
                except Exception as e:
                    log.error("Analysis failed for post %s: %s", msg.id, e)

        asyncio.create_task(schedule_daily_generation(config))
        log.info("Daemon running on %d channels", len(channel_entities))
        await client.run_until_disconnected()
    finally:
        await client.disconnect()


def _parse_since(since_str: str) -> datetime:
    """Parse --since into a UTC datetime. Accepts HH:MM (today), YYYY-MM-DD, or YYYY-MM-DDTHH:MM."""
    now = datetime.now(timezone.utc)
    for fmt in ("%H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M"):
        try:
            parsed = datetime.strptime(since_str, fmt)
            if fmt == "%H:%M":
                return now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
            if fmt == "%Y-%m-%d":
                return parsed.replace(hour=0, minute=0, second=0, tzinfo=timezone.utc)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise SystemExit(f"Cannot parse --since value: {since_str!r}. Use HH:MM, YYYY-MM-DD, or YYYY-MM-DDTHH:MM")


def main() -> None:
    parser = argparse.ArgumentParser(prog="tg_compiler")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--analyse", action="store_true")
    parser.add_argument(
        "--since",
        metavar="TIME",
        help="Re-scrape from this point (HH:MM, YYYY-MM-DD, or YYYY-MM-DDTHH:MM). "
             "Resets channel cursors and overrides lookback_seconds.",
    )
    args = parser.parse_args()

    if not (args.batch or args.daemon or args.generate or args.analyse):
        parser.print_help()
        return

    cfg = load_config(args.config, env_override=True)
    os.makedirs(cfg.storage.media_dir, exist_ok=True)

    since_dt = None
    if args.since:
        if not (args.batch or args.analyse):
            raise SystemExit("--since can only be used with --batch or --analyse")
        since_dt = _parse_since(args.since)
        if args.batch:
            now = datetime.now(timezone.utc)
            cfg.telegram.lookback_seconds = max(1, int((now - since_dt).total_seconds()))
            db = Database(cfg.storage.db_path)
            db.init_schema()
            db.reset_all_cursors()
            log.info("--since %s: lookback set to %ds, all channel cursors reset", args.since, cfg.telegram.lookback_seconds)

    if args.batch:
        asyncio.run(run_batch(cfg))
    elif args.daemon:
        asyncio.run(run_daemon(cfg))
    elif args.analyse:
        from tg_compiler.synthesiser import run_analysis
        target_date = since_dt.date() if since_dt else date.today()
        asyncio.run(run_analysis(cfg, target_date))
    elif args.generate:
        db = Database(cfg.storage.db_path)
        db.init_schema()
        out = asyncio.run(generate_daily_briefing(cfg, date.today(), db))
        print(f"Generated: {out}")


if __name__ == "__main__":
    main()
