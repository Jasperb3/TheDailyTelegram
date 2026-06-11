# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Python environment

> **IMPORTANT: Always install packages into `.venv` — never system-wide.**

A virtual environment lives at `.venv/`. Activate it before running any Python command:

```bash
source .venv/bin/activate
```

To set up from scratch:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Common commands

```bash
# Run all tests
pytest

# Run a single test file
pytest tests/test_db.py -v

# Run a single test
pytest tests/test_db.py::test_duplicate_post_is_ignored -v

# Run the CLI (requires config.yaml)
python -m tg_compiler.main --batch
python -m tg_compiler.main --batch --since 00:00        # re-scrape from midnight UTC today
python -m tg_compiler.main --batch --since 2026-06-01   # re-scrape from a specific date
python -m tg_compiler.main --daemon
python -m tg_compiler.main --generate
python -m tg_compiler.main --analyse                    # prepend front page to latest PDF for today
python -m tg_compiler.main --analyse --since 2026-06-07 # prepend front page for a specific date
```

## Architecture

The pipeline runs in six sequential stages, each in its own module:

```
Telegram (Telethon) → scraper.py → db.py ← analyzer.py (LM Studio) → triage.py → generator.py
                                                                                        ↓
                                                              briefings/YYYY-MM-DD/TheDailyTelegram_YYYY-MM-DD_HHMMSS.pdf
                                                                                        ↓
                                                                          synthesiser.py (LM Studio)
                                                                                        ↓
                                                                   intelligence front page prepended to PDF
```

**`config.py`** — Single `AppConfig` Pydantic model loaded from `config.yaml`. Use `load_config(path, env_override=True)` everywhere; it reads `TG_API_ID` / `TG_API_HASH` / `LM_API_TOKEN` from env (`.env` is auto-loaded by `main.py` via `python-dotenv`). `AppConfig` has `extra="forbid"` so YAML typos fail loudly. All fields in the config model are actively used — see `config.yaml.example` for the full reference with comments.

**`db.py`** — Owns the SQLite schema and all SQL. The two domain dataclasses (`PostRecord`, `AnalysisRecord`) live here. `AnalysisRecord` includes `title` (LLM-generated 5-10 word headline) and `threat_level` (one of `CRITICAL`, `HIGH`, `MODERATE`, `LOW`). `insert_post` returns `None` on duplicate (UNIQUE on `channel_id + message_id`) — callers use this to skip already-seen posts. `get_unanalysed_posts` drives the analyzer's work queue. `reset_all_cursors()` sets every channel cursor to 0 (used by `--since`). Safe migrations run at `init_schema()` time using `PRAGMA table_info` before any `ALTER TABLE` — safe on both fresh and existing databases.

**`scraper.py`** — Telethon async context manager. `Scraper.scrape_channel()` fetches messages since the last cursor (`offset_id=last_seen_id`); when the cursor is 0 (first run or after reset), uses `offset_date = now - lookback_seconds` instead. `iter_messages` is called with `limit=None` — no artificial cap, so a long backlog (e.g. after `--since`) is fully drained in one run. Skips `MessageService` objects (system events), downloads photos (with one retry), inserts to DB, and advances the per-channel cursor. `get_entity()` and the whole iteration are inside one try/except — a renamed/deleted channel or other error logs and returns whatever was collected so far instead of propagating; `run_batch` also wraps each `scrape_channel` call so one bad channel never aborts the rest of the batch.

**`analyzer.py`** — Uses the `openai` package against LM Studio's OpenAI-compatible REST endpoint (`/v1/chat/completions`), with a `timeout=120` client to bound a hung server. `PostAnalysis` is a Pydantic schema with `title`, `summary`, four 1-5 score fields, `category`, `key_entities`, `image_description`, and `threat_level`. `threat_level` is validated by a `@field_validator` — anything outside `{CRITICAL, HIGH, MODERATE, LOW}` defaults to `MODERATE`. Structured output via `client.beta.chat.completions.parse(response_format=PostAnalysis)` with a plain text fallback (no `response_format` parameter — LM Studio only supports `json_schema` or `text`). Fallback response strips markdown fences before JSON parsing, then falls through to regex extraction. `_sanitize()` cleans title, entities, and image description (`_clean_image_insights` also rejects "none provided"/"no video provided" variants), blanks `title`/`summary` if they match `_REFUSAL_RE` (LLM meta-talk like "the user provided", "no content provided", "as an AI"), then HTML-escapes `title`, `summary`, `key_entities`, and `image_description` via `escape_html()` (utils.py) so LLM output can never inject markup into the rendered briefing. `analysis_to_record(post_id, analysis, model_used)` builds an `AnalysisRecord` from a `PostAnalysis` and is the single place both `process_unanalysed` (batch) and `run_daemon` (main.py) construct records, so fields like `title` can't diverge between the two paths again. `process_unanalysed()` runs analyses concurrently up to `lmstudio.max_concurrent_analyses`; posts with `len(text.strip()) < MIN_CONTENT_CHARS` (30) and no media are skipped before calling the LLM and recorded with `category="Skipped"` (filtered out in `triage.py`) so they're never retried.

**`triage.py`** — Pure function `triage(pairs, config, channel_priorities) -> BriefingContent`. Posts with `analysis.category == "Skipped"` (see `analyzer.py` content gate) are excluded before scoring. Composite score: `(0.4*importance + 0.3*urgency + 0.2*credibility + 0.1*relevance) × channel_priority`, keyword-boosted (first match only, capped at 5.0), then multiplied by a recency factor from `_recency_multiplier()` — an exponential half-life decay (`recency_half_life_hours`, default 12h) floored at `recency_floor` (default 0.6) so very old posts are demoted but never zeroed out. After scoring, applies cross-channel deduplication: Jaccard word-overlap ≥ 0.28 on summary/title within `dedup_summary_window_secs` (default 6h) OR ≥3 shared named entities within `dedup_window_secs` → lower-scored duplicate dropped. A second pass drops posts sharing ≥4 entities within `entity_cluster_window_secs`. Splits survivors into `main_items` / `appendix_items` at `min_composite_score`, then caps `main_items` at `max_main_items` (overflow goes to appendix). `BriefingContent` also carries `executive_items` — all `CRITICAL`-threat-level `main_items` first, then the remaining highest-scored `main_items`, capped at 10, used for the Executive Summary section so a CRITICAL item is never bumped out by score alone. `channel_links` (slug → bare username) drives deep links and `category_counts` (category → post count) feeds the statistics table.

**`generator.py`** — `render_markdown(content)` via Jinja2 (`templates/briefing.md.j2`), then `generate_briefing(..., pdf=True)` via `markdown-pdf` (PyMuPDF backend). PDFs are saved to `{output_dir}/{YYYY-MM-DD}/TheDailyTelegram_{YYYY-MM-DD}_{HHMMSS}.pdf`; the `.md` source goes alongside it. Threat level badges are HTML `<span>` elements with inline `color:` styles (MuPDF renders text colour but not background-colour on inline elements). The Executive Summary loops over `content.executive_items` (not a slice of `main_items`). The image/video block shows a "Video" line (with `▶ Watch on Telegram` link) when `item.post.has_video`, an "Image" line when `item.analysis.image_insights` is set, or nothing for text-only posts. CSS at `templates/briefing.css`.

**`synthesiser.py`** — Intelligence front page generator. `_triaged_to_dicts(main_items)` converts a list of `TriagedPost` objects into the dict format consumed by `synthesise()`. `synthesise(config, posts)` calls LM Studio (temp=0.2, max_tokens=3000, `timeout=300`, no `response_format`) and strips markdown fences before parsing. `_validate_intel(data)` checks all four required keys and their sub-structure. `_sanitize_intel()` HTML-escapes every text field via `escape_html()` (utils.py) before rendering. `_render_front_page_md(intel, date)` renders `templates/intel_front_page.md.j2`. `_prepend_pdf()` uses `pypdf` to merge front page + briefing, writing to a temp file then atomically replacing the original with `os.replace()`. `run_analysis(config, target_date, main_items=None)` is the async entry point — when `main_items` is provided (passed from `run_batch`), synthesises directly from that triaged post list; when `main_items=None` (standalone `--analyse`), re-runs `get_days_posts_with_analyses()` + `triage()` to reconstruct the same post set as the main briefing. Finds the latest `TheDailyTelegram_*.pdf` in the date dir, fails gracefully if LM Studio is unreachable, and never corrupts the existing PDF.

**`utils.py`** — `escape_html(text)` escapes `&`, `<`, `>` (via `html.escape(text, quote=False)`) so LLM-derived text can't inject markup into the rendered Markdown/HTML output. `clean_entities()` and `_ENTITY_GARBAGE` filter junk/JSON-artifact entity strings (unchanged).

**`main.py`** — CLI entry point. `generate_daily_briefing()` returns `(pdf_path, BriefingContent)`. `run_batch()` wires full scrape→analyse→triage→generate→synthesise pipeline; calls `run_analysis()` at the end, passing `content.main_items` so the Intel Assessment synthesises from the same triaged post set as the main briefing. All "today" values use `datetime.now(timezone.utc).date()` — the briefing day is always the UTC calendar date, regardless of host timezone. `--analyse` flag runs synthesis standalone (useful after `--generate`); accepts `--since` to target a specific date. `--since TIME` flag (with `--batch`) resets all cursors and sets `lookback_seconds` in memory (accepts `HH:MM`, `YYYY-MM-DD`, `YYYY-MM-DDTHH:MM`). `run_daemon()` listens for live Telethon events, downloads media, analyses inline (including `threat_level`), and runs `schedule_daily_generation()` as a background task with a `done_callback` that logs (`"Daily generation scheduler crashed"`, with traceback) if the task raises, so a scheduler crash is visible instead of silently vanishing. `schedule_daily_generation()` uses `generation.timezone` (IANA name, e.g. `"Europe/London"`) to interpret `generate_at` (the wall-clock trigger time), but the briefing date itself is computed via UTC `date()`; also calls `run_analysis()` after each nightly generation so the Intel Assessment is produced in daemon mode too. `purge_old_media()` removes date dirs older than `retention_days`. Config is only loaded after an action flag is confirmed (so `--help` works without a `config.yaml`).

## Data flow details

- **Idempotency**: `UNIQUE(channel_id, message_id)` in SQLite. Re-running the same window never duplicates records.
- **Cursor tracking**: `channel_cursors` table stores `last_seen_id` per channel. Scraper uses `offset_id=last_seen` on `iter_messages`. When cursor is 0, falls back to `offset_date` based on `lookback_seconds`. `--since` resets all cursors to 0 for a one-off historical lookback — do NOT use it for routine re-runs, as it forces Telegram to re-serve already-seen messages (wasted API quota). Plain `--batch` uses the cursor and only fetches new posts.
- **Daemon is live-only**: `run_daemon()` processes messages that arrive while running — it never backfills history. Always run `--batch` before switching to `--daemon` to catch up on historical posts.
- **Media**: Downloaded to `./data/media/<channel_slug>/<YYYY-MM-DD>/<message_id>.jpg`. Path constructed by `media_path_for()`.
- **`key_entities`**: Stored as JSON text in SQLite; deserialized back to `list[str]` in `get_days_posts_with_analyses`.
- **Output paths**: `briefings/YYYY-MM-DD/TheDailyTelegram_YYYY-MM-DD_HHMMSS.pdf` (unique per run) and `briefings/YYYY-MM-DD/briefing_YYYY-MM-DD.md` (overwritten on each run, stable name).
- **LM Studio compatibility**: Do not use `response_format={"type": "json_object"}` — this build only accepts `json_schema` or `text`. The structured output path uses `client.beta.chat.completions.parse()` with a Pydantic model; the fallback and synthesiser use plain `chat.completions.create()` with no `response_format` and parse the text response directly.

## Testing notes

- DB tests use `":memory:"` SQLite — the `db` fixture in `conftest.py` initialises schema fresh per test.
- `scraper.py` has no integration tests for the Telethon layer (requires live credentials). Only `media_path_for()` is unit-tested.
- `analyzer.py` tests construct `PostAnalysis` and message payloads locally (no LM Studio server needed). `Analyzer` and `Scraper` are not mocked — integration tests require live services.
- `asyncio_mode = "auto"` in `pyproject.toml` — async test functions work without decorators.
- 105 tests across 9 files; all pass with `pytest`.

## Runtime requirements

- **LM Studio** must be running (local or remote) with a VLM loaded. Set `lmstudio.server_host` and `lmstudio.server_port` in `config.yaml`. If authentication is enabled, set `LM_API_TOKEN` in `.env`.
- **`.env`** is auto-loaded at startup via `python-dotenv`. Supported vars: `TG_API_ID`, `TG_API_HASH`, `LM_API_TOKEN`. These override the corresponding YAML fields.
- **Telegram session**: First run will prompt for a phone number/code; the session is persisted to `<session_name>.session` (gitignored).
- `config.yaml` is gitignored. Copy `config.yaml.example` and fill in `api_id` and `api_hash`.
