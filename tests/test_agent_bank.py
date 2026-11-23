"""Tests for the agent's tool-dispatch layer and bank-statement CSV ingest.

The Claude API loop itself is not exercised here — we test `dispatch_tool`
directly because it's a pure function of (conn, name, args), which is what
needs to be correct for the real agent to work.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qbai import agent, bank
from qbai.coa import seed_chart_of_accounts
from qbai.db import get_connection, init_db
from qbai.ledger import list_entries
from qbai.money import to_cents
from qbai.reports import trial_balance


def fresh_db():
    conn = get_connection(":memory:")
    init_db(conn)
    seed_chart_of_accounts(conn)
    return conn


class TestAgentDispatch(unittest.TestCase):
    def test_list_accounts_tool(self):
        conn = fresh_db()
        res = agent.dispatch_tool(conn, "list_accounts", {"type": "income"})
        self.assertTrue(res.ok)
        self.assertTrue(all(a["type"] == "income" for a in res.data))
        self.assertTrue(any(a["code"] == "4000" for a in res.data))

    def test_unknown_tool_returns_error(self):
        conn = fresh_db()
        res = agent.dispatch_tool(conn, "bogus_tool", {})
        self.assertFalse(res.ok)
        self.assertIn("unknown tool", res.error)

    def test_create_invoice_add_line_finalize_via_agent_tools(self):
        conn = fresh_db()
        # Make a customer
        r = agent.dispatch_tool(conn, "add_party",
                                {"name": "Acme Corp", "kind": "customer"})
        self.assertTrue(r.ok, r.error)
        r = agent.dispatch_tool(conn, "create_invoice",
                                {"customer_name": "Acme Corp",
                                 "invoice_date": "2026-05-01"})
        inv_id = r.data["invoice_id"]
        r = agent.dispatch_tool(conn, "add_invoice_line",
                                {"invoice_id": inv_id,
                                 "quantity": 10, "unit_price": 50,
                                 "income_account_code": "4100",
                                 "description": "Consulting"})
        self.assertTrue(r.ok, r.error)
        r = agent.dispatch_tool(conn, "finalize_invoice", {"invoice_id": inv_id})
        self.assertTrue(r.ok, r.error)
        # Books balance and invoice is open
        rows = trial_balance(conn)
        self.assertEqual(sum(x.debit for x in rows), sum(x.credit for x in rows))
        r = agent.dispatch_tool(conn, "get_invoice", {"invoice_id": inv_id})
        self.assertEqual(r.data["status"], "open")
        self.assertEqual(r.data["total"], 500.0)

    def test_report_tool_returns_structured_pnl(self):
        conn = fresh_db()
        agent.dispatch_tool(conn, "add_party", {"name": "C", "kind": "customer"})
        agent.dispatch_tool(conn, "create_invoice", {"customer_name": "C"})
        agent.dispatch_tool(conn, "add_invoice_line",
                            {"invoice_id": 1, "quantity": 1, "unit_price": 1000,
                             "income_account_code": "4000"})
        agent.dispatch_tool(conn, "finalize_invoice", {"invoice_id": 1})
        agent.dispatch_tool(conn, "record_expense",
                            {"expense_account_code": "6300", "amount": 25,
                             "vendor_name": "Lunch", "memo": "with C"})
        r = agent.dispatch_tool(conn, "report", {"name": "pnl"})
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.data["total_income"], 1000)
        self.assertEqual(r.data["total_expenses"], 25)
        self.assertEqual(r.data["net_income"], 975)

    def test_post_manual_je_tool_rejects_unbalanced(self):
        conn = fresh_db()
        r = agent.dispatch_tool(conn, "post_manual_journal_entry", {
            "lines": [
                {"account_code": "1010", "debit": 100},
                {"account_code": "4000", "credit": 90},
            ],
        })
        self.assertFalse(r.ok)
        self.assertIn("balance", r.error.lower())


SAMPLE_BANK_CSV_AMOUNT = """\
Date,Description,Amount
2026-05-01,Starbucks #1234,-8.45
2026-05-02,Deposit from Acme Corp,1500.00
2026-05-05,Office Depot,-42.99
2026-05-10,AWS subscription,-150.00
2026-05-12,,0
"""

SAMPLE_BANK_CSV_DEBIT_CREDIT = """\
Posted Date,Payee,Debit,Credit
05/01/2026,Starbucks,8.45,
05/02/2026,Client Payment,,1500.00
05/05/2026,Office Depot,42.99,
"""


class TestBankCsv(unittest.TestCase):
    def test_amount_column_format(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            csv_path = tmp / "stmt.csv"
            csv_path.write_text(SAMPLE_BANK_CSV_AMOUNT)
            conn = fresh_db()
            res = bank.ingest_bank_csv(conn, csv_path, db_path=tmp / "qbai.db")
            # Four non-zero rows parsed, zero-amount row skipped on parse
            self.assertEqual(res.rows_parsed, 4)
            self.assertEqual(len(res.proposed_je_ids), 4)
            proposed = list_entries(conn, status="proposed")
            self.assertEqual(len(proposed), 4)
            # None of the proposed entries affect the trial balance yet
            self.assertEqual(sum(r.debit for r in trial_balance(conn)), 0)

    def test_debit_credit_column_format(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            csv_path = tmp / "stmt.csv"
            csv_path.write_text(SAMPLE_BANK_CSV_DEBIT_CREDIT)
            conn = fresh_db()
            res = bank.ingest_bank_csv(conn, csv_path, db_path=tmp / "qbai.db")
            self.assertEqual(res.rows_parsed, 3)
            self.assertEqual(len(res.proposed_je_ids), 3)

    def test_approve_all_proposed_balances_books(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            csv_path = tmp / "stmt.csv"
            csv_path.write_text(SAMPLE_BANK_CSV_AMOUNT)
            conn = fresh_db()
            res = bank.ingest_bank_csv(conn, csv_path, db_path=tmp / "qbai.db")
            from qbai.ledger import approve_proposed
            for je_id in res.proposed_je_ids:
                approve_proposed(conn, je_id, approved_by="tester")
            rows = trial_balance(conn)
            self.assertEqual(sum(r.debit for r in rows), sum(r.credit for r in rows))
            # Net cash change: -8.45 + 1500 - 42.99 - 150 = 1298.56
            cash_row = next(r for r in rows if r.code == "1010")
            self.assertEqual(cash_row.debit, to_cents("1298.56"))

    def test_malformed_csv_rejected_with_row_context(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            csv_path = tmp / "bad.csv"
            csv_path.write_text("Date,Description,Amount\n"
                                "2026-05-01,Coffee,xyz\n")
            conn = fresh_db()
            with self.assertRaises(ValueError) as ctx:
                bank.ingest_bank_csv(conn, csv_path, db_path=tmp / "qbai.db")
            self.assertIn("row 2", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
