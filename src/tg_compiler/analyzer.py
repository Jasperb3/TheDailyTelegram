from __future__ import annotations
import asyncio
import base64
import logging
import re
from pathlib import Path
from typing import Optional

from openai import OpenAI
from pydantic import BaseModel, Field, field_validator

from tg_compiler.config import AppConfig, ChannelConfig
from tg_compiler.db import Database, PostRecord, AnalysisRecord
from tg_compiler.utils import clean_entities

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an award-winning intelligence analyst. For each Telegram post:\n"
    "1. Write a concise headline title (5-10 words, no punctuation at end).\n"
    "2. Write a 1-2 sentence summary.\n"
    "3. Score importance, urgency, credibility, relevance each 1-5.\n"
    "4. Category: Breaking News | Analysis | Official Statement | Rumor | Media | Other.\n"
    "5. List up to 5 key named entities.\n"
    "6. Set image_substantive=true only if the image contains info absent from the text.\n"
    "7. Set threat_level to exactly one of: CRITICAL, HIGH, MODERATE, LOW.\n"
    "   CRITICAL — imminent risk of mass casualties, confirmed state-level military action underway, "
    "nuclear/chemical/biological threat, or active attack on critical infrastructure.\n"
    "   HIGH — confirmed armed conflict development, significant political crisis, major terror attack, "
    "or credible escalation warning from a named senior state official.\n"
    "   MODERATE — ongoing conflict updates, diplomatic developments, significant arrests or detentions, "
    "or unverified but plausible escalation claims.\n"
    "   LOW — background context, routine troop movement reports, unverified rumours, "
    "social media content, statistical or historical reports.\n"
    "Respond with valid JSON matching the PostAnalysis schema."
)


_VALID_THREAT_LEVELS = {"CRITICAL", "HIGH", "MODERATE", "LOW"}


class PostAnalysis(BaseModel):
    title: str = ""
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
    threat_level: str = "MODERATE"

    @field_validator("threat_level")
    @classmethod
    def validate_threat_level(cls, v: str) -> str:
        normalised = v.upper().strip() if v else "MODERATE"
        return normalised if normalised in _VALID_THREAT_LEVELS else "MODERATE"



_TITLE_GARBAGE = re.compile(r'<\||\`\`\`|\{|\}|<image>|thought', re.IGNORECASE)

def _clean_title(title: str) -> str:
    if not title or not title.strip():
        return ""
    if len(title) > 120:
        return ""
    if _TITLE_GARBAGE.search(title):
        return ""
    return title


def _clean_image_insights(text: str | None) -> str | None:
    if not text:
        return None
    stripped = text.strip()
    if stripped.lower() in ('n/a', 'none', 'no image provided', 'no image provided.',
                             'no image.', 'no image', 'na', ''):
        return None
    low = stripped.lower()
    if (
        low.startswith('a telegram post from')
        or low.startswith('a screenshot of a post')
        or 'text-only announcement' in low
        or 'text-based report' in low
        or 'featuring a text' in low
    ):
        return None
    if len(stripped) < 10:
        return None
    return stripped


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
        threat_level="MODERATE",
        reasoning="Extracted via fallback parser",
    )


def _encode_image(path: str) -> str | None:
    try:
        data = Path(path).read_bytes()
        return base64.b64encode(data).decode()
    except Exception as e:
        log.warning("Could not read image %s: %s", path, e)
        return None


def build_messages(post: PostRecord, system_prompt: str) -> list[dict]:
    text = post.text[:3000] if len(post.text) > 3000 else post.text
    header = f"Post from {post.channel_name} at {post.timestamp.isoformat()}:\n\n{text}"

    content: list[dict] = [{"type": "text", "text": header}]
    for path in post.media_paths[:3]:
        b64 = _encode_image(path)
        if b64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]


def _sanitize(analysis: PostAnalysis) -> PostAnalysis:
    analysis.title = _clean_title(analysis.title)
    analysis.key_entities = clean_entities(analysis.key_entities)
    analysis.image_description = _clean_image_insights(analysis.image_description)
    return analysis


class Analyzer:
    def __init__(self, config: AppConfig, db: Database):
        self._cfg = config
        self._db = db
        self._client: OpenAI | None = None

    def _get_client(self) -> OpenAI:
        if self._client is None:
            cfg = self._cfg.lmstudio
            api_key = cfg.api_token or "lm-studio"
            self._client = OpenAI(
                base_url=f"http://{cfg.server_host}:{cfg.server_port}/v1",
                api_key=api_key,
            )
        return self._client

    def _call_llm(self, messages: list[dict], structured: bool) -> PostAnalysis | str:
        cfg = self._cfg.lmstudio
        client = self._get_client()
        if structured:
            completion = client.beta.chat.completions.parse(
                model=cfg.model,
                messages=messages,
                response_format=PostAnalysis,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is not None:
                return parsed
            # structured output returned None — fall through to text path
            raw = completion.choices[0].message.content or ""
        else:
            completion = client.chat.completions.create(
                model=cfg.model,
                messages=messages,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
            )
            raw = completion.choices[0].message.content or ""

        # Strip markdown fences then try to parse JSON
        stripped = raw.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return PostAnalysis.model_validate_json(stripped)
        except Exception:
            return raw

    async def analyze_post(
        self, post: PostRecord, channel_cfg: ChannelConfig | None = None
    ) -> PostAnalysis:
        system = (
            channel_cfg.custom_prompt
            if (channel_cfg and channel_cfg.custom_prompt)
            else SYSTEM_PROMPT
        )
        messages = build_messages(post, system)

        for attempt in range(3):
            try:
                result = await asyncio.to_thread(self._call_llm, messages, True)
                if isinstance(result, PostAnalysis):
                    return _sanitize(result)
                return _sanitize(parse_analysis_fallback(result if isinstance(result, str) else ""))
            except Exception as e:
                if attempt == 2:
                    log.warning(
                        "Analysis failed for post %s after 3 attempts, using fallback: %s",
                        post.message_id, e,
                    )
                    try:
                        result = await asyncio.to_thread(self._call_llm, messages, False)
                        if isinstance(result, PostAnalysis):
                            return _sanitize(result)
                        return _sanitize(parse_analysis_fallback(result if isinstance(result, str) else ""))
                    except Exception as fe:
                        log.error("Fallback also failed for post %s: %s", post.message_id, fe)
                        return _sanitize(parse_analysis_fallback(""))
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
                title=analysis.title,
                summary=analysis.summary,
                importance_score=analysis.importance_score,
                urgency_score=analysis.urgency_score,
                credibility_score=analysis.credibility_score,
                relevance_score=analysis.relevance_score,
                category=analysis.category,
                key_entities=analysis.key_entities,
                image_insights=analysis.image_description,
                model_used=self._cfg.lmstudio.model,
                threat_level=analysis.threat_level,
            ))
            count += 1
        return count
