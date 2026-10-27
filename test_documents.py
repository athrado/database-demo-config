"""Tests for the transactions (receipts, documents) of the banking config, make_documents.py and the {{docs:...}} links.

    python test_documents.py
"""
import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path

import datasets
import make_documents as md

# The tests must not depend on anyone's private .env: an empty value (not a missing one) keeps .env from filling it in.
os.environ["EXT_DATA_DOCS_URL"] = ""
os.environ["EXT_DATA_DOCS_SAS"] = ""

TODAY = date(2026, 10, 26)
PAYMENT_TYPES = {"SEPA transfer", "CBILL", "F24", "card", "direct debit"}
COLUMNS = {"customer_id", "account_id", "reference", "booking_date", "value_date", "payment_type", "beneficiary_name",
           "causale", "amount", "currency", "status", "doc_id", "doc_category", "doc_title", "doc_url"}


def banking(today=TODAY):
    ds = datasets.get_dataset("banking")
    return ds.data, datasets.resolve_placeholders(ds.data, today)


def day(text: str) -> date:
    return datetime.strptime(text, "%d %B %Y").date()


def digits(text: str) -> str:
    return "".join(c for c in text if c.isdigit())


def check_pdf(data: bytes) -> str:
    """Structural check of a PDF written by make_pdf: header, every object where the xref says, trailer. Returns its text."""
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    start = int(re.search(rb"startxref\n(\d+)\n%%EOF", data).group(1))
    assert data[start:start + 4] == b"xref"
    lines = data[start:].split(b"\n")
    count = int(lines[1].split()[1])
    entries = lines[2:2 + count]
    for number, entry in enumerate(entries[1:], start=1):                       # entry 0 is the free-list head
        offset = int(entry.split()[0])
        assert data[offset:offset + len(f"{number} 0 obj".encode())] == f"{number} 0 obj".encode(), number
    stream = re.search(rb"stream\n(.*)\nendstream", data, re.S).group(1)
    return " ".join(m.decode("cp1252") for m in re.findall(rb"\((.*?)(?<!\\)\) Tj", stream))


# ---------------------------------------------------------------- the data
def test_transactions_have_the_required_columns_and_one_receipt_each():
    raw, data = banking()
    assert "transfers" not in data and "documents" not in data                 # one collection only
    tx = data["transactions"]
    assert 15 <= len(tx) <= 30 and len({t["customer_id"] for t in tx.values()}) in (3, 4)
    doc_ids = set()
    for tid, t in tx.items():
        assert COLUMNS <= set(t), (tid, COLUMNS - set(t))
        assert t["payment_type"] in PAYMENT_TYPES, tid
        assert t["reference"].startswith("CRO") == (t["payment_type"] == "SEPA transfer"), tid
        assert t["reference"].startswith(("CRO", "TRN")), tid
        assert day(t["booking_date"]) and day(t["value_date"])
        assert t["doc_id"] not in doc_ids
        doc_ids.add(t["doc_id"])
        assert t["doc_url"].endswith(t["doc_id"] + ".pdf"), tid
        assert t["doc_title"] and t["doc_category"] and t["causale"]
    assert {t["payment_type"] for t in tx.values()} == PAYMENT_TYPES            # all five kinds are there
    assert any(t["booking_date"] != t["value_date"] for t in tx.values())       # booking and value date differ somewhere
    for tid, t in raw["transactions"].items():
        assert t["doc_url"] == "{{docs:%s.pdf}}" % t["doc_id"], tid             # the link comes from the placeholder


def test_every_customer_has_one_documents_page_url():
    raw, data = banking()
    for cid, c in raw["customers"].items():
        assert c["documents_page_url"] == "{{docs:documents-%s.html}}" % cid
    assert data["rules"]["R-DOCLINKS"]["max_links_in_chat"] == 5


def test_result_counts_for_the_search_scenarios():
    """1 result, 2 to 5 results, more than 5 results: the cases the assistant has to handle."""
    _, data = banking()
    tx = data["transactions"]
    mine = lambda cid: [t for t in tx.values() if t["customer_id"] == cid]
    last_month = lambda t: (day(t["booking_date"]).year, day(t["booking_date"]).month) == (2026, 9)
    by_iban = lambda cid, tail: [t for t in mine(cid) if digits(t.get("beneficiary_iban", "")).endswith(tail)]

    electrician = [t for t in by_iban("C004", "120001") if last_month(t)]
    assert len(electrician) == 3 and sum(-t["amount"] for t in electrician) == 1200.00           # 2 to 5 results
    assert len(by_iban("C004", "120001")) == 4                                                    # the 4th is today's, pending
    assert [t["status"] for t in by_iban("C004", "120001") if not last_month(t)] == ["pending"]
    assert sum(1 for t in mine("C004") if "Notar" in t["beneficiary_name"]) == 1                  # 1 result
    assert sum(1 for t in mine("C002") if "Bellini" in t["beneficiary_name"]) == 1                # 1 result
    renovation = [t for t in mine("C004") if digits(t.get("beneficiary_iban", ""))[-6:] in {"120004", "120005", "120006", "120007", "120008"}
                  and day(t["booking_date"]).year == 2025]
    assert len(renovation) == 7                                                                    # more than 5
    rent = [t for t in mine("C001") if t["causale"].lower().startswith("affitto")]
    assert len(rent) == 8                                                                          # more than 5
    assert sum(1 for t in tx.values() if (TODAY - day(t["booking_date"])).days > 183) >= 8       # older than 6 months


def test_messy_payee_names_are_there_on_purpose():
    _, data = banking()
    names = [t["beneficiary_name"] for t in data["transactions"].values()]
    for messy in ("G. Ferrari", "Ferari Giorgio", "VITALE MARCO PIASTRELLE", "M. Vitale", "Costa Ed. Srl", "Tinteggiature Rosi", "IMMOB. DUOMO SRL"):
        assert messy in names, messy
    nameless = [t for t in data["transactions"].values() if not t["beneficiary_name"]]
    assert len(nameless) >= 2 and all(t["causale"] for t in nameless)                              # the name is only in the causale
    assert any("Serramenti Torino" in t["causale"] for t in nameless)
    # the same payee keeps the same IBAN, however it is spelled: that is how a messy name can still be resolved
    ferrari = {t["beneficiary_iban"] for t in data["transactions"].values() if "errari" in t["beneficiary_name"] or "Ferrari" in t["beneficiary_name"]}
    assert len(ferrari) == 1


def test_dates_follow_the_day_the_dataset_is_loaded():
    raw, _ = banking()
    jan = datasets.resolve_placeholders(raw, date(2027, 1, 15))["transactions"]
    assert {t["booking_date"] for t in jan.values() if "Ferrari" in t["beneficiary_name"] or "Ferari" in t["beneficiary_name"]} >= {"3 December 2026", "15 December 2026", "28 December 2026"}
    renovation_years = {day(t["booking_date"]).year for t in jan.values() if t["customer_id"] == "C004" and "Vitale" in t["beneficiary_name"].title()}
    assert renovation_years == {2026}                                                              # "last year" moved on


def test_password_reset_scenario_data():
    """Password reset by phone: masked identity data, who is blocked, the interview log and the backend history agree with each other."""
    _, data = banking()
    customers, checks, requests, rules = data["customers"], data["identity_checks"], data["password_requests"], data["rules"]
    months = {"January": "A", "February": "B", "March": "C", "April": "D", "May": "E", "June": "H", "July": "L", "August": "M", "September": "P", "October": "R",
              "November": "S", "December": "T"}
    for cid, c in customers.items():
        card, tax = c["identity"]["id_card_masked"], c["identity"]["tax_code_masked"]
        assert len(card) == 9 and "*" in card and len(tax) == 16 and "*" in tax, cid                      # masked: the full numbers are not in the data
        assert not re.fullmatch(r"[A-Z]{2}\d{5}[A-Z]{2}", card) and not re.fullmatch(r"[A-Z0-9]{16}", tax), cid
        dob = c["date_of_birth"].split()                                                                  # the visible part of the tax code fits the person
        assert tax[6:8] == dob[2][2:] and tax[8] == months[dob[1]], (cid, tax, c["date_of_birth"])
        assert c["online_banking"]["password_status"] in ("active", "blocked") and "*" in c["online_banking"]["username_masked"]
        assert (c["online_banking"]["password_status"] == "blocked") == ("blocked_since" in c["online_banking"]), cid
    assert [cid for cid, c in customers.items() if c["online_banking"]["password_status"] == "blocked"] == ["C002", "C003", "C004"]

    for iid, ic in checks.items():                                                                        # the interview log
        assert ic["customer_id"] in customers and set(ic["questions_asked"]) == {"id_card", "account_last3", "tax_code"}, iid
        assert ic["result"] == ("passed" if not ic["failed_fields"] else "failed"), iid
        assert all("*" in v for v in ic["answers_given_masked"].values()), iid                           # answers are logged masked too
    todays = [ic for ic in checks.values() if ic["customer_id"] == "C003" and ic["started"] == datasets.resolve_placeholders("{{today}}", TODAY)]
    assert [ic["attempt"] for ic in todays] == [1, 2, 3] and all(ic["result"] == "failed" for ic in todays)    # C003: locked out today
    assert rules["R-IDENTITY"]["max_attempts_per_day"] == 3

    for rid, r in requests.items():                                                                       # the backend history
        assert r["customer_id"] in customers and r["result"] in ("OK", "KO"), rid
        assert (r["result"] == "KO") == bool(r["reason_code"]), rid
        if r["reason_code"]:
            assert r["reason_code"] in rules["R-PWRESET"]["ko_reasons"], rid                              # every KO has a sentence for the customer
        if r["identity_check"]:
            assert checks[r["identity_check"]]["customer_id"] == r["customer_id"] and checks[r["identity_check"]]["result"] == "passed", rid
    assert [(r["customer_id"], r["result"]) for r in requests.values() if r["requested"] == datasets.resolve_placeholders("{{today}}", TODAY)] == [("C004", "OK")]
    assert rules["R-PWRESET"]["case_3a"]["queue"] and rules["R-PWRESET"]["case_3b"]["logged_in"] == "password_requests"
    ibans = {a["customer_id"]: a["iban"] for a in data["accounts"].values() if a["type"] == "Current account"}
    assert set(ibans) == set(customers) and all(re.fullmatch(r"\d{3}", i.replace(" ", "")[-3:]) for i in ibans.values())    # "last three digits of the account"


# ---------------------------------------------------------------- the files
def test_pdf_is_well_formed_and_escaped():
    pdf = md.make_pdf("Transfer (receipt)", "Bank \\ test", [("Beneficiary", "Caffè Münch (EUR 5)"), ("Amount", "€ 12.00")])
    text = check_pdf(pdf)
    assert "Transfer \\(receipt\\)" in text and "Bank \\\\ test" in text and "Caffè Münch" in text
    assert b"\x80 12.00" in pdf                                             # the euro sign in WinAnsi


def test_every_receipt_becomes_a_valid_pdf_with_its_own_data():
    _, data = banking()
    for tid, t in data["transactions"].items():
        text = check_pdf(md.build_pdf(t, data))
        for expected in (t["doc_id"], t["reference"], t["booking_date"], t["value_date"], t["payment_type"]):
            assert expected in text, (tid, expected)
    first = data["transactions"]["TX1001"]
    text = check_pdf(md.build_pdf(first, data))
    for expected in ("Transfer receipt", "Alessandro Conti", "Giorgio Ferrari", "EUR 400.00", "Impianto elettrico cucina", "IT"):
        assert expected in text, expected
    nameless = next(t for t in data["transactions"].values() if not t["beneficiary_name"])
    assert "not stated" in check_pdf(md.build_pdf(nameless, data))


def test_customer_page_lists_only_their_documents_with_links_and_escapes():
    txs = {"A": {"customer_id": "C9", "doc_id": "D1", "doc_category": "Transfer receipt", "doc_title": "Receipt <b>x</b>", "booking_date": "1 May 2026",
                 "doc_url": "https://x.example/D1.pdf?sv=1&sig=a%3D"},
           "B": {"customer_id": "C9", "doc_id": "D2", "doc_category": "Card receipt", "doc_title": "Card", "booking_date": "2 May 2026", "doc_url": "https://x.example/D2.pdf"},
           "C": {"customer_id": "C1", "doc_id": "D3", "doc_category": "Transfer receipt", "doc_title": "Other customer", "booking_date": "", "doc_url": "https://x.example/D3.pdf"}}
    page = md.customer_page("C9", {"name": "Test <User>"}, txs)
    assert page.count("<li>") == 2 and "D3.pdf" not in page
    assert 'href="https://x.example/D1.pdf?sv=1&amp;sig=a%3D"' in page            # the & in the SAS is escaped for HTML
    assert "&lt;b&gt;x&lt;/b&gt;" in page and "<b>x</b>" not in page and "&lt;User&gt;" in page
    assert md.FOOTER in page
    assert "No documents yet" in md.customer_page("C5", {"name": "Nobody"}, txs)


def test_generator_writes_a_pdf_per_receipt_and_a_page_per_customer():
    _, data = banking()
    with tempfile.TemporaryDirectory() as tmp:
        saved = os.environ.get("EXT_DATA_DOCS_URL")
        os.environ["EXT_DATA_DOCS_URL"] = "https://acct.blob.core.windows.net/documents"
        try:
            import sys
            argv, sys.argv = sys.argv, ["make_documents.py", "--out", tmp]
            try:
                assert md.main() == 0
            finally:
                sys.argv = argv
        finally:
            if saved is None:
                os.environ.pop("EXT_DATA_DOCS_URL", None)
            else:
                os.environ["EXT_DATA_DOCS_URL"] = saved
        files = {p.name for p in Path(tmp, "banking").iterdir()}
        raw, _ = banking()
        assert {f"{t['doc_id']}.pdf" for t in raw["transactions"].values()} <= files
        assert {f"documents-{cid}.html" for cid in raw["customers"]} <= files                       # every customer, even without documents
        assert "https://acct.blob.core.windows.net/documents/DOC8001.pdf" in Path(tmp, "banking", "documents-C004.html").read_text(encoding="utf-8")


def test_docs_placeholder_uses_the_container_url_and_sas():
    saved = {k: os.environ.get(k) for k in ("EXT_DATA_DOCS_URL", "EXT_DATA_DOCS_SAS")}
    try:
        os.environ["EXT_DATA_DOCS_URL"] = "https://acct.blob.core.windows.net/documents/"
        os.environ["EXT_DATA_DOCS_SAS"] = "?sv=2025-01-01&sr=c&sp=r&sig=ab%3D"
        link = datasets.resolve_placeholders("{{docs:DOC8001.pdf}}")
        assert link == "https://acct.blob.core.windows.net/documents/DOC8001.pdf?sv=2025-01-01&sr=c&sp=r&sig=ab%3D"
        os.environ["EXT_DATA_DOCS_SAS"] = ""
        assert datasets.resolve_placeholders("{{docs:documents-C004.html}}") == "https://acct.blob.core.windows.net/documents/documents-C004.html"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
