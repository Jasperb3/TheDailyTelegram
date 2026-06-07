from __future__ import annotations
import asyncio
import logging
import re
from typing import Optional

import lmstudio as lms
from pydantic import BaseModel, Field

from tg_compiler.config import AppConfig, ChannelConfig
from tg_compiler.db import Database, PostRecord, AnalysisRecord

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an intelligence analyst. For each Telegram post:\n"
    "1. Write a 1-2 sentence summary.\n"
    "2. Score importance, urgency, credibility, relevance each 1-5.\n"
    "3. Category: Breaking News | Analysis | Official Statement | Rumor | Media | Other.\n"
    "4. List up to 5 key named entities.\n"
    "5. Set image_substantive=true only if the image contains info absent from the text.\n"
    "Respond with valid JSON matching the PostAnalysis schema."
)


class PostAnalysis(BaseModel):
    summary: str
    importance_score: int = Field(..., ge=1, le=5)
    urgency_score: int = Field(..., ge=1, le=5)
    credibility_score: int = Field(..., ge=1, le=5)
    relevance_score: int = Field(..., ge=1, le=5)
    category: str
    key_entities: list[str] = Field(default_factory=list)
    image_substantive: bool = False
    image_description: Optional[str] = None
    reasoning: str = ""


def parse_analysis_fallback(raw: str) -> PostAnalysis:
    score_match = re.search(r"importance[^\d]*(\d)", raw, re.IGNORECASE)
    importance = int(score_match.group(1)) if score_match else 3
    importance = max(1, min(5, importance))

    summary_match = re.search(r"[Ss]ummary[:\s]+([^.\n]+)", raw)
    summary = summary_match.group(1).strip() if summary_match else raw[:120].strip()

    cat_match = re.search(
        r"(Breaking News|Analysis|Official Statement|Rumor|Media|Other)", raw
    )
    category = cat_match.group(1) if cat_match else "Other"

    return PostAnalysis(
        summary=summary,
        importance_score=importance,
        urgency_score=3,
        credibility_score=3,
        relevance_score=3,
        category=category,
        reasoning="Extracted via fallback parser",
    )


def build_chat_for_post(post: PostRecord, system_prompt: str) -> lms.Chat:
    chat = lms.Chat(system_prompt)
    text = post.text[:3000] if len(post.text) > 3000 else post.text
    header = f"Post from {post.channel_name} at {post.timestamp.isoformat()}:\n\n{text}"

    image_handles = []
    for path in post.media_paths[:3]:
        try:
            image_handles.append(lms.prepare_image(path))
        except Exception as e:
            log.warning("Could not prepare image %s: %s", path, e)

    if image_handles:
        chat.add_user_message(header, images=image_handles)
    else:
        chat.add_user_message(header)

    return chat


class Analyzer:
    def __init__(self, config: AppConfig, db: Database):
        self._cfg = config
        self._db = db
        self._model = None

    def _get_model(self):
        if self._model is None:
            client = lms.Client(f"localhost:{self._cfg.lmstudio.server_port}")
            self._model = client.llm.model(self._cfg.lmstudio.model)
        return self._model

    async def analyze_post(
        self, post: PostRecord, channel_cfg: ChannelConfig | None = None
    ) -> PostAnalysis:
        system = (
            channel_cfg.custom_prompt
            if (channel_cfg and channel_cfg.custom_prompt)
            else SYSTEM_PROMPT
        )
        chat = build_chat_for_post(post, system)
        inference_config = {
            "max_tokens": self._cfg.lmstudio.max_tokens,
            "temperature": self._cfg.lmstudio.temperature,
        }

        for attempt in range(3):
            try:
                result = await asyncio.to_thread(
                    self._get_model().respond,
                    chat,
                    structured=PostAnalysis,
                    config=inference_config,
                )
                return result
            except Exception as e:
                if attempt == 2:
                    log.warning(
                        "Structured output failed for post %s after 3 attempts, using fallback: %s",
                        post.message_id, e,
                    )
                    raw = await asyncio.to_thread(
                        self._get_model().respond, chat, config=inference_config
                    )
                    return parse_analysis_fallback(str(raw))
                await asyncio.sleep(10 * (attempt + 1))

    async def process_unanalysed(
        self, channel_map: dict[int, ChannelConfig] | None = None
    ) -> int:
        posts = self._db.get_unanalysed_posts()
        count = 0
        for post in posts:
            channel_cfg = channel_map.get(post.channel_id) if channel_map else None
            analysis = await self.analyze_post(post, channel_cfg)
            self._db.insert_analysis(AnalysisRecord(
                post_id=post.id,
                summary=analysis.summary,
                importance_score=analysis.importance_score,
                urgency_score=analysis.urgency_score,
                credibility_score=analysis.credibility_score,
                relevance_score=analysis.relevance_score,
                category=analysis.category,
                key_entities=analysis.key_entities,
                image_insights=analysis.image_description,
                model_used=self._cfg.lmstudio.model,
            ))
            count += 1
        return count
