#!/usr/bin/env python3
"""
Notion Night Agent — A Claude-powered daemon that automates your Notion workspace 24/7.

Tasks:
  1. Inbox Triage     — auto-categorize and prioritize new pages
  2. Deadline Tracker — flag overdue/urgent tasks and reprioritize
  3. Weekly Digest    — generate a summary of your week's activity
  4. Stale Archiver   — find and archive abandoned pages
"""

import logging
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agent import ClaudeAgent
from config import Config
from notion_client import NotionClient
from tasks import (
    run_deadline_tracker,
    run_inbox_triage,
    run_stale_archiver,
    run_weekly_digest,
)

logger = logging.getLogger("notion-night-agent")


def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main():
    config = Config()
    config.validate()
    setup_logging(config.log_level)

    logger.info("=" * 60)
    logger.info("  Notion Night Agent starting up")
    logger.info("=" * 60)

    notion = NotionClient(config.notion_api_key)
    agent = ClaudeAgent(config.anthropic_api_key, config.claude_model)

    scheduler = BlockingScheduler()

    # 1. Inbox triage — runs every N minutes
    scheduler.add_job(
        run_inbox_triage,
        trigger=IntervalTrigger(minutes=config.inbox_triage_interval),
        args=[config, notion, agent],
        id="inbox_triage",
        name="Inbox Triage",
        next_run_time=None,  # don't run immediately; let the one-shot below handle it
    )

    # 2. Deadline tracker — runs every N minutes
    scheduler.add_job(
        run_deadline_tracker,
        trigger=IntervalTrigger(minutes=config.deadline_check_interval),
        args=[config, notion, agent],
        id="deadline_tracker",
        name="Deadline Tracker",
        next_run_time=None,
    )

    # 3. Weekly digest — runs once a week
    scheduler.add_job(
        run_weekly_digest,
        trigger=CronTrigger(
            day_of_week=config.weekly_digest_day,
            hour=config.weekly_digest_hour,
        ),
        args=[config, notion, agent],
        id="weekly_digest",
        name="Weekly Digest",
    )

    # 4. Stale archiver — runs daily
    scheduler.add_job(
        run_stale_archiver,
        trigger=IntervalTrigger(minutes=config.stale_archive_interval),
        args=[config, notion, agent],
        id="stale_archiver",
        name="Stale Archiver",
        next_run_time=None,
    )

    # Run all tasks once on startup
    logger.info("Running initial pass of all tasks...")
    for task_fn in [run_inbox_triage, run_deadline_tracker, run_stale_archiver]:
        try:
            task_fn(config, notion, agent)
        except Exception:
            logger.exception(f"Error during initial run of {task_fn.__name__}")

    # Graceful shutdown
    def shutdown(signum, frame):
        logger.info("Shutting down Notion Night Agent...")
        scheduler.shutdown(wait=False)
        notion.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("Scheduler started. Running 24/7. Press Ctrl+C to stop.")
    scheduler.start()


if __name__ == "__main__":
    main()
