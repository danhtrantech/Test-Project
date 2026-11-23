"""Tests for document ingestion, heuristic extraction, and the proposal flow.

These avoid the Claude API by using the heuristic extractor directly, so they
run offline and are deterministic.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qbai import documents, extract, ingest
from qbai.coa import list_accounts, seed_chart_of_accounts
from qbai.db import get_connection, init_db
from qbai.ledger import approve_proposed, get_entry, list_entries
from qbai.money import to_cents
from qbai.reports import trial_balance


SAMPLE_RECEIPT = """\
Starbucks Coffee
1234 Main Street
Seattle, WA

Date: 05/15/2026
Order #: 44218

Latte              4.50
Croissant          3.25

Subtotal           7.75
Tax                0.70
Total              8.45

Paid: credit card
Thank you for your visit!
"""

SAMPLE_BILL = """\
Office Depot
Invoice #OD-9912
Bill To: My Company

Date: 06/01/2026
Due Date: 07/01/2026

Office paper (10 reams)       25.00
Pens (box of 24)              12.00

Subtotal                      37.00
Sales Tax                      2.96
Total Due                     39.96
"""

SAMPLE_CUSTOMER_INVOICE = """\
Invoice to: Beta Corp
Invoice Number: INV-0042
Date: 2026-07-15

Consulting services, July 2026    1500.00

Subtotal                          1500.00
Total                             1500.00
"""


def fresh_db(tmp: Path):
    db_path = tmp / "qbai.db"
    conn = get_connection(db_path)
    init_db(conn)
    seed_chart_of_accounts(conn)
    return conn, db_path


class TestHeuristicExtractor(unittest.TestCase):
    def test_extracts_receipt_totals(self):
        res = extract.extract_with_heuristics(SAMPLE_RECEIPT, chart_of_accounts=[])
        d = res.data
        self.assertEqual(d["total_cents"], 845)
        self.assertEqual(d["tax_cents"], 70)
        self.assertEqual(d["subtotal_cents"], 775)
        self.assertEqual(d["date"], "2026-05-15")
        self.assertIn("Starbucks", d["vendor_or_customer_name"])
        self.assertEqual(d["doc_type"], "receipt")
        self.assertEqual(d["line_items"][0]["suggested_account_code"], "6300")

    def test_extracts_bill_totals(self):
        res = extract.extract_with_heuristics(SAMPLE_BILL, chart_of_accounts=[])
        d = res.data
        self.assertEqual(d["total_cents"], 3996)
        self.assertEqual(d["tax_cents"], 296)
        self.assertEqual(d["subtotal_cents"], 3700)
        self.assertEqual(d["date"], "2026-06-01")
        self.assertIn("Office Depot", d["vendor_or_customer_name"])

    def test_classifies_customer_invoice(self):
        res = extract.extract_with_heuristics(SAMPLE_CUSTOMER_INVOICE, chart_of_accounts=[])
        self.assertEqual(res.data["doc_type"], "customer_invoice")
        self.assertEqual(res.data["total_cents"], 150000)


class TestIngestAndPropose(unittest.TestCase):
    def test_receipt_flow_full_cycle(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            conn, db_path = fresh_db(tmp)
            receipt = tmp / "starbucks.txt"
            receipt.write_text(SAMPLE_RECEIPT)

            doc_id = documents.ingest_file(conn, receipt, db_path=db_path)
            doc = documents.get_document(conn, doc_id)
            self.assertEqual(doc.extraction_status, "extracted")
            self.assertIn("Starbucks", doc.extracted_text)

            coa = [{"code": a.code, "name": a.name, "type": a.type}
                   for a in list_accounts(conn)]
            result = extract.extract_with_heuristics(
                doc.extracted_text, chart_of_accounts=coa,
            )
            documents.set_extracted_data(
                conn, doc.id, result.data, extractor="heuristic",
                doc_type=result.data["doc_type"],
            )

            res = ingest.propose_from_document(conn, doc.id)
            self.assertEqual(res.kind, "je")
            # Proposed JEs are excluded from trial balance until approved
            self.assertEqual(sum(r.debit for r in trial_balance(conn)), 0)

            # The proposed entry exists
            proposed = list_entries(conn, status="proposed")
            self.assertEqual(len(proposed), 1)
            self.assertEqual(proposed[0]["id"], res.target_id)

            # Approve and verify it moves to posted + balances
            approve_proposed(conn, res.target_id, approved_by="tester")
            entry = get_entry(conn, res.target_id)
            self.assertEqual(entry.status, "posted")
            # Trial balance balances
            rows = trial_balance(conn)
            self.assertEqual(sum(r.debit for r in rows), sum(r.credit for r in rows))
            self.assertEqual(sum(r.debit for r in rows), 845)

    def test_bill_flow_creates_draft_bill(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            conn, db_path = fresh_db(tmp)
            bill_file = tmp / "od_bill.txt"
            bill_file.write_text(SAMPLE_BILL)

            doc_id = documents.ingest_file(conn, bill_file, db_path=db_path,
                                           doc_type="bill")
            doc = documents.get_document(conn, doc_id)
            coa = [{"code": a.code, "name": a.name, "type": a.type}
                   for a in list_accounts(conn)]
            result = extract.extract_with_heuristics(
                doc.extracted_text, chart_of_accounts=coa, doc_type_hint="bill",
            )
            documents.set_extracted_data(conn, doc.id, result.data,
                                         extractor="heuristic", doc_type="bill")

            res = ingest.propose_from_document(conn, doc.id)
            self.assertEqual(res.kind, "bill")
            # Vendor party auto-created
            self.assertIsNotNone(res.party_id)
            # Bill is in draft state until user finalizes
            from qbai import ap
            bill = ap.get_bill(conn, res.target_id)
            self.assertEqual(bill.status, "draft")
            self.assertEqual(len(bill.lines), 1)

    def test_deduplication_by_sha256(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            conn, db_path = fresh_db(tmp)
            r = tmp / "r.txt"
            r.write_text(SAMPLE_RECEIPT)
            id1 = documents.ingest_file(conn, r, db_path=db_path)
            id2 = documents.ingest_file(conn, r, db_path=db_path)
            # Different document rows, same stored file
            d1 = documents.get_document(conn, id1)
            d2 = documents.get_document(conn, id2)
            self.assertEqual(d1.sha256, d2.sha256)
            self.assertEqual(d1.stored_path, d2.stored_path)

    def test_image_is_stored_as_pending_for_vision_extraction(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            conn, db_path = fresh_db(tmp)
            img = tmp / "receipt.png"
            img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            doc_id = documents.ingest_file(conn, img, db_path=db_path)
            doc = documents.get_document(conn, doc_id)
            # Images are left pending; Claude Vision handles them in a later step.
            self.assertEqual(doc.extraction_status, "pending")
            self.assertEqual(doc.mime_type, "image/png")

    def test_truly_unsupported_file_type_is_marked_failed(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            conn, db_path = fresh_db(tmp)
            weird = tmp / "thing.xyz"
            weird.write_bytes(b"gibberish")
            doc_id = documents.ingest_file(conn, weird, db_path=db_path)
            doc = documents.get_document(conn, doc_id)
            self.assertEqual(doc.extraction_status, "failed")
            self.assertIn("unsupported", (doc.extraction_error or "").lower())

    def test_reject_proposed_removes_entry(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            conn, db_path = fresh_db(tmp)
            r = tmp / "r.txt"
            r.write_text(SAMPLE_RECEIPT)
            doc_id = documents.ingest_file(conn, r, db_path=db_path)
            doc = documents.get_document(conn, doc_id)
            coa = [{"code": a.code, "name": a.name, "type": a.type}
                   for a in list_accounts(conn)]
            result = extract.extract_with_heuristics(
                doc.extracted_text, chart_of_accounts=coa,
            )
            documents.set_extracted_data(conn, doc.id, result.data,
                                         extractor="heuristic",
                                         doc_type=result.data["doc_type"])
            res = ingest.propose_from_document(conn, doc.id)
            # Simulate CLI reject
            conn.execute("UPDATE documents SET proposed_je_id = NULL WHERE proposed_je_id = ?",
                         (res.target_id,))
            conn.execute("DELETE FROM journal_lines WHERE journal_entry_id = ?",
                         (res.target_id,))
            conn.execute("DELETE FROM journal_entries WHERE id = ?", (res.target_id,))
            conn.commit()
            self.assertEqual(list_entries(conn, status="proposed"), [])
            # Books still balanced (and still zero)
            self.assertEqual(sum(r.debit for r in trial_balance(conn)), 0)


if __name__ == "__main__":
    unittest.main()
