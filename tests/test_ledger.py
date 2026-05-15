"""Tests for the load-bearing double-entry invariants and trial-balance math.

Run with `python -m unittest tests.test_ledger` from the project root.
"""

from __future__ import annotations

import unittest

from qbai.coa import seed_chart_of_accounts
from qbai.db import get_connection, init_db
from qbai.ledger import (
    JournalLine, approve_proposed, get_entry, post_journal_entry, void_entry,
)
from qbai.money import to_cents
from qbai.reports import trial_balance


def fresh_db():
    conn = get_connection(":memory:")
    init_db(conn)
    seed_chart_of_accounts(conn)
    return conn


class TestJournalLine(unittest.TestCase):
    def test_line_must_be_debit_xor_credit(self):
        with self.assertRaises(ValueError):
            JournalLine(account_code="1000", debit=100, credit=100)
        with self.assertRaises(ValueError):
            JournalLine(account_code="1000", debit=0, credit=0)
        with self.assertRaises(ValueError):
            JournalLine(account_code="1000", debit=-50, credit=0)

    def test_valid_lines(self):
        JournalLine(account_code="1000", debit=100)
        JournalLine(account_code="1000", credit=100)


class TestPostEntry(unittest.TestCase):
    def test_unbalanced_entry_rejected(self):
        conn = fresh_db()
        with self.assertRaises(ValueError):
            post_journal_entry(conn, lines=[
                JournalLine("1010", debit=10000),
                JournalLine("4000", credit=9000),
            ])

    def test_single_line_rejected(self):
        conn = fresh_db()
        with self.assertRaises(ValueError):
            post_journal_entry(conn, lines=[JournalLine("1010", debit=10000)])

    def test_unknown_account_rejected(self):
        conn = fresh_db()
        with self.assertRaises(LookupError):
            post_journal_entry(conn, lines=[
                JournalLine("9999", debit=100),
                JournalLine("4000", credit=100),
            ])

    def test_balanced_entry_posts_and_round_trips(self):
        conn = fresh_db()
        eid = post_journal_entry(
            conn,
            entry_date="2026-01-15",
            memo="Cash sale",
            lines=[
                JournalLine("1010", debit=to_cents("100.00"), description="Deposit"),
                JournalLine("4000", credit=to_cents("100.00"), description="Sale"),
            ],
        )
        entry = get_entry(conn, eid)
        assert entry is not None
        self.assertEqual(entry.status, "posted")
        self.assertEqual(entry.memo, "Cash sale")
        self.assertEqual(len(entry.lines), 2)
        self.assertEqual(sum(l["debit"] for l in entry.lines), 10000)
        self.assertEqual(sum(l["credit"] for l in entry.lines), 10000)


class TestProposedFlow(unittest.TestCase):
    def test_proposed_entries_not_in_trial_balance_until_approved(self):
        conn = fresh_db()
        eid = post_journal_entry(
            conn,
            lines=[
                JournalLine("1010", debit=5000),
                JournalLine("4000", credit=5000),
            ],
            status="proposed",
        )
        # Proposed entries are excluded from reports.
        rows = trial_balance(conn)
        self.assertEqual(rows, [])

        approve_proposed(conn, eid, approved_by="alice")
        rows = trial_balance(conn)
        self.assertEqual(sum(r.debit for r in rows), sum(r.credit for r in rows))
        self.assertEqual(sum(r.debit for r in rows), 5000)


class TestVoid(unittest.TestCase):
    def test_voiding_returns_trial_balance_to_zero(self):
        conn = fresh_db()
        eid = post_journal_entry(
            conn,
            lines=[
                JournalLine("1010", debit=12345),
                JournalLine("4000", credit=12345),
            ],
        )
        void_entry(conn, eid, reason="duplicate", voided_by="alice")
        rows = trial_balance(conn)
        # Original + reversal net to zero, so trial balance should be empty.
        self.assertEqual(sum(r.debit for r in rows), sum(r.credit for r in rows))
        self.assertEqual(sum(r.debit for r in rows), 0)


class TestTrialBalanceBalances(unittest.TestCase):
    def test_many_entries_still_balance(self):
        conn = fresh_db()
        # Mix of cash sales, expenses, and a customer invoice posting.
        post_journal_entry(conn, lines=[
            JournalLine("1010", debit=to_cents("250.00")),
            JournalLine("4000", credit=to_cents("250.00")),
        ])
        post_journal_entry(conn, lines=[
            JournalLine("6200", debit=to_cents("42.99")),
            JournalLine("1010", credit=to_cents("42.99")),
        ])
        post_journal_entry(conn, lines=[
            JournalLine("1100", debit=to_cents("1000.00")),
            JournalLine("4100", credit=to_cents("1000.00")),
        ])
        post_journal_entry(conn, lines=[
            JournalLine("1010", debit=to_cents("500.00")),
            JournalLine("1100", credit=to_cents("500.00")),
        ])
        rows = trial_balance(conn)
        self.assertEqual(sum(r.debit for r in rows), sum(r.credit for r in rows))


if __name__ == "__main__":
    unittest.main()
