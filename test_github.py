"""Tests for github_sync.py and "Update seed data" with a commit, against a small fake GitHub on localhost.

    python test_github.py
"""
import hashlib
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ["GITHUB_TOKEN"] = ""                      # an empty value (not a missing one) keeps a private .env from filling it in
os.environ["GITHUB_REPO"] = ""
os.environ["EXT_DATA_DOCS_URL"] = ""
os.environ["EXT_DATA_DOCS_SAS"] = ""

import datasets
import github_sync
from store import LocalStore, StoreError

REPO = "exemplario/demo"


def sha(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


class FakeGitHub:
    """Just enough of the Git Data API: refs, commits, trees (flat path -> blob), blobs."""

    def __init__(self):
        self.blobs = {}
        self.trees = {}                              # tree sha -> {path: blob sha}
        self.commits = {}                            # commit sha -> {"tree", "parents", "message"}
        self.branch = "main"
        self.token = "good-token"
        self.reject_next_patch = False               # pretend somebody pushed in between
        self.requests = []
        files = {"README.md": "hello\n", "datasets/demo/data.json": "{}\n"}
        self.head = self._commit(self._tree({p: self._blob(t) for p, t in files.items()}), [], "first")

    def _blob(self, text):
        s = sha("blob" + text)
        self.blobs[s] = text
        return s

    def _tree(self, entries):
        s = sha("tree" + json.dumps(entries, sort_keys=True))
        self.trees[s] = dict(entries)
        return s

    def _commit(self, tree, parents, message):
        s = sha("commit" + tree + "".join(parents) + message + str(len(self.commits)))
        self.commits[s] = {"tree": tree, "parents": parents, "message": message}
        return s

    def files(self):
        return {p: self.blobs[b] for p, b in self.trees[self.commits[self.head]["tree"]].items()}

    def handle(self, method, path, body, auth):
        self.requests.append((method, path))
        path = path.split("?")[0]
        if auth != f"Bearer {self.token}":
            return 401, {"message": "Bad credentials"}
        if path.endswith("/"):
            return 404, {"message": "Not Found"}                       # the real GitHub does this for a trailing slash
        prefix = f"/repos/{REPO}"
        if not path.startswith(prefix):
            return 404, {"message": "Not Found"}
        rest = path[len(prefix):].strip("/")
        if rest == "" and method == "GET":
            return 200, {"full_name": REPO, "permissions": {"push": True}}
        if rest == f"git/ref/heads/{self.branch}" and method == "GET":
            return 200, {"object": {"sha": self.head}}
        if rest.startswith("git/ref/heads/"):
            return 404, {"message": "Not Found"}
        if rest.startswith("git/commits/") and method == "GET":
            return 200, {"tree": {"sha": self.commits[rest.split("/")[-1]]["tree"]}}
        if rest.startswith("git/trees/") and method == "GET":
            tree = self.trees[rest.split("/")[2]]
            return 200, {"truncated": False, "tree": [{"path": p, "type": "blob", "sha": b, "size": len(self.blobs[b].encode())} for p, b in tree.items()]}
        if rest.startswith("git/blobs/") and method == "GET":
            import base64
            return 200, {"encoding": "base64", "content": base64.b64encode(self.blobs[rest.split("/")[2]].encode()).decode()}
        if rest == "git/blobs" and method == "POST":
            return 201, {"sha": self._blob(body["content"])}
        if rest == "git/trees" and method == "POST":
            tree = dict(self.trees[body["base_tree"]])
            for e in body["tree"]:
                if e["sha"] is None:
                    tree.pop(e["path"], None)
                else:
                    tree[e["path"]] = e["sha"]
            return 201, {"sha": self._tree(tree)}
        if rest == "git/commits" and method == "POST":
            return 201, {"sha": self._commit(body["tree"], body["parents"], body["message"])}
        if rest == f"git/refs/heads/{self.branch}" and method == "PATCH":
            if self.reject_next_patch:
                self.reject_next_patch = False
                other = self._commit(self._tree({**self.trees[self.commits[self.head]["tree"]], "other.txt": self._blob("x")}), [self.head], "someone else")
                self.head = other
                return 422, {"message": "Update is not a fast forward"}
            if self.commits[body["sha"]]["parents"] != [self.head]:
                return 422, {"message": "Update is not a fast forward"}
            self.head = body["sha"]
            return 200, {}
        return 404, {"message": "Not Found"}


def serve(fake):
    class Handler(BaseHTTPRequestHandler):
        def _go(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n)) if n else None
            status, payload = fake.handle(self.command, self.path, body, self.headers.get("Authorization"))
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PATCH = _go

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def config(server, token="good-token", repo=REPO):
    return github_sync.GitHubConfig(token, repo, "main", f"http://127.0.0.1:{server.server_port}")


def expect_error(fn, *needles):
    try:
        fn()
    except StoreError as exc:
        for needle in needles:
            assert needle in str(exc), (needle, str(exc))
        return str(exc)
    raise AssertionError("expected a StoreError")


# ---------------------------------------------------------------- settings
def test_settings_come_from_the_environment():
    saved = {k: os.environ.get(k) for k in ("GITHUB_TOKEN", "GITHUB_REPO", "GITHUB_BRANCH")}
    try:
        os.environ["GITHUB_TOKEN"] = os.environ["GITHUB_REPO"] = ""
        assert github_sync.get_config() is None                                   # not set up: only local files
        os.environ["GITHUB_TOKEN"] = "t"
        expect_error(github_sync.get_config, "half set up")
        os.environ["GITHUB_REPO"] = "not a repo"
        expect_error(github_sync.get_config, "owner/name")
        os.environ["GITHUB_REPO"] = "/exemplario/demo/"
        os.environ["GITHUB_BRANCH"] = ""
        cfg = github_sync.get_config()
        assert (cfg.repo, cfg.branch, cfg.api_url) == ("exemplario/demo", "main", "https://api.github.com")
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


# ---------------------------------------------------------------- commits
def test_one_commit_changes_and_deletes_files():
    fake = FakeGitHub()
    server = serve(fake)
    first = fake.head
    result = github_sync.commit_files(config(server), {"datasets/demo/data.json": '{"a": 1}\n', "new/file.txt": "n\n", "README.md": None}, "Change things")
    assert result["url"] == f"https://github.com/{REPO}/commit/{fake.head}" and fake.head != first
    assert fake.files() == {"datasets/demo/data.json": '{"a": 1}\n', "new/file.txt": "n\n"}
    assert fake.commits[fake.head]["message"] == "Change things" and fake.commits[fake.head]["parents"] == [first]


def test_nothing_is_committed_when_nothing_changed():
    fake = FakeGitHub()
    server = serve(fake)
    before = fake.head
    assert github_sync.commit_files(config(server), {"README.md": "hello\n"}, "No change") is None
    assert fake.head == before
    assert github_sync.commit_files(config(server), {}, "Empty") is None
    expect_error(lambda: github_sync.commit_files(config(server), {"README.md": "x"}, "  "), "empty")


def test_a_push_in_between_is_retried_once():
    fake = FakeGitHub()
    server = serve(fake)
    fake.reject_next_patch = True
    result = github_sync.commit_files(config(server), {"README.md": "mine\n"}, "Mine")
    assert result and fake.files()["README.md"] == "mine\n" and fake.files()["other.txt"] == "x"    # on top of the other commit


def test_errors_say_what_to_do():
    fake = FakeGitHub()
    server = serve(fake)
    expect_error(lambda: github_sync.commit_files(config(server, token="bad"), {"a": "b"}, "m"), "HTTP 401", "token is wrong")
    expect_error(lambda: github_sync.commit_files(config(server, repo="exemplario/other"), {"a": "b"}, "m"), "HTTP 404", "exemplario/other")
    cfg = config(server)
    cfg.branch = "nope"
    expect_error(lambda: github_sync.commit_files(cfg, {"a": "b"}, "m"), "HTTP 404", "nope")
    expect_error(lambda: github_sync.commit_files(github_sync.GitHubConfig("t", REPO, "main", "http://127.0.0.1:9"), {"a": "b"}, "m"), "Cannot reach GitHub")
    assert "good-token" not in "".join(str(r) for r in fake.requests)               # the token is only ever in the header
    assert github_sync.check_access(config(server)).startswith("Connected to exemplario/demo (main)")


# ---------------------------------------------------------------- "Update seed data"
def make_dataset(tmp):
    folder = Path(tmp, "demo")
    folder.mkdir()
    (folder / "config.json").write_text(json.dumps({"title": "Demo", "default_table": "demodemo"}), encoding="utf-8")
    (folder / "data.json").write_text(json.dumps({"customers": {"C1": {"name": "Anna"}}}), encoding="utf-8")
    return folder


def test_update_seed_data_commits_first_then_writes_the_local_file():
    fake = FakeGitHub()
    server = serve(fake)
    saved = {k: os.environ.get(k) for k in ("GITHUB_TOKEN", "GITHUB_REPO", "GITHUB_API_URL")}
    original = datasets.DATASET_DIR
    with tempfile.TemporaryDirectory() as tmp:
        datasets.DATASET_DIR = Path(tmp)
        try:
            folder = make_dataset(tmp)
            ds = datasets.load_dataset(folder)
            db = {"customers": {"C1": {"name": "Anna"}, "C2": {"name": "Berta"}}}
            os.environ.update(GITHUB_TOKEN="good-token", GITHUB_REPO=REPO, GITHUB_API_URL=f"http://127.0.0.1:{server.server_port}")

            summary = datasets.update_seed_from_database(ds, db, commit_message=datasets.default_commit_message(ds, {"added": 1, "removed": 0, "changed": 0}, "demodemo"))
            assert summary["added"] == 1 and summary["commit_url"].endswith(fake.head)
            committed = json.loads(fake.files()["datasets/demo/data.json"])
            assert committed == db and (folder / "data.json").read_text(encoding="utf-8") == fake.files()["datasets/demo/data.json"]
            assert fake.commits[fake.head]["message"].startswith("Update seed data: Demo (from table demodemo)")
            assert not (folder / "data.backup.json").exists()                        # the git history is the backup

            again = datasets.update_seed_from_database(datasets.load_dataset(folder), db, commit_message="Same again")
            assert again["commit_url"] is None                                         # nothing differs: no empty commit

            os.environ["GITHUB_TOKEN"] = "wrong"                                       # a failed commit leaves the local file alone
            before = (folder / "data.json").read_text(encoding="utf-8")
            expect_error(lambda: datasets.update_seed_from_database(datasets.load_dataset(folder), {"customers": {"C9": {"name": "Z"}}}, commit_message="x"), "HTTP 401")
            assert (folder / "data.json").read_text(encoding="utf-8") == before

            os.environ.update(GITHUB_TOKEN="", GITHUB_REPO="")                         # no GitHub: local file + backup, as before
            local = datasets.update_seed_from_database(datasets.load_dataset(folder), {"customers": {"C9": {"name": "Z"}}}, commit_message="ignored")
            assert "commit_url" not in local and (folder / "data.backup.json").exists()
        finally:
            datasets.DATASET_DIR = original
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_deleting_a_config_removes_its_whole_folder_in_one_commit():
    fake = FakeGitHub()
    server = serve(fake)
    cfg = config(server)
    github_sync.commit_files(cfg, {"datasets/pilot/config.json": "{}\n", "datasets/pilot/data.json": "{}\n", "datasets/pilot/view.json": "{}\n",
                                   "datasets/pilot/notes.md": "n\n", "datasets/pilot2/data.json": "keep\n"}, "Add pilot")
    assert sorted(github_sync.files_under(cfg, "datasets/pilot")) == [f"datasets/pilot/{n}" for n in ("config.json", "data.json", "notes.md", "view.json")]
    before = fake.head
    result = github_sync.delete_folder(cfg, "datasets/pilot", "Delete template: Pilot")
    assert result and fake.head != before and fake.commits[fake.head]["parents"] == [before]
    assert [p for p in fake.files() if p.startswith("datasets/pilot/")] == []                  # every file of the folder, not just config.json
    assert fake.files()["datasets/pilot2/data.json"] == "keep\n" and fake.files()["README.md"] == "hello\n"   # a similar name is left alone
    assert github_sync.delete_folder(cfg, "datasets/pilot", "again") is None                    # nothing left: no empty commit


def test_commit_delete_is_skipped_without_github_and_checks_the_name():
    fake = FakeGitHub()
    server = serve(fake)
    saved = {k: os.environ.get(k) for k in ("GITHUB_TOKEN", "GITHUB_REPO", "GITHUB_API_URL")}
    try:
        os.environ.update(GITHUB_TOKEN="", GITHUB_REPO="")
        assert datasets.commit_delete("demo", "x") is None                                      # not set up: nothing to do
        os.environ.update(GITHUB_TOKEN="good-token", GITHUB_REPO=REPO, GITHUB_API_URL=f"http://127.0.0.1:{server.server_port}")
        expect_error(lambda: datasets.commit_delete("../x", "m"), "not a config name")
        result = datasets.commit_delete("demo", "Delete template: Demo")
        assert result and "datasets/demo/data.json" not in fake.files()
        assert fake.commits[fake.head]["message"] == "Delete template: Demo"
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_datasets_on_github_are_loaded_when_missing_here():
    fake = FakeGitHub()
    server = serve(fake)
    cfg = config(server)
    github_sync.commit_files(cfg, {
        "datasets/remote1/config.json": '{"title": "Remote"}', "datasets/remote1/data.json": '{"customers": {}}', "datasets/remote1/view.json": "{}",
        "datasets/remote1/data.backup.json": "old",                                           # a backup file is not part of a dataset
        "datasets/local1/config.json": '{"title": "FROM GITHUB"}', "datasets/local1/data.json": "{}",
        "datasets/half/data.json": "{}",                                                      # no config.json: not a dataset
        "datasets/.hidden/config.json": "{}", "datasets/.hidden/data.json": "{}",             # not a valid name
        "datasets/deep/inner/config.json": "{}",                                              # nested: ignored
        "other/config.json": "{}"}, "Add remote datasets")
    saved = {k: os.environ.get(k) for k in ("GITHUB_TOKEN", "GITHUB_REPO", "GITHUB_API_URL")}
    original = datasets.DATASET_DIR
    with tempfile.TemporaryDirectory() as tmp:
        datasets.DATASET_DIR = Path(tmp)
        try:
            Path(tmp, "local1").mkdir()
            Path(tmp, "local1", "config.json").write_text('{"title": "MY LOCAL EDIT"}', encoding="utf-8")
            Path(tmp, "local1", "data.json").write_text("{}", encoding="utf-8")
            os.environ.update(GITHUB_TOKEN="", GITHUB_REPO="")
            assert datasets.pull_from_github() == []                                          # GitHub not set up: nothing happens
            os.environ.update(GITHUB_TOKEN="good-token", GITHUB_REPO=REPO, GITHUB_API_URL=f"http://127.0.0.1:{server.server_port}")
            assert datasets.pull_from_github() == ["remote1"]
            assert sorted(p.name for p in Path(tmp, "remote1").iterdir()) == ["config.json", "data.json", "view.json"]
            assert json.loads(Path(tmp, "remote1", "config.json").read_text(encoding="utf-8")) == {"title": "Remote"}
            assert "MY LOCAL EDIT" in Path(tmp, "local1", "config.json").read_text(encoding="utf-8")      # an existing dataset is not overwritten
            assert sorted(p.name for p in Path(tmp).iterdir()) == ["local1", "remote1"]
            assert datasets.pull_from_github() == []                                          # the second time there is nothing new
            os.environ["GITHUB_TOKEN"] = "wrong"
            expect_error(datasets.pull_from_github, "HTTP 401")
        finally:
            datasets.DATASET_DIR = original
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
