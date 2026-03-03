"""Stale Page Archiver — find and archive abandoned pages using AI judgment."""

import logging

from agent import ClaudeAgent
from notion_client import NotionClient
from config import Config

logger = logging.getLogger(__name__)


def run_stale_archiver(config: Config, notion: NotionClient, agent: ClaudeAgent):
    """Find pages that haven't been edited recently and archive the stale ones."""
    if not config.inbox_database_id:
        logger.warning("No NOTION_INBOX_DATABASE_ID set — skipping stale archiver.")
        return

    logger.info(
        f"Running stale page archiver (threshold: {config.stale_days} days)..."
    )
    pages = notion.get_stale_pages(config.inbox_database_id, config.stale_days)

    if not pages:
        logger.info("No stale pages found.")
        return

    logger.info(f"Found {len(pages)} stale page(s). Evaluating with Claude...")

    # Prepare page info for AI evaluation
    page_info = []
    for p in pages:
        title = notion.get_page_title(p)
        content_preview = ""
        try:
            content_preview = notion.get_page_content(p["id"])[:500]
        except Exception:
            pass
        page_info.append({
            "page_id": p["id"],
            "title": title,
            "last_edited": p.get("last_edited_time", ""),
            "content_preview": content_preview,
        })

    try:
        decisions = agent.evaluate_stale_pages(page_info)
    except Exception:
        logger.exception("Failed to evaluate stale pages with Claude.")
        return

    archived_count = 0
    kept_count = 0

    for decision in decisions:
        page_id = decision.get("page_id", "")
        title = decision.get("title", "")
        action = decision.get("action", "keep")
        reason = decision.get("reason", "")

        if action == "archive":
            try:
                notion.archive_page(page_id)
                archived_count += 1
                logger.info(f"  Archived '{title}': {reason}")
            except Exception:
                logger.exception(f"  Failed to archive '{title}'")
        else:
            kept_count += 1
            logger.info(f"  Kept '{title}': {reason}")

    logger.info(
        f"  Stale archiver complete: {archived_count} archived, {kept_count} kept."
    )
