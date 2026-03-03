import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # API keys
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    notion_api_key: str = os.getenv("NOTION_API_KEY", "")

    # Notion database/page IDs
    inbox_database_id: str = os.getenv("NOTION_INBOX_DATABASE_ID", "")
    tasks_database_id: str = os.getenv("NOTION_TASKS_DATABASE_ID", "")

    # Claude model
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

    # Schedule intervals (in minutes)
    inbox_triage_interval: int = int(os.getenv("INBOX_TRIAGE_INTERVAL", "15"))
    deadline_check_interval: int = int(os.getenv("DEADLINE_CHECK_INTERVAL", "30"))
    stale_archive_interval: int = int(os.getenv("STALE_ARCHIVE_INTERVAL", "1440"))  # daily
    weekly_digest_day: int = int(os.getenv("WEEKLY_DIGEST_DAY", "0"))  # Monday
    weekly_digest_hour: int = int(os.getenv("WEEKLY_DIGEST_HOUR", "9"))

    # Thresholds
    stale_days: int = int(os.getenv("STALE_DAYS", "30"))

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # Categories for inbox triage
    categories: list = field(default_factory=lambda: [
        "Work", "Personal", "Research", "Meeting Notes",
        "Ideas", "Reference", "Archive",
    ])

    def validate(self):
        missing = []
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.notion_api_key:
            missing.append("NOTION_API_KEY")
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}"
            )
