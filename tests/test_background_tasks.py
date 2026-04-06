"""Unit tests for background scheduler and purge tasks."""

from types import SimpleNamespace

import pytest

from app import background_tasks as tasks


class FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return FakeScalars(self._items)


class FakeSession:
    def __init__(self, items):
        self._items = list(items)
        self.deleted = []
        self.commit_calls = 0
        self.execute_calls = 0

    async def execute(self, _stmt):
        self.execute_calls += 1
        return FakeResult(self._items)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commit_calls += 1


class FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeEngine:
    def __init__(self):
        self.dispose_calls = 0

    async def dispose(self):
        self.dispose_calls += 1


@pytest.fixture(autouse=True)
def reset_scheduler_global():
    tasks.scheduler = None
    yield
    tasks.scheduler = None


@pytest.mark.asyncio
async def test_purge_expired_deleted_listings_deletes_and_commits(monkeypatch):
    expired_1 = object()
    expired_2 = object()
    fake_session = FakeSession([expired_1, expired_2])
    fake_engine = FakeEngine()

    monkeypatch.setattr(tasks, "create_async_engine", lambda *_args, **_kwargs: fake_engine)
    monkeypatch.setattr(
        tasks,
        "sessionmaker",
        lambda *_args, **_kwargs: (lambda: FakeSessionContext(fake_session)),
    )

    await tasks.purge_expired_deleted_listings()

    assert fake_session.execute_calls == 1
    assert len(fake_session.deleted) == 2
    assert fake_session.commit_calls == 1
    assert fake_engine.dispose_calls == 1


@pytest.mark.asyncio
async def test_purge_expired_deleted_listings_no_matches(monkeypatch):
    fake_session = FakeSession([])
    fake_engine = FakeEngine()

    monkeypatch.setattr(tasks, "create_async_engine", lambda *_args, **_kwargs: fake_engine)
    monkeypatch.setattr(
        tasks,
        "sessionmaker",
        lambda *_args, **_kwargs: (lambda: FakeSessionContext(fake_session)),
    )

    await tasks.purge_expired_deleted_listings()

    assert fake_session.execute_calls == 1
    assert len(fake_session.deleted) == 0
    assert fake_session.commit_calls == 0
    assert fake_engine.dispose_calls == 1


@pytest.mark.asyncio
async def test_purge_expired_deleted_listings_skips_when_unlimited_window(monkeypatch):
    called = {"engine": 0}

    def fake_create_engine(*_args, **_kwargs):
        called["engine"] += 1
        raise AssertionError("Engine should not be created when restore window is unlimited")

    monkeypatch.setattr(tasks, "SOFT_DELETE_RESTORE_DAYS", 0)
    monkeypatch.setattr(tasks, "create_async_engine", fake_create_engine)

    await tasks.purge_expired_deleted_listings()

    assert called["engine"] == 0


def test_start_scheduler_registers_job_and_starts(monkeypatch):
    class FakeScheduler:
        def __init__(self, timezone=None):
            self.timezone = timezone
            self.started = False
            self.jobs = []
            self.running = False

        def add_job(self, func, **kwargs):
            self.jobs.append((func, kwargs))

        def start(self):
            self.started = True
            self.running = True

        def shutdown(self, wait=True):
            self.running = False

    fake_module = SimpleNamespace(AsyncIOScheduler=FakeScheduler)
    monkeypatch.setattr(tasks, "import_module", lambda _name: fake_module)

    tasks.start_scheduler()

    assert tasks.scheduler is not None
    assert tasks.scheduler.started is True
    assert tasks.scheduler.timezone == "UTC"
    assert len(tasks.scheduler.jobs) == 1

    func, kwargs = tasks.scheduler.jobs[0]
    assert func == tasks.purge_expired_deleted_listings
    assert kwargs["trigger"] == "cron"
    assert kwargs["hour"] == 2
    assert kwargs["minute"] == 0
    assert kwargs["id"] == "purge_expired_listings"


def test_stop_scheduler_shutdown_and_resets_global(monkeypatch):
    class FakeScheduler:
        def __init__(self):
            self.running = True
            self.shutdown_calls = []

        def shutdown(self, wait=True):
            self.shutdown_calls.append(wait)
            self.running = False

    tasks.scheduler = FakeScheduler()

    tasks.stop_scheduler()

    assert tasks.scheduler is None
