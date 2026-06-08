from __future__ import annotations
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from tg_compiler.config import TriageConfig
from tg_compiler.db import PostRecord, AnalysisRecord

_NON_WORD = re.compile(r'[^\w\s]')

_SEVERITY_RANK: dict[str, int] = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "LOW": 1}


@dataclass
class TriagedPost:
    post: PostRecord
    analysis: AnalysisRecord
    composite_score: float


@dataclass
class BriefingContent:
    date: date
    main_items: list[TriagedPost]
    appendix_items: list[TriagedPost]
    channel_names: list[str] = field(default_factory=list)
    # slug → bare username (no @) for building t.me deep links
    channel_links: dict[str, str] = field(default_factory=dict)
    category_counts: dict[str, int] = field(default_factory=dict)


def _composite(a: AnalysisRecord) -> float:
    return (
        0.4 * a.importance_score
        + 0.3 * a.urgency_score
        + 0.2 * a.credibility_score
        + 0.1 * a.relevance_score
    )


def _jaccard(a: str, b: str, min_len: int = 3) -> float:
    words_a = {w for w in _NON_WORD.sub('', a.lower()).split() if len(w) >= min_len}
    words_b = {w for w in _NON_WORD.sub('', b.lower()).split() if len(w) >= min_len}
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def _is_duplicate(
    candidate: TriagedPost,
    kept: list[TriagedPost],
    time_window_secs: float,
    entity_cluster_window_secs: float = 86400,
    threshold: float = 0.28,
) -> bool:
    for existing in kept:
        delta = abs(
            (candidate.post.timestamp - existing.post.timestamp).total_seconds()
        )
        if delta > time_window_secs:
            continue
        if _jaccard(candidate.analysis.summary, existing.analysis.summary) >= threshold:
            return True
        if candidate.analysis.title and existing.analysis.title:
            if _jaccard(candidate.analysis.title, existing.analysis.title) >= threshold:
                return True
        # Entity overlap: ≥3 shared named entities within time window
        cand_entities = {e.lower() for e in candidate.analysis.key_entities}
        exist_entities = {e.lower() for e in existing.analysis.key_entities}
        if len(cand_entities) >= 3 and len(exist_entities) >= 3:
            if len(cand_entities & exist_entities) >= 3:
                return True

    # Extended entity-cluster pass: ≥4 shared entities within the cluster window (default 24h)
    for existing in kept:
        delta = abs(
            (candidate.post.timestamp - existing.post.timestamp).total_seconds()
        )
        if delta > entity_cluster_window_secs:
            continue
        cand_entities = {e.lower() for e in candidate.analysis.key_entities}
        exist_entities = {e.lower() for e in existing.analysis.key_entities}
        if len(cand_entities) >= 4 and len(exist_entities) >= 4:
            if len(cand_entities & exist_entities) >= 4:
                return True

    return False


def triage(
    pairs: list[tuple[PostRecord, AnalysisRecord]],
    config: TriageConfig,
    today: date | None = None,
) -> BriefingContent:
    today = today or date.today()
    scored: list[TriagedPost] = []

    for post, analysis in pairs:
        if not analysis.summary or len(analysis.summary.strip()) < 10:
            continue
        score = _composite(analysis)
        text_lower = (post.text or "").lower()
        for kw in config.keywords:
            if kw.lower() in text_lower:
                score = min(5.0, score + config.keyword_boost)
                break
        scored.append(TriagedPost(post=post, analysis=analysis, composite_score=score))

    scored.sort(key=lambda t: (
        -t.composite_score,
        -_SEVERITY_RANK.get(t.analysis.threat_level, 0),
        -t.post.timestamp.timestamp(),
    ))

    # Deduplicate: keep highest-scoring report per story cluster.
    # Two posts are duplicates if they share ≥35% words in summary/title
    # AND are within a 2-hour window.
    kept: list[TriagedPost] = []
    for item in scored:
        if not _is_duplicate(item, kept, time_window_secs=config.dedup_window_secs,
                              entity_cluster_window_secs=config.entity_cluster_window_secs):
            kept.append(item)

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

    return BriefingContent(
        date=today,
        main_items=main_items,
        appendix_items=appendix_items,
        channel_names=channel_names,
        category_counts=category_counts,
    )
