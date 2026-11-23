"""Tests for P&L, Balance Sheet, and General Ledger.

The load-bearing invariant verified across all tests is:
    Total Assets == Total Liabilities + Total Equity (incl. current-period NI).
"""

from __future__ import annotations

import unittest

from qbai import ap, ar, expenses, payments
from qbai.coa import seed_chart_of_accounts
from qbai.db import get_connection, init_db
from qbai.ledger import JournalLine, post_journal_entry
from qbai.money import to_cents
from qbai.parties import add_party
from qbai.reports import (
    balance_sheet, general_ledger, profit_and_loss, trial_balance,
)


def fresh_db():
    conn = get_connection(":memory:")
    init_db(conn)
    seed_chart_of_accounts(conn)
    return conn


class TestProfitAndLoss(unittest.TestCase):
    def test_empty_pnl(self):
        conn = fresh_db()
        pnl = profit_and_loss(conn)
        self.assertEqual(pnl.total_income, 0)
        self.assertEqual(pnl.total_expenses, 0)
        self.assertEqual(pnl.net_income, 0)

    def test_pnl_from_cash_sale_and_expense(self):
        conn = fresh_db()
        post_journal_entry(conn, entry_date="2026-03-15", lines=[
            JournalLine("1010", debit=to_cents("1000")),
            JournalLine("4000", credit=to_cents("1000")),
        ])
        post_journal_entry(conn, entry_date="2026-03-20", lines=[
            JournalLine("6200", debit=to_cents("50")),
            JournalLine("1010", credit=to_cents("50")),
        ])
        pnl = profit_and_loss(conn)
        self.assertEqual(pnl.total_income, to_cents("1000"))
        self.assertEqual(pnl.total_expenses, to_cents("50"))
        self.assertEqual(pnl.net_income, to_cents("950"))

    def test_pnl_period_filter(self):
        conn = fresh_db()
        post_journal_entry(conn, entry_date="2026-01-15", lines=[
            JournalLine("1010", debit=to_cents("500")),
            JournalLine("4000", credit=to_cents("500")),
        ])
        post_journal_entry(conn, entry_date="2026-06-15", lines=[
            JournalLine("1010", debit=to_cents("200")),
            JournalLine("4000", credit=to_cents("200")),
        ])
        # Only Q1
        pnl_q1 = profit_and_loss(conn, date_from="2026-01-01", date_to="2026-03-31")
        self.assertEqual(pnl_q1.total_income, to_cents("500"))
        # Full year
        pnl_full = profit_and_loss(conn, date_from="2026-01-01", date_to="2026-12-31")
        self.assertEqual(pnl_full.total_income, to_cents("700"))

    def test_proposed_entries_do_not_affect_pnl(self):
        conn = fresh_db()
        post_journal_entry(conn, lines=[
            JournalLine("1010", debit=to_cents("100")),
            JournalLine("4000", credit=to_cents("100")),
        ], status="proposed")
        pnl = profit_and_loss(conn)
        self.assertEqual(pnl.total_income, 0)


class TestBalanceSheet(unittest.TestCase):
    def test_empty_balance_sheet(self):
        conn = fresh_db()
        bs = balance_sheet(conn)
        self.assertEqual(bs.assets.total, 0)
        self.assertEqual(bs.total_liabilities_and_equity, 0)

    def test_a_equals_l_plus_e_after_mixed_activity(self):
        conn = fresh_db()
        # Owner puts in $10,000
        post_journal_entry(conn, entry_date="2026-01-01", lines=[
            JournalLine("1010", debit=to_cents("10000")),
            JournalLine("3000", credit=to_cents("10000")),
        ])
        # Buy inventory on credit for $1,000
        post_journal_entry(conn, entry_date="2026-01-05", lines=[
            JournalLine("1200", debit=to_cents("1000")),
            JournalLine("2000", credit=to_cents("1000")),
        ])
        # Cash sale $500 (no COGS for simplicity)
        post_journal_entry(conn, entry_date="2026-01-10", lines=[
            JournalLine("1010", debit=to_cents("500")),
            JournalLine("4000", credit=to_cents("500")),
        ])
        # Expense $50
        post_journal_entry(conn, entry_date="2026-01-12", lines=[
            JournalLine("6200", debit=to_cents("50")),
            JournalLine("1010", credit=to_cents("50")),
        ])
        bs = balance_sheet(conn)
        self.assertEqual(
            bs.assets.total, bs.total_liabilities_and_equity,
            f"A={bs.assets.total}, L+E={bs.total_liabilities_and_equity}",
        )
        # And the net income component is correct
        self.assertEqual(bs.net_income_to_date, to_cents("450"))

    def test_bs_balances_through_ap_ar_cycle(self):
        conn = fresh_db()
        customer = add_party(conn, "Cust", "customer")
        vendor = add_party(conn, "Vend", "vendor")
        # Invoice $1,000
        i = ar.create_invoice(conn, party_id=customer.id, invoice_date="2026-02-01")
        ar.add_invoice_line(conn, i, description="x", quantity=1,
                            unit_price_cents=to_cents("1000"), income_account_code="4000")
        ar.finalize_invoice(conn, i)
        # Bill $200
        b = ap.create_bill(conn, party_id=vendor.id, bill_date="2026-02-02")
        ap.add_bill_line(conn, b, description="y", quantity=1,
                         unit_price_cents=to_cents("200"), expense_account_code="6200")
        ap.finalize_bill(conn, b)
        # Receive $600 partial
        payments.receive_payment(conn, party_id=customer.id,
                                 amount_cents=to_cents("600"),
                                 allocations=[payments.Allocation(
                                     invoice_id=i, amount_cents=to_cents("600"))])
        # Pay vendor $200 full
        payments.send_payment(conn, party_id=vendor.id, amount_cents=to_cents("200"),
                              allocations=[payments.Allocation(
                                  bill_id=b, amount_cents=to_cents("200"))])
        # Standalone expense $75
        expenses.record_expense(conn, expense_account_code="6300",
                                amount_cents=to_cents("75"),
                                vendor_name="Lunch Place", memo="meeting")
        bs = balance_sheet(conn)
        self.assertEqual(bs.assets.total, bs.total_liabilities_and_equity)
        # Sanity: NI = 1000 - 200 - 75 = 725
        self.assertEqual(bs.net_income_to_date, to_cents("725"))


class TestGeneralLedger(unittest.TestCase):
    def test_running_balance_on_cash_account(self):
        conn = fresh_db()
        post_journal_entry(conn, entry_date="2026-01-01", lines=[
            JournalLine("1010", debit=to_cents("1000")),
            JournalLine("3000", credit=to_cents("1000")),
        ])
        post_journal_entry(conn, entry_date="2026-01-05", lines=[
            JournalLine("6200", debit=to_cents("50")),
            JournalLine("1010", credit=to_cents("50")),
        ])
        post_journal_entry(conn, entry_date="2026-01-10", lines=[
            JournalLine("1010", debit=to_cents("200")),
            JournalLine("4000", credit=to_cents("200")),
        ])
        gl = general_ledger(conn, account_code="1010")
        self.assertEqual(len(gl), 1)
        account = gl[0]
        self.assertEqual(account.code, "1010")
        self.assertEqual(len(account.entries), 3)
        # Running balance: 1000 -> 950 -> 1150
        balances = [e.balance for e in account.entries]
        self.assertEqual(balances,
                         [to_cents("1000"), to_cents("950"), to_cents("1150")])
        self.assertEqual(account.closing_balance, to_cents("1150"))
        self.assertEqual(account.opening_balance, 0)

    def test_opening_balance_pre_dates(self):
        conn = fresh_db()
        post_journal_entry(conn, entry_date="2026-01-01", lines=[
            JournalLine("1010", debit=to_cents("500")),
            JournalLine("3000", credit=to_cents("500")),
        ])
        post_journal_entry(conn, entry_date="2026-02-15", lines=[
            JournalLine("1010", debit=to_cents("100")),
            JournalLine("4000", credit=to_cents("100")),
        ])
        gl = general_ledger(conn, account_code="1010",
                            date_from="2026-02-01", date_to="2026-02-28")
        account = gl[0]
        # Opening carries the Jan deposit
        self.assertEqual(account.opening_balance, to_cents("500"))
        self.assertEqual(len(account.entries), 1)
        self.assertEqual(account.closing_balance, to_cents("600"))

    def test_sign_for_liability_account(self):
        conn = fresh_db()
        # Credit $500 to A/P — liability grows in credit direction, so GL shows +500.
        post_journal_entry(conn, entry_date="2026-01-01", lines=[
            JournalLine("6200", debit=to_cents("500")),
            JournalLine("2000", credit=to_cents("500")),
        ])
        gl = general_ledger(conn, account_code="2000")
        self.assertEqual(gl[0].closing_balance, to_cents("500"))


if __name__ == "__main__":
    unittest.main()
