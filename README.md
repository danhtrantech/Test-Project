# Notion Night Agent

A Claude-powered daemon that automates your Notion workspace 24/7. It runs in the background and handles tedious organizational tasks while you sleep.

## What It Does

| Task | Schedule | Description |
|------|----------|-------------|
| **Inbox Triage** | Every 15 min | Auto-categorizes and prioritizes uncategorized pages using Claude |
| **Deadline Tracker** | Every 30 min | Flags overdue/urgent tasks and bumps their priority |
| **Weekly Digest** | Weekly (Mon 9 AM) | Generates a summary page of your week's Notion activity |
| **Stale Archiver** | Daily | Finds pages untouched for 30+ days, uses Claude to decide archive vs. keep |

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A [Notion integration](https://www.notion.so/my-integrations) with access to your databases
- An [Anthropic API key](https://console.anthropic.com/)

### 2. Notion Setup

1. Create a Notion integration at https://www.notion.so/my-integrations
2. Share your target databases with the integration
3. Your databases should have these properties:
   - **Inbox DB**: `Name` (title), `Category` (select), `Priority` (select), `Status` (select)
   - **Tasks DB**: `Name` (title), `Due Date` (date), `Status` (select), `Priority` (select)

### 3. Install & Run

```bash
# Clone and enter the project
git clone <repo-url> && cd notion-night-agent

# Create a virtual environment
python -m venv .venv && source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys and database IDs

# Run the agent
python main.py
```

### 4. Run with Docker (optional)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

```bash
docker build -t notion-night-agent .
docker run -d --env-file .env --name notion-agent notion-night-agent
```

## Configuration

All settings are configured via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Your Anthropic API key (required) |
| `NOTION_API_KEY` | — | Your Notion integration token (required) |
| `NOTION_INBOX_DATABASE_ID` | — | Database ID for inbox triage |
| `NOTION_TASKS_DATABASE_ID` | — | Database ID for deadline tracking |
| `CLAUDE_MODEL` | `claude-sonnet-4-20250514` | Claude model to use |
| `INBOX_TRIAGE_INTERVAL` | `15` | Minutes between inbox scans |
| `DEADLINE_CHECK_INTERVAL` | `30` | Minutes between deadline checks |
| `STALE_DAYS` | `30` | Days before a page is considered stale |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

## Architecture

```
main.py              → Entry point + APScheduler daemon
agent.py             → Claude AI wrapper (categorization, analysis, digest)
notion_client.py     → Notion API client (queries, updates, page creation)
config.py            → Environment-based configuration
tasks/
  inbox_triage.py    → Scan & categorize uncategorized pages
  deadline_tracker.py→ Flag overdue tasks, reprioritize
  weekly_digest.py   → Generate weekly summary page
  stale_archiver.py  → AI-powered stale page cleanup
```

## License

MIT
