"""Database demo config: browse the demo data of a config, update or reset it, add or delete configs, edit records.

    streamlit run app.py

Reads the same storage as the Teneo solution: Azure Table Storage when EXT_DATA_ACCOUNT_URL is
configured (environment variable or .env file), otherwise local files (one per table).
Each config has its own table (its `default_table`): pick the config in the sidebar and the table follows.
"""
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import datasets
import github_sync
import render
import store as storage
from store import META_COLLECTION, META_ID, StoreError, check_table, get_store

st.set_page_config(page_title="Database demo config", page_icon="🗄️", layout="wide")
st.markdown(render.CSS, unsafe_allow_html=True)
st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; max-width: 1250px; }
    .backend-pill { display:inline-block; padding:2px 10px; border-radius:999px; font-size:0.8rem; font-weight:600; }
    .backend-azure { background:#dbeafe; color:#1e40af; }
    .backend-local { background:#fef3c7; color:#92400e; }
    .backend-github { background:#e5e7eb; color:#1f2937; }
    </style>
    """,
    unsafe_allow_html=True,
)

NEW_DATASET_TEMPLATE = """{
  "customers": {
    "C001": {
      "name": "Alex Example",
      "email": "alex.example@example.com",
      "phone": "+441234567001",
      "plan": "Standard",
      "preferences": { "language": "en", "contact_by": "email" }
    }
  }
}"""


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


DEFAULT_DATASET = "banking"                                          # shown when the app opens (without ?config=... in the address)


# ---------------------------------------------------------------- storage
def _code_stamp() -> float:
    """Changes when store.py changes. A redeploy keeps the running process, so connections cached from the OLD code must not be reused."""
    return Path(storage.__file__).stat().st_mtime


@st.cache_resource
def _base_store(stamp: float):
    return get_store()


@st.cache_resource
def _store_for(table: str, stamp: float):
    return _base_store(stamp).with_table(table)


def base_store():
    return _base_store(_code_stamp())


def store_for(table: str):
    return _store_for(table, _code_stamp())


def repo_web_url(cfg) -> str:
    """The repository's address in the browser (github.com, or the web host of a GitHub Enterprise server)."""
    if cfg.api_url == github_sync.DEFAULT_API:
        return f"https://github.com/{cfg.repo}"
    return f"{cfg.api_url.removesuffix('/api/v3')}/{cfg.repo}"


def is_azure(store) -> bool:
    return hasattr(store, "account_url")                              # not isinstance(): the class can be a reloaded copy


def table_of(store) -> str:
    return store.table if is_azure(store) else store.path.stem


@st.cache_data(ttl=60, show_spinner=False)
def known_tables(version: int) -> list:
    return base_store().list_tables()


@st.cache_data(ttl=30, show_spinner="Reading data…")
def read_table(table: str, version: int, _store):
    data = {c: _store.all(c) for c in _store.collections()}
    return data, _store.get(META_COLLECTION, META_ID)


def done(message: str) -> None:
    """Confirm at the top of the next run, forget cached reads, and rerun so every tab shows the new data."""
    st.session_state["flash"] = message
    st.session_state["version"] += 1
    st.rerun()


def to_error(exc: Exception) -> None:
    st.error(str(exc))


def pretty_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y %H:%M UTC")
    except (TypeError, ValueError):
        return str(iso)


def counted(n: int, word: str) -> str:
    """1 record, 2 records."""
    return f"{n} {word}" + ("" if n == 1 else "s")


def stuck_folder_note(name: str) -> str:
    return (f" The empty folder datasets/{name} could not be removed because another program has it open (File Explorer, a terminal or OneDrive). "
            "It is no longer a config; it disappears by itself the next time the app starts once it is released, or you can delete it by hand.")


def suggest_table(name: str) -> str:
    """A valid table name for a new config, e.g. "ACME retail demo" -> demoacmeretaildemo."""
    return ("demo" + re.sub(r"[^a-z0-9]", "", name.lower()))[:40]


try:
    base = base_store()
except StoreError as exc:
    st.error(str(exc))
    st.stop()

st.session_state.setdefault("version", 0)

# ---------------------------------------------------------------- which dataset, and so which table
datasets.tidy_empty_folders()                                         # folders left behind by an earlier delete
if not st.session_state.get("github_pulled"):                         # at the start and on Refresh: datasets that are on GitHub but not here yet
    st.session_state["github_pulled"] = True
    st.session_state.pop("github_pull_error", None)
    try:
        pulled = datasets.pull_from_github()
        if pulled:
            st.session_state["flash"] = f"Loaded from GitHub: {', '.join(pulled)}"
    except StoreError as exc:
        st.session_state["github_pull_error"] = f"Could not read the datasets on GitHub: {exc}"
all_datasets = {d.name: d for d in datasets.list_datasets()}
if "_pick_next" in st.session_state:                                  # a dataset was just created: select it
    st.session_state["config_pick"] = st.session_state.pop("_pick_next")
if "config_pick" not in st.session_state and st.query_params.get("config") in all_datasets:
    st.session_state["config_pick"] = st.query_params["config"]      # kept in the address, so a page reload (theme switch) stays on it
if all_datasets and st.session_state.get("config_pick") not in all_datasets:
    usual = [n for n, d in all_datasets.items() if d.default_table == table_of(base)]
    st.session_state["config_pick"] = DEFAULT_DATASET if DEFAULT_DATASET in all_datasets else (usual[0] if usual else next(iter(all_datasets)))
picked = all_datasets.get(st.session_state.get("config_pick"))
if picked and st.query_params.get("config") != picked.name:
    st.query_params["config"] = picked.name

table = table_of(base)
if picked and picked.default_table:
    try:
        table = check_table(picked.default_table)
    except StoreError:
        pass

store, data, marker = None, {}, None
try:
    store = store_for(table)
    data, marker = read_table(table, st.session_state["version"], store)
except StoreError as exc:
    load_error = str(exc)
else:
    load_error = None

loaded = all_datasets.get(marker["dataset"]) if marker else None
view = (loaded or picked).view if (loaded or picked) else {}

# A table normally belongs to one dataset. Then a reset empties the WHOLE table, so collections left over from an older
# version of the dataset go too. If another dataset uses the same table, only this dataset's collections are touched.
sharing = datasets.others_using_table(table, picked.name) if picked else []
leftovers = [c for c in data if c not in picked.data] if (picked and not sharing) else []


# ---------------------------------------------------------------- pop-ups
@st.dialog("Reset database")
def reset_dialog():
    st.write(f"Are you sure you want to reset the database of **{picked.title}** to its seed data?")
    st.caption("The seed data itself is not changed. Only the Azure table is.")
    if sharing:
        st.caption(f"This deletes every record in the collections the config defines ({', '.join(picked.data)}) in the table "
                   f"**{table}** and loads the seed data again. Changes you made to those records are lost. "
                   f"The table is also used by {', '.join(sharing)}, so its other collections are left alone.")
    else:
        st.caption(f"This empties the whole table **{table}** and loads the seed data again. "
                   "Changes you made to the records are lost.")
        if leftovers:
            st.warning(f"The table also holds collections that are not part of this config any more: **{', '.join(leftovers)}**. "
                       "They are removed too.")
    yes, no = st.columns(2)
    if yes.button("Yes, reset", type="primary", width="stretch"):
        try:
            count = datasets.load_into(store, picked, reset=True, wipe_all=not sharing)
        except StoreError as exc:
            to_error(exc)
        else:
            done(f"Reset the Azure table {table} to the seed data of “{picked.title}”.")
    if no.button("Cancel", width="stretch"):
        st.rerun()


@st.dialog("Update seed data")
def seed_dialog():
    st.write(f"Overwrite the seed data of **{picked.title}** with what is in the database now?")
    st.caption(f"The seed data is the template in `datasets/{picked.name}/data.json`. The database is the table **{table}**. "
               "This copies the table into the seed data, so Reset and Update database will bring back these records from now on. "
               "The database itself is not changed.")
    try:
        new_seed, summary = datasets.seed_from_database(picked, data)
    except StoreError as exc:
        st.error(str(exc))
        if st.button("Close", width="stretch"):
            st.rerun()
        return
    st.markdown(f"What changes in the seed data: **{counted(summary['added'], 'record')}** added (new in the database), "
                f"**{counted(summary['removed'], 'record')}** removed (no longer in the database), "
                f"**{counted(summary['changed'], 'record')}** changed.")
    try:
        github = github_sync.get_config()
    except StoreError as exc:
        st.error(str(exc))
        if st.button("Close", width="stretch"):
            st.rerun()
        return
    st.caption("Dates written as `{{today-7d}}` stay that way where nothing changed. Links to documents are stored without their access token. "
               + (f"The change is committed to GitHub ({github.describe()}); the history there is the backup." if github
                  else "A copy of the current seed data is kept as `data.backup.json`."))
    if leftovers:
        st.warning(f"The table also holds collections that are not in the seed data: **{', '.join(leftovers)}**. They would be added to it. "
                   "If they are leftovers, cancel and use Reset database first.")
    message = None
    if github:
        message = st.text_area("Commit message", value=datasets.default_commit_message(picked, summary, table), height=130)
    yes, no = st.columns(2)
    if yes.button("Yes, commit to GitHub" if github else "Yes, overwrite the seed data", type="primary", width="stretch"):
        try:
            summary = datasets.update_seed_from_database(picked, data, commit_message=message)
        except StoreError as exc:
            to_error(exc)
        else:
            text = f"Updated the seed data of “{picked.title}”: {summary['added']} added, {summary['removed']} removed, {summary['changed']} changed."
            if github:
                url = summary.get("commit_url")
                text += f" Committed to {github.describe()}: {url}" if url else f" {github.repo} already had exactly this content, so no commit was made."
            done(text)
    if no.button("Cancel", width="stretch"):
        st.rerun()


@st.dialog("Delete config")
def delete_dialog():
    in_table = sum(len(data.get(c, {})) for c in (picked.data if sharing else data))
    st.write(f"Delete **{picked.title}**? There are two separate things, and you choose what goes:")
    st.markdown(f"- **The config** is the template: its seed data and display settings, kept in the folder `datasets/{picked.name}`. "
                f"**Update** and **Reset** load from it.")
    st.markdown(f"- **The data** is what is stored in the Azure table **{table}** right now. "
                f"That is what you see in the app and what Teneo reads.")
    if sharing:
        st.caption(f"The table {table} is also used by: {', '.join(sharing)}. Only the collections of {picked.title} are ever removed.")
    try:
        github = github_sync.get_config()
    except StoreError as exc:
        st.error(str(exc))
        return
    message = None
    if github:
        message = st.text_input("Commit message for GitHub", value=datasets.default_delete_message(picked).splitlines()[0],
                                help=f"The whole folder datasets/{picked.name} is deleted from {github.describe()} in one commit, before anything else is deleted. "
                                     "The history there lets you get it back.")
        git_note = f" and from GitHub ({github.repo})"
    else:
        git_note = ""

    def delete_in_github():
        """The folder goes from the repository first: that can be undone from the history, deleting data in Azure cannot."""
        commit = datasets.commit_delete(picked.name, message or datasets.default_delete_message(picked)) if github else None
        return f" Removed from GitHub: {commit['url']}" if commit else ""

    with st.container(border=True):
        st.markdown("**Delete the config, keep the data**")
        st.caption(f"Removes the folder `datasets/{picked.name}`{git_note}. The Azure table {table} and its data stay, and Teneo keeps working with it. "
                   "You can no longer Update or Reset them from here.")
        if st.button("Delete config, keep the data", width="stretch"):
            try:
                git_text = delete_in_github()
                folder_gone = datasets.delete_dataset(picked.name)
            except StoreError as exc:
                to_error(exc)
            else:
                st.session_state.pop("config_pick", None)
                done(f"Deleted the config “{picked.title}”. The data in {table} was kept.{git_text}" + ("" if folder_gone else stuck_folder_note(picked.name)))
    with st.container(border=True):
        st.markdown("**Delete the config and the data**")
        owns_table = bool(picked.default_table) and picked.default_table == table and not sharing     # only then may the table itself go
        if owns_table:
            st.caption(f"Removes the folder `datasets/{picked.name}`{git_note} and deletes the Azure table **{table}** with all its data. "
                       "Teneo will find nothing there until you load other data.")
        else:
            why = f"because {', '.join(sharing)} uses it too" if sharing else "because it is not this config's own table"
            st.caption(f"Removes the folder `datasets/{picked.name}`{git_note} and removes this config's data from the Azure table {table}. "
                       f"The table itself stays, {why}.")
        if st.button("Delete config and the data", type="primary", width="stretch"):
            step = "GitHub"
            try:
                git_text = delete_in_github()
                step = "the data"
                if owns_table:
                    store.delete_table()                                               # the whole table, not only its records
                else:
                    store.wipe_collections(list(picked.data))
                    store.wipe_collections([META_COLLECTION])                          # the note about what was loaded: not a record of the dataset
                step = "the config folder"
                folder_gone = datasets.delete_dataset(picked.name)
            except StoreError as exc:
                if step == "GitHub":
                    st.error(f"The folder could not be deleted from GitHub, so nothing was deleted: {exc}")
                elif step == "the data":
                    st.error(f"The data in {table} could not be deleted. The config was already removed from GitHub (get it back from the history there) "
                             f"but is still here; nothing else was deleted: {exc}")
                else:
                    st.error(f"The data was deleted, but the config folder could not be removed: {exc}")
            else:
                st.session_state.pop("config_pick", None)
                note = git_text + ("" if folder_gone else stuck_folder_note(picked.name))
                if owns_table:
                    done(f"Deleted the config “{picked.title}” and removed the Azure table {table} with all its data." + note)
                else:
                    done(f"Deleted the config “{picked.title}” and removed its data from the Azure table {table}. The table itself stays." + note)
    st.caption("Deleting cannot be undone.")
    if st.button("Cancel", width="stretch"):
        st.rerun()


@st.dialog("Delete table")
def orphan_dialog(name: str):
    target = store_for(name)
    try:
        collections = target.collections()
        records = sum(len(target.all(c)) for c in collections)
    except StoreError as exc:
        to_error(exc)
        return
    st.write(f"Delete the table **{name}**?")
    st.warning(f"It holds {counted(records, 'record')} in {counted(len(collections), 'collection')}. The table and everything in it "
               "is removed for good. No config uses this table.")
    st.caption("Make sure no Teneo solution points at it (its `ext_data_table` secret).")
    yes, no = st.columns(2)
    if yes.button("Yes, delete the table", type="primary", width="stretch"):
        try:
            target.delete_table()
        except StoreError as exc:
            to_error(exc)
        else:
            done(f"Removed the Azure table {name} with all its data.")
    if no.button("Cancel", width="stretch"):
        st.rerun()


def fill_new_json() -> None:
    """When "start from" changes: put that config's data into the JSON box."""
    source = st.session_state.get("new_ds_source")
    if source in all_datasets:
        st.session_state["new_ds_json"] = (all_datasets[source].path / "data.json").read_text(encoding="utf-8")
    else:
        st.session_state["new_ds_json"] = NEW_DATASET_TEMPLATE


@st.dialog("Add New Dataset", width="large")
def add_dialog():
    st.caption("Create a config from JSON: write or paste your own, or start from a copy of an existing one. "
               "It gets its own table, which is created when the data is written.")
    st.session_state.setdefault("new_ds_json", NEW_DATASET_TEMPLATE)
    name = st.text_input("Name", placeholder="e.g. ACME retail demo", key="new_ds_name")
    description = st.text_input("Description (optional)", key="new_ds_description")
    st.selectbox("Start from", ["__empty__"] + list(all_datasets), key="new_ds_source", on_change=fill_new_json,
                 format_func=lambda n: "An empty template" if n == "__empty__" else f"A copy of {all_datasets[n].icon} {all_datasets[n].title}")
    table_name = st.text_input("Table", value=suggest_table(name) if name.strip() else "", key=f"new_ds_table_{datasets.slugify(name) if name.strip() else ''}",
                               placeholder="e.g. demoacme", help="3-63 letters and digits, starting with a letter.")
    text = st.text_area("Data (JSON): {collection: {record id: record}}", height=320, key="new_ds_json")

    problems = []
    parsed = None
    if not name.strip():
        problems.append("Give the config a name.")
    else:
        slug = datasets.slugify(name)
        if slug in all_datasets:
            problems.append(f"A config called “{slug}” already exists.")
    try:
        check_table(table_name)
    except StoreError as exc:
        problems.append(str(exc))
    try:
        parsed = json.loads(text)
        candidate = datasets.Dataset(name="new", title=name or "new", data=parsed, view={})
        problems += datasets.validate_dataset(candidate)
    except json.JSONDecodeError as exc:
        problems.append(f"The data is not valid JSON: {exc}")
    if problems:
        st.error("\n\n".join(problems[:6]))
    else:
        st.caption(f"✅ {counted(sum(len(r) for r in parsed.values()), 'record')} in {counted(len(parsed), 'collection')}")
        used = [d.title for d in all_datasets.values() if d.default_table == table_name]
        if used:
            st.warning(f"The table {table_name} is also used by: {', '.join(used)}.")

    if st.button("Create config", type="primary", disabled=bool(problems)):
        source = st.session_state.get("new_ds_source")
        origin = all_datasets.get(source)
        try:
            ds = datasets.create_dataset(name, description, table_name, parsed, view=origin.view if origin else None,
                                         icon=origin.icon if origin else "🗂️")
            count = datasets.load_into(store_for(table_name), ds)
        except StoreError as exc:
            to_error(exc)
        else:
            for key in ("new_ds_name", "new_ds_description", "new_ds_json", "new_ds_source"):
                st.session_state.pop(key, None)
            st.session_state["_pick_next"] = ds.name
            done(f"Created the config “{ds.title}” and loaded its data into the new Azure table {table_name}.")


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("Database demo config")
    st.subheader("Datasets")
    if "github_pull_error" in st.session_state:
        st.warning(st.session_state["github_pull_error"])
    if all_datasets:
        st.selectbox("Config", list(all_datasets), key="config_pick", label_visibility="collapsed",
                     format_func=lambda n: f"{all_datasets[n].icon} {all_datasets[n].title}")
        st.caption(picked.description)
        st.caption(f"**Seed data:** {counted(picked.n_records, 'record')} in {counted(len(picked.data), 'collection')} "
                   f"(`datasets/{picked.name}`)")
        if load_error:
            st.error(load_error)
        elif marker:
            st.caption(f"**Database:** table {table}, {counted(sum(len(r) for r in data.values()), 'record')}, loaded {pretty_time(marker.get('loaded_at'))}")
        elif data:
            st.caption(f"**Database:** table {table}, holds data that was not loaded from a config here")
        else:
            st.caption(f"**Database:** table {table}, empty")
        if leftovers:
            st.warning(f"The table also holds collections that are not part of this config any more: {', '.join(leftovers)}. "
                       "**Reset database** removes them.")
        if st.button("🔄 Refresh database", width="stretch",
                     help="Read the table again, and load the datasets that are on GitHub but not here yet (existing ones are not changed)."):
            st.session_state["version"] += 1
            st.session_state["github_pulled"] = False
            st.rerun()
        if st.button("⬆️ Update database", width="stretch", disabled=bool(load_error),
                     help="Seed data → database. Writes the seed records into the Azure table, adding or overwriting. Records you added yourself stay."):
            try:
                datasets.load_into(store, picked)
                done(f"Updated the Azure table {table} with the seed data of “{picked.title}”.")
            except StoreError as exc:
                to_error(exc)
        if st.button("📥 Update seed data", width="stretch", disabled=bool(load_error) or not data,
                     help="Database → seed data. Overwrites the config's seed data with what is in the table now. Asks first."):
            seed_dialog()
        if st.button("♻️ Reset database", width="stretch", disabled=bool(load_error),
                     help="Puts the Azure table back to the seed data (the seed data itself is not changed). Asks first."):
            reset_dialog()
        if st.button("🗑️ Delete config", width="stretch", disabled=bool(load_error), help="Removes the config. Asks first."):
            delete_dialog()
    else:
        st.info("There are no datasets yet. Add one with Add New Dataset below.")

    st.divider()
    st.subheader("Add New Dataset")
    st.caption("Create one from JSON, or start from a copy of an existing one.")
    if st.button("➕ Add New Dataset", width="stretch"):
        add_dialog()

    st.divider()
    st.subheader("Storage Connections")
    kind = "azure" if is_azure(base) else "local"
    label = "Azure Table Storage" if kind == "azure" else "Local files (no Azure configured)"
    html(f'<span class="backend-pill backend-{kind}">{label}</span>')
    if kind == "azure":
        st.caption(base.account_url)
        if st.button("Test Azure connection", width="stretch"):
            try:
                st.success(store.ping() if store else base.ping())
            except StoreError as exc:
                to_error(exc)
    try:
        gh_config = github_sync.get_config()
    except StoreError as exc:
        gh_config = None
        st.session_state["_gh_error"] = True
        st.warning(str(exc))
    else:
        st.session_state["_gh_error"] = False
    if gh_config:
        html('<span class="backend-pill backend-github">GitHub</span>')
        link = repo_web_url(gh_config)
        st.caption(f"[{link}]({link}) ({gh_config.branch})")
        if st.button("Test GitHub connection", width="stretch"):
            try:
                st.success(github_sync.check_access(gh_config))
            except StoreError as exc:
                to_error(exc)
    elif not st.session_state.get("_gh_error"):
        html('<span class="backend-pill backend-local">GitHub not set up</span>')

    st.divider()
    if "dark_mode" not in st.session_state:                           # start from what the page shows now
        st.session_state["dark_mode"] = st.context.theme.get("type") == "dark"
    st.toggle("🌙 Dark mode", key="dark_mode", on_change=lambda: st.session_state.update(_apply_theme=st.session_state["dark_mode"]),
              help="Switches between the light and the dark look. The page reloads once; your choice is remembered in this browser.")
    if "_apply_theme" in st.session_state:                            # Streamlit has no call for this: it keeps the choice in the browser
        name = "Dark" if st.session_state.pop("_apply_theme") else "Light"
        st.html("<script>"
                "const keys = Object.keys(localStorage).filter(k => k.startsWith('stActiveTheme-'));"
                f"(keys.length ? keys : ['stActiveTheme-/-v2']).forEach(k => localStorage.setItem(k, JSON.stringify('{name}')));"
                "window.location.reload();</script>", unsafe_allow_javascript=True)

if not picked:
    st.info("Add a dataset in the sidebar to get started.")
    st.stop()
if load_error:
    st.error(load_error)
    st.stop()

# ---------------------------------------------------------------- header
st.markdown(f"## {picked.icon} {picked.title}")
if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))

tab_browse, tab_tables, tab_edit, tab_datasets = st.tabs(["👁️ Browse", "📋 Tables", "✏️ Edit records", "🗂️ Configs"])

# ---------------------------------------------------------------- browse
with tab_browse:
    if not data:
        st.info("This table is empty. Press **Update database** in the sidebar.")
    else:
        primary = render.primary_of(data, view)
        order = [primary] + [c for c in data if c != primary]
        cfgs = {c: render.collection_config(c, view) for c in order}
        if st.session_state.get("browse_collection") not in order:
            st.session_state["browse_collection"] = primary
        selected = st.pills("Collection", order, selection_mode="single", key="browse_collection",
                            format_func=lambda c: f"{cfgs[c]['icon']} {cfgs[c]['label']} ({len(data[c])})", label_visibility="collapsed")
        if selected is None:                                          # a click on the chosen button un-chooses it: choose the main one again
            st.rerun()
        query = st.text_input("Search", placeholder="🔍 Search names, ids, anything…", key="browse_search", label_visibility="collapsed").strip().lower()
        links = {c: render.link_field(c, data[c], primary, view) for c in order if c != primary}

        def related_text(pid: str) -> str:
            return " ".join(render.record_text(rid, rec) for c, link in links.items() if link
                            for rid, rec in data[c].items() if rec.get(link) == pid)

        if selected == primary:
            matches = {rid: rec for rid, rec in data[primary].items()
                       if not query or query in render.record_text(rid, rec) or query in related_text(rid)}
            if not matches:
                st.info("Nothing matches your search.")
            else:
                pcfg = cfgs[primary]
                pid = st.selectbox(pcfg["label_one"], list(matches), key=f"pick_{table}",
                                   format_func=lambda rid: f"{render.title_of(rid, matches[rid], pcfg)[0]}  ·  {rid}")
                html(render.profile_html(primary, pid, matches[pid], view) + render.related_html(primary, pid, data, view))
        else:
            link = links.get(selected)
            owners = {rid: f"{cfgs[primary]['icon']} {render.title_of(rid, rec, cfgs[primary])[0]}" for rid, rec in data[primary].items()}
            matches = {rid: rec for rid, rec in data[selected].items() if not query or query in render.record_text(rid, rec)}
            if not matches:
                st.info("Nothing matches your search.")
            for rid, rec in list(matches.items())[:60]:
                html(render.record_card(selected, rid, rec, view, link=link, owner=owners.get(rec.get(link)) if link else None))
            if len(matches) > 60:
                st.caption(f"Showing the first 60 of {len(matches)}. Use the search to narrow it down.")

        with st.expander("Raw JSON of everything in this table"):
            st.json(data)

    st.caption(f"Table: **{table}**")                                  # the very end of the page

# ---------------------------------------------------------------- tables
with tab_tables:
    if not data:
        st.info("Nothing to show yet.")
    else:
        names = list(data)
        collection = st.selectbox("Collection", names, key="tables_collection",
                                  format_func=lambda c: f"{render.collection_config(c, view)['icon']} {c} ({len(data[c])})")
        rows = []
        for rid, rec in data[collection].items():
            rows.append({"id": rid, **{k: (v if isinstance(v, (str, int, float, bool)) or v is None else json.dumps(v, ensure_ascii=False))
                                       for k, v in rec.items()}})
        frame = pd.DataFrame(rows)
        link_columns = {}
        if "doc_url" in frame:                                              # show the document id, not the whole link
            link_columns["doc_url"] = st.column_config.LinkColumn("doc_url", display_text=r"/(DOC[A-Za-z0-9_-]+)\.pdf")
        if "documents_page_url" in frame:
            link_columns["documents_page_url"] = st.column_config.LinkColumn("documents_page_url", display_text="Documents page")
        st.dataframe(frame, width="stretch", hide_index=True, column_config=link_columns)

# ---------------------------------------------------------------- edit
with tab_edit:
    st.caption("A record is a JSON object. Saving replaces the whole record; Teneo sees the change on its next read.")
    left, right = st.columns([1, 2])
    names = list(data)
    with left:
        mode = st.radio("What do you want to do?", ["Edit a record", "Add a record"])
        if mode == "Edit a record":
            if not names:
                st.info("Nothing to edit yet.")
                collection, record_id, current = None, None, {}
            else:
                collection = st.selectbox("Collection", names, key="edit_collection")
                record_id = st.selectbox("Record id", list(data[collection]), key=f"edit_id_{table}_{collection}")
                current = data[collection].get(record_id, {}) if record_id else {}
        else:
            collection = st.text_input("Collection", value=names[0] if names else "customers", key="new_collection")
            record_id = st.text_input("New record id", placeholder="e.g. C010", key="new_id")
            current = {}
    with right:
        if collection and (record_id or mode == "Add a record"):
            editor_key = f"editor_{table}_{mode}_{collection}_{record_id}_{st.session_state['version']}"
            text = st.text_area("Record (JSON)", value=json.dumps(current or {"name": ""}, ensure_ascii=False, indent=2), height=380, key=editor_key)
            save_col, delete_col, _ = st.columns([1, 1, 3])
            if save_col.button("💾 Save", type="primary", key="save_record"):
                try:
                    if not record_id:
                        raise StoreError("Enter a record id first")
                    store.put(collection, record_id, json.loads(text))
                    done(f"Saved {collection}/{record_id}")
                except json.JSONDecodeError as exc:
                    st.error(f"Not valid JSON: {exc}")
                except StoreError as exc:
                    to_error(exc)
            if mode == "Edit a record" and delete_col.button("🗑️ Delete", key="delete_record"):
                try:
                    store.delete(collection, record_id)
                    done(f"Deleted {collection}/{record_id}")
                except StoreError as exc:
                    to_error(exc)

# ---------------------------------------------------------------- datasets
with tab_datasets:
    st.caption("A config is a folder in `datasets/` with `config.json`, `data.json` and `view.json`. "
               "Update, reset, add or delete configs in the sidebar, or from the command line with `python seed.py <name> --table <table>`.")
    st.dataframe(pd.DataFrame([{"": d.icon, "Config": d.title, "Folder": d.name, "Records": d.n_records, "Collections": ", ".join(d.data),
                                "Table": d.default_table, "About": d.description} for d in all_datasets.values()]),
                 width="stretch", hide_index=True)

    st.subheader("Tables without a config")
    used_tables = {d.default_table for d in all_datasets.values()}
    try:
        orphans = [t for t in known_tables(st.session_state["version"]) if t not in used_tables]
    except StoreError as exc:
        orphans = None
        st.caption(f"The list of tables could not be read ({exc}). The token needs the List permission.")
    if orphans is not None:
        if not orphans:
            st.caption("Every table in the storage belongs to a config.")
        else:
            st.caption("These tables exist in the storage, but no config points at them, for example because the config was deleted "
                       "and the table was kept.")
            orphan = st.selectbox("Table", orphans, key="orphan_table", label_visibility="collapsed")
            if st.button("🗑️ Delete this table…", key="orphan_delete"):
                orphan_dialog(orphan)

    st.subheader("Edit a config's data")
    edit_name = st.selectbox("Config", list(all_datasets), key="edit_config",
                             format_func=lambda n: f"{all_datasets[n].icon} {all_datasets[n].title}")
    ds = all_datasets[edit_name]
    if ds.notes:
        with st.expander(f"📝 Demo notes: {ds.title}", expanded=True):
            st.text(ds.notes)
    raw = (ds.path / "data.json").read_text(encoding="utf-8")
    edited = st.text_area("data.json", value=raw, height=380, key=f"data_editor_{edit_name}_{st.session_state['version']}")
    st.caption("Dates like `{{today-7d}}` are filled in when the config is loaded. Editing the file changes the config for everyone who uses this copy of the template.")
    try:
        parsed = json.loads(edited)
        candidate = datasets.Dataset(name=ds.name, title=ds.title, data=parsed, view=ds.view, default_table=ds.default_table)
        problems = datasets.validate_dataset(candidate)
    except json.JSONDecodeError as exc:
        parsed, problems = None, [f"Not valid JSON: {exc}"]
    if problems:
        st.error("\n\n".join(problems[:8]))
    elif parsed is not None:
        st.caption(f"✅ Valid: {counted(sum(len(r) for r in parsed.values()), 'record')} in {counted(len(parsed), 'collection')}")
    if st.button("💾 Save data.json", disabled=bool(problems) or parsed is None):
        try:
            datasets.save_data(ds, parsed)
            done(f"Saved datasets/{ds.name}/data.json")
        except StoreError as exc:
            to_error(exc)

    st.subheader("Save this table as a new config")
    st.caption(f"Takes a snapshot of everything currently in **{table}** (your tweaks included) so you can reload it later, e.g. one per customer demo.")
    snap_title = st.text_input("Name of the new config", placeholder="e.g. ACME retail demo", key="snap_title")
    snap_desc = st.text_input("Description (optional)", key="snap_desc")
    if st.button("📸 Save snapshot", disabled=not (snap_title.strip() and data)):
        try:
            snap = datasets.save_snapshot(store, snap_title, snap_desc, loaded or picked, table)
            done(f"Saved config “{snap.title}” (datasets/{snap.name}). Pick it in the sidebar to reload it.")
        except StoreError as exc:
            to_error(exc)
