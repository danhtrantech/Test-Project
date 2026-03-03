"""Core Claude AI agent that powers decision-making for Notion automation."""

import json
import logging

import anthropic

logger = logging.getLogger(__name__)


class ClaudeAgent:
    """Thin wrapper around the Anthropic API for structured Notion decisions."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def _ask(self, system: str, user: str, max_tokens: int = 1024) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text

    def _ask_json(self, system: str, user: str, max_tokens: int = 1024) -> dict:
        raw = self._ask(system, user, max_tokens)
        # Extract JSON from possible markdown fences
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0]
        return json.loads(text)

    # ── Inbox triage ──────────────────────────────────────────────────

    def categorize_page(
        self, title: str, content: str, categories: list[str]
    ) -> dict:
        """Return {"category": "...", "priority": "high|medium|low", "summary": "..."}"""
        system = (
            "You are a Notion workspace assistant. Categorize the following page "
            "into exactly one of the provided categories. Also assign a priority "
            "(high, medium, low) and write a one-sentence summary. "
            "Respond with ONLY a JSON object: "
            '{"category": "...", "priority": "...", "summary": "..."}'
        )
        user = (
            f"Categories: {', '.join(categories)}\n\n"
            f"Page title: {title}\n\n"
            f"Page content:\n{content[:3000]}"
        )
        return self._ask_json(system, user)

    # ── Deadline analysis ─────────────────────────────────────────────

    def analyze_deadlines(self, tasks: list[dict]) -> dict:
        """Analyze a batch of tasks and return prioritization advice.

        Returns {"overdue": [...], "urgent": [...], "suggestions": "..."}
        """
        system = (
            "You are a productivity assistant. Analyze these tasks with deadlines. "
            "Identify overdue items, urgent items (due within 48h), and provide "
            "brief reprioritization suggestions. "
            "Respond with ONLY a JSON object: "
            '{"overdue": [{"title": "...", "due": "..."}], '
            '"urgent": [{"title": "...", "due": "..."}], '
            '"suggestions": "..."}'
        )
        user = f"Tasks:\n{json.dumps(tasks, indent=2, default=str)}"
        return self._ask_json(system, user, max_tokens=2048)

    # ── Weekly digest ─────────────────────────────────────────────────

    def generate_weekly_digest(self, activity: list[dict]) -> str:
        """Generate a human-readable weekly digest from page activity."""
        system = (
            "You are a Notion workspace assistant. Write a concise, well-formatted "
            "weekly digest summarizing the user's Notion activity. Group by theme, "
            "highlight key accomplishments, and note anything that may need follow-up. "
            "Use markdown formatting suitable for a Notion page."
        )
        user = (
            "Here is this week's Notion activity (recently modified pages):\n\n"
            + json.dumps(activity, indent=2, default=str)
        )
        return self._ask(system, user, max_tokens=2048)

    # ── Stale page analysis ───────────────────────────────────────────

    def evaluate_stale_pages(self, pages: list[dict]) -> list[dict]:
        """Decide which stale pages to archive vs keep.

        Returns [{"page_id": "...", "title": "...", "action": "archive|keep", "reason": "..."}]
        """
        system = (
            "You are a Notion workspace assistant. Evaluate these stale pages "
            "(not edited recently) and decide which should be archived and which "
            "should be kept. Pages with ongoing relevance or reference value should "
            "be kept. Abandoned drafts, outdated notes, etc. should be archived. "
            "Respond with ONLY a JSON array of objects: "
            '[{"page_id": "...", "title": "...", "action": "archive|keep", "reason": "..."}]'
        )
        user = f"Stale pages:\n{json.dumps(pages, indent=2, default=str)}"
        raw = self._ask(system, user, max_tokens=2048)
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0]
        return json.loads(text)
