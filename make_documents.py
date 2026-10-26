"""Make the sample documents of a config: one PDF per transaction that carries a receipt (doc_id), and one web page
per customer that lists their documents with links.

    python make_documents.py                       # the banking config -> documents_out/banking/
    python make_documents.py --config banking --out documents_out

The files are named like the links in the data: DOC8001.pdf (the transaction's doc_id) and documents-C004.html (the
customer's documents_page_url). Upload the whole folder to the Blob container (see the README, "Hosting the
documents"); the links in the config then work. The links are built from EXT_DATA_DOCS_URL and EXT_DATA_DOCS_SAS
in .env, so set those first, then (re)load the config.

Everything in the documents is fictional and says so. No extra library is needed: the PDFs are written directly.
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

import datasets

BANK = "Banca Esempio (fictional demo bank)"
FOOTER = "SAMPLE DOCUMENT - fictional data for demonstration only. Not a real bank document."


# ---------------------------------------------------------------- a tiny PDF writer
def _pdf_text(text: str) -> bytes:
    """Escape for a PDF string; the font uses WinAnsi, so euro signs and accents survive."""
    data = str(text).encode("cp1252", errors="replace")
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def make_pdf(title: str, subtitle: str, rows: list[tuple[str, str]], footer: str = FOOTER) -> bytes:
    """One A4 page: a coloured header band with the title, label/value rows, and a footer note."""
    ops: list[bytes] = []
    ops.append(b"0.23 0.51 0.96 rg 0 752 595 90 re f")                                     # header band
    ops.append(b"BT 1 1 1 rg /F2 22 Tf 48 804 Td (" + _pdf_text(title) + b") Tj ET")
    ops.append(b"BT 1 1 1 rg /F1 10 Tf 48 776 Td (" + _pdf_text(subtitle) + b") Tj ET")
    y = 700
    for label, value in rows:
        ops.append(b"BT 0.4 0.4 0.4 rg /F1 9 Tf 48 " + str(y).encode() + b" Td (" + _pdf_text(label.upper()) + b") Tj ET")
        ops.append(b"BT 0 0 0 rg /F2 12 Tf 48 " + str(y - 15).encode() + b" Td (" + _pdf_text(value) + b") Tj ET")
        ops.append(b"0.85 0.85 0.85 RG 0.5 w 48 " + str(y - 24).encode() + b" m 547 " + str(y - 24).encode() + b" l S")
        y -= 46
    ops.append(b"BT 0.5 0.5 0.5 rg /F1 8 Tf 48 36 Td (" + _pdf_text(footer) + b") Tj ET")
    stream = b"\n".join(ops)

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# ---------------------------------------------------------------- what goes into a document
def euro(amount, currency="EUR") -> str:
    return f"{currency} {abs(float(amount)):,.2f}"


def build_pdf(tx: dict, data: dict) -> bytes:
    """The receipt of one transaction, from the transaction's own fields."""
    customer = data["customers"].get(tx["customer_id"], {})
    account = data.get("accounts", {}).get(tx.get("account_id"), {})
    payee = tx.get("beneficiary_name") or "(not stated: see the description)"
    rows = [
        ("Document number", tx["doc_id"]),
        ("Reference", tx.get("reference", "")),
        ("Booking date", tx.get("booking_date", "")),
        ("Value date", tx.get("value_date", "")),
        ("Payment type", tx.get("payment_type", "")),
        ("Status", str(tx.get("status", "")).capitalize()),
        ("Customer", customer.get("name", "")),
        ("Account (IBAN)", account.get("iban", "")),
        ("Beneficiary", payee),
    ]
    if tx.get("beneficiary_iban"):
        rows.append(("Beneficiary IBAN", tx["beneficiary_iban"]))
    rows.append(("Amount", euro(tx.get("amount", 0), tx.get("currency", "EUR"))))
    rows.append(("Description (causale)", tx.get("causale", "")))
    return make_pdf(tx.get("doc_category", "Receipt"), BANK, rows[:14])


# ---------------------------------------------------------------- the documents page of one customer
def customer_page(customer_id: str, customer: dict, transactions: dict) -> str:
    groups: dict[str, list[dict]] = {}
    for tx in transactions.values():
        if tx.get("customer_id") == customer_id and tx.get("doc_id"):
            groups.setdefault(tx.get("doc_category", "Document"), []).append(tx)
    sections = ""
    for kind, items in sorted(groups.items()):
        rows = "".join(f'<li><a href="{html.escape(t["doc_url"], quote=True)}" target="_blank" rel="noopener">{html.escape(t.get("doc_title") or t["doc_id"])}</a>'
                       f'<span class="d">{html.escape(str(t.get("booking_date", "")))}</span></li>' for t in items)
        sections += f"<h2>{html.escape(kind)}s <span class=\"n\">{len(items)}</span></h2><ul>{rows}</ul>"
    name = html.escape(customer.get("name", customer_id))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Documents - {name}</title>
<style>
body{{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem;color:#1f2937;background:#fff}}
@media (prefers-color-scheme:dark){{body{{color:#e5e7eb;background:#0e1117}}a{{color:#7aa7ff}}li{{border-color:#2a2f3a}}}}
h1{{margin-bottom:.2rem}} .sub{{opacity:.65;margin-top:0}} h2{{margin-top:2rem;font-size:1.15rem}} .n{{background:#3b82f6;color:#fff;border-radius:999px;padding:0 .5rem;font-size:.8rem}}
ul{{list-style:none;padding:0}} li{{display:flex;justify-content:space-between;gap:1rem;padding:.55rem 0;border-bottom:1px solid #e5e7eb}} .d{{opacity:.6;white-space:nowrap}}
footer{{margin-top:3rem;font-size:.8rem;opacity:.6}}
</style></head><body>
<h1>Your documents</h1><p class="sub">{html.escape(BANK)} &middot; {name}</p>
{sections or '<p>No documents yet.</p>'}
<footer>{html.escape(FOOTER)}</footer>
</body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="banking")
    parser.add_argument("--out", default="documents_out")
    args = parser.parse_args()

    ds = datasets.get_dataset(args.config)
    data = datasets.resolve_placeholders(ds.data)
    transactions = {tid: tx for tid, tx in data.get("transactions", {}).items() if tx.get("doc_id")}
    if not transactions:
        print(f"The {args.config} config has no transactions with a doc_id.")
        return 1
    out = Path(args.out) / args.config
    out.mkdir(parents=True, exist_ok=True)
    for tx in transactions.values():
        (out / f"{tx['doc_id']}.pdf").write_bytes(build_pdf(tx, data))
    for cid, customer in data["customers"].items():          # a page for every customer, even without documents
        (out / f"documents-{cid}.html").write_text(customer_page(cid, customer, transactions), encoding="utf-8")
    print(f"Wrote {len(transactions)} PDFs and {len(data['customers'])} documents pages to {out}")
    print("Upload that folder to the Blob container, for example:")
    print(f'  az storage blob upload-batch --account-name <account> --destination documents --source "{out}" --overwrite')
    return 0


if __name__ == "__main__":
    sys.exit(main())
