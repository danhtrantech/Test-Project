"""Deadline Tracker — flag overdue and urgent tasks, reprioritize them."""

import logging
from datetime import datetime, timezone

from agent import ClaudeAgent
from notion_client import NotionClient
from config import Config

logger = logging.getLogger(__name__)


def _extract_task_info(page: dict, notion: NotionClient) -> dict:
    """Pull title and due date from a Notion page."""
    title = notion.get_page_title(page)
    props = page.get("properties", {})
    due_date = None
    for prop in props.values():
        if prop.get("type") == "date" and prop.get("date"):
            due_date = prop["date"].get("start")
            break
    status = None
    for prop in props.values():
        if prop.get("type") == "select" and prop.get("select"):
            name = prop["select"].get("name", "").lower()
            if name in ("done", "in progress", "not started", "todo", "blocked"):
                status = prop["select"]["name"]
                break
    return {
        "page_id": page["id"],
        "title": title,
        "due_date": due_date,
        "status": status,
    }


def run_deadline_tracker(config: Config, notion: NotionClient, agent: ClaudeAgent):
    """Check tasks database for overdue / upcoming deadlines and reprioritize."""
    if not config.tasks_database_id:
        logger.warning("No NOTION_TASKS_DATABASE_ID set — skipping deadline tracker.")
        return

    logger.info("Running deadline tracker...")
    pages = notion.get_tasks_with_deadlines(config.tasks_database_id)

    if not pages:
        logger.info("No tasks with deadlines found.")
        return

    tasks = [_extract_task_info(p, notion) for p in pages]
    logger.info(f"Analyzing {len(tasks)} task(s) with deadlines...")

    try:
        analysis = agent.analyze_deadlines(tasks)
    except Exception:
        logger.exception("Failed to analyze deadlines with Claude.")
        return

    overdue = analysis.get("overdue", [])
    urgent = analysis.get("urgent", [])
    suggestions = analysis.get("suggestions", "")

    # Mark overdue tasks with high priority
    now = datetime.now(timezone.utc).date()
    for page in pages:
        title = notion.get_page_title(page)
        props = page.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "date" and prop.get("date"):
                due_str = prop["date"].get("start", "")
                if due_str:
                    due = datetime.fromisoformat(due_str).date()
                    if due < now:
                        try:
                            notion.update_page_properties(page["id"], {
                                "Priority": {"select": {"name": "High"}},
                            })
                            logger.info(f"  Marked overdue: '{title}' (due {due_str})")
                        except Exception:
                            logger.exception(f"  Failed to update '{title}'")
                break

    if overdue:
        logger.info(f"  {len(overdue)} overdue task(s)")
    if urgent:
        logger.info(f"  {len(urgent)} urgent task(s) (due within 48h)")
    if suggestions:
        logger.info(f"  AI suggestions: {suggestions}")
