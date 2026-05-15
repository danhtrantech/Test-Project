"""Money is stored as integer cents everywhere in qbai.

Float arithmetic does not balance to the penny on long ledgers, so all amounts
that touch the ledger flow through to_cents / from_cents. CLI and reports
present whole dollars and cents; the database stores integers.
"""

from decimal import Decimal, ROUND_HALF_UP


def to_cents(value) -> int:
    """Parse a user-supplied amount (str / int / float / Decimal) into cents."""
    if isinstance(value, int):
        return value * 100 if not isinstance(value, bool) else 0
    d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(d * 100)


def from_cents(cents: int) -> Decimal:
    return (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))


def format_money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${from_cents(abs(cents)):,.2f}"
