from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date

from tg_compiler.config import TriageConfig
from tg_compiler.db import PostRecord, AnalysisRecord


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


def _composite(a: AnalysisRecord) -> float:
    return (
        0.4 * a.importance_score
        + 0.3 * a.urgency_score
        + 0.2 * a.credibility_score
        + 0.1 * a.relevance_score
    )


def triage(
    pairs: list[tuple[PostRecord, AnalysisRecord]],
    config: TriageConfig,
    today: date | None = None,
) -> BriefingContent:
    today = today or date.today()
    scored: list[TriagedPost] = []

    for post, analysis in pairs:
        score = _composite(analysis)
        text_lower = (post.text or "").lower()
        for kw in config.keywords:
            if kw.lower() in text_lower:
                score = min(5.0, score + config.keyword_boost)
                break
        scored.append(TriagedPost(post=post, analysis=analysis, composite_score=score))

    scored.sort(key=lambda t: (-t.composite_score, -t.post.timestamp.timestamp()))

    main_items = [t for t in scored if t.composite_score >= config.min_composite_score]
    appendix_items = [t for t in scored if t.composite_score < config.min_composite_score]
    channel_names = sorted({p.channel_name for p, _ in pairs})

    return BriefingContent(
        date=today,
        main_items=main_items,
        appendix_items=appendix_items,
        channel_names=channel_names,
    )
