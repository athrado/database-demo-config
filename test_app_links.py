"""The app opened through a link such as ?config=banking&collection=transactions&ids=TX1001,TX1002 shows just those records.

    python test_app_links.py
"""
import os
import tempfile
from pathlib import Path

folder = Path(tempfile.mkdtemp())
os.environ["EXT_DATA_LOCAL_FILE"] = str(folder / "x.json")
for key in ("EXT_DATA_ACCOUNT_URL", "EXT_DATA_SAS_TOKEN", "EXT_DATA_TABLE", "GITHUB_TOKEN", "GITHUB_REPO", "EXT_DATA_DOCS_URL", "EXT_DATA_DOCS_SAS"):
    os.environ[key] = ""                                  # local files only, never a real account

import datasets
import store
from streamlit.testing.v1 import AppTest

datasets.load_into(store.LocalStore(folder / "demobanking.json"), datasets.get_dataset("banking"))
TX = ("TX1001", "TX1002", "TX1003", "TX1004", "TX1005")


def open_app(**params):
    at = AppTest.from_file("app.py", default_timeout=90)
    at.query_params["config"] = "banking"
    for name, value in params.items():
        at.query_params[name] = value
    at.run()
    assert not at.exception, at.exception
    cards = [m.value for m in at.markdown if 'class="xd-card' in m.value]
    return at, [t for t in TX if any(t in c for c in cards)]


def test_a_link_shows_only_the_records_it_names():
    at, shown = open_app(collection="transactions", ids="TX1001,TX1002,NOPE")
    assert shown == ["TX1001", "TX1002"]                                                  # the unknown id is ignored
    assert "Showing 2 of the transactions" in " ".join(i.value for i in at.info)
    assert at.session_state["browse_collection"] == "transactions"                       # it opens on that collection


def test_a_link_to_records_that_do_not_exist_shows_none():
    at, shown = open_app(collection="transactions", ids="NOPE")
    assert shown == [] and "Showing none of the transactions" in " ".join(i.value for i in at.info)


def test_without_a_link_the_page_is_unchanged():
    at, shown = open_app()
    assert shown == [] and not [b for b in at.button if b.label.startswith("Show all")]
    assert at.session_state["browse_collection"] == "customers"


def test_a_link_with_a_customer_shows_only_that_customers_records():
    at, _ = open_app(collection="password_requests", customer="C005")
    cards = [m.value for m in at.markdown if 'class="xd-card' in m.value]
    assert len(cards) == 1 and "PR6001" in cards[0]                                       # Francesca's one request, not the others
    assert "Showing 1 of the" in " ".join(i.value for i in at.info)
    at, _ = open_app(collection="password_requests", customer="C002")
    assert not [m for m in at.markdown if 'class="xd-card' in m.value] and "Showing none of the" in " ".join(i.value for i in at.info)
    at, _ = open_app(collection="password_requests", customer="C004", ids="PR6002")
    cards = [m.value for m in at.markdown if 'class="xd-card' in m.value]
    assert len(cards) == 1 and "PR6002" in cards[0]                                       # ids and customer combine


def test_the_clean_view_is_a_page_for_the_customer():
    at, shown = open_app(collection="transactions", customer="C004", ids="TX1001,TX1002", view="clean")
    assert shown == ["TX1001", "TX1002"]
    text = " ".join(m.value for m in at.markdown)
    assert "Your transactions" in text and "Banco BPM" in text and "Alessandro Conti" in text
    assert not at.sidebar.button and not at.sidebar.markdown and not at.tabs                # nothing of the database app
    assert not at.info and not [b for b in at.button if b.label.startswith("Show all")]
    at, shown = open_app(collection="password_requests", customer="C005", view="clean")     # a customer's own records of a collection
    assert "Your password requests" in " ".join(m.value for m in at.markdown) and "PR6001" in " ".join(m.value for m in at.markdown)


def test_show_all_removes_the_restriction():
    at, _ = open_app(collection="transactions", ids="TX1001")
    {b.label: b for b in at.button}["Show all transactions"].click()
    at.run()
    assert not at.exception and "ids" not in at.query_params and "collection" not in at.query_params
    cards = [m.value for m in at.markdown if 'class="xd-card' in m.value]
    assert len([t for t in TX if any(t in c for c in cards)]) > 1                      # all transactions again


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
