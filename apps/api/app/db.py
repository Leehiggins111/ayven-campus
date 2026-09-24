import sqlite3
from pathlib import Path

import os

DB_PATH = Path(os.environ.get("AYVEN_DB", "/tmp/ayven-campus.db"))


_initialized = False


def connect() -> sqlite3.Connection:
    global _initialized
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if not _initialized:
        init_db(conn)
        _initialized = True
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS departments (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            x REAL NOT NULL,
            z REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            department_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'idle',
            current_task_id TEXT,
            current_tool TEXT,
            last_summary TEXT,
            progress REAL DEFAULT 0,
            tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            agent_id TEXT,
            title TEXT NOT NULL,
            brief TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT,
            requires_approval INTEGER DEFAULT 0,
            approval_status TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            project_id TEXT,
            task_id TEXT,
            agent_id TEXT,
            department_id TEXT,
            type TEXT NOT NULL,
            status TEXT,
            tool TEXT,
            summary TEXT,
            progress REAL,
            metadata TEXT
        );
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            namespace TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            project_id TEXT,
            agent_id TEXT,
            summary TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    seed(conn)


SEED_AGENTS = [
    ("milo", "Milo", "Chief of Staff", "command"),
    ("web-researcher", "Ivy Chen", "Web Researcher", "research"),
    ("verifier", "Noah Hale", "Verification Agent", "research"),
    ("travel-researcher", "Sofia Berg", "Travel Researcher", "travel"),
    ("ticket-researcher", "Jonas Krueger", "Football Ticket Researcher", "travel"),
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
        conn.execute(
            "INSERT INTO departments(id,name,x,z) VALUES(?,?,?,?)", d
        )
    for a in SEED_AGENTS:
        conn.execute(
            """INSERT INTO agents(id,name,role,department_id,status)
               VALUES(?,?,?,?, 'idle')""",
            a,
        )
    conn.commit()
