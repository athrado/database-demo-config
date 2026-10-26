"""Storage layer for the Ext Data template.

Data model (the same in every backend): COLLECTIONS of RECORDS.

    collection  "customers", "orders", ...   any name you like
    record id   "C001", "O1001", ...         unique inside its collection
    record      any JSON object              nested objects and lists are fine

Two backends, same interface:

    AzureTableStore   Azure Table Storage over its REST API, authenticated with a SAS token.
                      This is what Teneo (cloud) and the Streamlit app share.
    LocalStore        one JSON file on disk. For trying things out without an Azure account.

In Azure a record is one table row: PartitionKey = collection, RowKey = record id,
and the record itself as JSON text in the `json` property (a property holds up to
32,000 characters, plenty for demo data). The Groovy code in teneo/ reads and writes the very
same layout, so Python and Teneo see each other's records.

`get_store()` picks the backend from the environment (or a .env file next to this file):

    EXT_DATA_ACCOUNT_URL   https://<account>.table.core.windows.net   (setting this selects Azure)
    EXT_DATA_SAS_TOKEN     the SAS token, with or without the leading "?"
    EXT_DATA_TABLE         table name, default "extdata"
    EXT_DATA_LOCAL_FILE    local backend file, default data/local_store.json
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

HERE = Path(__file__).resolve().parent
DEFAULT_TABLE = "extdata"
META_COLLECTION = "_meta"   # internal: records about the table itself (which dataset was loaded)
META_ID = "seed"
API_VERSION = "2019-02-02"
MAX_JSON_CHARS = 32000  # one Table Storage string property holds at most 32K characters

_FORBIDDEN_KEY_CHARS = set('/\\#?')


class StoreError(Exception):
    """Anything that went wrong talking to the storage (message is meant for a human)."""


def check_key(value: str, what: str) -> str:
    """Azure does not allow / \\ # ? or control characters in partition and row keys."""
    if not isinstance(value, str) or not value.strip():
        raise StoreError(f"{what} must be a non-empty text")
    bad = [c for c in value if c in _FORBIDDEN_KEY_CHARS or ord(c) < 32 or 0x7F <= ord(c) <= 0x9F]
    if bad:
        raise StoreError(f"{what} {value!r} contains a character that is not allowed (/ \\ # ? or a control character)")
    return value.strip()


def check_table(name: str) -> str:
    """Azure table names: 3-63 letters and digits, starting with a letter."""
    name = (name or "").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2,62}", name):
        raise StoreError(f"Table name {name!r} is not valid: use 3-63 letters and digits, starting with a letter (no spaces, dashes or underscores)")
    return name


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env reader (KEY=value lines); real environment variables win."""
    path = path or HERE / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Store:
    """Interface shared by both backends."""

    name = "store"

    def describe(self) -> str:
        raise NotImplementedError

    def _collection_names(self) -> list[str]:
        """Every collection in the storage, including internal ones (names starting with "_")."""
        raise NotImplementedError

    def collections(self) -> list[str]:
        """The collections to show: internal ones such as _meta are left out."""
        return [c for c in self._collection_names() if not c.startswith("_")]

    def list_tables(self) -> list[str]:
        """Tables (one per demo) available in this storage."""
        raise NotImplementedError

    def with_table(self, table: str) -> "Store":
        """The same storage, pointed at another table."""
        raise NotImplementedError

    def delete_table(self) -> bool:
        """Delete this whole table with everything in it. Returns False if there was no such table."""
        raise NotImplementedError

    def all(self, collection: str) -> dict[str, dict]:
        """Every record of a collection as {id: record}."""
        raise NotImplementedError

    def get(self, collection: str, record_id: str) -> dict | None:
        raise NotImplementedError

    def put(self, collection: str, record_id: str, record: dict) -> None:
        """Create or completely replace one record."""
        raise NotImplementedError

    def delete(self, collection: str, record_id: str) -> bool:
        raise NotImplementedError

    # -- shared helpers -------------------------------------------------------------------

    def ids(self, collection: str) -> list[str]:
        return sorted(self.all(collection))

    def wipe(self) -> None:
        """Delete every record in every collection, including internal ones."""
        self.wipe_collections(self._collection_names())

    def wipe_collections(self, names) -> int:
        """Delete every record of the given collections. Returns how many were deleted."""
        count = 0
        for collection in names:
            for record_id in self.ids(collection):
                if self.delete(collection, record_id):
                    count += 1
        return count

    def seed(self, data: dict[str, dict[str, dict]], wipe: bool = False) -> int:
        """Write a whole {collection: {id: record}} dictionary. Returns the number of records."""
        validate_seed(data)
        if wipe:
            self.wipe()
        count = 0
        for collection, records in data.items():
            for record_id, record in records.items():
                self.put(collection, record_id, record)
                count += 1
        return count

    def export(self) -> dict[str, dict[str, dict]]:
        return {c: self.all(c) for c in self.collections()}


def check_record(collection: str, record_id: str, record) -> str:
    """Validate a write and return the record as JSON text. Both backends refuse the same things,
    so data that works locally also works in Azure."""
    check_key(collection, "Collection name")
    check_key(record_id, "Record id")
    if not isinstance(record, dict):
        raise StoreError(f"Record {collection}/{record_id} must be a JSON object")
    text = json.dumps(record, ensure_ascii=False)
    if len(text) > MAX_JSON_CHARS:
        raise StoreError(f"Record {collection}/{record_id} is {len(text)} characters; the limit is {MAX_JSON_CHARS}")
    return text


def validate_seed(data) -> None:
    if not isinstance(data, dict):
        raise StoreError("Seed data must be an object: {collection: {record id: record}}")
    for collection, records in data.items():
        check_key(collection, "Collection name")
        if not isinstance(records, dict):
            raise StoreError(f"Collection {collection!r} must be an object {{record id: record}}")
        for record_id, record in records.items():
            check_record(collection, record_id, record)


# ======================================================================================
# Local file backend
# ======================================================================================
class LocalStore(Store):
    name = "local"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def describe(self) -> str:
        return f"Local file: {self.path}"

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8") or "{}")

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _collection_names(self) -> list[str]:
        return sorted(c for c, records in self._load().items() if records)

    def list_tables(self) -> list[str]:
        """Local tables are the .json files next to this one."""
        return sorted(p.stem for p in self.path.parent.glob("*.json")) if self.path.parent.exists() else []

    def with_table(self, table: str) -> "LocalStore":
        return LocalStore(self.path.parent / f"{check_table(table)}.json")

    def delete_table(self) -> bool:
        with self._lock:
            existed = self.path.exists()
            self.path.unlink(missing_ok=True)
            return existed

    def all(self, collection: str) -> dict[str, dict]:
        return dict(sorted(self._load().get(collection, {}).items()))

    def get(self, collection: str, record_id: str) -> dict | None:
        return self._load().get(collection, {}).get(record_id)

    def put(self, collection: str, record_id: str, record: dict) -> None:
        check_record(collection, record_id, record)
        with self._lock:
            data = self._load()
            data.setdefault(collection, {})[record_id] = record
            self._save(data)

    def delete(self, collection: str, record_id: str) -> bool:
        with self._lock:
            data = self._load()
            existed = record_id in data.get(collection, {})
            if existed:
                del data[collection][record_id]
                self._save(data)
            return existed


# ======================================================================================
# Azure Table Storage backend (REST + SAS)
# ======================================================================================
def _q(value: str) -> str:
    """Escape a key for use inside PartitionKey='...' in the URL (quotes doubled, then percent-encoded)."""
    return quote(value.replace("'", "''"), safe="")


class AzureTableStore(Store):
    name = "azure"

    def __init__(self, account_url: str, sas_token: str, table: str = DEFAULT_TABLE, timeout: float = 15):
        self.account_url = account_url.strip().rstrip("/")
        self.sas = sas_token.strip().lstrip("?")
        self.table = table.strip() or DEFAULT_TABLE
        self.timeout = timeout
        self._session = requests.Session()
        self._table_checked = False

    def describe(self) -> str:
        return f"Azure Table Storage: {self.account_url} / table '{self.table}'"

    # -- HTTP -----------------------------------------------------------------------------
    def _url(self, path: str, query: str = "") -> str:
        parts = [p for p in (query, self.sas) if p]
        return f"{self.account_url}/{path}?{'&'.join(parts)}"

    def _call(self, method: str, url: str, body: dict | None = None, extra_headers: dict | None = None) -> requests.Response:
        headers = {
            "Accept": "application/json;odata=nometadata",
            "x-ms-version": API_VERSION,
            "DataServiceVersion": "3.0",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        headers.update(extra_headers or {})
        try:
            return self._session.request(
                method, url, headers=headers, timeout=self.timeout,
                data=json.dumps(body).encode("utf-8") if body is not None else None,
            )
        except requests.exceptions.RequestException as exc:
            raise StoreError(f"Cannot reach Azure Table Storage at {self.account_url}: {exc}") from exc

    @staticmethod
    def _fail(resp: requests.Response, action: str) -> StoreError:
        detail = resp.text[:300]
        try:
            detail = resp.json().get("odata.error", {}).get("message", {}).get("value", detail)
        except ValueError:
            pass
        hints = {
            403: " (the SAS token is wrong, expired, or lacks permission for this operation)",
            404: " (table or record not found)",
        }
        return StoreError(f"Azure {action} failed with HTTP {resp.status_code}{hints.get(resp.status_code, '')}: {detail}")

    def _ensure_table(self) -> None:
        resp = self._call("POST", self._url("Tables"), {"TableName": self.table}, {"Prefer": "return-no-content"})
        if resp.status_code == 409 and "TableBeingDeleted" in resp.text:
            raise StoreError(f"The table '{self.table}' was deleted a moment ago. Azure needs about 40 seconds before the same name "
                             "can be used again: try again shortly.")
        if resp.status_code not in (201, 204, 409):  # 409 = already exists
            raise self._fail(resp, f"creating table '{self.table}'")
        self._table_checked = True

    def _entity_url(self, collection: str, record_id: str) -> str:
        return self._url(f"{self.table}(PartitionKey='{_q(collection)}',RowKey='{_q(record_id)}')")

    def _query(self, filter_expr: str | None, select: str | None = None) -> list[dict]:
        rows: list[dict] = []
        cont = ""
        while True:
            parts = []
            if filter_expr:
                parts.append("%24filter=" + quote(filter_expr, safe=""))
            if select:
                parts.append("%24select=" + quote(select, safe=","))
            if cont:
                parts.append(cont)
            resp = self._call("GET", self._url(f"{self.table}()", "&".join(parts)))
            if resp.status_code == 404:
                return []  # table does not exist yet: nothing stored
            if resp.status_code != 200:
                raise self._fail(resp, "reading")
            rows += resp.json().get("value", [])
            next_pk = resp.headers.get("x-ms-continuation-NextPartitionKey")
            next_rk = resp.headers.get("x-ms-continuation-NextRowKey")
            if not next_pk:
                return rows
            cont = f"NextPartitionKey={quote(next_pk, safe='')}" + (f"&NextRowKey={quote(next_rk, safe='')}" if next_rk else "")

    @staticmethod
    def _decode(entity: dict) -> dict:
        try:
            return json.loads(entity.get("json") or "{}")
        except ValueError as exc:
            raise StoreError(f"Record {entity.get('PartitionKey')}/{entity.get('RowKey')} does not contain valid JSON: {exc}") from exc

    # -- Store interface ------------------------------------------------------------------
    def _collection_names(self) -> list[str]:
        return sorted({row["PartitionKey"] for row in self._query(None, select="PartitionKey")})

    def list_tables(self) -> list[str]:
        """Names of all tables in the storage account (needs the List permission on the Service resource type)."""
        names: list[str] = []
        cont = ""
        while True:
            resp = self._call("GET", self._url("Tables", cont))
            if resp.status_code != 200:
                raise self._fail(resp, "listing tables")
            names += [row["TableName"] for row in resp.json().get("value", [])]
            next_table = resp.headers.get("x-ms-continuation-NextTableName")
            if not next_table:
                return sorted(names)
            cont = "NextTableName=" + quote(next_table, safe="")

    def with_table(self, table: str) -> "AzureTableStore":
        return AzureTableStore(self.account_url, self.sas, check_table(table), self.timeout)

    def delete_table(self) -> bool:
        """Delete the table itself (needs the Delete permission on the Service resource type). Azure removes it in the
        background: the same name can't be created again for about 40 seconds."""
        resp = self._call("DELETE", self._url(f"Tables('{_q(self.table)}')"))
        if resp.status_code == 404:
            return False
        if resp.status_code not in (200, 204):
            raise self._fail(resp, f"deleting the table '{self.table}'")
        self._table_checked = False
        return True

    def all(self, collection: str) -> dict[str, dict]:
        check_key(collection, "Collection name")
        rows = self._query("PartitionKey eq '" + collection.replace("'", "''") + "'")
        return {row["RowKey"]: self._decode(row) for row in sorted(rows, key=lambda r: r["RowKey"])}

    def get(self, collection: str, record_id: str) -> dict | None:
        check_key(collection, "Collection name")
        check_key(record_id, "Record id")
        resp = self._call("GET", self._entity_url(collection, record_id))
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise self._fail(resp, "reading")
        return self._decode(resp.json())

    def put(self, collection: str, record_id: str, record: dict) -> None:
        text = check_record(collection, record_id, record)
        entity = {
            "PartitionKey": collection.strip(),
            "RowKey": record_id.strip(),
            "json": text,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        for attempt in (1, 2):
            resp = self._call("PUT", self._entity_url(collection, record_id), entity)  # insert or replace
            if resp.status_code == 404 and attempt == 1 and not self._table_checked:
                self._ensure_table()
                continue
            break
        if resp.status_code not in (200, 204):
            raise self._fail(resp, "writing")

    def delete(self, collection: str, record_id: str) -> bool:
        check_key(collection, "Collection name")
        check_key(record_id, "Record id")
        resp = self._call("DELETE", self._entity_url(collection, record_id), extra_headers={"If-Match": "*"})
        if resp.status_code == 404:
            return False
        if resp.status_code not in (200, 204):
            raise self._fail(resp, "deleting")
        return True

    def ping(self) -> str:
        """Read-only connection test: asks for one row of the current table and never creates anything."""
        resp = self._call("GET", self._url(f"{self.table}()", "%24top=1"))
        if resp.status_code == 404:
            return f"Connected. The table '{self.table}' does not exist yet: it is created when you update a config."
        if resp.status_code != 200:
            raise self._fail(resp, "connecting")
        return f"Connected. The table '{self.table}' can be read."

    def check_connection(self) -> str:
        """Try the table; create it if missing. Returns a short status text or raises StoreError."""
        resp = self._call("GET", self._url(f"{self.table}()", "%24top=1"))
        if resp.status_code == 404:
            self._ensure_table()
            return f"Connected. Table '{self.table}' was created."
        if resp.status_code != 200:
            raise self._fail(resp, "connecting")
        return f"Connected. Table '{self.table}' exists."


def get_store(table: str | None = None) -> Store:
    """Azure when EXT_DATA_ACCOUNT_URL is set, otherwise local files (one per table).

    `table` overrides EXT_DATA_TABLE, e.g. get_store("demoretail").
    """
    load_dotenv()
    account_url = os.environ.get("EXT_DATA_ACCOUNT_URL", "").strip()
    wanted = (table or os.environ.get("EXT_DATA_TABLE", "")).strip()
    if account_url:
        sas = os.environ.get("EXT_DATA_SAS_TOKEN", "").strip()
        if not sas:
            raise StoreError("EXT_DATA_ACCOUNT_URL is set but EXT_DATA_SAS_TOKEN is empty")
        return AzureTableStore(account_url, sas, wanted or DEFAULT_TABLE)
    local_file = os.environ.get("EXT_DATA_LOCAL_FILE")
    if local_file and not table:
        return LocalStore(local_file)
    folder = Path(local_file).parent if local_file else HERE / "data"
    return LocalStore(folder / f"{check_table(wanted) if wanted else 'local_store'}.json")
