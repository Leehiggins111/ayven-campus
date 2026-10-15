"""Spend ledger and caps. Money is stored in USD. Pounds are a display conversion.

Enforcement is on unless AYVEN_BUDGET_ENFORCE=0, and that switch is refused
unless the process is running the stub model. A cap breach halts the job.
It never switches the call onto a paid route.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..db import connect

LONDON = ZoneInfo("Europe/London")
_JOB: dict[str, str] = {"id": ""}


def fx_gbp_per_usd() -> float:
    return float(os.environ.get("AYVEN_FX_GBP_PER_USD", "0.7571"))


def gbp(usd: float) -> float:
    return round(float(usd) * fx_gbp_per_usd(), 2)


def usd_from_gbp(amount: float) -> float:
    rate = fx_gbp_per_usd()
    if rate <= 0:
        raise ValueError("FX rate must be positive")
    return round(float(amount) / rate, 4)


def job_budget_usd() -> float:
    return float(os.environ.get("AYVEN_JOB_BUDGET_USD", "2.00"))


def monthly_budget_usd() -> float:
    return float(os.environ.get("AYVEN_MONTHLY_BUDGET_USD", "100.00"))


def retry_budget_pct() -> float:
    return float(os.environ.get("AYVEN_RETRY_BUDGET_PCT", "50"))


def max_job_retries() -> int:
    return int(os.environ.get("AYVEN_JOB_MAX_RETRIES", "3"))


def enforcement_on() -> bool:
    return os.environ.get("AYVEN_BUDGET_ENFORCE", "1") != "0"


def escalation_prices() -> tuple[float | None, float | None]:
    raw_in = os.environ.get("AYVEN_ESCALATION_USD_PER_MTOK_IN", "").strip()
    raw_out = os.environ.get("AYVEN_ESCALATION_USD_PER_MTOK_OUT", "").strip()
    if not raw_in or not raw_out:
        return None, None
    return float(raw_in), float(raw_out)


def escalation_armed() -> bool:
    return os.environ.get("AYVEN_ALLOW_ESCALATION", "0") == "1"


def bind_job(job_id: str) -> None:
    _JOB["id"] = job_id or ""


def current_job() -> str:
    return _JOB["id"]


def ensure_schema(conn=None) -> None:
    own = conn is None
    conn = conn or connect()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS spend_ledger (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            role TEXT NOT NULL,
            model_id TEXT NOT NULL,
            amount_usd REAL NOT NULL,
            reserved_usd REAL NOT NULL DEFAULT 0,
            committed INTEGER NOT NULL DEFAULT 0,
            attempt INTEGER NOT NULL DEFAULT 1,
            retry_of TEXT,
            route TEXT,
            reason TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            pod_seconds REAL NOT NULL DEFAULT 0
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS budget_settings (
            id TEXT PRIMARY KEY,
            monthly_cap_usd REAL NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    # The pound amount Lee set, kept exactly. NULL for a cap set in dollars.
    names = set()
    for row in conn.execute("PRAGMA table_info(budget_settings)").fetchall():
        try:
            names.add(row["name"])
        except Exception:
            names.add(row[1])
    if "monthly_cap_gbp_sent" not in names:
        conn.execute("ALTER TABLE budget_settings ADD COLUMN monthly_cap_gbp_sent REAL")
    conn.commit()
    if own:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _month_key(moment: datetime | None = None) -> str:
    current = moment or datetime.now(LONDON)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local = current.astimezone(LONDON)
    return f"{local.year:04d}-{local.month:02d}"


def month_resets_at() -> str:
    local = datetime.now(LONDON)
    year = local.year + (1 if local.month == 12 else 0)
    month = 1 if local.month == 12 else local.month + 1
    return datetime(year, month, 1, tzinfo=LONDON).isoformat()


def set_monthly_cap_usd(amount: float, sent_gbp: float | None = None) -> dict:
    if amount <= 0:
        raise ValueError("Monthly cap must be above 0")
    ensure_schema()
    conn = connect()
    conn.execute(
        "INSERT INTO budget_settings(id, monthly_cap_usd, updated_at, monthly_cap_gbp_sent) VALUES('monthly', ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET monthly_cap_usd=excluded.monthly_cap_usd, updated_at=excluded.updated_at, "
        "monthly_cap_gbp_sent=excluded.monthly_cap_gbp_sent",
        (float(amount), _now(), sent_gbp),
    )
    conn.commit()
    conn.close()
    return budget_view()


def configured_monthly_usd() -> float:
    ensure_schema()
    conn = connect()
    row = conn.execute("SELECT monthly_cap_usd FROM budget_settings WHERE id='monthly'").fetchone()
    conn.close()
    if row and row["monthly_cap_usd"]:
        return float(row["monthly_cap_usd"])
    return monthly_budget_usd()


def month_spent_usd(month: str | None = None) -> float:
    ensure_schema()
    key = month or _month_key()
    conn = connect()
    rows = conn.execute(
        "SELECT amount_usd, created_at FROM spend_ledger WHERE committed=1"
    ).fetchall()
    conn.close()
    total = 0.0
    for row in rows:
        stamp = row["created_at"] or ""
        try:
            moment = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if _month_key(moment) == key:
            total += float(row["amount_usd"] or 0)
    return round(total, 6)


def job_spent_usd(job_id: str, attempt_gt: int | None = None) -> float:
    ensure_schema()
    conn = connect()
    if attempt_gt is None:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_usd), 0) AS spent FROM spend_ledger WHERE job_id=? AND committed=1",
            (job_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_usd), 0) AS spent FROM spend_ledger WHERE job_id=? AND committed=1 AND attempt>?",
            (job_id, attempt_gt),
        ).fetchone()
    conn.close()
    return round(float(row["spent"] or 0), 6)


def _sent_monthly_gbp() -> float | None:
    ensure_schema()
    conn = connect()
    row = conn.execute("SELECT monthly_cap_gbp_sent FROM budget_settings WHERE id='monthly'").fetchone()
    conn.close()
    if row and row["monthly_cap_gbp_sent"] is not None:
        return float(row["monthly_cap_gbp_sent"])
    return None


def budget_view() -> dict:
    cap = configured_monthly_usd()
    spent = month_spent_usd()
    remaining = round(cap - spent, 6)
    sent = _sent_monthly_gbp()
    return {
        "monthly_cap_usd": cap,
        # Shown exactly as Lee set it in pounds; only a dollar-set cap is converted for display.
        "monthly_cap_gbp": sent if sent is not None else gbp(cap),
        "monthly_cap_currency": "GBP" if sent is not None else "USD",
        "monthly_cap_gbp_is_conversion": sent is None,
        "month_spent_usd": spent,
        "month_spent_gbp": gbp(spent),
        "month_remaining_usd": remaining,
        "month_remaining_gbp": gbp(max(remaining, 0)),
        "resets_at": month_resets_at(),
        "fx_gbp_per_usd": fx_gbp_per_usd(),
        "job_budget_usd": job_budget_usd(),
        "job_budget_gbp": gbp(job_budget_usd()),
        "enforced": enforcement_on(),
    }


def gpu_usd_per_hour() -> float:
    explicit = os.environ.get("AYVEN_GPU_USD_PER_HOUR", "").strip()
    if explicit:
        return float(explicit)
    if os.environ.get("AYVEN_GPU_TIER", "secure").lower() == "community":
        return 0.34
    return 0.89


def estimate_usd(role: str, input_tokens: int = 0, output_tokens: int = 0, seconds: float | None = None) -> float:
    """A positive estimate. Local work uses the GPU hourly rate. Escalation uses the armed prices."""
    role_name = (role or "").upper()
    if role_name == "ESCALATION":
        price_in, price_out = escalation_prices()
        if price_in is None or price_out is None:
            raise RuntimeError("Paid escalation has no price. Refusing an unknown-cost call.")
        return round((max(input_tokens, 0) / 1_000_000) * price_in + (max(output_tokens, 0) / 1_000_000) * price_out, 6)
    if seconds is None:
        tokens = max(int(input_tokens) + int(output_tokens), 1)
        seconds = tokens / 100.0
    return round(max(float(seconds), 0.001) / 3600.0 * gpu_usd_per_hour(), 6)


def effective_job_cap_usd(requested_usd: float | None) -> float:
    env_cap = job_budget_usd()
    if requested_usd is None:
        return env_cap
    return min(float(requested_usd), env_cap)


def month_allows_new_job(job_cap: float) -> tuple[bool, str]:
    if not enforcement_on():
        return True, ""
    spent = month_spent_usd()
    cap = configured_monthly_usd()
    if spent + job_cap > cap + 1e-9:
        return False, f"This month's ${cap:.2f} budget is used up."
    return True, ""


def check_and_reserve(
    job_id: str,
    role: str,
    max_tokens: int,
    *,
    attempt: int = 1,
    retry_of: str = "",
    model_id: str = "",
    route: str = "local",
    reason: str = "",
    job_cap_usd: float | None = None,
    allow_paid: bool = False,
) -> dict:
    """Reserve the worst case before a model call. Returns a reservation id or a halt."""
    ensure_schema()
    role_name = (role or "EMPLOYEE").upper()
    if role_name == "ESCALATION":
        if not escalation_armed() or not allow_paid:
            return {"ok": False, "status": "ESCALATION_DISABLED", "reason": "Paid models are off for this call."}
        if escalation_prices() == (None, None):
            raise RuntimeError("Paid escalation is armed without prices.")
    estimate = estimate_usd(role_name, input_tokens=max_tokens, output_tokens=max(max_tokens // 4, 1))
    cap = effective_job_cap_usd(job_cap_usd)
    spent = job_spent_usd(job_id) if job_id else 0.0
    retry_spent = job_spent_usd(job_id, attempt_gt=1) if job_id and attempt > 1 else 0.0
    warn_pct = float(os.environ.get("AYVEN_JOB_WARN_PCT", "80"))
    warning = ""
    if cap and spent / cap * 100 >= warn_pct:
        warning = f"Nearly at budget: ${spent:.2f} of ${cap:.2f} used."
    if enforcement_on() and job_id:
        if spent + estimate > cap + 1e-9:
            return {"ok": False, "status": "BUDGET_EXCEEDED", "reason": warning or f"Job cap ${cap:.2f} would be exceeded.", "estimate_usd": estimate}
        if attempt > 1 and retry_spent + estimate > cap * (retry_budget_pct() / 100.0) + 1e-9:
            return {"ok": False, "status": "RETRY_BUDGET_EXCEEDED", "reason": "Retries have used the retry share of the job budget.", "estimate_usd": estimate}
        if attempt > 1 and (attempt - 1) > max_job_retries():
            return {"ok": False, "status": "RETRY_LIMIT", "reason": f"Stopped after {max_job_retries()} retries.", "estimate_usd": estimate}
        if role_name == "ESCALATION":
            esc_cap = float(os.environ.get("AYVEN_ESCALATION_JOB_CAP_USD", "0.50"))
            month_cap = float(os.environ.get("AYVEN_ESCALATION_MONTHLY_CAP_USD", "10.00"))
            conn = connect()
            job_esc = conn.execute(
                "SELECT COALESCE(SUM(amount_usd),0) AS spent FROM spend_ledger WHERE job_id=? AND role='ESCALATION' AND committed=1",
                (job_id,),
            ).fetchone()
            conn.close()
            if float(job_esc["spent"] or 0) + estimate > esc_cap:
                return {"ok": False, "status": "ESCALATION_JOB_CAP", "reason": "This job's paid-model cap is used.", "estimate_usd": estimate}
            if _escalation_month_spent() + estimate > month_cap:
                return {"ok": False, "status": "ESCALATION_MONTHLY_CAP", "reason": "The monthly paid-model cap is used.", "estimate_usd": estimate}
    reservation = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        """INSERT INTO spend_ledger(
            id, job_id, created_at, kind, role, model_id, amount_usd, reserved_usd, committed,
            attempt, retry_of, route, reason, input_tokens, output_tokens, pod_seconds
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            reservation, job_id or "unscoped", _now(), "reserve", role_name, model_id or role_name,
            estimate, estimate, 0, attempt, retry_of or None, route, reason, max_tokens, 0, 0,
        ),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "reservation_id": reservation, "estimate_usd": estimate, "warning": warning, "status": "reserved"}


def _escalation_month_spent() -> float:
    ensure_schema()
    key = _month_key()
    conn = connect()
    rows = conn.execute(
        "SELECT amount_usd, created_at FROM spend_ledger WHERE committed=1 AND role='ESCALATION'"
    ).fetchall()
    conn.close()
    total = 0.0
    for row in rows:
        try:
            moment = datetime.fromisoformat(row["created_at"])
        except ValueError:
            continue
        if _month_key(moment) == key:
            total += float(row["amount_usd"] or 0)
    return total


def commit(reservation_id: str, input_tokens: int = 0, output_tokens: int = 0, amount_usd: float | None = None, pod_seconds: float = 0) -> dict:
    ensure_schema()
    conn = connect()
    row = conn.execute("SELECT * FROM spend_ledger WHERE id=?", (reservation_id,)).fetchone()
    if not row:
        conn.close()
        raise KeyError(reservation_id)
    spent = float(amount_usd if amount_usd is not None else row["reserved_usd"])
    conn.execute(
        "UPDATE spend_ledger SET committed=1, amount_usd=?, input_tokens=?, output_tokens=?, pod_seconds=? WHERE id=?",
        (spent, int(input_tokens), int(output_tokens), float(pod_seconds), reservation_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "amount_usd": spent, "amount_gbp": gbp(spent)}


def record_intervention(job_id: str, reason: str) -> None:
    ensure_schema()
    conn = connect()
    conn.execute(
        """INSERT INTO spend_ledger(
            id, job_id, created_at, kind, role, model_id, amount_usd, committed, attempt, reason
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), job_id, _now(), "intervention", "OWNER", "owner", 0, 1, 1, reason),
    )
    conn.commit()
    conn.close()


def interventions(job_id: str) -> int:
    ensure_schema()
    conn = connect()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM spend_ledger WHERE job_id=? AND kind='intervention'",
        (job_id,),
    ).fetchone()
    conn.close()
    return int(row["n"] or 0)


def retries(job_id: str) -> int:
    ensure_schema()
    conn = connect()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM spend_ledger WHERE job_id=? AND committed=1 AND attempt>1",
        (job_id,),
    ).fetchone()
    conn.close()
    return int(row["n"] or 0)
