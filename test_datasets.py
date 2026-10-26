"""Tests for datasets.py and render.py.

    python test_datasets.py
"""
import json
import re
import tempfile
from datetime import date
from pathlib import Path

import os

os.environ["EXT_DATA_DOCS_URL"] = ""        # keep a private .env out of the tests
os.environ["EXT_DATA_DOCS_SAS"] = ""

import datasets
import render
from store import LocalStore, StoreError, check_table, MAX_JSON_CHARS


def test_every_dataset_is_valid():
    found = datasets.list_datasets()
    assert {d.name for d in found} >= {"starter", "insurance", "retail", "telco", "banking", "medical", "it"}
    tables = [d.default_table for d in found if d.default_table]
    assert len(tables) == len(set(tables)), "two configs share a default table"
    for d in found:
        assert datasets.validate_dataset(d) == [], (d.name, datasets.validate_dataset(d))
        resolved = json.dumps(datasets.resolve_placeholders(d.data))
        assert "{{" not in resolved, d.name
        for collection, records in datasets.resolve_placeholders(d.data).items():
            for rid, rec in records.items():
                assert len(json.dumps(rec, ensure_ascii=False)) < MAX_JSON_CHARS, (d.name, collection, rid)


def test_customers_work_with_the_example_flow():
    """The Teneo example flow reads customers/<id> and uses name, plan and preferences.contact_by."""
    for d in datasets.list_datasets():
        customers = d.data["customers"]
        assert "C001" in customers, d.name
        for cid, rec in customers.items():
            assert rec.get("name") and rec.get("plan"), (d.name, cid)
            assert rec["preferences"]["contact_by"] in ("email", "phone", "sms"), (d.name, cid)
            assert rec.get("email") and rec.get("phone"), (d.name, cid)


def test_placeholders():
    today = date(2026, 3, 31)
    r = lambda text: datasets.resolve_placeholders(text, today)
    assert r("{{today}}") == "31 March 2026"
    assert r("{{today-7d}}") == "24 March 2026"
    assert r("{{today+2w}}") == "14 April 2026"
    assert r("{{ today - 1m }}") == "28 February 2026"            # month end is clamped
    assert r("{{today+10m}}") == "31 January 2027"
    assert r("{{iso:today+3d}}") == "2026-04-03"
    assert r("{{month:today}}") == "March 2026" and r("{{month:today-2m}}") == "January 2026" and r("{{month:today-3m}}") == "December 2025"
    assert r({"a": ["x {{iso:today}} y", 5, None]}) == {"a": ["x 2026-03-31 y", 5, None]}
    assert r("{{lastmonth:3}}") == "3 February 2026" and r("{{lastmonth:31}}") == "28 February 2026" and r("{{iso:lastmonth:15}}") == "2026-02-15"
    assert r("{{lastyear:03-15}}") == "15 March 2025" and r("{{iso:lastyear:02-29}}") == "2025-02-28"
    assert datasets.resolve_placeholders("{{lastmonth:5}}", date(2026, 1, 10)) == "5 December 2025"
    assert r("{{monthago:3:12}}") == "12 December 2025" and r("{{iso:monthago:1:31}}") == "2026-02-28" and r("{{monthago:14:5}}") == "5 January 2025"
    assert datasets.resolve_placeholders("{{monthago:2:3}}", date(2026, 1, 10)) == "3 November 2025"
    assert r("{{mese:today-1m}}") == "febbraio" and r("{{mese:today}}") == "marzo" and r("Affitto {{mese:today-3m}}") == "Affitto dicembre"
    assert r("no placeholder") == "no placeholder"


def test_table_names():
    assert check_table("demoretail") == "demoretail"
    for bad in ("it", "demo_it", "demo-it", "1table", "has space", ""):
        try:
            check_table(bad)
        except StoreError:
            continue
        raise AssertionError(bad)


def test_load_and_reset():
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(Path(tmp) / "t.json")
        retail = datasets.get_dataset("retail")
        n = datasets.load_into(store, retail)
        assert n == retail.n_records
        assert store.collections() == sorted(retail.data)              # _meta is hidden
        marker = datasets.marker(store)
        assert marker["dataset"] == "retail" and marker["reset"] is False and marker["records"] == n

        # a demo changes things, and someone else keeps a collection of their own in the same table
        store.put("orders", "O1001", {"customer_id": "C001", "status": "cancelled"})
        store.put("orders", "OEXTRA", {"customer_id": "C001"})
        store.put("my_notes", "n1", {"text": "keep me"})
        datasets.load_into(store, retail)                               # plain load: overwrites, keeps extras
        assert store.get("orders", "OEXTRA") is not None
        assert store.get("orders", "O1001")["status"] == "delivered"

        datasets.load_into(store, retail, reset=True)                   # reset: dataset collections cleaned, others kept
        assert store.get("orders", "OEXTRA") is None
        assert store.get("my_notes", "n1") == {"text": "keep me"}
        assert datasets.marker(store)["reset"] is True
        assert "my_notes" in store.collections()

        # switching to another dataset in the same table
        datasets.load_into(store, datasets.get_dataset("telco"), reset=True)
        assert datasets.marker(store)["dataset"] == "telco"


def test_snapshot():
    base = datasets.get_dataset("insurance")
    with tempfile.TemporaryDirectory() as tmp:
        original = datasets.DATASET_DIR
        datasets.DATASET_DIR = Path(tmp)
        try:
            store = LocalStore(Path(tmp) / "t.json")
            datasets.load_into(store, base)
            store.put("customers", "C001", {**store.get("customers", "C001"), "name": "Sophie Renamed"})
            snap = datasets.save_snapshot(store, "ACME demo 1", "my tweaks", base, "demoacme")
            assert snap.name == "acme-demo-1" and snap.default_table == "demoacme"
            assert snap.data["customers"]["C001"]["name"] == "Sophie Renamed"
            assert "_meta" not in snap.data
            assert snap.view == base.view
            assert datasets.validate_dataset(snap) == []
            try:
                datasets.save_snapshot(store, "ACME demo 1")
                raise AssertionError("duplicate name must fail")
            except StoreError:
                pass
        finally:
            datasets.DATASET_DIR = original


def test_field_kinds_and_formatting():
    k = render.kind_of
    assert k("email", "a@b.co") == "email" and k("contact", "a@b.co") == "email"
    assert k("phone", "+44 7700") == "phone" and k("mobile number", "123") == "phone"
    assert k("date_of_birth", "03 March 1986") == "dob"
    assert k("home address", "12 Linden Gardens") == "address"
    assert k("status", "pending") == "status" and k("priority", "high") == "status"
    assert k("total", 49.9) == "money" and k("quantity", 3) == "plain"
    assert k("tracking_number", "TRK1") == "id" and k("policy_number", "2594") == "id"
    assert k("due_date", "2026-10-01") == "date" and k("photos_sent", "action required") == "state"
    assert k("items", [{"a": 1}]) == "table" and k("allergies", ["Nuts"]) == "chips" and k("preferences", {"a": 1}) == "dict"
    assert k("active", True) == "bool" and k("note", "") == "empty" and k("url", "https://x.example") == "url"
    assert render.money_text(1234.5, "EUR") == "€1,234.50" and render.money_text(-5, "GBP") == "-£5.00" and render.money_text(7, None) == "7.00"
    assert render.humanize("date_of_birth") == "Date of birth" and render.humanize("iban") == "IBAN" and render.humanize("data_allowance_gb") == "Data allowance gb"
    assert render.status_class("overdue") == "xd-bad" and render.status_class("In progress") == "xd-warn" and render.status_class("delivered") == "xd-good"
    assert render.status_class("something else") == "xd-neutral"
    assert render.collection_config("orders", None)["icon"] == "🛒" and render.collection_config("claims", None)["icon"] == "📋"
    assert render.collection_config("odd_things", None)["icon"] == "🗂️"
    assert render.collection_config("orders", {"collections": {"orders": {"icon": "X"}}})["icon"] == "X"


def test_rendering_every_dataset():
    for d in datasets.list_datasets():
        data = datasets.resolve_placeholders(d.data)
        for cid, rec in data["customers"].items():
            page = render.profile_html("customers", cid, rec, d.view) + render.related_html("customers", cid, data, d.view)
            assert "\n" not in page, "HTML must be one line for Streamlit's markdown"
            assert rec["name"].replace("&", "&amp;") in page or rec["name"] in page
            assert "xd-tile" in page                                           # email / phone tiles
        for name, records in data.items():
            for rid, rec in records.items():
                assert render.record_card(name, rid, rec, d.view).startswith("<div")


def test_rendering_details():
    data = datasets.resolve_placeholders(datasets.get_dataset("insurance").data)
    d = datasets.get_dataset("insurance")
    rec = data["customers"]["C001"]
    profile = render.profile_html("customers", "C001", rec, d.view)
    for expected in ("Sophie Berthier", "✉️", "📞", "🏠", "🎂", "sophie.berthier@hotmail.com", "mailto:", "Home &amp; Car", "Malo Berthier"):
        assert expected in profile, expected
    related = render.related_html("customers", "C001", data, d.view)
    assert related.index("Claims") < related.index("Requests") < related.index("Quotes")
    assert 'id="section-claims"' in related and 'id="section-quotes"' in related
    assert related.count("xd-count") == 3
    assert "xd-warn" in related                                                # "pending" / "info required" badges
    assert "CL710234" not in related                                           # other customers' claims are not shown

    # linked records are not repeated with their link field, and a customer without any has no sections
    assert "Customer id" not in related
    assert render.related_html("customers", "C004", {"customers": data["customers"], "claims": {}, "quotes": {}}, d.view) == ""

    # everything is escaped
    evil = {"name": "<script>alert(1)</script>", "note": "<img src=x onerror=y>", "email": "a@b.co\"><b>"}
    out = render.profile_html("customers", "X1", evil, {}) + render.record_card("orders", "O1", evil, {})
    assert "<script>" not in out and "<img" not in out and "<b>" not in out
    assert "&lt;script&gt;" in out

    # search text
    assert "lindel" not in render.record_text("C001", rec) and "linden" in render.record_text("C001", rec)


def test_create_and_delete_dataset():
    with tempfile.TemporaryDirectory() as tmp:
        original = datasets.DATASET_DIR
        datasets.DATASET_DIR = Path(tmp)
        try:
            ds = datasets.create_dataset("Pilot Test", "a pilot", "demopilot", {"customers": {"C001": {"name": "A"}}},
                                         view={"primary": "customers"}, icon="🧪")
            assert ds.name == "pilot-test" and ds.default_table == "demopilot" and ds.icon == "🧪" and ds.view == {"primary": "customers"}
            assert [d.name for d in datasets.list_datasets()] == ["pilot-test"]
            for bad in (lambda: datasets.create_dataset("Pilot Test", "", "demopilot", {}),                         # name taken
                        lambda: datasets.create_dataset("Other", "", "bad_table", {"c": {"1": {}}}),                 # table name
                        lambda: datasets.create_dataset("Other", "", "demoother", {"c": "not records"}),             # data shape
                        lambda: datasets.create_dataset("   ", "", "demoother", {"c": {"1": {}}})):                 # no name
                try:
                    bad()
                except StoreError:
                    continue
                raise AssertionError("expected StoreError")
            assert [d.name for d in datasets.list_datasets()] == ["pilot-test"]                                      # nothing half-written
            # only real dataset folders can be deleted
            Path(tmp, "other").mkdir()
            Path(tmp, "other", "x.txt").write_text("keep me")
            for name in ("other", "../outside", "missing"):
                try:
                    datasets.delete_dataset(name)
                except StoreError:
                    continue
                raise AssertionError(name)
            assert Path(tmp, "other", "x.txt").exists()
            datasets.delete_dataset("pilot-test")
            assert not Path(tmp, "pilot-test").exists() and datasets.list_datasets() == []
        finally:
            datasets.DATASET_DIR = original


def test_leftover_empty_folders_do_not_block_a_name_and_failures_leave_nothing_behind():
    with tempfile.TemporaryDirectory() as tmp:
        original = datasets.DATASET_DIR
        datasets.DATASET_DIR = Path(tmp)
        try:
            Path(tmp, "test").mkdir()                                              # what a half-finished delete can leave behind
            assert datasets.list_datasets() == []                                 # an empty folder is not a dataset
            ds = datasets.create_dataset("Test", "", "demotest", {"customers": {"C001": {"name": "A"}}})
            assert ds.name == "test" and [d.name for d in datasets.list_datasets()] == ["test"]
            # a failure while writing removes what was written
            real = Path.write_text
            calls = []

            def flaky(self, *args, **kwargs):
                calls.append(self.name)
                if self.name == "data.json" and "broken" in str(self):
                    raise OSError("disk full")
                return real(self, *args, **kwargs)
            Path.write_text = flaky
            try:
                datasets.create_dataset("Broken", "", "demobroken", {"c": {"1": {}}})
                raise AssertionError("expected StoreError")
            except StoreError as exc:
                assert "disk full" in str(exc)
            finally:
                Path.write_text = real
            assert not Path(tmp, "broken").exists(), "a failed create must not leave a folder"
            # a normal delete removes the folder too
            assert datasets.delete_dataset("test") is True and not Path(tmp, "test").exists()
            datasets.create_dataset("Test", "", "demotest", {"customers": {"C001": {"name": "B"}}})   # and the name is free again
        finally:
            datasets.DATASET_DIR = original


def test_a_folder_that_is_held_open_is_reported_and_tidied_up_later():
    """On Windows a folder survives its deletion while a program has it as its working folder (as File Explorer or
    OneDrive can). The config is gone anyway, the app is told, and the empty folder is removed once it is released."""
    import subprocess
    import sys
    import time
    with tempfile.TemporaryDirectory() as tmp:
        original = datasets.DATASET_DIR
        datasets.DATASET_DIR = Path(tmp)
        holder = None
        try:
            datasets.create_dataset("Held", "", "demoheld", {"customers": {"C1": {"name": "x"}}})
            holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(Path(tmp, "held")))
            time.sleep(0.5)
            gone = datasets.delete_dataset("held", wait=1.0)
            assert datasets.list_datasets() == [], "the config is gone either way"
            assert not Path(tmp, "held", "config.json").exists() and not Path(tmp, "held", "data.json").exists()
            if sys.platform == "win32":
                assert gone is False and Path(tmp, "held").exists(), "the held folder cannot be removed yet"
                assert datasets.tidy_empty_folders() == []                        # still held: left alone, no error
            holder.kill()
            holder.wait()
            holder = None
            time.sleep(0.3)
            datasets.tidy_empty_folders()                                          # released: now it goes
            assert not Path(tmp, "held").exists()
            Path(tmp, "keep").mkdir()
            Path(tmp, "keep", "notes.txt").write_text("not empty")
            assert datasets.tidy_empty_folders() == [] and Path(tmp, "keep", "notes.txt").exists()   # only EMPTY folders are touched
        finally:
            if holder:
                holder.kill()
                holder.wait()
            datasets.DATASET_DIR = original


def test_reset_can_empty_the_whole_table_and_protects_a_shared_one():
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(Path(tmp) / "demobanking.json")
        banking = datasets.get_dataset("banking")
        datasets.load_into(store, banking)
        # leftovers from an older version of the dataset, and a note of someone else's
        store.put("transfers", "TR1", {"customer_id": "C004", "beneficiary": "old"})
        store.put("documents", "DOC1", {"url": "https://onlinebanking.example.com/documents/DOC1"})
        store.put("my_notes", "n1", {"text": "mine"})
        assert datasets.leftover_collections(store, banking) == ["documents", "my_notes", "transfers"]
        datasets.load_into(store, banking, reset=True)                       # the old way: only the dataset's own collections
        assert store.get("transfers", "TR1") is not None and store.get("documents", "DOC1") is not None
        datasets.load_into(store, banking, reset=True, wipe_all=True)        # the whole table
        assert datasets.leftover_collections(store, banking) == []
        assert store.get("my_notes", "n1") is None
        assert sorted(store.collections()) == sorted(banking.data)
        assert datasets.marker(store)["dataset"] == "banking"                # the marker is written again after the wipe
        assert store.get("transactions", "TX1001")["doc_id"] == "DOC8001"


def test_which_other_datasets_share_a_table():
    original = datasets.DATASET_DIR
    with tempfile.TemporaryDirectory() as tmp:
        datasets.DATASET_DIR = Path(tmp)
        try:
            datasets.create_dataset("One", "", "demoshared", {"c": {"1": {"a": 1}}})
            datasets.create_dataset("Two", "", "demoshared", {"c": {"1": {"a": 2}}})
            datasets.create_dataset("Three", "", "demoalone", {"c": {"1": {"a": 3}}})
            assert datasets.others_using_table("demoshared", "one") == ["Two"]
            assert datasets.others_using_table("demoalone", "three") == []
            assert datasets.others_using_table("demoshared", "") == ["One", "Two"]
        finally:
            datasets.DATASET_DIR = original


def test_update_seed_data_from_the_database():
    with tempfile.TemporaryDirectory() as tmp:
        original = datasets.DATASET_DIR
        datasets.DATASET_DIR = Path(tmp)
        saved = {k: os.environ.get(k) for k in ("EXT_DATA_DOCS_URL", "EXT_DATA_DOCS_SAS")}
        try:
            os.environ["EXT_DATA_DOCS_URL"] = "https://acct.blob.core.windows.net/documents"
            os.environ["EXT_DATA_DOCS_SAS"] = "sv=2025-01-01&sr=c&sp=r&sig=abc%3D"
            seed = {"customers": {"C1": {"name": "Alex", "joined": "{{today-7d}}", "tags": ["a", "b"], "doc": "{{docs:D1.pdf}}"},
                                  "C2": {"name": "Sam", "joined": "{{today-3d}}"}}}
            ds = datasets.create_dataset("Pilot", "", "demopilot", seed)
            store = LocalStore(Path(tmp) / "demopilot.json")
            datasets.load_into(store, ds)

            # nothing changed: the seed stays exactly as it was, placeholders included
            new_seed, summary = datasets.seed_from_database(ds, store.export())
            assert new_seed == seed and summary == {"added": 0, "removed": 0, "changed": 0}

            # someone edits the database: a changed name, a new record, a deleted record
            c1 = store.get("customers", "C1")
            c1["name"] = "Alexandra"
            c1["tags"].append("c")
            store.put("customers", "C1", c1)
            store.put("customers", "C3", {"name": "Pat", "joined": "1 May 2026", "doc": "https://acct.blob.core.windows.net/documents/D3.pdf?sv=1&sig=zzz"})
            store.delete("customers", "C2")
            summary = datasets.update_seed_from_database(ds, store.export())
            assert summary == {"added": 1, "removed": 1, "changed": 1}
            written = json.loads((Path(tmp) / "pilot" / "data.json").read_text(encoding="utf-8"))
            assert written["customers"]["C1"]["name"] == "Alexandra" and written["customers"]["C1"]["tags"] == ["a", "b", "c"]
            assert written["customers"]["C1"]["joined"] == "{{today-7d}}"                       # unchanged value keeps its placeholder
            assert written["customers"]["C1"]["doc"] == "{{docs:D1.pdf}}"
            assert written["customers"]["C3"]["doc"] == "{{docs:D3.pdf}}"                        # a new link is stored without its token
            assert "C2" not in written["customers"]
            assert "sig=" not in json.dumps(written)
            backup = json.loads((Path(tmp) / "pilot" / "data.backup.json").read_text(encoding="utf-8"))
            assert backup == seed                                                                # the old seed is kept

            # nothing to write from an empty database; a token in the data is refused, nothing is written
            try:
                datasets.seed_from_database(ds, {})
                raise AssertionError("empty database must be refused")
            except StoreError:
                pass
            before = (Path(tmp) / "pilot" / "data.json").read_text(encoding="utf-8")
            os.environ["EXT_DATA_DOCS_URL"] = "https://another.example.com/documents"             # links no longer recognised
            try:
                datasets.update_seed_from_database(datasets.load_dataset(Path(tmp) / "pilot"),
                                                   {"customers": {"C9": {"doc": "https://acct.blob.core.windows.net/documents/D9.pdf?sv=1&sig=zzz"}}})
                raise AssertionError("a token must never be written into a seed file")
            except StoreError as exc:
                assert "access token" in str(exc)
            assert (Path(tmp) / "pilot" / "data.json").read_text(encoding="utf-8") == before
            # and the updated seed loads back into the same database state
            os.environ["EXT_DATA_DOCS_URL"] = "https://acct.blob.core.windows.net/documents"
            fresh = LocalStore(Path(tmp) / "other.json")
            datasets.load_into(fresh, datasets.load_dataset(Path(tmp) / "pilot"))
            assert fresh.get("customers", "C1")["name"] == "Alexandra" and fresh.get("customers", "C2") is None
        finally:
            datasets.DATASET_DIR = original
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_links_show_a_short_label_not_the_whole_url():
    url = "https://acct.blob.core.windows.net/documents/DOC8001.pdf?sv=2025-01-01&sr=c&sp=r&sig=SECRET%3D"
    assert render.link_label("doc_url", url, {"doc_id": "DOC8001"}) == "Receipt DOC8001"                              # whatever the kind of receipt
    assert render.link_label("doc_url", url, {"doc_id": "DOC8001", "doc_category": "Transfer receipt"}) == "Receipt DOC8001"
    assert render.link_label("doc_url", url, {"doc_id": "DOC8010", "doc_category": "Card receipt"}) == "Receipt DOC8010"
    assert render.link_label("documents_page_url", "https://x.example/documents-C004.html?sig=S") == "Documents page"
    assert render.link_label("website", "https://example.com/a/very/long/path/that/goes/on/and/on/and/on/forever.html?token=1").endswith("…")
    assert "token" not in render.link_label("website", "https://example.com/page?token=1")
    card = render.record_card("transactions", "TX1", {"customer_id": "C1", "doc_id": "DOC8001", "doc_category": "Transfer receipt", "doc_url": url,
                                                      "doc_title": "Receipt", "causale": "x"}, {})
    visible = re.sub(r"<[^>]+>", " ", card)
    assert "Receipt DOC8001" in visible and "SECRET" not in visible and "sig=" not in visible
    assert f'href="{url.replace("&", "&amp;")}"' in card                           # the link itself still carries the token


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
