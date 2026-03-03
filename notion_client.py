"""Notion API client wrapper for common operations."""

import logging
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionClient:
    def __init__(self, api_key: str):
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(headers=self.headers, timeout=30)

    def close(self):
        self._client.close()

    # ── Database queries ──────────────────────────────────────────────

    def query_database(
        self,
        database_id: str,
        filter_obj: dict | None = None,
        sorts: list | None = None,
        page_size: int = 100,
    ) -> list[dict]:
        """Query a Notion database and return all pages (handles pagination)."""
        url = f"{NOTION_API_BASE}/databases/{database_id}/query"
        payload: dict = {"page_size": page_size}
        if filter_obj:
            payload["filter"] = filter_obj
        if sorts:
            payload["sorts"] = sorts

        results = []
        has_more = True
        while has_more:
            resp = self._client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            if has_more:
                payload["start_cursor"] = data["next_cursor"]
        return results

    def get_uncategorized_pages(self, database_id: str) -> list[dict]:
        """Get pages that have no category/status set (for inbox triage)."""
        return self.query_database(
            database_id,
            filter_obj={
                "or": [
                    {"property": "Category", "select": {"is_empty": True}},
                    {"property": "Status", "select": {"is_empty": True}},
                ]
            },
        )

    def get_tasks_with_deadlines(self, database_id: str) -> list[dict]:
        """Get tasks that have a due date set."""
        return self.query_database(
            database_id,
            filter_obj={
                "and": [
                    {"property": "Due Date", "date": {"is_not_empty": True}},
                    {
                        "property": "Status",
                        "select": {"does_not_equal": "Done"},
                    },
                ]
            },
            sorts=[{"property": "Due Date", "direction": "ascending"}],
        )

    def get_stale_pages(self, database_id: str, stale_days: int) -> list[dict]:
        """Get pages not edited in the last `stale_days` days."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=stale_days)
        ).isoformat()
        return self.query_database(
            database_id,
            filter_obj={
                "and": [
                    {"timestamp": "last_edited_time", "last_edited_time": {"before": cutoff}},
                    {"property": "Status", "select": {"does_not_equal": "Done"}},
                ]
            },
        )

    def get_recently_modified_pages(
        self, database_id: str, since_days: int = 7
    ) -> list[dict]:
        """Get pages modified in the last N days (for weekly digest)."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=since_days)
        ).isoformat()
        return self.query_database(
            database_id,
            filter_obj={
                "timestamp": "last_edited_time",
                "last_edited_time": {"on_or_after": cutoff},
            },
            sorts=[
                {"timestamp": "last_edited_time", "direction": "descending"}
            ],
        )

    # ── Page operations ───────────────────────────────────────────────

    def get_page(self, page_id: str) -> dict:
        resp = self._client.get(f"{NOTION_API_BASE}/pages/{page_id}")
        resp.raise_for_status()
        return resp.json()

    def get_page_content(self, page_id: str) -> str:
        """Retrieve all block children of a page and return as plain text."""
        url = f"{NOTION_API_BASE}/blocks/{page_id}/children"
        blocks = []
        has_more = True
        params: dict = {"page_size": 100}
        while has_more:
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            blocks.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            if has_more:
                params["start_cursor"] = data["next_cursor"]
        return self._blocks_to_text(blocks)

    def update_page_properties(self, page_id: str, properties: dict) -> dict:
        """Update properties on a Notion page."""
        resp = self._client.patch(
            f"{NOTION_API_BASE}/pages/{page_id}",
            json={"properties": properties},
        )
        resp.raise_for_status()
        return resp.json()

    def archive_page(self, page_id: str) -> dict:
        resp = self._client.patch(
            f"{NOTION_API_BASE}/pages/{page_id}",
            json={"archived": True},
        )
        resp.raise_for_status()
        return resp.json()

    def create_page(
        self, parent_database_id: str, properties: dict, children: list | None = None
    ) -> dict:
        """Create a new page inside a database."""
        payload: dict = {
            "parent": {"database_id": parent_database_id},
            "properties": properties,
        }
        if children:
            payload["children"] = children
        resp = self._client.post(f"{NOTION_API_BASE}/pages", json=payload)
        resp.raise_for_status()
        return resp.json()

    def append_blocks(self, page_id: str, children: list[dict]) -> dict:
        """Append block children to a page."""
        resp = self._client.patch(
            f"{NOTION_API_BASE}/blocks/{page_id}/children",
            json={"children": children},
        )
        resp.raise_for_status()
        return resp.json()

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def get_page_title(page: dict) -> str:
        props = page.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                title_parts = prop.get("title", [])
                return "".join(t.get("plain_text", "") for t in title_parts)
        return "Untitled"

    @staticmethod
    def _blocks_to_text(blocks: list[dict]) -> str:
        lines = []
        for block in blocks:
            btype = block.get("type", "")
            block_data = block.get(btype, {})
            rich_text = block_data.get("rich_text", [])
            text = "".join(rt.get("plain_text", "") for rt in rich_text)
            if text:
                lines.append(text)
        return "\n".join(lines)

    @staticmethod
    def make_rich_text(text: str) -> list[dict]:
        return [{"type": "text", "text": {"content": text}}]

    @staticmethod
    def make_paragraph_block(text: str) -> dict:
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": NotionClient.make_rich_text(text),
            },
        }

    @staticmethod
    def make_heading_block(text: str, level: int = 2) -> dict:
        heading_type = f"heading_{min(max(level, 1), 3)}"
        return {
            "object": "block",
            "type": heading_type,
            heading_type: {
                "rich_text": NotionClient.make_rich_text(text),
            },
        }
