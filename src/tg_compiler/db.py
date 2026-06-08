from __future__ import annotations
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PostRecord:
    channel_id: int
    channel_name: str
    message_id: int
    timestamp: datetime
    text: str
    media_paths: list[str]
    has_images: bool
    raw_json: str
    id: int | None = None
    has_video: bool = False


@dataclass
class AnalysisRecord:
    post_id: int
    summary: str
    importance_score: int
    urgency_score: int
    credibility_score: int
    relevance_score: int
    category: str
    key_entities: list[str]
    model_used: str
    image_insights: str | None = None
    title: str = ""
    threat_level: str = "MODERATE"
    id: int | None = None


class Database:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        self._conn.row_factory = sqlite3.Row

    def init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id BIGINT NOT NULL,
                channel_name TEXT,
                message_id BIGINT NOT NULL,
                timestamp DATETIME NOT NULL,
                text TEXT,
                media_paths TEXT,
                has_images BOOLEAN,
                has_video BOOLEAN DEFAULT 0,
                raw_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_id, message_id)
            );
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER REFERENCES posts(id),
                title TEXT,
                summary TEXT NOT NULL,
                importance_score INTEGER,
                urgency_score INTEGER,
                credibility_score INTEGER,
                relevance_score INTEGER,
                category TEXT,
                key_entities TEXT,
                image_insights TEXT,
                model_used TEXT,
                processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS channel_cursors (
                channel_id BIGINT PRIMARY KEY,
                last_seen_id BIGINT NOT NULL DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self._conn.commit()
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(analyses)").fetchall()
        }
        for col, defn in [
            ("title", "TEXT"),
            ("threat_level", "TEXT DEFAULT 'MODERATE'"),
        ]:
            if col not in existing:
                self._conn.execute(f"ALTER TABLE analyses ADD COLUMN {col} {defn}")
                self._conn.commit()

        post_cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(posts)").fetchall()
        }
        if "has_video" not in post_cols:
            self._conn.execute("ALTER TABLE posts ADD COLUMN has_video BOOLEAN DEFAULT 0")
            self._conn.commit()

    def insert_post(self, post: PostRecord) -> int | None:
        try:
            cur = self._conn.execute(
                """INSERT INTO posts
                   (channel_id, channel_name, message_id, timestamp, text,
                    media_paths, has_images, has_video, raw_json)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (post.channel_id, post.channel_name, post.message_id,
                 post.timestamp.isoformat(), post.text,
                 json.dumps(post.media_paths), post.has_images, post.has_video, post.raw_json),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def get_post(self, post_id: int) -> PostRecord:
        row = self._conn.execute(
            "SELECT * FROM posts WHERE id=?", (post_id,)
        ).fetchone()
        return _row_to_post(row)

    def get_last_seen_id(self, channel_id: int) -> int:
        row = self._conn.execute(
            "SELECT last_seen_id FROM channel_cursors WHERE channel_id=?",
            (channel_id,),
        ).fetchone()
        return row["last_seen_id"] if row else 0

    def reset_all_cursors(self) -> None:
        self._conn.execute("UPDATE channel_cursors SET last_seen_id=0")
        self._conn.commit()

    def set_last_seen_id(self, channel_id: int, message_id: int) -> None:
        self._conn.execute(
            """INSERT INTO channel_cursors(channel_id, last_seen_id)
               VALUES(?,?)
               ON CONFLICT(channel_id) DO UPDATE SET
                 last_seen_id=excluded.last_seen_id,
                 updated_at=CURRENT_TIMESTAMP""",
            (channel_id, message_id),
        )
        self._conn.commit()

    def get_unanalysed_posts(self) -> list[PostRecord]:
        rows = self._conn.execute(
            """SELECT p.* FROM posts p
               LEFT JOIN analyses a ON a.post_id = p.id
               WHERE a.id IS NULL"""
        ).fetchall()
        return [_row_to_post(r) for r in rows]

    def insert_analysis(self, rec: AnalysisRecord) -> int:
        cur = self._conn.execute(
            """INSERT INTO analyses
               (post_id, title, summary, importance_score, urgency_score, credibility_score,
                relevance_score, category, key_entities, image_insights, model_used, threat_level)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rec.post_id, rec.title, rec.summary, rec.importance_score, rec.urgency_score,
             rec.credibility_score, rec.relevance_score, rec.category,
             json.dumps(rec.key_entities), rec.image_insights, rec.model_used, rec.threat_level),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_days_posts_with_analyses(self, date_str: str) -> list[tuple[PostRecord, AnalysisRecord]]:
        rows = self._conn.execute(
            """SELECT p.*, a.id as a_id, a.title, a.summary, a.importance_score, a.urgency_score,
                      a.credibility_score, a.relevance_score, a.category,
                      a.key_entities, a.image_insights, a.model_used, a.threat_level
               FROM posts p
               JOIN analyses a ON a.post_id = p.id
               WHERE DATE(p.timestamp) = ?""",
            (date_str,),
        ).fetchall()
        result = []
        for row in rows:
            post = _row_to_post(row)
            analysis = AnalysisRecord(
                id=row["a_id"],
                post_id=post.id,
                title=row["title"] or "",
                summary=row["summary"],
                importance_score=row["importance_score"],
                urgency_score=row["urgency_score"],
                credibility_score=row["credibility_score"],
                relevance_score=row["relevance_score"],
                category=row["category"],
                key_entities=json.loads(row["key_entities"] or "[]"),
                image_insights=row["image_insights"],
                model_used=row["model_used"],
                threat_level=row["threat_level"] or "MODERATE",
            )
            result.append((post, analysis))
        return result

    def get_top_posts_for_date(self, date_str: str, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            """SELECT
                   a.title,
                   a.summary,
                   a.category,
                   COALESCE(a.threat_level, 'MODERATE') as threat_level,
                   (0.4*a.importance_score + 0.3*a.urgency_score
                    + 0.2*a.credibility_score + 0.1*a.relevance_score) as composite_score,
                   p.channel_name as channel_slug,
                   p.timestamp,
                   a.key_entities
               FROM analyses a
               JOIN posts p ON p.id = a.post_id
               WHERE DATE(p.timestamp) = ?
               ORDER BY composite_score DESC
               LIMIT ?""",
            (date_str, limit),
        ).fetchall()
        result = []
        for row in rows:
            result.append({
                "title": row["title"] or "",
                "summary": row["summary"] or "",
                "category": row["category"] or "Other",
                "threat_level": row["threat_level"],
                "composite_score": row["composite_score"],
                "channel_slug": row["channel_slug"],
                "timestamp": row["timestamp"],
                "entities": json.loads(row["key_entities"] or "[]"),
            })
        return result


def _row_to_post(row: sqlite3.Row) -> PostRecord:
    return PostRecord(
        id=row["id"],
        channel_id=row["channel_id"],
        channel_name=row["channel_name"],
        message_id=row["message_id"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        text=row["text"] or "",
        media_paths=json.loads(row["media_paths"] or "[]"),
        has_images=bool(row["has_images"]),
        has_video=bool(row["has_video"]),
        raw_json=row["raw_json"] or "{}",
    )
