from __future__ import annotations
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from tg_compiler.config import TriageConfig
from tg_compiler.db import PostRecord, AnalysisRecord

_NON_WORD = re.compile(r'[^\w\s]')

_SEVERITY_RANK: dict[str, int] = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "LOW": 1}

# Aliases for common actors so entity-overlap dedup matches across naming variants.
# Keys are compared with periods stripped (so "U.S." and "us" both normalize to "us").
_ENTITY_ALIASES: dict[str, str] = {
    "us": "united states",
    "usa": "united states",
    "united states of america": "united states",
    "idf": "israel defense forces",
    "israeli defense forces": "israel defense forces",
    "israeli military": "israel defense forces",
}


def _normalize_entity(e: str) -> str:
    stripped = e.strip().lower().replace(".", "")
    return _ENTITY_ALIASES.get(stripped, stripped)


@dataclass
class CorroborationRef:
    channel_slug: str
    message_id: int
    timestamp: datetime


@dataclass
class TriagedPost:
    post: PostRecord
    analysis: AnalysisRecord
    composite_score: float
    corroborations: list[CorroborationRef] = field(default_factory=list)


@dataclass
class BriefingContent:
    date: date
    main_items: list[TriagedPost]
    appendix_items: list[TriagedPost]
    channel_names: list[str] = field(default_factory=list)
    # slug → bare username (no @) for building t.me deep links
    channel_links: dict[str, str] = field(default_factory=dict)
    category_counts: dict[str, int] = field(default_factory=dict)
    # Executive Summary items: all CRITICAL items, then highest-scoring others, capped at 10
    executive_items: list[TriagedPost] = field(default_factory=list)
    # Pipeline funnel stats (set externally by main.py / inside triage())
    posts_scraped: int = 0
    posts_analysed: int = 0
    posts_skipped: int = 0
    posts_clustered: int = 0


def _composite(a: AnalysisRecord) -> float:
    return (
        0.4 * a.importance_score
        + 0.3 * a.urgency_score
        + 0.2 * a.credibility_score
        + 0.1 * a.relevance_score
    )


def _recency_multiplier(post_time: datetime, now: datetime, half_life_hours: float, floor: float) -> float:
    age_hours = max(0.0, (now - post_time).total_seconds() / 3600)
    multiplier = 0.5 ** (age_hours / half_life_hours)
    return max(floor, multiplier)


def _jaccard(a: str, b: str, min_len: int = 3) -> float:
    words_a = {w for w in _NON_WORD.sub('', a.lower()).split() if len(w) >= min_len}
    words_b = {w for w in _NON_WORD.sub('', b.lower()).split() if len(w) >= min_len}
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def _find_duplicate(
    candidate: TriagedPost,
    kept: list[TriagedPost],
    time_window_secs: float,
    entity_cluster_window_secs: float = 86400,
    threshold: float = 0.28,
    summary_window_secs: float | None = None,
) -> TriagedPost | None:
    if summary_window_secs is None:
        summary_window_secs = time_window_secs

    for existing in kept:
        delta = abs(
            (candidate.post.timestamp - existing.post.timestamp).total_seconds()
        )
        if delta <= summary_window_secs:
            if _jaccard(candidate.analysis.summary, existing.analysis.summary) >= threshold:
                return existing
            if candidate.analysis.title and existing.analysis.title:
                if _jaccard(candidate.analysis.title, existing.analysis.title) >= threshold:
                    return existing
        if delta > time_window_secs:
            continue
        # Entity overlap: ≥3 shared named entities within time window
        cand_entities = {_normalize_entity(e) for e in candidate.analysis.key_entities}
        exist_entities = {_normalize_entity(e) for e in existing.analysis.key_entities}
        if len(cand_entities) >= 3 and len(exist_entities) >= 3:
            if len(cand_entities & exist_entities) >= 3:
                return existing

    # Extended entity-cluster pass: ≥4 shared entities within the cluster window (default 24h)
    for existing in kept:
        delta = abs(
            (candidate.post.timestamp - existing.post.timestamp).total_seconds()
        )
        if delta > entity_cluster_window_secs:
            continue
        cand_entities = {_normalize_entity(e) for e in candidate.analysis.key_entities}
        exist_entities = {_normalize_entity(e) for e in existing.analysis.key_entities}
        if len(cand_entities) >= 4 and len(exist_entities) >= 4:
            if len(cand_entities & exist_entities) >= 4:
                return existing

    return None


def triage(
    pairs: list[tuple[PostRecord, AnalysisRecord]],
    config: TriageConfig,
    today: date | None = None,
    channel_priorities: dict[str, float] | None = None,
) -> BriefingContent:
    today = today or date.today()
    now = datetime.now(timezone.utc)
    scored: list[TriagedPost] = []

    for post, analysis in pairs:
        if analysis.category == "Skipped":
            continue
        if not analysis.summary or len(analysis.summary.strip()) < 10:
            continue
        if not all([analysis.importance_score, analysis.urgency_score,
                    analysis.credibility_score, analysis.relevance_score]):
            continue
        priority = (channel_priorities or {}).get(post.channel_name, 1.0)
        score = _composite(analysis) * priority
        text_lower = (post.text or "").lower()
        for kw in config.keywords:
            if kw.lower() in text_lower:
                score = min(5.0, score + config.keyword_boost)
                break
        score = min(5.0, score)
        score *= _recency_multiplier(post.timestamp, now, config.recency_half_life_hours, config.recency_floor)
        scored.append(TriagedPost(post=post, analysis=analysis, composite_score=score))

    scored.sort(key=lambda t: (
        -t.composite_score,
        -_SEVERITY_RANK.get(t.analysis.threat_level, 0),
        -t.post.timestamp.timestamp(),
    ))

    # Cluster duplicates: keep the highest-scoring report per story as the representative,
    # and record lower-scored cross-channel reposts as corroborations rather than dropping
    # them. Two posts are duplicates if they share >= dedup threshold words in summary/title
    # within dedup_summary_window_secs, or share enough named entities within the other
    # configured windows (see _find_duplicate).
    kept: list[TriagedPost] = []
    for item in scored:
        match = _find_duplicate(item, kept, time_window_secs=config.dedup_window_secs,
                                 entity_cluster_window_secs=config.entity_cluster_window_secs,
                                 summary_window_secs=config.dedup_summary_window_secs)
        if match is not None:
            match.corroborations.append(CorroborationRef(
                channel_slug=item.post.channel_name,
                message_id=item.post.message_id,
                timestamp=item.post.timestamp,
            ))
        else:
            kept.append(item)

    posts_clustered = len(scored) - len(kept)

    # Apply a multiplicative corroboration boost to representatives with corroborations,
    # then re-sort since boosting can change relative ranking.
    for item in kept:
        if item.corroborations:
            boost = min(
                1 + config.corroboration_weight * len(item.corroborations),
                config.corroboration_cap,
            )
            item.composite_score *= boost

    kept.sort(key=lambda t: (
        -t.composite_score,
        -_SEVERITY_RANK.get(t.analysis.threat_level, 0),
        -t.post.timestamp.timestamp(),
    ))

    main_scored = [t for t in kept if t.composite_score >= config.min_composite_score]
    appendix_items = [t for t in kept if t.composite_score < config.min_composite_score]
    if config.max_main_items > 0:
        appendix_items = main_scored[config.max_main_items:] + appendix_items
        main_items = main_scored[:config.max_main_items]
    else:
        main_items = main_scored

    all_kept = main_items + appendix_items
    category_counts = dict(Counter(t.analysis.category for t in all_kept))
    channel_names = sorted({p.channel_name for p, _ in pairs})

    # Executive Summary: every CRITICAL item is guaranteed a slot, regardless of score,
    # then filled with the highest-scoring remaining items up to 10.
    critical_items = [t for t in main_items if t.analysis.threat_level == "CRITICAL"]
    other_items = [t for t in main_items if t.analysis.threat_level != "CRITICAL"]
    executive_items = (critical_items + other_items)[:10]

    return BriefingContent(
        date=today,
        main_items=main_items,
        appendix_items=appendix_items,
        channel_names=channel_names,
        category_counts=category_counts,
        executive_items=executive_items,
        posts_clustered=posts_clustered,
    )
