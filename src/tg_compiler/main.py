from __future__ import annotations
import argparse
import asyncio
import logging
import os
from collections import defaultdict
from datetime import date, datetime, timezone
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

    db = Database(config.storage.db_path)
    db.init_schema()

    channel_map = {
        (c.id or 0): c for c in config.telegram.channels
    }

    async with Scraper(config, db) as scraper:
        for channel_cfg in config.telegram.channels:
            posts = await scraper.scrape_channel(channel_cfg)
            log.info("Scraped %d new posts from %s", len(posts), channel_cfg.slug)

    analyzer = Analyzer(config, db)
    count = await analyzer.process_unanalysed(channel_map)
    log.info("Analysed %d posts", count)


def main() -> None:
    parser = argparse.ArgumentParser(prog="tg_compiler")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--generate", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config, env_override=True)
    os.makedirs(cfg.storage.media_dir, exist_ok=True)

    if args.batch:
        asyncio.run(run_batch(cfg))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
