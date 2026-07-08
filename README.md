# Knowledge Planet Daily Scraper

**English** | [简体中文](README.zh-CN.md)

Automatically scrapes the **owner posts** and **digests** of specified [ZSXQ (知识星球)](https://zsxq.com) groups every day, writes them into a **Feishu (Lark) Bitable**, optionally uses an LLM to generate a one-line summary + topic tags for each post, classifies everything into topics and builds an auto-updating **knowledge graph** and a **daily reading queue**, pushes a **weekly digest** to a Feishu group, and offers a command-line **semantic Q&A**. Alerts you via a Feishu bot webhook when the login cookie expires.

> Personal, self-hosted tooling for turning a paid content feed into a searchable, topic-organized knowledge base.

## Showcase

**How the pieces fit together** (plain-language system map):

![System architecture](assets/architecture.svg)

**The knowledge graph** — center → 5 categories → 24 topics (auto-rebuilt daily; in the live HTML, click a topic to read its narrative overview and posts, grouped by author with read/unread tracking):

![Knowledge graph](assets/graph.svg)

> A screenshot of the Feishu table and an interaction GIF live under [`assets/`](assets/). To add your own: capture with `Win + Shift + S` (screenshot) or [ScreenToGif](https://www.screentogif.com/) (GIF), save as `assets/feishu.png` / `assets/demo.gif`, then reference them here.

## Features

- **Daily scrape** — pulls `scope=by_owner` (owner posts) and `scope=digests` (digests) only, so the table isn't flooded by ordinary member chatter. Deduplicated via a local state file.
- **Pluggable LLM enrichment** — one-line summary + topic tags + a single topic classification per post. Provider is swappable via `.env` (DeepSeek / OpenAI / Claude / any OpenAI-compatible API); the pipeline never hardcodes a model.
- **Knowledge graph** — a self-contained, auto-rebuilding `HTML` map: center → 5 categories → 24 topics; click a topic to read an LLM-written narrative overview and its posts (grouped by author, newest first, with read/unread tracking).
- **Today's reading queue** (`今日待看.html`) — a newest-first to-read list = items you haven't finished + freshly scraped ones. Tick "read" to clear each from the queue; write reflections per item with a built-in **knowledge-card template**. Read state and notes are saved in the browser and survive the daily rebuild.
- **Weekly digest** — every Sunday 20:00, summarizes the past week's highlights and pushes to a Feishu group.
- **Semantic Q&A (lightweight RAG)** — `python src/ask.py "what does X say about gold"` retrieves relevant posts and answers with citations.
- **Cookie-expiry alerts** — pushes a Feishu bot message when the ZSXQ login goes stale.
- **Resilient scheduling** — Windows Task Scheduler with dual triggers (at-logon + daily 09:00), once-per-day guard, auto-retry on failure, and catch-up when the machine was off.

## How it works

```
ZSXQ v2 API  ──scrape──▶  normalize/clean  ──enrich (LLM)──▶  Feishu Bitable
   (owner + digests)         (dedup)          summary/tags/topic      │
                                                                      ├─▶ Knowledge graph (HTML, auto-rebuilt daily)
                                                                      ├─▶ Today's reading queue (HTML, auto-rebuilt daily)
                                                                      ├─▶ Weekly digest (Feishu group, Sundays)
                                                                      └─▶ Semantic Q&A (CLI)
```

## Tech stack

Python 3 · `requests` · `python-dotenv` · `jieba` · `openai` (or `anthropic`). No web framework — just API calls to ZSXQ and Feishu.

## Quick start

1. **Clone & install**
   ```bash
   git clone https://github.com/Liz-Ji/Knowledge-Planet-daily-scraper.git
   cd Knowledge-Planet-daily-scraper
   python -m venv .venv
   .venv/Scripts/python.exe -m pip install -r requirements.txt
   ```
2. **Configure** — copy `.env.example` to `.env` and fill in:
   - `ZSXQ_COOKIE` — from a logged-in `wx.zsxq.com` session (F12 → Network → any `api.zsxq.com` request → copy the `zsxq_access_token`).
   - `ZSXQ_GROUPS` — `groupId:DisplayName` pairs, comma-separated.
   - `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_APP_TOKEN` / `FEISHU_TABLE_ID` — your Feishu app + Bitable.
   - `FEISHU_ALERT_WEBHOOK` — a Feishu group bot webhook.
   - `LLM_*` — leave `LLM_API_KEY` empty to skip AI enrichment, or set a provider (DeepSeek recommended for mainland China: direct connection, no proxy).
3. **Run once**
   ```bash
   .venv/Scripts/python.exe src/main.py
   ```
4. **Schedule it** (Windows)
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts/setup_task.ps1
   ```

> The Feishu Bitable must contain the expected fields (post id, group, type, author, title, body, publish time, likes, comments, link, scrape time, summary, tags, topic). See [CLAUDE.md](CLAUDE.md) for the exact schema.

## Usage

| Command | What it does |
|---|---|
| `python src/main.py [--force]` | Daily scrape → enrich → write to Feishu → rebuild graph |
| `python src/build_graph.py [--refresh]` | Rebuild the knowledge-graph HTML (`--refresh` re-generates all topic overviews) |
| `python src/build_reading.py` | Rebuild the "today's reading queue" HTML |
| `python src/weekly_report.py [--dry]` | Generate & push the weekly digest (`--dry` prints only) |
| `python src/ask.py "your question"` | Command-line semantic Q&A over the collected content |
| `python src/backfill_enrich.py` | Back-fill summaries/tags for existing records (run once) |
| `python src/backfill_topics.py` | Back-fill topic classification for existing records (run once) |
| `python src/backfill_history.py` | Deep-scrape a full year of history (default 2025, run once) |
| `python src/fix_links.py` | Rebuild all "view original" links to the correct format (idempotent) |

## Project structure

```
src/
  config.py          # reads .env
  zsxq_client.py     # ZSXQ scraping (v2 API, entity cleaning, retry on anti-bot)
  feishu_client.py   # Feishu Bitable read/write
  notifier.py        # webhook alerts
  summarizer.py      # swappable LLM layer: enrich() + chat()
  topics.py          # 24-topic taxonomy (knowledge-graph backbone)
  build_graph.py     # generate/refresh the knowledge-graph HTML
  build_reading.py   # generate/refresh the "today's reading queue" HTML
  weekly_report.py   # weekly digest
  ask.py             # semantic Q&A CLI
  main.py            # daily entry point
  backfill_*.py / fix_links.py  # one-off maintenance scripts
scripts/
  run_daily.ps1 / run_weekly.ps1 / setup_task.ps1   # Windows scheduling
```

## Notes

- Uses ZSXQ's **unofficial** web API; header/version values may need occasional updates if ZSXQ tightens anti-scraping. Details and troubleshooting are in [CLAUDE.md](CLAUDE.md).
- Secrets live only in `.env` (git-ignored). The generated `知识图谱.html`, `今日待看.html`, logs, and dedup state are git-ignored too.
- Designed for personal use on a local Windows machine.

## License

Personal project — no license specified. Use at your own discretion.
