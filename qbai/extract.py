"""Structured extraction from document text.

Two extractors:

  extract_with_claude(text, ...)   - uses Anthropic SDK with prompt caching.
                                     Requires ANTHROPIC_API_KEY. Returns a dict.
  extract_with_heuristics(text)    - no network; regex-based; best-effort.
                                     Used as a fallback and for tests.

Both return the same shape (see EXTRACTION_SCHEMA below). The agent then
feeds this into qbai.ingest to propose a bill / invoice / JE.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path


EXTRACTION_SCHEMA = {
    "doc_type": "one of: receipt | bill | customer_invoice | bank_statement | other",
    "vendor_or_customer_name": "name of vendor (for receipts/bills) or customer (for invoices)",
    "date": "YYYY-MM-DD",
    "reference_number": "invoice/receipt number if present, else null",
    "line_items": [
        {
            "description": "what was bought/sold",
            "quantity": "number, default 1",
            "unit_price_cents": "integer cents",
            "amount_cents": "integer cents = quantity * unit_price_cents",
            "suggested_account_code": "one of the account codes from the provided chart of accounts",
        }
    ],
    "subtotal_cents": "integer cents",
    "tax_cents": "integer cents, default 0",
    "total_cents": "integer cents = subtotal + tax",
    "payment_method": "cash | credit_card | check | ach | debit | unknown",
    "confidence": "0.0..1.0 overall confidence",
    "notes": "any ambiguities, warnings, or clarifications",
}


@dataclass
class ExtractionResult:
    data: dict
    extractor: str   # "claude" | "heuristic"
    model: str | None = None


# ---------- Claude extractor -----------------------------------------------

def extract_with_claude(
    text: str, *, chart_of_accounts: list[dict],
    doc_type_hint: str | None = None, model: str | None = None,
) -> ExtractionResult:
    """Call Claude to extract structured data. Raises RuntimeError if the
    SDK isn't installed or ANTHROPIC_API_KEY isn't set."""
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError("anthropic SDK not installed (pip install anthropic)") from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    model = model or os.environ.get("QBAI_CLAUDE_MODEL", "claude-sonnet-5-5")
    client = anthropic.Anthropic()

    coa_text = "\n".join(f"  {a['code']}  {a['type']:<10} {a['name']}" for a in chart_of_accounts)
    system = (
        "You are a bookkeeping assistant. Extract structured data from the document "
        "the user provides. Pick a suggested_account_code from the chart of accounts "
        "below that best fits each line item.\n\n"
        f"Chart of accounts:\n{coa_text}\n\n"
        "Return ONLY a single JSON object with this shape (no prose, no markdown fence):\n"
        f"{json.dumps(EXTRACTION_SCHEMA, indent=2)}\n\n"
        "All money fields are INTEGER CENTS. If something is unclear, still produce a best "
        "guess and lower the 'confidence' value; explain ambiguities in 'notes'."
    )
    user_prefix = f"Document type hint: {doc_type_hint or 'auto-detect'}\n\nDocument content:\n"

    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        system=[
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": user_prefix + text}],
    )
    raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    data = _parse_json_response(raw)
    return ExtractionResult(data=data, extractor="claude", model=model)


def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        # strip possible fenced code block
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # try to find the outermost JSON object
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


# ---------- Heuristic extractor (no network) -------------------------------

MONEY_RE = re.compile(r"\$?\s*(\d{1,3}(?:[,\s]\d{3})*|\d+)(?:\.(\d{2}))")
DATE_PATTERNS = [
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), lambda m: f"{m[1]}-{m[2]}-{m[3]}"),
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"),
     lambda m: f"{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}"),
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2})"),
     lambda m: f"20{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}"),
    (re.compile(
        r"(?i)(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+(\d{1,2}),?\s+(\d{4})"
     ),
     lambda m: _parse_month_name_date(m)),
]


def _parse_month_name_date(m: re.Match) -> str:
    try:
        d = datetime.strptime(f"{m[1][:3]} {m[2]} {m[3]}", "%b %d %Y")
        return d.date().isoformat()
    except ValueError:
        return m[0]


# Keyword → account code hints. Not exhaustive; used only by the heuristic.
EXPENSE_KEYWORDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\b(restaurant|cafe|coffee|starbucks|diner|lunch|dinner|meal)\b"), "6300"),
    (re.compile(r"(?i)\b(hotel|airbnb|flight|airline|uber|lyft|taxi|rental car|gas|fuel)\b"),
     "6400"),
    (re.compile(r"(?i)\b(paper|staples|office depot|supplies|pen|notebook|toner|ink)\b"),
     "6200"),
    (re.compile(r"(?i)\b(aws|google cloud|azure|github|slack|zoom|saas|subscription)\b"),
     "6850"),
    (re.compile(r"(?i)\b(rent|lease)\b"), "6000"),
    (re.compile(r"(?i)\b(electric|water|gas company|internet|phone|utility)\b"), "6100"),
    (re.compile(r"(?i)\b(bank fee|wire fee|service charge|overdraft)\b"), "6900"),
    (re.compile(r"(?i)\b(insurance|premium)\b"), "6800"),
    (re.compile(r"(?i)\b(attorney|lawyer|cpa|accountant|consulting)\b"), "6500"),
]


def _dollars_to_cents(whole: str, frac: str | None) -> int:
    whole = whole.replace(",", "").replace(" ", "")
    d = Decimal(f"{whole}.{frac or '00'}")
    return int((d * 100).to_integral_value())


def _find_all_money(text: str) -> list[int]:
    return [_dollars_to_cents(m[1], m[2]) for m in MONEY_RE.finditer(text)]


def _find_total(text: str) -> int | None:
    # Prefer a line containing "total" (but not "subtotal") near a money amount.
    for line in text.splitlines():
        low = line.lower()
        if "subtotal" in low:
            continue
        if any(tok in low for tok in ("grand total", "total due", "amount due", "total")):
            m = MONEY_RE.search(line)
            if m:
                return _dollars_to_cents(m[1], m[2])
    amounts = _find_all_money(text)
    return max(amounts) if amounts else None


def _find_subtotal(text: str) -> int | None:
    for line in text.splitlines():
        if "subtotal" in line.lower():
            m = MONEY_RE.search(line)
            if m:
                return _dollars_to_cents(m[1], m[2])
    return None


def _find_tax(text: str) -> int | None:
    for line in text.splitlines():
        low = line.lower()
        if "tax" in low and "subtotal" not in low and "pre-tax" not in low:
            m = MONEY_RE.search(line)
            if m:
                return _dollars_to_cents(m[1], m[2])
    return None


def _find_date(text: str) -> str | None:
    for pat, fn in DATE_PATTERNS:
        m = pat.search(text)
        if m:
            return fn(m)
    return None


def _guess_vendor(text: str) -> str | None:
    for line in text.splitlines():
        s = line.strip()
        if s and not MONEY_RE.search(s) and not any(c.isdigit() for c in s[:4]):
            # First non-empty, non-numeric-leading line is likely the business name.
            return s[:80]
    return None


def _guess_account(text: str) -> str:
    for pat, code in EXPENSE_KEYWORDS:
        if pat.search(text):
            return code
    return "6999"  # Other Expense


def _guess_doc_type(text: str, hint: str | None) -> str:
    if hint:
        return hint
    low = text.lower()
    if "invoice to" in low or "bill to" in low:
        # These can appear on both. Default to customer invoice if "Invoice #" shows up.
        if "invoice #" in low or re.search(r"invoice\s+number", low):
            return "customer_invoice"
    if "receipt" in low or "thank you" in low:
        return "receipt"
    if "bill" in low or "amount due" in low or "due date" in low:
        return "bill"
    return "other"


def extract_with_heuristics(
    text: str, *, chart_of_accounts: list[dict] | None = None,
    doc_type_hint: str | None = None,
) -> ExtractionResult:
    """Best-effort regex extraction. No network, deterministic — used by tests."""
    total = _find_total(text) or 0
    subtotal = _find_subtotal(text)
    tax = _find_tax(text) or 0
    if subtotal is None:
        subtotal = max(total - tax, 0)

    vendor = _guess_vendor(text)
    date = _find_date(text)
    doc_type = _guess_doc_type(text, doc_type_hint)
    acct = _guess_account(text)

    line_desc = (vendor or "Expense") + (" — see document" if not vendor else " purchase")
    data = {
        "doc_type": doc_type,
        "vendor_or_customer_name": vendor,
        "date": date,
        "reference_number": None,
        "line_items": [
            {
                "description": line_desc,
                "quantity": 1,
                "unit_price_cents": subtotal,
                "amount_cents": subtotal,
                "suggested_account_code": acct,
            }
        ],
        "subtotal_cents": subtotal,
        "tax_cents": tax,
        "total_cents": total or (subtotal + tax),
        "payment_method": "unknown",
        "confidence": 0.5,
        "notes": "heuristic extractor; verify before posting",
    }
    return ExtractionResult(data=data, extractor="heuristic")


def extract_image_with_claude(
    image_path: str | Path, *, chart_of_accounts: list[dict],
    doc_type_hint: str | None = None, model: str | None = None,
) -> ExtractionResult:
    """Send a receipt/invoice image to Claude Vision. Supports PNG / JPG."""
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError("anthropic SDK not installed (pip install anthropic)") from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    path = Path(image_path)
    mime, _ = mimetypes.guess_type(path.name)
    if not mime or not mime.startswith("image/"):
        raise ValueError(f"Not an image: {path.name} (mime={mime})")

    data_b64 = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    model = model or os.environ.get("QBAI_CLAUDE_MODEL", "claude-sonnet-5-5")
    client = anthropic.Anthropic()

    coa_text = "\n".join(f"  {a['code']}  {a['type']:<10} {a['name']}"
                         for a in chart_of_accounts)
    system = (
        "You are a bookkeeping assistant. Read the image of a receipt / bill / "
        "invoice the user provides and extract structured data. Pick a "
        "suggested_account_code from the chart of accounts below that best fits "
        "each line item.\n\n"
        f"Chart of accounts:\n{coa_text}\n\n"
        "Return ONLY a single JSON object with this shape (no prose, no markdown):\n"
        f"{json.dumps(EXTRACTION_SCHEMA, indent=2)}\n\n"
        "All money fields are INTEGER CENTS."
    )
    resp = client.messages.create(
        model=model, max_tokens=2048,
        system=[{"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": mime, "data": data_b64}},
                {"type": "text",
                 "text": f"Document type hint: {doc_type_hint or 'auto-detect'}.\n"
                         "Extract the fields as JSON."},
            ],
        }],
    )
    raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    data = _parse_json_response(raw)
    return ExtractionResult(data=data, extractor="claude-vision", model=model)




def extract(
    text: str, *, chart_of_accounts: list[dict],
    doc_type_hint: str | None = None, force_heuristic: bool = False,
    model: str | None = None,
) -> ExtractionResult:
    """Try Claude; fall back to heuristic if unavailable. Set force_heuristic=True
    in tests."""
    if force_heuristic:
        return extract_with_heuristics(
            text, chart_of_accounts=chart_of_accounts, doc_type_hint=doc_type_hint,
        )
    try:
        return extract_with_claude(
            text, chart_of_accounts=chart_of_accounts,
            doc_type_hint=doc_type_hint, model=model,
        )
    except RuntimeError:
        return extract_with_heuristics(
            text, chart_of_accounts=chart_of_accounts, doc_type_hint=doc_type_hint,
        )
