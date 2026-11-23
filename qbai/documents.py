"""Document storage and text extraction.

Files are stored on disk under <storage_root>/<sha256-prefix>/<sha256>/<name>
so duplicates are deduplicated by content hash. The DB row holds metadata,
the extracted text, and (once extracted) the structured JSON.

Supported formats for text extraction:
  - .txt / .csv / .md       read as UTF-8
  - .pdf                    pypdf (optional; graceful failure if not installed)
  - .png / .jpg / .jpeg     no local OCR; Claude Vision handles these in
                            qbai.extract.extract_with_claude. The document is
                            still stored and its mime_type recorded.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .db import DEFAULT_DB_PATH, audit


UPLOADS_SUBDIR = "uploads"


@dataclass
class Document:
    id: int
    original_name: str
    stored_path: str
    sha256: str
    mime_type: str | None
    size_bytes: int
    doc_type: str | None
    extracted_text: str | None
    extracted_data: dict | None
    extraction_status: str
    extraction_error: str | None
    extractor: str | None
    proposed_je_id: int | None
    proposed_bill_id: int | None
    proposed_invoice_id: int | None
    uploaded_at: str


def _storage_root(db_path: str | Path | None) -> Path:
    base = Path(db_path).resolve().parent if db_path else DEFAULT_DB_PATH.parent
    root = base / UPLOADS_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest_file(
    conn: sqlite3.Connection, source_path: str | Path, *,
    doc_type: str | None = None, storage_root: Path | None = None,
    db_path: str | Path | None = None, actor: str = "system",
) -> int:
    """Copy a file into the uploads store, extract text, write a doc row.

    Returns the document id. The row's extraction_status is 'extracted' if
    text extraction worked, 'failed' if the file type is unreadable (the file
    is still stored).
    """
    src = Path(source_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(src)

    digest = _sha256_of(src)
    storage_root = storage_root or _storage_root(db_path)
    dest_dir = storage_root / digest[:2] / digest
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if not dest.exists():
        shutil.copy2(src, dest)

    size = src.stat().st_size
    mime, _ = mimetypes.guess_type(src.name)

    text, error = extract_text(dest, mime)
    is_image = (mime and mime.startswith("image/")) or dest.suffix.lower() in \
        (".png", ".jpg", ".jpeg")
    if text is not None:
        status = "extracted"
    elif error is None and is_image:
        status = "pending"   # Claude Vision will finish extraction
    else:
        status = "failed"

    with conn:
        cur = conn.execute(
            "INSERT INTO documents(original_name, stored_path, sha256, mime_type, "
            "size_bytes, doc_type, extracted_text, extraction_status, extraction_error, "
            "uploaded_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (src.name, str(dest), digest, mime, size, doc_type, text, status, error, actor),
        )
        doc_id = cur.lastrowid
        audit(conn, actor, "document_ingested", "document", doc_id,
              f"{src.name} ({mime or 'unknown'})")
    return doc_id


def extract_text(path: Path, mime: str | None) -> tuple[str | None, str | None]:
    """Return (text, error). text is None iff extraction failed.

    For images, returns (None, None) — the row is left pending and the Claude
    Vision extractor in qbai.extract.extract_image_with_claude will fill it.
    """
    name = path.name.lower()
    try:
        if name.endswith((".txt", ".csv", ".md")) or (mime and mime.startswith("text/")):
            return path.read_text(encoding="utf-8", errors="replace"), None
        if name.endswith(".pdf") or mime == "application/pdf":
            return _extract_pdf(path)
        if name.endswith((".png", ".jpg", ".jpeg")) or (mime and mime.startswith("image/")):
            return None, None   # pending; Claude Vision will handle it
        return None, f"unsupported file type: {mime or name}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _extract_pdf(path: Path) -> tuple[str | None, str | None]:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return None, "pypdf not installed (pip install pypdf)"
    try:
        reader = PdfReader(str(path))
        parts = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(parts).strip()
        if not text:
            return None, "PDF has no extractable text (scanned image?)"
        return text, None
    except Exception as e:
        return None, f"pypdf: {type(e).__name__}: {e}"


def set_extracted_data(
    conn: sqlite3.Connection, doc_id: int, data: dict, extractor: str,
    doc_type: str | None = None,
) -> None:
    with conn:
        conn.execute(
            "UPDATE documents SET extracted_data=?, extractor=?, doc_type=COALESCE(?, doc_type), "
            "extraction_status='extracted', extraction_error=NULL WHERE id=?",
            (json.dumps(data), extractor, doc_type, doc_id),
        )


def set_proposed_targets(
    conn: sqlite3.Connection, doc_id: int, *,
    je_id: int | None = None, bill_id: int | None = None, invoice_id: int | None = None,
) -> None:
    conn.execute(
        "UPDATE documents SET proposed_je_id=COALESCE(?, proposed_je_id), "
        "proposed_bill_id=COALESCE(?, proposed_bill_id), "
        "proposed_invoice_id=COALESCE(?, proposed_invoice_id) WHERE id=?",
        (je_id, bill_id, invoice_id, doc_id),
    )
    conn.commit()


def get_document(conn: sqlite3.Connection, doc_id: int) -> Document | None:
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        return None
    data = json.loads(row["extracted_data"]) if row["extracted_data"] else None
    return Document(
        id=row["id"], original_name=row["original_name"], stored_path=row["stored_path"],
        sha256=row["sha256"], mime_type=row["mime_type"], size_bytes=row["size_bytes"],
        doc_type=row["doc_type"], extracted_text=row["extracted_text"],
        extracted_data=data, extraction_status=row["extraction_status"],
        extraction_error=row["extraction_error"], extractor=row["extractor"],
        proposed_je_id=row["proposed_je_id"], proposed_bill_id=row["proposed_bill_id"],
        proposed_invoice_id=row["proposed_invoice_id"], uploaded_at=row["uploaded_at"],
    )


def list_documents(conn: sqlite3.Connection, *, status: str | None = None,
                   limit: int = 100) -> list[dict]:
    sql = ("SELECT id, original_name, mime_type, doc_type, extraction_status, "
           "proposed_je_id, proposed_bill_id, proposed_invoice_id, uploaded_at "
           "FROM documents")
    args: list = []
    if status:
        sql += " WHERE extraction_status = ?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]
