# tg_compiler

A local-only Telegram channel intelligence briefing system. Monitors Telegram channels, analyses posts with a locally-running Vision-Language Model via LM Studio, and generates daily ranked PDF briefings — no cloud APIs, no data leaves your machine.

## How it works

```
Telegram channels
       ↓  (Telethon)
   scraper.py  →  SQLite (posts)
                      ↓
   analyzer.py  ←  LM Studio VLM
   (scores each post 1–5 on importance, urgency, credibility, relevance)
                      ↓
     triage.py  (composite score, keyword boost, main/appendix split)
                      ↓
   generator.py  →  briefings/briefing_YYYY-MM-DD.pdf
```

Two operating modes:
- **`--batch`**: one-shot run — scrape all channels, analyse everything, generate today's PDF
- **`--daemon`**: long-running — listen for live messages, analyse as they arrive, generate PDF daily at a configured time

---

## Prerequisites

### 1. Python 3.11+

```bash
python --version   # must be 3.11 or newer
```

### 2. Telegram API credentials

1. Go to [https://my.telegram.org/apps](https://my.telegram.org/apps) and log in
2. Create a new application (any name)
3. Note your **API ID** (integer) and **API Hash** (hex string)

### 3. LM Studio

Download from [https://lmstudio.ai](https://lmstudio.ai) and install it. You need a Vision-Language Model (VLM) loaded — one that can analyse images alongside text.

Recommended models (small and capable):
- `google/gemma-3-4b-it` — good balance of speed and quality
- `llava-v1.5-7b` — strong multimodal reasoning
- Any GGUF model with vision support

To start the server inside LM Studio: **Local Server → Start Server** (default port 1234).

---

## Installation

```bash
# Clone or copy the project directory, then:
cd tg_compiler

# Create virtual environment
python -m venv .venv

# Activate it
source .venv/bin/activate          # Linux / macOS
# or: .venv\Scripts\activate       # Windows

# Install the package and all dependencies
pip install -e ".[dev]"
```

Verify the install:
```bash
python -m tg_compiler.main --help
```

Expected output:
```
usage: tg_compiler [-h] [--config CONFIG] [--batch] [--daemon] [--generate]
```

---

## Configuration

### Step 1 — Copy the example config

```bash
cp config.yaml.example config.yaml
```

### Step 2 — Fill in your credentials

Open `config.yaml` and edit:

```yaml
telegram:
  api_id: 123456          # your integer API ID from my.telegram.org
  api_hash: "abc123..."   # your API hash from my.telegram.org
  session_name: "briefing_session"   # filename for the session (no extension needed)
  channels:
    - username: "@channelname"   # public @username, or use id: 123456789 for private
      slug: "news"               # short identifier used in file paths and reports
      priority: 1.0
    - username: "@another_channel"
      slug: "intel"
      priority: 1.0
  rate_limit_delay_ms: 500   # pause between channel scrapes (be conservative)

lmstudio:
  model: "google/gemma-3-4b-it"   # must match the model name shown in LM Studio
  server_port: 1234
  temperature: 0.3
  max_tokens: 800

triage:
  keywords: ["urgent", "breaking", "attack", "launch"]  # words that boost post score
  keyword_boost: 0.5        # added to composite score when a keyword matches
  min_composite_score: 2.5  # posts below this go to the Appendix section

generation:
  output_dir: "./briefings"   # where PDFs are saved
  timezone: "UTC"
  generate_at: "23:59"        # time for daily auto-generation in daemon mode (HH:MM)

storage:
  db_path: "./data/briefing.db"
  media_dir: "./data/media"
  retention_days: 30          # delete media older than this many days
```

### Optional — environment variable override

Instead of putting credentials in config.yaml, you can use environment variables:

```bash
export TG_API_ID=123456
export TG_API_HASH=your_api_hash_here
```

These override `telegram.api_id` and `telegram.api_hash` in the YAML when you run with any command.

---

## First run — Telegram authentication

The first time you run any command that connects to Telegram, Telethon will prompt you to log in interactively. This only happens once; the session is saved to `<session_name>.session`.

```bash
source .venv/bin/activate
python -m tg_compiler.main --batch
```

You will see:
```
Please enter your phone number (or bot token):
```

1. Enter your phone number in international format (e.g. `+447700900000`)
2. Telegram will send a login code to your Telegram app
3. Enter the code when prompted
4. If you have 2FA enabled, enter your password

After successful login, `briefing_session.session` is created and subsequent runs connect silently.

---

## Batch mode — one-shot scrape and report

Batch mode scrapes all configured channels, analyses every unseen post, and generates today's PDF.

```bash
source .venv/bin/activate
python -m tg_compiler.main --batch
```

What happens:
1. Connects to Telegram
2. For each channel: fetches messages since the last run (up to 500), downloads attached photos
3. Sends each new post to LM Studio for analysis (importance, urgency, credibility, relevance, category, key entities)
4. Runs triage: scores posts, applies keyword boosts, splits into main/appendix
5. Generates `briefings/briefing_YYYY-MM-DD.pdf` and `briefings/briefing_YYYY-MM-DD.md`
6. Disconnects

Subsequent `--batch` runs on the same day are safe — cursor tracking ensures no post is fetched twice, and UNIQUE constraints prevent duplicate DB entries.

Typical log output:
```
2026-06-07 09:00:01 INFO Scraped 14 new posts from news
2026-06-07 09:00:02 INFO Scraped 3 new posts from intel
2026-06-07 09:00:45 INFO Analysed 17 posts
2026-06-07 09:00:46 INFO Briefing generated: briefings/briefing_2026-06-07.pdf
```

---

## Daemon mode — live monitoring

Daemon mode runs indefinitely, listening for new messages in real time and generating a PDF automatically at the configured `generate_at` time each day.

### Start LM Studio first

Ensure LM Studio is running with a model loaded before starting the daemon:
- Open LM Studio → Local Server tab → click **Start Server**
- Confirm the server shows "Running on port 1234"

### Start the daemon

```bash
source .venv/bin/activate
python -m tg_compiler.main --daemon
```

What happens at startup:
1. Opens a Telegram client session
2. Resolves all configured channels and registers a live message listener
3. Spawns a background scheduler for daily briefing generation
4. Logs: `Daemon running on N channels`

What happens when a new message arrives:
1. Downloads attached media (if any)
2. Inserts a `PostRecord` into SQLite
3. Sends the post to LM Studio for analysis and saves the result
4. Duplicate posts (by channel_id + message_id) are silently skipped

What happens at `generate_at` time each day:
1. Runs triage on all posts analysed that day
2. Generates `briefings/briefing_YYYY-MM-DD.pdf`
3. Purges media directories older than `retention_days`

### Stopping the daemon

Press `Ctrl+C`. The Telegram client disconnects cleanly.

### Running as a background service (optional)

```bash
# Using nohup
nohup python -m tg_compiler.main --daemon > logs/daemon.log 2>&1 &
echo $! > daemon.pid

# Stop it later
kill $(cat daemon.pid)
```

Or use a systemd service unit if running on Linux.

---

## Generating a report manually

If you have posts in the database but want to regenerate today's report without re-scraping:

```bash
source .venv/bin/activate
python -m tg_compiler.main --generate
```

This reads all posts for today that have been analysed, runs triage, and writes the PDF and Markdown files to `briefings/`. Useful if you want to tweak triage settings and re-generate without waiting for a new batch.

---

## Reading the reports

Reports are written to `./briefings/` (configurable via `generation.output_dir`).

```
briefings/
├── briefing_2026-06-07.pdf   ← primary report
└── briefing_2026-06-07.md    ← Markdown source (always generated alongside PDF)
```

### Report structure

**Executive Summary** — top 10 posts across all channels, one line each with importance badge and channel attribution.

**Per-channel sections** — posts that cleared `min_composite_score`, sorted by composite score descending. Each entry shows:
- Importance badge: 🔴 (score ≥4) · 🟡 (≥3) · 🟢 (<3)
- Post timestamp
- Summary from the VLM
- Category (Breaking News / Analysis / Official Statement / Rumor / Media / Other)
- Composite score out of 5
- Key named entities
- Image analysis excerpt (if the post had a substantive image)
- Attached images (up to 3, embedded in PDF)

**Appendix** — posts that scored below `min_composite_score`, listed compactly.

**Statistics table** — total posts, main/appendix counts, channels covered.

### Composite scoring formula

```
score = 0.4 × importance + 0.3 × urgency + 0.2 × credibility + 0.1 × relevance
```

Each dimension is rated 1–5 by the VLM. A post with all 5s scores 5.0. Keyword matches add `keyword_boost` (default 0.5) to the score, capped at 5.0.

---

## Troubleshooting

**"No module named tg_compiler"**  
The virtual environment is not active. Run `source .venv/bin/activate` first.

**"LM Studio connection refused"**  
LM Studio server is not running, or is on a different port. Start it via LM Studio → Local Server → Start Server. Check `lmstudio.server_port` in `config.yaml` matches.

**"ChannelPrivateError"**  
Your Telegram account is not a member of that channel. Join it in the Telegram app and retry.

**"FloodWaitError: X seconds"**  
Telegram rate-limited the request. The scraper will pause automatically and resume. If it happens often, increase `rate_limit_delay_ms`.

**"ValidationError: extra inputs are not permitted"**  
A field in `config.yaml` is misspelled or unknown. Check the field name against `config.yaml.example`.

**"TG_API_ID env var must be an integer"**  
The `TG_API_ID` environment variable is set but contains a non-integer value. Either unset it or fix the value.

**PDF is empty or has no posts**  
Either no posts were scraped today, or LM Studio analysis has not run yet. Run `--batch` to trigger a full scrape+analyse cycle, then check `--generate`.

**Session file issues after moving the project**  
Delete `<session_name>.session` and re-authenticate by running `--batch` again.

---

## Running tests

```bash
source .venv/bin/activate
pytest                          # all 36 tests
pytest tests/test_db.py -v      # single file
pytest tests/test_triage.py::test_composite_score_formula -v   # single test
```

Tests use in-memory SQLite and do not require Telegram credentials or a running LM Studio server.
