"""Weekly Digest — generate a summary page of this week's Notion activity."""

import logging
from datetime import datetime, timezone

from agent import ClaudeAgent
from notion_client import NotionClient
from config import Config

logger = logging.getLogger(__name__)


def run_weekly_digest(config: Config, notion: NotionClient, agent: ClaudeAgent):
    """Create a weekly digest page summarizing recent Notion activity."""
    db_id = config.inbox_database_id or config.tasks_database_id
    if not db_id:
        logger.warning("No database ID configured — skipping weekly digest.")
        return

    logger.info("Running weekly digest generation...")

    # Gather activity from all configured databases
    activity = []
    for label, did in [
        ("Inbox", config.inbox_database_id),
        ("Tasks", config.tasks_database_id),
    ]:
        if not did:
            continue
        pages = notion.get_recently_modified_pages(did, since_days=7)
        for p in pages:
            activity.append({
                "database": label,
                "title": notion.get_page_title(p),
                "last_edited": p.get("last_edited_time", ""),
                "url": p.get("url", ""),
            })

    if not activity:
        logger.info("No recent activity — skipping digest.")
        return

    logger.info(f"Summarizing {len(activity)} page(s) from the past week...")

    try:
        digest_text = agent.generate_weekly_digest(activity)
    except Exception:
        logger.exception("Failed to generate weekly digest with Claude.")
        return

    # Create a new digest page in the first available database
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = f"Weekly Digest — {today}"

    # Build page content blocks
    blocks = [
        notion.make_heading_block("Weekly Digest", level=1),
        notion.make_paragraph_block(digest_text),
    ]

    try:
        target_db = config.inbox_database_id or config.tasks_database_id
        notion.create_page(
            parent_database_id=target_db,
            properties={
                "Name": {
                    "title": notion.make_rich_text(title),
                },
                "Category": {"select": {"name": "Reference"}},
            },
            children=blocks,
        )
        logger.info(f"  Created digest page: '{title}'")
    except Exception:
        logger.exception("Failed to create weekly digest page.")
