"""Tests for store.py.

    python test_store.py

The local-file backend is always tested. The Azure backend is tested against the Azurite emulator
(https://github.com/Azure/Azurite) when it is running on 127.0.0.1:10002, e.g.

    npx azurite-table --silent --location ./azurite-data --skipApiVersionCheck

Nothing here talks to real Azure.
"""
import base64
import hashlib
import hmac
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from store import AzureTableStore, LocalStore, StoreError, check_key

SEED = json.loads((Path(__file__).parent / "datasets" / "starter" / "data.json").read_text(encoding="utf-8"))

AZURITE_URL = "http://127.0.0.1:10002/devstoreaccount1"
AZURITE_ACCOUNT = "devstoreaccount1"
AZURITE_KEY = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="  # public, documented emulator key


def azurite_sas(permissions: str = "racuwdl", days: int = 1) -> str:
    """An account SAS for the emulator (what `az storage account generate-sas` produces for real Azure)."""
    version = "2019-02-02"
    expiry = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fields = [AZURITE_ACCOUNT, permissions, "t", "sco", "", expiry, "", "https,http", version]
    to_sign = "\n".join(fields) + "\n"
    sig = base64.b64encode(hmac.new(base64.b64decode(AZURITE_KEY), to_sign.encode(), hashlib.sha256).digest()).decode()
    return f"sv={version}&ss=t&srt=sco&sp={permissions}&se={quote(expiry, safe='')}&spr=https,http&sig={quote(sig, safe='')}"


def azurite_running() -> bool:
    try:
        requests.get(AZURITE_URL + "/Tables", timeout=1)
        return True
    except requests.exceptions.RequestException:
        return False


# -- behaviour every backend must have ------------------------------------------------------
def exercise(store):
    assert store.seed(SEED) == 6
    assert store.collections() == ["customers", "orders"]
    assert store.ids("customers") == ["C001", "C002", "C003"]
    assert store.get("customers", "C001")["name"] == "Alex Example"
    assert store.get("customers", "C999") is None
    assert store.all("orders")["O1002"]["total"] == 199.0

    # replace one record, including non-ASCII text and a quote in the id
    record = store.get("customers", "C003")
    record["preferences"]["contact_by"] = "email"
    record["notes"].append("Préfère l'e-mail – très bien")
    store.put("customers", "C003", record)
    assert store.get("customers", "C003") == record
    store.put("customers", "O'Brien 7", {"name": "Sinéad O'Brien"})
    assert store.get("customers", "O'Brien 7") == {"name": "Sinéad O'Brien"}
    assert "O'Brien 7" in store.ids("customers")

    # delete
    assert store.delete("customers", "O'Brien 7") is True
    assert store.delete("customers", "O'Brien 7") is False
    assert store.get("customers", "O'Brien 7") is None

    # internal collections (_meta) are hidden from collections() and export() but still readable
    store.put("_meta", "seed", {"dataset": "x"})
    assert "_meta" not in store.collections() and "_meta" not in store.export()
    assert store.get("_meta", "seed") == {"dataset": "x"}

    # wipe_collections removes only the named collections
    store.put("scratch", "s1", {"a": 1})
    assert store.wipe_collections(["scratch"]) == 1
    assert store.get("scratch", "s1") is None and store.get("customers", "C001") is not None
    store.delete("_meta", "seed")

    # export round-trips
    exported = store.export()
    assert exported["customers"]["C003"] == record

    # reseed with wipe restores the original data
    store.put("customers", "EXTRA", {"x": 1})
    assert store.seed(SEED, wipe=True) == 6
    assert store.get("customers", "EXTRA") is None
    assert store.get("customers", "C003") == SEED["customers"]["C003"]

    # bad input is refused with a readable message
    for bad in (lambda: store.put("customers", "a/b", {}), lambda: store.put("", "x", {}),
                lambda: store.put("customers", "x", {"big": "y" * 40000}), lambda: store.seed({"c": {"id": "not a record"}})):
        try:
            bad()
        except StoreError:
            continue
        raise AssertionError("expected StoreError")


def test_check_key():
    assert check_key(" C001 ", "id") == "C001"
    for bad in ("", "  ", "a/b", "a#b", "a?b", "a\\b", "a\nb"):
        try:
            check_key(bad, "id")
        except StoreError:
            continue
        raise AssertionError(bad)


def test_local_store():
    with tempfile.TemporaryDirectory() as tmp:
        exercise(LocalStore(Path(tmp) / "store.json"))


def test_local_delete_table():
    with tempfile.TemporaryDirectory() as tmp:
        a = LocalStore(Path(tmp) / "demoa.json")
        a.put("customers", "C1", {"name": "A"})
        b = a.with_table("demob")
        b.put("customers", "C1", {"name": "B"})
        assert b.delete_table() is True and b.delete_table() is False
        assert a.list_tables() == ["demoa"] and a.get("customers", "C1")["name"] == "A"


def test_local_tables():
    with tempfile.TemporaryDirectory() as tmp:
        a = LocalStore(Path(tmp) / "demoa.json")
        a.put("customers", "C1", {"name": "A"})
        b = a.with_table("demob")
        b.put("customers", "C1", {"name": "B"})
        assert a.get("customers", "C1")["name"] == "A" and b.get("customers", "C1")["name"] == "B"
        assert a.list_tables() == ["demoa", "demob"]


def test_azure_store_against_azurite():
    if not azurite_running():
        print("   (Azurite not running on 127.0.0.1:10002: skipped)")
        return
    store = AzureTableStore(AZURITE_URL, azurite_sas(), "testtable" + datetime.now().strftime("%H%M%S"))
    assert store.collections() == []                       # table does not exist yet: empty, no error
    assert store.get("customers", "C001") is None
    exercise(store)                                        # first write creates the table
    assert "Connected" in store.check_connection()
    assert "can be read" in store.ping()                                                    # read-only test
    assert "does not exist yet" in store.with_table("nosuchtable").ping()
    assert "nosuchtable" not in store.list_tables()                                         # ...and it did not create the table

    # tables: listing and switching to another table of the same account
    assert store.table in store.list_tables()
    other = store.with_table(store.table + "b")
    assert other.table == store.table + "b" and other.get("customers", "C001") is None        # new table: empty, not an error
    other.put("customers", "C001", {"name": "Elsewhere"})
    assert store.table + "b" in store.list_tables()
    assert store.get("customers", "C001")["name"] == "Alex Example"                         # the first table is untouched
    try:
        store.with_table("bad_name")
        raise AssertionError("invalid table name must fail")
    except StoreError:
        pass

    # deleting a table removes the table itself, not just its records; the others stay
    assert other.get("customers", "C001")["name"] == "Elsewhere"
    assert other.delete_table() is True
    assert other.table not in store.list_tables() and store.table in store.list_tables()
    assert other.delete_table() is False                                                    # already gone
    assert store.get("customers", "C001")["name"] == "Alex Example"

    # more records than one page returns, so continuation tokens are followed
    many = {f"R{i:04d}": {"n": i} for i in range(1200)}
    store.seed({"bulk": many})
    assert len(store.all("bulk")) == 1200

    # a token that may only read cannot write, and the error says so
    read_only = AzureTableStore(AZURITE_URL, azurite_sas("rl"), store.table)
    assert read_only.get("customers", "C001")["name"] == "Alex Example"
    try:
        read_only.put("customers", "C001", {})
        raise AssertionError("write with a read-only SAS must fail")
    except StoreError as exc:
        assert "403" in str(exc), exc

    # a wrong signature is rejected
    broken = AzureTableStore(AZURITE_URL, azurite_sas()[:-6] + "AAAAAA", store.table)
    try:
        broken.get("customers", "C001")
        raise AssertionError("bad signature must fail")
    except StoreError as exc:
        assert "403" in str(exc), exc


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
