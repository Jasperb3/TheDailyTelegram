from __future__ import annotations
import argparse
import asyncio
import logging
import os
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from tg_compiler.config import load_config, AppConfig
from tg_compiler.db import Database, PostRecord
from tg_compiler.scraper import Scraper

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


async def run_batch(config: AppConfig) -> None:
    from tg_compiler.analyzer import Analyzer
    from tg_compiler.triage import triage as do_triage
    from tg_compiler.generator import generate_briefing

    db = Database(config.storage.db_path)
    db.init_schema()
    today = date.today()

    channel_map = {(c.id or 0): c for c in config.telegram.channels}

    async with Scraper(config, db) as scraper:
        for channel_cfg in config.telegram.channels:
            posts = await scraper.scrape_channel(channel_cfg)
            log.info("Scraped %d new posts from %s", len(posts), channel_cfg.slug)

    analyzer = Analyzer(config, db)
    count = await analyzer.process_unanalysed(channel_map)
    log.info("Analysed %d posts", count)

    pairs = db.get_days_posts_with_analyses(today.isoformat())
    content = do_triage(pairs, config.triage, today=today)
    path = generate_briefing(content, config.generation.output_dir, pdf=True)
    log.info("Briefing generated: %s", path)


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
    from tg_compiler.triage import triage as do_triage
    from tg_compiler.generator import generate_briefing

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
        pairs = db.get_days_posts_with_analyses(today.isoformat())
        content = do_triage(pairs, config.triage, today=today)
        generate_briefing(content, config.generation.output_dir, pdf=True)

        removed = purge_old_media(config.storage.media_dir, config.storage.retention_days)
        log.info("Daily briefing complete. Purged %d old media directories.", removed)


def main() -> None:
    parser = argparse.ArgumentParser(prog="tg_compiler")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--generate", action="store_true")
    args = parser.parse_args()

    if not (args.batch or args.daemon or args.generate):
        parser.print_help()
        return

    cfg = load_config(args.config, env_override=True)
    os.makedirs(cfg.storage.media_dir, exist_ok=True)

    if args.batch:
        asyncio.run(run_batch(cfg))
    elif args.generate:
        from tg_compiler.triage import triage as do_triage
        from tg_compiler.generator import generate_briefing
        db = Database(cfg.storage.db_path)
        db.init_schema()
        today = date.today()
        pairs = db.get_days_posts_with_analyses(today.isoformat())
        content = do_triage(pairs, cfg.triage, today=today)
        out = generate_briefing(content, cfg.generation.output_dir, pdf=True)
        print(f"Generated: {out}")


if __name__ == "__main__":
    main()
