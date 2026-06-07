from __future__ import annotations
import asyncio
import json
import logging
from pathlib import Path
from datetime import timezone

from telethon import TelegramClient
from telethon.errors import ChannelPrivateError, FloodWaitError
from telethon.tl.types import Message

from tg_compiler.config import AppConfig, ChannelConfig
from tg_compiler.db import Database, PostRecord

log = logging.getLogger(__name__)


def media_path_for(base_dir: str, channel_slug: str, date_str: str, message_id: int, ext: str) -> str:
    p = Path(base_dir) / channel_slug / date_str
    p.mkdir(parents=True, exist_ok=True)
    return str(p / f"{message_id}.{ext}")


class Scraper:
    def __init__(self, config: AppConfig, db: Database):
        self._cfg = config
        self._db = db
        self._client = TelegramClient(
            config.telegram.session_name,
            config.telegram.api_id,
            config.telegram.api_hash,
        )

    async def __aenter__(self):
        await self._client.start()
        return self

    async def __aexit__(self, *_):
        await self._client.disconnect()

    async def scrape_channel(self, channel_cfg: ChannelConfig) -> list[PostRecord]:
        entity = channel_cfg.username or channel_cfg.id
        channel_entity = await self._client.get_entity(entity)
        channel_id = channel_entity.id
        last_seen = self._db.get_last_seen_id(channel_id)
        collected: list[PostRecord] = []
        max_id_seen = last_seen

        try:
            async for msg in self._client.iter_messages(
                channel_entity, offset_id=last_seen, reverse=True, limit=500
            ):
                if not isinstance(msg, Message):
                    if msg.id > max_id_seen:
                        max_id_seen = msg.id
                    continue
                text = msg.text or msg.caption or ""
                media_paths: list[str] = []

                if msg.photo:
                    date_str = msg.date.strftime("%Y-%m-%d")
                    dest = media_path_for(
                        self._cfg.storage.media_dir,
                        channel_cfg.slug,
                        date_str,
                        msg.id,
                        "jpg",
                    )
                    try:
                        await self._client.download_media(msg, file=dest)
                        media_paths.append(dest)
                    except Exception as e:
                        log.warning("Media download failed for msg %s (attempt 1): %s", msg.id, e)
                        try:
                            await self._client.download_media(msg, file=dest)
                            media_paths.append(dest)
                        except Exception:
                            log.error("Media download permanently failed for msg %s", msg.id)

                ts = msg.date
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)

                record = PostRecord(
                    channel_id=channel_id,
                    channel_name=channel_cfg.slug,
                    message_id=msg.id,
                    timestamp=ts,
                    text=text,
                    media_paths=media_paths,
                    has_images=bool(media_paths),
                    raw_json=json.dumps({"id": msg.id, "text": text}),
                )
                post_id = self._db.insert_post(record)
                if post_id is not None:
                    record.id = post_id
                    collected.append(record)
                if msg.id > max_id_seen:
                    max_id_seen = msg.id

            if max_id_seen > last_seen:
                self._db.set_last_seen_id(channel_id, max_id_seen)

            await asyncio.sleep(self._cfg.telegram.rate_limit_delay_ms / 1000)

        except ChannelPrivateError:
            log.error("Channel %s is private or inaccessible", entity)
        except FloodWaitError as e:
            log.warning("FloodWait: sleeping %d seconds", e.seconds)
            await asyncio.sleep(e.seconds)

        return collected
