"""Configs: ready-made demo data you can load into a table, and snapshots of your own.

A config is a folder in datasets/:

    datasets/retail/
        config.json   title, description, icon, default_table, order
        data.json      {collection: {record id: record}}
        view.json      how the Streamlit app shows it (optional, see render.py)

Loading a config writes its records into the table you pick. "Reset" first deletes the
collections the config defines (other collections in the table are left alone), so a demo can be
put back to its starting state in one click. Every load writes a marker record (_meta/seed)
saying which config it was and when.

Dates that should always look fresh are written as placeholders in data.json and resolved when
the config is loaded:

    {{today}}        15 October 2026          {{iso:today}}       2026-10-15
    {{today-7d}}     8 October 2026           {{iso:today+3d}}   2026-10-18
    {{today+2w}}     29 October 2026          {{today-1m}}        15 September 2026
                                              {{month:today-1m}}  September 2026
                                              {{mese:today-1m}}   settembre   (the month name in Italian)
    {{lastmonth:3}}  3 September 2026         {{iso:lastmonth:3}} 2026-09-03   (a fixed day of the previous calendar month)
    {{lastyear:03-15}}  15 March 2025                                       (a fixed day of the previous calendar year)
    {{monthago:3:12}}  12 July 2026                                         (the 12th, three calendar months back)
    {{docs:DOC8001.pdf}}  the link to a document file in the Blob container (EXT_DATA_DOCS_URL + EXT_DATA_DOCS_SAS in .env)
"""
from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import os

import github_sync
from store import META_COLLECTION, META_ID, Store, StoreError, check_key, check_table, load_dotenv, validate_seed

HERE = Path(__file__).resolve().parent
DATASET_DIR = HERE / "datasets"

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]
# {{lastmonth:3}} = the 3rd of the previous calendar month; {{lastyear:03-15}} = 15 March of the previous year
_CALENDAR = re.compile(r"\{\{\s*(iso:)?(lastmonth|lastyear|monthago):(\d{1,2}(?:[-:]\d{1,2})?)\s*\}\}")
# {{docs:DOC8001.pdf}} = the link to a document: EXT_DATA_DOCS_URL (the Blob container) + "/" + name + "?" + EXT_DATA_DOCS_SAS
_DOCS = re.compile(r"\{\{\s*docs:([A-Za-z0-9._-]+)\s*\}\}")
DEFAULT_DOCS_URL = "https://onlinebanking.example.com/documents"
_MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
         "agosto", "settembre", "ottobre", "novembre", "dicembre"]
_PLACEHOLDER = re.compile(r"\{\{\s*(iso:|month:|mese:)?today(?:\s*([+-])\s*(\d+)\s*([dwm]))?\s*\}\}")


@dataclass
class Dataset:
    name: str                       # folder name
    title: str
    description: str = ""
    notes: str = ""                 # demo notes (who to call, what to expect): shown on the Datasets tab only
    icon: str = "🗂️"
    default_table: str = ""
    order: int = 100
    data: dict = field(default_factory=dict)
    view: dict = field(default_factory=dict)
    path: Path | None = None

    @property
    def n_records(self) -> int:
        return sum(len(records) for records in self.data.values())


# ---------------------------------------------------------------- placeholders
def _shift(day: date, sign: str, amount: int, unit: str) -> date:
    amount = amount if sign == "+" else -amount
    if unit == "d":
        return day + timedelta(days=amount)
    if unit == "w":
        return day + timedelta(weeks=amount)
    month_index = day.year * 12 + (day.month - 1) + amount          # unit == "m"
    year, month = divmod(month_index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def resolve_placeholders(value, today: date | None = None):
    """Replace {{today...}} placeholders in every string of a nested structure."""
    today = today or date.today()

    def fix(match: re.Match) -> str:
        day = _shift(today, match.group(2), int(match.group(3)), match.group(4)) if match.group(2) else today
        if match.group(1) == "iso:":
            return day.isoformat()
        if match.group(1) == "month:":
            return f"{_MONTHS[day.month - 1]} {day.year}"
        if match.group(1) == "mese:":
            return _MESI[day.month - 1]
        return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"

    def calendar_day(match: re.Match) -> str:
        kind, spec = match.group(2), match.group(3)
        if kind in ("lastmonth", "monthago"):
            back, day = (1, int(spec)) if kind == "lastmonth" else (int(spec.split(":")[0]), int(spec.split(":")[1]))
            index = today.year * 12 + (today.month - 1) - back
            year, month = divmod(index, 12)
            month += 1
        else:
            year, (month, day) = today.year - 1, ((int(p) for p in spec.split("-")) if "-" in spec else (1, int(spec)))
        day = min(day, calendar.monthrange(year, month)[1])
        d = date(year, month, day)
        return d.isoformat() if match.group(1) else f"{d.day} {_MONTHS[d.month - 1]} {d.year}"

    def docs_link(match: re.Match) -> str:
        load_dotenv()
        base = os.environ.get("EXT_DATA_DOCS_URL", "").strip().rstrip("/") or DEFAULT_DOCS_URL
        sas = os.environ.get("EXT_DATA_DOCS_SAS", "").strip().lstrip("?")
        return f"{base}/{match.group(1)}" + (f"?{sas}" if sas else "")

    if isinstance(value, str):
        return _DOCS.sub(docs_link, _CALENDAR.sub(calendar_day, _PLACEHOLDER.sub(fix, value)))
    if isinstance(value, list):
        return [resolve_placeholders(v, today) for v in value]
    if isinstance(value, dict):
        return {k: resolve_placeholders(v, today) for k, v in value.items()}
    return value


# ---------------------------------------------------------------- reading datasets
def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StoreError(f"Cannot read {path}: {exc}") from exc


def load_dataset(folder: Path) -> Dataset:
    meta = _read_json(folder / "config.json")
    view_file = folder / "view.json"
    return Dataset(
        name=folder.name,
        title=meta.get("title", folder.name),
        description=meta.get("description", ""),
        notes=meta.get("notes", ""),
        icon=meta.get("icon", "🗂️"),
        default_table=meta.get("default_table", ""),
        order=int(meta.get("order", 100)),
        data=_read_json(folder / "data.json"),
        view=_read_json(view_file) if view_file.exists() else {},
        path=folder,
    )


def list_datasets() -> list[Dataset]:
    if not DATASET_DIR.exists():
        return []
    found = [load_dataset(p) for p in DATASET_DIR.iterdir() if (p / "config.json").exists() and (p / "data.json").exists()]
    return sorted(found, key=lambda d: (d.order, d.title.lower()))


def get_dataset(name: str) -> Dataset:
    folder = DATASET_DIR / name
    if not (folder / "config.json").exists():
        raise StoreError(f"No config called {name!r}")
    return load_dataset(folder)


# ---------------------------------------------------------------- loading into a table
def load_into(store: Store, dataset: Dataset, reset: bool = False, today: date | None = None, wipe_all: bool = False) -> int:
    """Write the config into the store. With reset=True the config's collections are emptied first; with
    wipe_all=True the WHOLE table is emptied, so collections left over from older versions of the config go too
    (use it when the table belongs to this config alone). Returns the number of records written."""
    data = resolve_placeholders(dataset.data, today)
    validate_seed(data)
    if reset:
        if wipe_all:
            store.wipe()
        else:
            store.wipe_collections(list(data) + [META_COLLECTION])
    count = store.seed(data)
    store.put(META_COLLECTION, META_ID, {
        "dataset": dataset.name,
        "title": dataset.title,
        "loaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reset": reset,
        "collections": sorted(data),
        "records": count,
    })
    return count


def others_using_table(table: str, exclude: str = "") -> list[str]:
    """Titles of the other configs whose default table is this one (a reset must not wipe their data)."""
    return [ds.title for ds in list_datasets() if ds.name != exclude and ds.default_table == table]


def leftover_collections(store: Store, dataset: Dataset) -> list[str]:
    """Collections in the table that the config does not define (e.g. from an older version of it)."""
    return [c for c in store.collections() if c not in dataset.data]


def marker(store: Store) -> dict | None:
    """What was loaded last (the _meta/seed record), or None for a table nobody has seeded."""
    return store.get(META_COLLECTION, META_ID)


# ---------------------------------------------------------------- snapshots
def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not slug:
        raise StoreError("Give the config a name")
    return slug


def create_dataset(title: str, description: str, table: str, data: dict, view: dict | None = None, icon: str = "🗂️",
                   notes: str = "", order: int = 500) -> Dataset:
    """Write a new config folder (config.json, data.json and, if given, view.json). The records are checked first.
    If writing fails half-way, what was written is removed again, so no half-made config is left behind."""
    slug = slugify(title)
    folder = DATASET_DIR / slug
    if (folder / "config.json").exists():
        raise StoreError(f"A config called {slug!r} already exists. Pick another name.")
    if table:
        check_table(table)
    validate_seed(data)
    meta = {"title": title.strip(), "description": description.strip(), "icon": icon, "default_table": table, "order": order}
    if notes:
        meta["notes"] = notes
    files = {"config.json": meta, "data.json": data}
    if view:
        files["view.json"] = view
    existed = folder.exists()                                  # an empty leftover folder is fine to reuse
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for name, content in files.items():
            (folder / name).write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            written.append(name)
    except OSError as exc:
        for name in written:
            (folder / name).unlink(missing_ok=True)
        if not existed:
            try:
                folder.rmdir()
            except OSError:
                pass
        raise StoreError(f"Could not write the config files: {exc}") from exc
    return load_dataset(folder)


def save_snapshot(store: Store, title: str, description: str = "", base: Dataset | None = None, table: str = "") -> Dataset:
    """Save what is in the store right now as a new config (data.json = the records, view.json copied
    from the config it came from)."""
    data = store.export()
    if not data:
        raise StoreError("The table is empty: nothing to save")
    return create_dataset(title, description, table, data, view=base.view if base else None, icon=base.icon if base else "📸")


def delete_dataset(name: str, wait: float = 5.0) -> bool:
    """Remove a config folder. Only folders inside datasets/ that hold a config.json are ever deleted.

    Returns True when the folder is gone. It returns False when the files are deleted (so it is no config any more)
    but the empty folder could not be removed within `wait` seconds: on Windows a folder stays while something has it
    open (a File Explorer window, a terminal in it, OneDrive syncing). Such a leftover is cleaned up later by
    tidy_empty_folders()."""
    import shutil
    import time
    folder = (DATASET_DIR / name).resolve()
    if folder.parent != DATASET_DIR.resolve() or not (folder / "config.json").exists():
        raise StoreError(f"{name!r} is not a config")
    shutil.rmtree(folder, ignore_errors=True)
    if (folder / "config.json").exists() or (folder / "data.json").exists():
        raise StoreError(f"Could not delete the files of {name!r}. Is a file open in another program?")
    deadline = time.monotonic() + wait
    while folder.exists():
        try:
            folder.rmdir()
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)
    return True


MAX_FILE_BYTES = 5_000_000


def pull_from_github() -> list[str]:
    """Download the datasets that are in the GitHub repository but not here yet (a dataset made on another machine, say).
    A dataset needs config.json and data.json in datasets/<name>/. Datasets that already exist here are never touched, so
    nothing you changed locally is overwritten, and nothing is deleted. Returns the names that were added; [] when GitHub
    is not set up."""
    cfg = github_sync.get_config()
    if not cfg:
        return []
    folders: dict[str, dict[str, dict]] = {}
    for f in github_sync.list_files(cfg):
        parts = f["path"].split("/")
        if len(parts) == 3 and parts[0] == "datasets" and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", parts[1]) and f["size"] <= MAX_FILE_BYTES:
            folders.setdefault(parts[1], {})[parts[2]] = f
    added = []
    for name, files in sorted(folders.items()):
        target = DATASET_DIR / name
        if (target / "config.json").exists() or "config.json" not in files or "data.json" not in files:
            continue
        try:
            target.mkdir(parents=True, exist_ok=True)
            for filename, f in files.items():
                if filename.endswith(".backup.json"):
                    continue
                (target / filename).write_bytes(github_sync.read_file(cfg, f["sha"]))
        except OSError as exc:
            raise StoreError(f"Could not save the dataset {name!r} from GitHub: {exc}") from exc
        added.append(name)
    return added


def default_delete_message(dataset: Dataset) -> str:
    return f"Delete config: {dataset.title}\n\nRemoves datasets/{dataset.name}/"


def commit_delete(name: str, message: str) -> dict | None:
    """Delete the config's whole folder (every file in it) from the GitHub repository, in one commit. Does nothing
    when GitHub is not set up or the repository has no such folder. Returns {"sha", "url"} or None."""
    cfg = github_sync.get_config()
    if not cfg:
        return None
    if not name or "/" in name or name.startswith("."):
        raise StoreError(f"{name!r} is not a config name")
    return github_sync.delete_folder(cfg, f"datasets/{name}", message)


def tidy_empty_folders() -> list[str]:
    """Remove empty folders left in datasets/ (for example by a delete that could not remove its folder).
    Folders that are still locked are left alone and tried again next time. Returns the names removed."""
    removed = []
    if DATASET_DIR.exists():
        for child in DATASET_DIR.iterdir():
            if child.is_dir() and not any(child.iterdir()):
                try:
                    child.rmdir()
                    removed.append(child.name)
                except OSError:
                    pass
    return removed


def save_data(dataset: Dataset, data: dict) -> None:
    """Replace a config's data.json (used by the editor in the app)."""
    validate_seed(data)
    (dataset.path / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- database -> seed data
def unresolve_docs(value):
    """A document link of the Blob container, turned back into its {{docs:NAME}} placeholder (so no token is stored)."""
    if not isinstance(value, str):
        return value
    load_dotenv()
    base = os.environ.get("EXT_DATA_DOCS_URL", "").strip().rstrip("/") or DEFAULT_DOCS_URL
    prefix = base + "/"
    if value.startswith(prefix):
        name = value[len(prefix):].split("?")[0]
        if name and all(ch.isalnum() or ch in "._-" for ch in name):
            return "{{docs:%s}}" % name
    return value


def _keep_placeholders(raw, resolved, new):
    """`new` is what the database holds. Where it still equals what the old seed value resolves to, keep the old
    seed value (with its {{today-7d}} placeholder); otherwise write the new value."""
    if isinstance(new, dict):
        raw_d = raw if isinstance(raw, dict) else {}
        res_d = resolved if isinstance(resolved, dict) else {}
        return {k: _keep_placeholders(raw_d.get(k), res_d.get(k), v) for k, v in new.items()}
    if isinstance(new, list):
        if isinstance(raw, list) and isinstance(resolved, list) and len(raw) == len(resolved) == len(new):
            return [_keep_placeholders(r, s, n) for r, s, n in zip(raw, resolved, new)]
        return [_keep_placeholders(None, None, n) for n in new]
    if raw is not None and resolved == new:
        return raw
    return unresolve_docs(new)


def _find_token(value) -> str | None:
    """A string that carries a SAS access token (it must never end up in a seed file)."""
    if isinstance(value, str):
        return value if "sig=" in value and ("sv=" in value or "se=" in value or "sp=" in value) else None
    if isinstance(value, dict):
        for v in value.values():
            hit = _find_token(v)
            if hit:
                return hit
    if isinstance(value, list):
        for v in value:
            hit = _find_token(v)
            if hit:
                return hit
    return None


def seed_from_database(dataset: Dataset, db_data: dict, today: date | None = None) -> tuple[dict, dict]:
    """The seed data that matches what is in the database now, and a summary of what differs from the old seed:
    {"added": n, "removed": n, "changed": n} (counted in records). Placeholders are kept where nothing changed."""
    if not any(db_data.values()):
        raise StoreError("The database is empty: there is nothing to write into the seed data")
    old_res = resolve_placeholders(dataset.data, today)
    new_seed = {}
    for collection, records in db_data.items():
        new_seed[collection] = {rid: _keep_placeholders(dataset.data.get(collection, {}).get(rid),
                                                        old_res.get(collection, {}).get(rid), rec)
                                for rid, rec in records.items()}
    leaked = _find_token(new_seed)
    if leaked:
        raise StoreError("A link in the database still carries an access token, and it would be written into the seed files. "
                         "Set EXT_DATA_DOCS_URL in .env to the address of the document container, so the links can be stored as {{docs:...}}.")
    validate_seed(new_seed)
    old_ids = {(c, rid) for c, recs in old_res.items() for rid in recs}
    new_ids = {(c, rid) for c, recs in db_data.items() for rid in recs}
    summary = {"added": len(new_ids - old_ids), "removed": len(old_ids - new_ids),
               "changed": sum(1 for c, rid in old_ids & new_ids if old_res[c][rid] != db_data[c][rid])}
    return new_seed, summary


def default_commit_message(dataset: Dataset, summary: dict, table: str = "") -> str:
    source = f" (from table {table})" if table else ""
    return (f"Update seed data: {dataset.title}{source}\n\n"
            f"{summary['added']} added, {summary['removed']} removed, {summary['changed']} changed records in datasets/{dataset.name}/data.json")


def update_seed_from_database(dataset: Dataset, db_data: dict, today: date | None = None, commit_message: str | None = None) -> dict:
    """Overwrite the config's data.json with what is in the database.

    With `commit_message` (and GitHub set up, see github_sync.py) the new file is first committed to the repository: if that
    fails nothing is written locally. The summary then also holds "commit_url" (None when the repository already had exactly
    this content). Without GitHub, the old local file is kept as data.backup.json (git history is the backup otherwise)."""
    new_seed, summary = seed_from_database(dataset, db_data, today)
    text = json.dumps(new_seed, ensure_ascii=False, indent=2) + "\n"
    target = dataset.path / "data.json"
    cfg = github_sync.get_config() if commit_message is not None else None
    if cfg:
        commit = github_sync.commit_files(cfg, {f"datasets/{dataset.name}/data.json": text}, commit_message)
        summary["commit_url"] = commit["url"] if commit else None
    try:
        if target.exists() and not cfg:
            (dataset.path / "data.backup.json").write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise StoreError(f"Could not write the seed data: {exc}") from exc
    return summary


# ---------------------------------------------------------------- checking
def validate_dataset(dataset: Dataset) -> list[str]:
    """Everything that could go wrong when loading or showing a config, as a list of readable problems."""
    problems: list[str] = []
    try:
        data = resolve_placeholders(dataset.data)
        validate_seed(data)
    except StoreError as exc:
        return [str(exc)]
    if dataset.default_table:
        try:
            check_table(dataset.default_table)
        except StoreError as exc:
            problems.append(str(exc))
    view = dataset.view or {}
    primary = view.get("primary")
    if primary and primary not in data:
        problems.append(f"view.json: primary collection {primary!r} is not in the data")
    for name, cfg in (view.get("collections") or {}).items():
        if name not in data:
            problems.append(f"view.json: collection {name!r} is not in the data")
            continue
        link = cfg.get("link")
        if link and primary:
            for rid, rec in data[name].items():
                target = rec.get(link)
                if target is not None and target not in data[primary]:
                    problems.append(f"{name}/{rid}: {link}={target!r} does not match any {primary} record")
    for name, records in data.items():
        for rid in records:
            try:
                check_key(rid, f"{name} id")
            except StoreError as exc:
                problems.append(str(exc))
    return problems
