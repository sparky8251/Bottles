"""Network behaviour against a local scripted server.

These replace tests that asserted which options were passed to pycurl or
requests; those restated the implementation and broke whenever it moved.
Here the real client code talks to a server on 127.0.0.1 and only the outcome
is asserted: fallbacks, cleanup, cancellation, ordering.
"""

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from bottles.backend.downloader import Downloader
from bottles.backend.globals import Paths
from bottles.backend.managers import component as component_module
from bottles.backend.managers import repository as repository_module
from bottles.backend.managers.component import ComponentManager
from bottles.backend.managers.repository import RepositoryManager
from bottles.backend.models.result import Result
from bottles.backend.repos import repo as repo_module
from bottles.backend.repos.component import ComponentRepo
from bottles.backend.repos.repo import Repo
from bottles.backend.state import Signals, Status
from bottles.backend.utils import connection as connection_module
from bottles.backend.utils import httpclient
from bottles.backend.utils.connection import ConnectionUtils
from bottles.tests.backend.localserver import STALL, serve

SHORT = (0.5, 0.3)  # shorter than the server's stall
CATALOG = b"entry:\n  Category: test\n"
REFUSED = "http://127.0.0.1:1/"  # nothing listens on port 1


@pytest.fixture(autouse=True)
def _short_timeouts(monkeypatch):
    monkeypatch.setattr(httpclient, "_shared", None)
    monkeypatch.setattr(repo_module, "CATALOG_TIMEOUT", SHORT)
    monkeypatch.setattr(repository_module, "INDEX_TIMEOUT", SHORT)
    monkeypatch.setattr(connection_module, "PING_TIMEOUT", SHORT)
    monkeypatch.setattr(httpclient, "DOWNLOAD_TIMEOUT", SHORT)


def make_repo():
    repo = object.__new__(Repo)
    repo.url = "https://example.test"
    repo.name = "test"
    repo.offline = False
    return repo


class ImmediateAsync:
    def __init__(self, task_func, **kwargs):
        task_func(**kwargs)

    def join(self):
        pass


def make_repository_manager(monkeypatch, personal_repositories=None):
    signals = []
    for env_var in (
        "PERSONAL_COMPONENTS",
        "PERSONAL_DEPENDENCIES",
        "PERSONAL_INSTALLERS",
    ):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setattr(
        repository_module,
        "DataManager",
        lambda: SimpleNamespace(get=lambda _key: personal_repositories or {}),
    )
    monkeypatch.setattr(repository_module.SignalManager, "connect", lambda *_args: None)
    monkeypatch.setattr(
        repository_module.SignalManager,
        "send",
        lambda signal, result: signals.append((signal, result)),
    )
    monkeypatch.setattr(repository_module, "RunAsync", ImmediateAsync)
    monkeypatch.setattr(repository_module, "APP_VERSION", "64.1")
    return RepositoryManager(get_index=False), signals


def keep_component_repository(manager):
    repositories = manager._RepositoryManager__repositories
    repository = repositories["components"]
    manager._RepositoryManager__repositories = {"components": repository}
    return repository


def point_sources_at(repository, base, names=("primary", "fallback")):
    """Replace the real sources with paths on the local server; return them.

    url and cache_url follow the first source, as they do for the defaults.
    """
    sources = [f"{base}/{name}/" for name in names]
    repository["sources"] = sources
    repository["url"] = sources[0]
    repository["cache_url"] = sources[0]
    return sources


def paths_of(server):
    return [path for _method, path, _headers in server.requests]


# --- repository catalogs and manifests ------------------------------------


def test_catalog_fetch_parses_and_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(Paths, "temp", str(tmp_path))
    with serve({"/index.yml": (200, b"entry: value\n")}) as (_server, base):
        catalog = Repo._Repo__get_catalog(make_repo(), f"{base}/index.yml")
    assert catalog == {"entry": "value"}
    assert len(list(tmp_path.glob("repositories/test/*/catalog.yml"))) == 1


@pytest.mark.parametrize("outcome", [STALL, 403, (200, b"not: [a: dict")], ids=["timeout", "forbidden", "invalid"])
def test_catalog_fetch_failure_falls_back_to_the_cache(monkeypatch, tmp_path, outcome):
    monkeypatch.setattr(Paths, "temp", str(tmp_path))
    repo = make_repo()
    with serve({"/index.yml": outcome}) as (server, base):
        url = f"{base}/index.yml"
        assert Repo._Repo__get_catalog(repo, url) == {}
        # with a cached catalog from an earlier success, the failure returns it
        server.script["/index.yml"] = (200, b"cached: earlier\n")
        assert Repo._Repo__get_catalog(repo, url) == {"cached": "earlier"}
        server.script["/index.yml"] = outcome
        assert Repo._Repo__get_catalog(repo, url) == {"cached": "earlier"}


def test_manifest_fetch_parses_and_falls_back_on_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(Paths, "temp", str(tmp_path))
    repo = make_repo()
    with serve({"/m.yml": (200, b"entry: value\n")}) as (server, base):
        url = f"{base}/m.yml"
        assert Repo.get_manifest(repo, url) == {"entry": "value"}
        server.script["/m.yml"] = STALL
        assert Repo.get_manifest(repo, url) == {"entry": "value"}


# --- connectivity check ----------------------------------------------------


def test_connection_check_is_online_when_a_check_url_answers_200(monkeypatch):
    with serve({"/ok": 200}) as (server, base):
        monkeypatch.setattr(ConnectionUtils, "_check_urls", (f"{base}/ok",))
        assert ConnectionUtils().check_connection() is True
    assert server.requests[0][0] == "HEAD"


def test_connection_check_is_offline_when_every_check_url_fails(monkeypatch):
    with serve({"/slow": STALL, "/gone": 404}) as (_server, base):
        monkeypatch.setattr(ConnectionUtils, "_check_urls", (f"{base}/slow", f"{base}/gone", REFUSED))
        assert ConnectionUtils().check_connection() is False


def test_connection_check_falls_through_to_a_later_url(monkeypatch):
    with serve({"/gone": 404, "/ok": 200}) as (server, base):
        monkeypatch.setattr(ConnectionUtils, "_check_urls", (REFUSED, f"{base}/gone", f"{base}/ok"))
        assert ConnectionUtils().check_connection() is True
    assert paths_of(server) == ["/gone", "/ok"]


# --- component download ----------------------------------------------------


def test_component_download_uses_stream_downloader(monkeypatch, tmp_path):
    request = {}

    class DownloadStub:
        def __init__(self, **kwargs):
            request.update(kwargs)

        def download(self):
            Path(request["file"]).write_bytes(b"runner")
            return Result(True)

    monkeypatch.setattr(component_module, "Downloader", DownloadStub)
    monkeypatch.setattr(Paths, "temp", str(tmp_path))

    component = object.__new__(ComponentManager)
    component._ComponentManager__manager = SimpleNamespace(check_app_dirs=lambda: None)

    result = ComponentManager.download(
        component,
        "https://example.test/runner.tar.xz",
        "runner.tar.xz",
    )

    assert result.ok is True
    assert request["url"] == "https://example.test/runner.tar.xz"


def test_download_writes_the_file_and_reports_progress(tmp_path):
    progress = []
    destination = tmp_path / "runner.tar.xz"
    with serve({"/runner.tar.xz": (200, b"runner-bytes")}) as (server, base):
        result = Downloader(
            f"{base}/runner.tar.xz",
            str(destination),
            update_func=lambda *a, **kw: progress.append((a, kw)),
        ).download()
    assert result.ok is True
    assert destination.read_bytes() == b"runner-bytes"
    assert not (tmp_path / "runner.tar.xz.part").exists()
    assert progress and progress[-1][0] == (12, 12)
    # the downloader keeps its own agent, which some servers require
    assert server.requests[0][2]["user-agent"].startswith("curl/")


def test_stalled_download_leaves_no_partial_file(tmp_path):
    destination = tmp_path / "runner.tar.xz"
    with serve({"/runner.tar.xz": STALL}) as (_server, base):
        result = Downloader(f"{base}/runner.tar.xz", str(destination)).download()
    assert result.ok is False
    assert not destination.exists()
    assert not (tmp_path / "runner.tar.xz.part").exists()


def test_cancelled_download_stops_and_cleans_up(tmp_path):
    import threading

    cancel = threading.Event()
    statuses = []

    def update(*_a, status=None, **_kw):
        if status is not None:
            statuses.append(status)
        cancel.set()  # cancel after the first progress report

    destination = tmp_path / "runner.tar.xz"
    chunks = [b"a" * 2048, b"b" * 2048, b"c" * 2048]
    with serve({"/runner.tar.xz": ("stream", chunks, 0.05)}) as (_server, base):
        result = Downloader(
            f"{base}/runner.tar.xz", str(destination), update_func=update, cancel_event=cancel
        ).download()
    assert result.ok is False
    assert Status.CANCELLED in statuses
    assert not destination.exists()
    assert not (tmp_path / "runner.tar.xz.part").exists()


def test_component_install_stops_after_extraction_failure(monkeypatch):
    statuses = []
    manifest = {
        "File": [{"url": "https://example.test/runner.tar.xz", "file_name": "runner.tar.xz"}],
        "Category": "runners",
    }

    component = object.__new__(ComponentManager)
    component._ComponentManager__manager = SimpleNamespace(check_app_dirs=lambda: None)

    monkeypatch.setattr(ComponentManager, "get_component", lambda *_args: manifest)
    monkeypatch.setattr(
        ComponentManager, "download", lambda *_args, **_kwargs: Result(True)
    )
    monkeypatch.setattr(ComponentManager, "extract", lambda *_args: False)

    result = ComponentManager.install(
        component,
        "runners",
        "runner",
        func=lambda *_a, status=None, **_kw: statuses.append(status),
    )

    assert result.ok is False
    assert statuses == [Status.FAILED]


# --- repository index: ordering and fallback -------------------------------


def test_repository_index_keeps_primary_when_available(monkeypatch):
    manager, signals = make_repository_manager(monkeypatch)
    repository = keep_component_repository(manager)
    with serve({"/primary/64.1.yml": (200, CATALOG)}) as (server, base):
        primary, _fallback = point_sources_at(repository, base)
        manager._RepositoryManager__get_index()

    assert paths_of(server) == ["/primary/64.1.yml"]
    assert repository["url"] == primary
    assert repository["index"] == f"{primary}64.1.yml"
    assert repository["catalog"] == CATALOG
    assert len(signals) == 1
    assert signals[0][1].ok is True


def test_repository_index_uses_fallback_after_timeout(monkeypatch, tmp_path):
    manager, signals = make_repository_manager(monkeypatch)
    repository = keep_component_repository(manager)
    catalog = b"runtime-0.6.3:\n  Category: runtimes\n"
    script = {
        "/primary/64.1.yml": STALL,
        "/fallback/64.1.yml": 404,
        "/fallback/index.yml": (200, catalog),
    }
    with serve(script) as (server, base):
        primary, fallback = point_sources_at(repository, base)
        manager._RepositoryManager__get_index()

        assert paths_of(server) == ["/primary/64.1.yml", "/fallback/64.1.yml", "/fallback/index.yml"]
        assert repository["url"] == fallback
        assert repository["index"] == f"{fallback}index.yml"
        assert repository["catalog"] == catalog
        assert len(signals) == 1
        assert signals[0][0] == Signals.RepositoryFetched
        assert signals[0][1].ok is True

        # the repo object built from that index uses the fallback for manifests too
        monkeypatch.setattr(Paths, "temp", str(tmp_path))
        repo = manager.get_repo("components")
        assert repo.url == fallback
        assert repo.catalog == {"runtime-0.6.3": {"Category": "runtimes"}}
        manifest_url = f"{fallback}/runtimes/runtime-0.6.3.yml"
        server.script[urlsplit(manifest_url).path] = (200, b"Name: Runtime\n")
        assert repo.get("runtime-0.6.3") == {"Name": "Runtime"}
        assert paths_of(server)[-1] == urlsplit(manifest_url).path

    cache_files = list(tmp_path.glob("repositories/components/*/catalog.yml"))
    assert len(cache_files) == 1
    assert cache_files[0].read_bytes() == repository["catalog"]

    offline_repo = object.__new__(ComponentRepo)
    offline_repo.url = primary
    offline_repo.cache_url = primary
    offline_repo.offline = True
    offline_repo.catalog = Repo._Repo__get_catalog(offline_repo, "", offline=True)
    assert offline_repo.catalog == repo.catalog
    assert offline_repo.get("runtime-0.6.3") == {"Name": "Runtime"}


def test_repository_index_falls_back_after_truncated_transfer(monkeypatch):
    manager, signals = make_repository_manager(monkeypatch)
    repository = keep_component_repository(manager)
    script = {
        "/primary/64.1.yml": 404,
        "/primary/index.yml": ("truncate", 100, b"entry:"),
        "/fallback/64.1.yml": 404,
        "/fallback/index.yml": (200, CATALOG),
    }
    with serve(script) as (server, base):
        _primary, fallback = point_sources_at(repository, base)
        manager._RepositoryManager__get_index()

    assert paths_of(server) == list(script)
    assert repository["url"] == fallback
    assert repository["catalog"] == CATALOG
    assert len(signals) == 1
    assert signals[0][1].ok is True


@pytest.mark.parametrize(
    "outcome",
    (403, (200, b"invalid catalog")),
    ids=("forbidden", "invalid-yaml"),
)
def test_repository_index_uses_fallback_after_bad_primary(monkeypatch, outcome):
    manager, signals = make_repository_manager(monkeypatch)
    repository = keep_component_repository(manager)
    script = {
        "/primary/64.1.yml": outcome,
        "/fallback/64.1.yml": 404,
        "/fallback/index.yml": (200, CATALOG),
    }
    with serve(script) as (server, base):
        _primary, fallback = point_sources_at(repository, base)
        manager._RepositoryManager__get_index()

    assert paths_of(server) == list(script)
    assert repository["url"] == fallback
    assert repository["catalog"] == CATALOG
    assert len(signals) == 1
    assert signals[0][1].ok is True


def test_repository_index_reports_one_failure_when_no_source_connects(monkeypatch):
    manager, signals = make_repository_manager(monkeypatch)
    repository = keep_component_repository(manager)
    repository["sources"] = [REFUSED + "primary/", REFUSED + "fallback/"]

    manager._RepositoryManager__get_index()

    assert repository["catalog"] is None
    assert len(signals) == 1
    assert signals[0][1].ok is False


def test_repository_index_reports_one_failure_when_every_candidate_is_bad(monkeypatch):
    manager, signals = make_repository_manager(monkeypatch)
    repository = keep_component_repository(manager)
    script = {"/primary/64.1.yml": 403, "/fallback/64.1.yml": (200, b"invalid catalog")}
    with serve(script) as (server, base):
        point_sources_at(repository, base)
        manager._RepositoryManager__get_index()

    assert paths_of(server) == list(script)
    assert repository["catalog"] is None
    assert len(signals) == 1
    assert signals[0][1].ok is False


def test_components_use_pinned_catalog_before_stale_proxy(monkeypatch):
    manager, _signals = make_repository_manager(monkeypatch)
    repository = keep_component_repository(manager)
    catalog = b"soda-current:\n  Category: runners\n"
    script = {
        "/pinned/64.1.yml": (200, catalog),
        "/proxy/64.1.yml": (200, b"soda-old:\n  Category: runners\n"),
    }
    with serve(script) as (server, base):
        point_sources_at(repository, base, names=("pinned", "proxy"))
        manager._RepositoryManager__get_index()

    assert repository["catalog"] == catalog
    assert paths_of(server) == ["/pinned/64.1.yml"]


def test_personal_repository_is_exclusive_and_instance_local(monkeypatch):
    with serve({"/custom/64.1.yml": 403}) as (server, base):
        custom = f"{base}/custom/"
        manager, signals = make_repository_manager(monkeypatch, {"components": custom})
        repository = keep_component_repository(manager)
        manager._RepositoryManager__get_index()

    assert paths_of(server) == ["/custom/64.1.yml"]
    assert repository["sources"] == [custom]
    assert repository["url"] == custom
    assert repository["cache_url"] == custom
    assert len(signals) == 1
    assert signals[0][1].ok is False

    clean_manager, _signals = make_repository_manager(monkeypatch)
    clean_repository = clean_manager._RepositoryManager__repositories["components"]
    assert clean_repository["url"].startswith(
        "https://raw.githubusercontent.com/bottlesdevs/components/"
    )
    assert clean_repository["cache_url"] == clean_repository["url"]


def test_local_file_repository_is_served_from_disk(monkeypatch, tmp_path):
    source = tmp_path / "local"
    source.mkdir()
    (source / "index.yml").write_bytes(CATALOG)
    manager, signals = make_repository_manager(monkeypatch)
    repository = keep_component_repository(manager)
    repository["sources"] = [source.as_uri() + "/"]

    manager._RepositoryManager__get_index()

    assert repository["catalog"] == CATALOG
    assert repository["index"] == source.as_uri() + "/index.yml"
    assert len(signals) == 1
    assert signals[0][1].ok is True


def test_default_repositories_include_commit_pinned_sources(monkeypatch):
    manager, _signals = make_repository_manager(monkeypatch)
    repositories = manager._RepositoryManager__repositories

    for name, project in (
        ("components", "components"),
        ("dependencies", "dependencies"),
        ("installers", "programs"),
    ):
        prefix = f"https://raw.githubusercontent.com/bottlesdevs/{project}/"
        pinned = [url for url in repositories[name]["sources"] if url.startswith(prefix)]
        assert len(pinned) == 1
        revision = pinned[0][len(prefix) :].removesuffix("/")
        assert len(revision) == 40
        assert all(character in "0123456789abcdef" for character in revision)
