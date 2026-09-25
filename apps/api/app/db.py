import sqlite3
from pathlib import Path

import os

_state = {"path": None}


def db_path() -> Path:
    return Path(os.environ.get("AYVEN_DB", "/tmp/ayven-campus.db"))


def reset_connection_state() -> None:
    """Allow a new AYVEN_DB path to initialise. Does not delete data."""
    _state["path"] = None


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if _state["path"] != str(path):
        init_db(conn)
        _state["path"] = str(path)
    return conn


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
