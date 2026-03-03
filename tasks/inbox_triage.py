"""Inbox Triage — auto-categorize and prioritize uncategorized Notion pages."""

import logging

from agent import ClaudeAgent
from notion_client import NotionClient
from config import Config

logger = logging.getLogger(__name__)


def run_inbox_triage(config: Config, notion: NotionClient, agent: ClaudeAgent):
    """Scan the inbox database for uncategorized pages, then categorize them."""
    if not config.inbox_database_id:
        logger.warning("No NOTION_INBOX_DATABASE_ID set — skipping inbox triage.")
        return

    logger.info("Running inbox triage...")
    pages = notion.get_uncategorized_pages(config.inbox_database_id)

    if not pages:
        logger.info("Inbox is clean — nothing to triage.")
        return

    logger.info(f"Found {len(pages)} uncategorized page(s). Triaging...")

    for page in pages:
        page_id = page["id"]
        title = notion.get_page_title(page)

        try:
            content = notion.get_page_content(page_id)
            result = agent.categorize_page(title, content, config.categories)

            category = result.get("category", "Uncategorized")
            priority = result.get("priority", "medium")
            summary = result.get("summary", "")

            # Update the page with category, priority, and AI summary
            properties = {
                "Category": {"select": {"name": category}},
                "Priority": {"select": {"name": priority.capitalize()}},
            }

            notion.update_page_properties(page_id, properties)

            # Append the AI summary as a callout block
            if summary:
                notion.append_blocks(page_id, [
                    {
                        "object": "block",
                        "type": "callout",
                        "callout": {
                            "rich_text": notion.make_rich_text(
                                f"AI Summary: {summary}"
                            ),
                            "icon": {"type": "emoji", "emoji": "🤖"},
                        },
                    }
                ])

            logger.info(
                f"  Triaged '{title}' → {category} ({priority})"
            )

        except Exception:
            logger.exception(f"  Failed to triage page '{title}'")
