import collections.abc
import sqlite3
from pathlib import Path

import os
from contextlib import contextmanager
from contextvars import ContextVar
import threading

_stage_connection = ContextVar("stage_connection", default=None)


class StageConnection:
    """One owner transaction; legacy commit/close calls cannot split its checkpoint."""
    def __init__(self, connection):
        self.connection = connection
        self.active = True
        self.lock = threading.RLock()

    def execute(self, *args, **kwargs):
        with self.lock:
            if not self.active:
                raise RuntimeError("stage transaction has ended")
            return self.connection.execute(*args, **kwargs)

    def commit(self):
        pass

    def close(self):
        pass


@contextmanager
def stage_transaction():
    if _stage_connection.get() is not None:
        raise RuntimeError("nested stage transaction")
    connection = connect()
    connection.execute("BEGIN IMMEDIATE")
    proxy = StageConnection(connection)
    token = _stage_connection.set(proxy)
    try:
        yield proxy
        with proxy.lock:
            from .intelligence.leases import assert_fence
            from .intelligence.run_token import StaleWrite

            try:
                target = getattr(connection, "_connection", connection)
                assert_fence(target)
            except StaleWrite:
                connection.rollback()
                raise
            connection.commit()
    except BaseException:
        with proxy.lock:
            connection.rollback()
        raise
    finally:
        with proxy.lock:
            proxy.active = False
        _stage_connection.reset(token)
        connection.close()


_state = {"path": None}


def db_path() -> Path:
    return Path(os.environ.get("AYVEN_DB", "/tmp/ayven-campus.db"))


def reset_connection_state() -> None:
    """Allow a new AYVEN_DB path to initialise. Does not delete data."""
    _state["path"] = None


class _MappedRow(collections.abc.Mapping):
    """libsql rows are tuples. Callers use row['id'] and dict(row)."""

    def __init__(self, keys, values):
        self._keys = list(keys)
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._keys.index(key)]

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)


class _MappedCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        description = cursor.description or []
        self._keys = [item[0] for item in description]

    def _wrap(self, row):
        if row is None or isinstance(row, collections.abc.Mapping):
            return row
        return _MappedRow(self._keys, row)

    def fetchone(self):
        return self._wrap(self._cursor.fetchone())

    def fetchall(self):
        return [self._wrap(row) for row in self._cursor.fetchall()]

    def __iter__(self):
        return (self._wrap(row) for row in self._cursor)

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _MappedConnection:
    """A libsql connection whose rows behave like sqlite3.Row."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        cursor = self._raw.execute(sql, params if params is not None else ())
        if hasattr(self._raw, "row_factory"):
            return cursor
        return _MappedCursor(cursor)

    def executemany(self, sql, seq):
        return self._raw.executemany(sql, seq)

    def executescript(self, sql):
        if hasattr(self._raw, "executescript"):
            return self._raw.executescript(sql)
        for statement in sql.split(";"):
            if statement.strip():
                self._raw.execute(statement)
        return self

    def commit(self):
        return self._raw.commit()

    def rollback(self):
        return self._raw.rollback()

    def close(self):
        return self._raw.close()

    def __getattr__(self, name):
        return getattr(self._raw, name)


def hosted_mode() -> bool:
    """Render, or an explicit durable requirement. Local tests set neither."""
    if os.environ.get("AYVEN_REQUIRE_DURABLE", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return bool(os.environ.get("RENDER", "").strip())


def _remote_url(url: str) -> bool:
    scheme = url.split(":", 1)[0].lower()
    return scheme in {"libsql", "https", "http", "wss", "ws"}


def require_hosted_database() -> None:
    """Hosted mode never opens temporary SQLite. Missing settings are a startup error."""
    if not hosted_mode():
        return
    url = os.environ.get("AYVEN_DB_URL", "").strip()
    token = os.environ.get("AYVEN_DB_AUTH_TOKEN", "").strip()
    missing = []
    if not url:
        missing.append("AYVEN_DB_URL")
    if not token:
        missing.append("AYVEN_DB_AUTH_TOKEN")
    if missing:
        raise RuntimeError(
            "Hosted mode refuses to start without "
            + " and ".join(missing)
            + ". Temporary SQLite is not used. Set AYVEN_DB_URL and AYVEN_DB_AUTH_TOKEN."
        )
    if url.startswith("file:"):
        raise RuntimeError(
            "Hosted mode refuses a file: database. "
            "Set AYVEN_DB_URL to a remote libsql:// or https:// URL. Temporary SQLite is not used."
        )
    if not _remote_url(url):
        raise RuntimeError(
            "Hosted mode requires a remote libsql URL (libsql:// or https://). "
            "Temporary SQLite is not used."
        )


def persistence_report() -> dict:
    """What /health can say without claiming a local file is durable."""
    url = os.environ.get("AYVEN_DB_URL", "").strip()
    token = os.environ.get("AYVEN_DB_AUTH_TOKEN", "").strip()
    if _remote_url(url) and token and not url.startswith("file:"):
        return {"persistence": "libsql_http", "durable": True, "persistence_state": "durable"}
    if url.startswith("file:"):
        return {"persistence": "libsql_file", "durable": False, "persistence_state": "not_durable"}
    return {"persistence": "local_sqlite", "durable": False, "persistence_state": "not_durable"}



def live_check(timeout: float = 2.0) -> tuple[bool, str]:
    """A cheap SELECT 1 against the configured database. Returns (ok, error).

    It opens its own short-timeout connection and never runs init_db, so a
    database that is down makes /health say so quickly instead of hanging.
    """
    url = os.environ.get("AYVEN_DB_URL", "").strip()
    token = os.environ.get("AYVEN_DB_AUTH_TOKEN", "").strip()
    conn = None
    try:
        if _remote_url(url) and token and not url.startswith("file:"):
            from .hrana_http import HranaConnection

            conn = HranaConnection(url, token, timeout=timeout)
        elif url.startswith("file:"):
            conn = _libsql_connect(url, "")
        else:
            conn = sqlite3.connect(db_path(), timeout=timeout, check_same_thread=False)
        conn.execute("SELECT 1").fetchone()
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:300]
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

def _libsql_connect(url: str, auth_token: str):
    """A local ``file:`` URL via the libsql driver. Remote URLs use Hrana HTTP instead."""
    try:
        import libsql
    except ImportError:
        try:
            import libsql_experimental as libsql
        except ImportError as exc:
            raise RuntimeError("AYVEN_DB_URL is set but the libsql package is not installed") from exc
    if not url.startswith("file:"):
        raise RuntimeError("Remote libsql URLs use HTTP. They are not opened with embedded sync.")
    local = url[5:]
    if local.startswith("//"):
        local = local[2:]
    conn = libsql.connect(local)
    if hasattr(conn, "row_factory"):
        conn.row_factory = sqlite3.Row
        return conn
    return _MappedConnection(conn)


_MUTATIONS = {"INSERT", "UPDATE", "DELETE", "REPLACE", "BEGIN"}


class _GuardedConnection:
    """Refuse mutations from a worker whose stage has already timed out."""

    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, *args, **kwargs):
        self._reject_if_stale(sql)
        return self._connection.execute(sql, *args, **kwargs)

    def executemany(self, sql, *args, **kwargs):
        self._reject_if_stale(sql)
        return self._connection.executemany(sql, *args, **kwargs)

    def executescript(self, sql):
        self._reject_if_stale(sql)
        return self._connection.executescript(sql)

    def _reject_if_stale(self, sql) -> None:
        if not isinstance(sql, str) or not sql.strip():
            return
        head = sql.lstrip().split(None, 1)[0].upper()
        if head not in _MUTATIONS:
            return
        from .intelligence.run_token import reject_if_stale

        reject_if_stale()

    def commit(self):
        from .intelligence.leases import assert_fence
        from .intelligence.run_token import StaleWrite

        try:
            assert_fence(self._connection)
        except StaleWrite:
            self._connection.rollback()
            raise
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def __getattr__(self, name):
        return getattr(self._connection, name)


def open_raw():
    """A connection that is not the stage proxy. Claims use this so two processes can lock."""
    require_hosted_database()
    url = os.environ.get("AYVEN_DB_URL", "").strip()
    auth_token = os.environ.get("AYVEN_DB_AUTH_TOKEN", "").strip()
    if url.startswith("file:"):
        conn = _libsql_connect(url, "")
        identity = f"libsql:{url}"
    elif _remote_url(url):
        if not auth_token:
            raise RuntimeError("AYVEN_DB_URL and AYVEN_DB_AUTH_TOKEN must be set together")
        from .hrana_http import HranaConnection

        conn = HranaConnection(url, auth_token)
        identity = f"libsql-http:{url}"
    elif url or auth_token:
        raise RuntimeError("AYVEN_DB_URL and AYVEN_DB_AUTH_TOKEN must be set together")
    else:
        path = db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False, timeout=180)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL").fetchone()
        conn.execute("PRAGMA busy_timeout=5000")
        identity = str(path)
    if _state["path"] != identity:
        init_db(conn)
        _state["path"] = identity
    return conn


def connect() -> sqlite3.Connection:
    owned = _stage_connection.get()
    if owned is not None:
        return owned
    return _GuardedConnection(open_raw())


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS departments (id TEXT PRIMARY KEY, name TEXT NOT NULL, x REAL NOT NULL, z REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, department_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'idle', current_task_id TEXT, current_tool TEXT, last_summary TEXT, progress REAL DEFAULT 0, tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, title TEXT NOT NULL, objective TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, agent_id TEXT, title TEXT NOT NULL, brief TEXT NOT NULL, status TEXT NOT NULL, result TEXT, requires_approval INTEGER DEFAULT 0, approval_status TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, project_id TEXT, task_id TEXT, agent_id TEXT, department_id TEXT, type TEXT NOT NULL, status TEXT, tool TEXT, summary TEXT, progress REAL, metadata TEXT);
        CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, project_id TEXT, agent_id TEXT, summary TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS work_packages (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT, title TEXT NOT NULL, objective TEXT NOT NULL, origin TEXT NOT NULL, agent_id TEXT, department_id TEXT, stage TEXT NOT NULL, status TEXT NOT NULL, findings TEXT, next_action TEXT, requires_approval INTEGER DEFAULT 0, destination TEXT, error TEXT, parent_id TEXT, tier TEXT, confidence REAL, review_status TEXT, attempt_count INTEGER DEFAULT 0, return_reason TEXT, model_role TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, package_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT, snippet TEXT, note TEXT, created_at TEXT NOT NULL);
        """
    )
    conn.commit()
    seed(conn)
    migrate_v04(conn)
    migrate_v10(conn)
    migrate_v20(conn)
    migrate_v21(conn)
    migrate_v22(conn)
    migrate_v23(conn)
    migrate_v24(conn)
    migrate_v25(conn)
    migrate_v26(conn)
    from .blobs import ensure_schema as blob_schema
    from .goals import ensure_schema as goal_schema
    from .intelligence.budget import ensure_schema as budget_schema

    blob_schema(conn)
    goal_schema(conn)
    budget_schema(conn)


SEED_AGENTS = [
    ("milo", "Milo", "Chief of Staff", "command"),
    ("research-mgr", "Rowan Vale", "Research Manager", "research"),
    ("research-sup", "Eden Shah", "Research Supervisor", "research"),
    ("research-e1", "Ivy Chen", "Research Employee 01", "research"),
    ("research-e2", "Noah Hale", "Research Employee 02", "research"),
    ("research-e3", "Sam Okoye", "Research Employee 03", "research"),
    ("web-researcher", "Ivy Chen", "Web Researcher", "research"),
    ("verifier", "Noah Hale", "Verification Agent", "research"),
    ("travel-researcher", "Sofia Berg", "Travel Researcher", "travel"),
    ("ticket-researcher", "Jonas Kruger", "Football Ticket Researcher", "travel"),
    ("supplier-researcher", "Amira Cole", "Supplier Researcher", "travel"),
    ("commercial-analyst", "Eli Park", "Commercial / Package Analyst", "travel"),
    ("compliance", "Priya Shah", "Compliance Agent", "outreach"),
    ("outreach", "Luca Moretti", "Outreach Agent", "outreach"),
    ("labs-validator", "Remy Okonkwo", "Validation Agent", "labs"),
]

SEED_DEPTS = [
    ("command", "Ayven Command", 0, 0),
    ("research", "Research & Intelligence", -18, -10),
    ("travel", "Travel", 18, -8),
    ("outreach", "Sales & Outreach", -16, 12),
    ("labs", "Ayven Labs", 16, 14),
    ("trades", "Trades & Property", -28, 2),
    ("procurement", "Procurement", 28, 2),
    ("grassroots", "Grassroots Football", 0, 22),
]


def seed(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) AS c FROM agents").fetchone()["c"] > 0:
        return
    for d in SEED_DEPTS:
        conn.execute("INSERT INTO departments(id,name,x,z) VALUES(?,?,?,?)", d)
    for a in SEED_AGENTS:
        conn.execute("INSERT INTO agents(id,name,role,department_id,status) VALUES(?,?,?,?, 'idle')", a)
    conn.commit()


def migrate_v04(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(work_packages)").fetchall()}
    for name, typ in {
        "parent_id": "TEXT", "tier": "TEXT", "confidence": "REAL", "review_status": "TEXT",
        "attempt_count": "INTEGER DEFAULT 0", "return_reason": "TEXT", "model_role": "TEXT",
    }.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE work_packages ADD COLUMN {name} {typ}")
    for a in [
        ("research-mgr", "Rowan Vale", "Research Manager", "research"),
        ("research-sup", "Eden Shah", "Research Supervisor", "research"),
        ("research-e1", "Ivy Chen", "Research Employee 01", "research"),
        ("research-e2", "Noah Hale", "Research Employee 02", "research"),
        ("research-e3", "Sam Okoye", "Research Employee 03", "research"),
    ]:
        conn.execute("INSERT OR IGNORE INTO agents(id,name,role,department_id,status) VALUES(?,?,?,?, 'idle')", a)
    conn.commit()


def _add_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, typ in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


def migrate_v10(conn: sqlite3.Connection) -> None:
    """Intelligence engine tables. Additive only — v0.4 rows are kept."""
    _add_columns(conn, "work_packages", {
        "task_class": "TEXT",
        "plan_id": "TEXT",
        "selected_model": "TEXT",
        "selected_skills": "TEXT",
        "quality_score": "REAL",
        "supervisor_decision": "TEXT",
        "manager_decision": "TEXT",
        "observability_json": "TEXT",
    })
    _add_columns(conn, "memories", {
        "scope": "TEXT",
        "subject_id": "TEXT",
        "provenance": "TEXT",
        "source_url": "TEXT",
        "expires_at": "TEXT",
        "superseded_by": "TEXT",
        "tags": "TEXT",
    })
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY,
            package_id TEXT NOT NULL,
            objective TEXT,
            deliverable TEXT,
            plan_json TEXT NOT NULL,
            task_class TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skills_used (
            id TEXT PRIMARY KEY,
            package_id TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            version TEXT,
            reason TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tool_calls (
            id TEXT PRIMARY KEY,
            package_id TEXT NOT NULL,
            agent_id TEXT,
            tool TEXT NOT NULL,
            status TEXT NOT NULL,
            query TEXT,
            source_url TEXT,
            source_title TEXT,
            retrieved_at TEXT,
            extracted_content TEXT,
            error TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS claims (
            id TEXT PRIMARY KEY,
            package_id TEXT NOT NULL,
            agent_id TEXT,
            claim_text TEXT NOT NULL,
            claim_type TEXT,
            source_id TEXT,
            evidence_text TEXT,
            source_url TEXT,
            source_type TEXT,
            retrieved_at TEXT,
            freshness TEXT,
            verification_status TEXT,
            confidence REAL,
            challenged_by TEXT,
            challenge_reason TEXT,
            supersedes TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS claim_evidence (
            id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL,
            package_id TEXT NOT NULL,
            source_url TEXT,
            source_title TEXT,
            source_type TEXT,
            evidence_text TEXT,
            retrieved_at TEXT,
            rank TEXT,
            supports TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS verification_results (
            id TEXT PRIMARY KEY,
            package_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            agent_id TEXT,
            decision TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS quality_results (
            id TEXT PRIMARY KEY,
            package_id TEXT NOT NULL,
            score_json TEXT NOT NULL,
            overall REAL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_calls (
            id TEXT PRIMARY KEY,
            package_id TEXT,
            role TEXT,
            model_id TEXT,
            provider TEXT,
            task_class TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            latency_s REAL,
            est_cost_usd REAL,
            backend TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def migrate_v20(conn: sqlite3.Connection) -> None:
    """v2 ledger, workflow, and memory columns. Additive only."""
    _add_columns(conn, "work_packages", {"workflow_state": "TEXT"})
    _add_columns(conn, "claims", {
        "origin": "TEXT",
        "contradicting_evidence": "TEXT",
        "authority": "TEXT",
        "repair_history": "TEXT",
        "verification_history": "TEXT",
    })
    _add_columns(conn, "memories", {
        "confidence": "REAL",
        "last_used": "TEXT",
        "supersedes": "TEXT",
    })
    conn.execute(
        """CREATE TABLE IF NOT EXISTS traces (
            id TEXT PRIMARY KEY,
            package_id TEXT NOT NULL,
            event TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.commit()


def migrate_v21(conn: sqlite3.Connection) -> None:
    """Trace links, document locators, and file hashes. Additive only."""
    _add_columns(conn, "claims", {"locator": "TEXT", "file_hash": "TEXT", "trace_id": "TEXT"})
    _add_columns(conn, "model_calls", {"trace_id": "TEXT"})
    _add_columns(conn, "tool_calls", {"trace_id": "TEXT"})
    _add_columns(conn, "sources", {"trace_id": "TEXT"})
    _add_columns(conn, "traces", {"trace_id": "TEXT"})
    conn.commit()


def migrate_v23(conn: sqlite3.Connection) -> None:
    """Keep the model reply that was parsed. Additive only."""
    _add_columns(conn, "model_calls", {"response_text": "TEXT", "finish_reason": "TEXT"})
    conn.commit()


def migrate_v24(conn: sqlite3.Connection) -> None:
    """Prompt-eval, generation, and load durations. Additive only."""
    _add_columns(conn, "model_calls", {"prompt_eval_s": "REAL", "eval_s": "REAL", "load_s": "REAL"})
    conn.commit()


def migrate_v25(conn: sqlite3.Connection) -> None:
    """Exclusive job claim and the side-effect outbox. Local SQLite is the claim store."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS job_leases (
            job_id TEXT PRIMARY KEY,
            owner TEXT NOT NULL DEFAULT '',
            lease_until INTEGER NOT NULL DEFAULT 0,
            fence INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            state TEXT NOT NULL DEFAULT 'ready'
        );
        CREATE TABLE IF NOT EXISTS side_effects (
            idem_key TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            fence INTEGER NOT NULL DEFAULT 0,
            stage TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            created_at INTEGER NOT NULL
        );
        """
    )
    conn.commit()


def migrate_v26(conn: sqlite3.Connection) -> None:
    """Milo constraints and the job mode Milo reads back."""
    _add_columns(conn, "projects", {"constraints_json": "TEXT", "run_mode": "TEXT"})
    conn.commit()


def migrate_v22(conn: sqlite3.Connection) -> None:
    """Campus visual state, clarification, and approval context. Additive only."""
    _add_columns(conn, "agents", {"visual_state": "TEXT"})
    _add_columns(conn, "work_packages", {
        "campus_stage": "TEXT",
        "clarification_question": "TEXT",
        "clarification_answer": "TEXT",
    })
    _add_columns(conn, "approvals", {"context_json": "TEXT"})
    conn.execute("UPDATE agents SET visual_state='IDLE' WHERE visual_state IS NULL OR visual_state=''")
    conn.commit()
