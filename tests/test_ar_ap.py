"""Tests for A/R, A/P, payments, and standalone expenses.

These test the business-level flows (invoice → finalize → payment, etc.) and
verify that the underlying ledger remains in balance after every operation.
"""

from __future__ import annotations

import unittest

from qbai import ap, ar, expenses, payments
from qbai.coa import seed_chart_of_accounts
from qbai.db import get_connection, init_db
from qbai.money import to_cents
from qbai.parties import add_party
from qbai.reports import trial_balance


def fresh_db():
    conn = get_connection(":memory:")
    init_db(conn)
    seed_chart_of_accounts(conn)
    return conn


def tb_balanced(conn) -> bool:
    rows = trial_balance(conn)
    return sum(r.debit for r in rows) == sum(r.credit for r in rows)


class TestPartyBasics(unittest.TestCase):
    def test_add_and_find(self):
        conn = fresh_db()
        c = add_party(conn, "Acme Corp", "customer")
        self.assertEqual(c.kind, "customer")
        v = add_party(conn, "Office Depot", "vendor")
        self.assertEqual(v.kind, "vendor")
        # Same name as customer + vendor is fine (different kind)
        add_party(conn, "Acme Corp", "vendor")

    def test_invalid_kind(self):
        conn = fresh_db()
        with self.assertRaises(ValueError):
            add_party(conn, "X", "supplier")  # type: ignore[arg-type]


class TestInvoiceFlow(unittest.TestCase):
    def test_full_invoice_lifecycle(self):
        conn = fresh_db()
        customer = add_party(conn, "Acme Corp", "customer")

        iid = ar.create_invoice(conn, party_id=customer.id, invoice_date="2026-05-01",
                                memo="Consulting")
        ar.add_invoice_line(conn, iid, description="20 hours consulting",
                            quantity=20, unit_price_cents=to_cents("150.00"),
                            income_account_code="4100")
        ar.add_invoice_line(conn, iid, description="Travel reimbursement",
                            quantity=1, unit_price_cents=to_cents("500.00"),
                            income_account_code="4900")

        inv = ar.get_invoice(conn, iid)
        self.assertEqual(inv.status, "draft")
        self.assertEqual(inv.subtotal_cents, to_cents("3500.00"))

        je_id = ar.finalize_invoice(conn, iid)
        inv = ar.get_invoice(conn, iid)
        self.assertEqual(inv.status, "open")
        self.assertEqual(inv.total_cents, to_cents("3500.00"))
        self.assertEqual(inv.posted_je_id, je_id)
        self.assertTrue(tb_balanced(conn))

    def test_invoice_with_tax(self):
        conn = fresh_db()
        customer = add_party(conn, "Beta LLC", "customer")
        iid = ar.create_invoice(conn, party_id=customer.id)
        ar.add_invoice_line(conn, iid, description="Widget",
                            quantity=10, unit_price_cents=to_cents("10.00"),
                            income_account_code="4000")
        ar.finalize_invoice(conn, iid, tax_cents=to_cents("7.50"))
        inv = ar.get_invoice(conn, iid)
        self.assertEqual(inv.tax_cents, 750)
        self.assertEqual(inv.total_cents, to_cents("107.50"))
        self.assertTrue(tb_balanced(conn))

    def test_cannot_finalize_empty_invoice(self):
        conn = fresh_db()
        c = add_party(conn, "X", "customer")
        iid = ar.create_invoice(conn, party_id=c.id)
        with self.assertRaises(ValueError):
            ar.finalize_invoice(conn, iid)

    def test_cannot_add_lines_after_finalize(self):
        conn = fresh_db()
        c = add_party(conn, "X", "customer")
        iid = ar.create_invoice(conn, party_id=c.id)
        ar.add_invoice_line(conn, iid, description="x", quantity=1,
                            unit_price_cents=1000, income_account_code="4000")
        ar.finalize_invoice(conn, iid)
        with self.assertRaises(ValueError):
            ar.add_invoice_line(conn, iid, description="y", quantity=1,
                                unit_price_cents=500, income_account_code="4000")

    def test_void_invoice_keeps_books_balanced(self):
        conn = fresh_db()
        c = add_party(conn, "X", "customer")
        iid = ar.create_invoice(conn, party_id=c.id)
        ar.add_invoice_line(conn, iid, description="x", quantity=1,
                            unit_price_cents=to_cents("100.00"),
                            income_account_code="4000")
        ar.finalize_invoice(conn, iid)
        ar.void_invoice(conn, iid, reason="customer cancelled")
        inv = ar.get_invoice(conn, iid)
        self.assertEqual(inv.status, "voided")
        self.assertIsNotNone(inv.voided_je_id)
        # Original JE + reversal net to zero
        rows = trial_balance(conn)
        self.assertEqual(sum(r.debit for r in rows), 0)
        self.assertTrue(tb_balanced(conn))


class TestBillFlow(unittest.TestCase):
    def test_bill_lifecycle(self):
        conn = fresh_db()
        v = add_party(conn, "Office Depot", "vendor")
        bid = ap.create_bill(conn, party_id=v.id, bill_date="2026-05-02")
        ap.add_bill_line(conn, bid, description="Paper",
                         quantity=2, unit_price_cents=to_cents("25.00"),
                         expense_account_code="6200")
        ap.finalize_bill(conn, bid)
        bill = ap.get_bill(conn, bid)
        self.assertEqual(bill.status, "open")
        self.assertEqual(bill.total_cents, to_cents("50.00"))
        self.assertTrue(tb_balanced(conn))


class TestReceivePayment(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.customer = add_party(self.conn, "Acme Corp", "customer")
        self.inv_id = ar.create_invoice(self.conn, party_id=self.customer.id,
                                        invoice_date="2026-05-01")
        ar.add_invoice_line(self.conn, self.inv_id, description="Service",
                            quantity=1, unit_price_cents=to_cents("1000.00"),
                            income_account_code="4100")
        ar.finalize_invoice(self.conn, self.inv_id)

    def test_full_payment_closes_invoice(self):
        pid = payments.receive_payment(
            self.conn, party_id=self.customer.id,
            amount_cents=to_cents("1000.00"),
            allocations=[payments.Allocation(invoice_id=self.inv_id,
                                              amount_cents=to_cents("1000.00"))],
            payment_date="2026-05-15",
        )
        inv = ar.get_invoice(self.conn, self.inv_id)
        self.assertEqual(inv.status, "paid")
        self.assertEqual(inv.amount_paid_cents, to_cents("1000.00"))
        self.assertTrue(tb_balanced(self.conn))
        pmt = payments.get_payment(self.conn, pid)
        self.assertEqual(pmt["kind"], "receive")

    def test_partial_payment_keeps_invoice_open(self):
        payments.receive_payment(
            self.conn, party_id=self.customer.id,
            amount_cents=to_cents("400.00"),
            allocations=[payments.Allocation(invoice_id=self.inv_id,
                                              amount_cents=to_cents("400.00"))],
        )
        inv = ar.get_invoice(self.conn, self.inv_id)
        self.assertEqual(inv.status, "open")
        self.assertEqual(inv.amount_paid_cents, to_cents("400.00"))
        self.assertTrue(tb_balanced(self.conn))

    def test_overpayment_rejected(self):
        with self.assertRaises(ValueError):
            payments.receive_payment(
                self.conn, party_id=self.customer.id,
                amount_cents=to_cents("1500.00"),
                allocations=[payments.Allocation(invoice_id=self.inv_id,
                                                  amount_cents=to_cents("1500.00"))],
            )

    def test_allocations_must_sum_to_amount(self):
        with self.assertRaises(ValueError):
            payments.receive_payment(
                self.conn, party_id=self.customer.id,
                amount_cents=to_cents("500.00"),
                allocations=[payments.Allocation(invoice_id=self.inv_id,
                                                  amount_cents=to_cents("400.00"))],
            )

    def test_cross_party_invoice_rejected(self):
        other = add_party(self.conn, "Other Co", "customer")
        with self.assertRaises(ValueError):
            payments.receive_payment(
                self.conn, party_id=other.id,
                amount_cents=to_cents("100.00"),
                allocations=[payments.Allocation(invoice_id=self.inv_id,
                                                  amount_cents=to_cents("100.00"))],
            )


class TestSendPayment(unittest.TestCase):
    def test_send_payment_closes_bill(self):
        conn = fresh_db()
        v = add_party(conn, "Office Depot", "vendor")
        bid = ap.create_bill(conn, party_id=v.id, bill_date="2026-05-02")
        ap.add_bill_line(conn, bid, description="Paper",
                         quantity=1, unit_price_cents=to_cents("42.99"),
                         expense_account_code="6200")
        ap.finalize_bill(conn, bid)
        payments.send_payment(
            conn, party_id=v.id, amount_cents=to_cents("42.99"),
            allocations=[payments.Allocation(bill_id=bid,
                                              amount_cents=to_cents("42.99"))],
        )
        bill = ap.get_bill(conn, bid)
        self.assertEqual(bill.status, "paid")
        self.assertTrue(tb_balanced(conn))


class TestStandaloneExpense(unittest.TestCase):
    def test_record_expense(self):
        conn = fresh_db()
        je_id = expenses.record_expense(
            conn, expense_account_code="6300",
            amount_cents=to_cents("38.50"),
            vendor_name="Some Restaurant",
            memo="Client lunch",
        )
        self.assertIsInstance(je_id, int)
        self.assertTrue(tb_balanced(conn))
        # Expense should show $38.50 debit, cash $38.50 credit
        rows = {r.code: r for r in trial_balance(conn)}
        self.assertEqual(rows["6300"].debit, to_cents("38.50"))
        self.assertEqual(rows["1010"].credit, to_cents("38.50"))


if __name__ == "__main__":
    unittest.main()
