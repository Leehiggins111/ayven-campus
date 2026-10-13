"""Pod lifetime, idle stop, and hard spend cap.

This module decides. It does not create a pod and it does not call RunPod.
The live script asks `decide` on a clock the caller supplies, including tests
that pass a fake clock.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field


COMMUNITY_USD_PER_HOUR = 0.34
SECURE_USD_PER_HOUR = 0.89
COMMUNITY_HARD_CAP_USD = 1.10
SECURE_HARD_CAP_USD = 2.75
MAX_WALL_S = 3.0 * 3600
IDLE_STOP_S = 600
STARTUP_TIMEOUT_S = 2400
MAX_LIFETIME_S = 10800


def tier_rate(tier: str) -> float:
    explicit = os.environ.get("AYVEN_GPU_USD_PER_HOUR", "").strip()
    if explicit:
        return float(explicit)
    return COMMUNITY_USD_PER_HOUR if tier == "community" else SECURE_USD_PER_HOUR


def hard_cap_usd(tier: str) -> float:
    if tier == "community":
        return float(os.environ.get("AYVEN_GPU_HARD_CAP_USD", str(COMMUNITY_HARD_CAP_USD)))
    return float(os.environ.get("AYVEN_GPU_HARD_CAP_USD", str(SECURE_HARD_CAP_USD)))


def pod_id_hash(pod_id: str) -> str:
    digest = hashlib.sha256((pod_id or "").encode()).hexdigest()
    return f"sha256:{digest}"


@dataclass
class PodClock:
    created_at: float
    last_active_at: float
    tier: str = "community"
    healthy: bool = False
    healthy_at: float | None = None
    in_flight: int = 0
    gpu_util_pct: float = 0.0
    consecutive_failures: int = 0
    results_confirmed: bool = False
    manual_stop: bool = False
    unhealthy_since: float | None = None
    notes: list[str] = field(default_factory=list)

    def mark_active(self, now: float) -> None:
        self.last_active_at = now

    def accrue_seconds(self, now: float) -> float:
        return max(0.0, now - self.created_at)


def limits() -> dict:
    return {
        "idle_s": int(os.environ.get("AYVEN_GPU_IDLE_STOP_S", str(IDLE_STOP_S))),
        "lifetime_s": int(os.environ.get("AYVEN_POD_MAX_LIFETIME_S", str(MAX_LIFETIME_S))),
        "startup_s": int(os.environ.get("AYVEN_POD_STARTUP_TIMEOUT_S", str(STARTUP_TIMEOUT_S))),
        "wall_s": int(os.environ.get("AYVEN_POD_MAX_WALL_S", str(int(MAX_WALL_S)))),
        "health_fail_s": int(os.environ.get("AYVEN_POD_HEALTH_FAIL_S", "300")),
    }


def decide(clock: PodClock, now: float) -> dict:
    """Return the first stop reason, or continue.

    Order matches the spend-controls spec: finished export, lifetime, spend
    cap, idle, two failures or a 5-minute health failure, then a manual stop.
    """
    bounds = limits()
    elapsed = clock.accrue_seconds(now)
    rate = tier_rate(clock.tier)
    accrued = elapsed / 3600.0 * rate
    cap = hard_cap_usd(clock.tier)
    idle_for = now - clock.last_active_at
    util_idle = clock.gpu_util_pct < 5 and clock.in_flight == 0
    reason = ""
    if clock.results_confirmed:
        reason = "completed"
    elif elapsed >= bounds["lifetime_s"] or elapsed >= bounds["wall_s"]:
        reason = "lifetime_cap"
    elif accrued >= cap - 1e-9:
        reason = "budget_cap"
    elif util_idle and idle_for >= bounds["idle_s"]:
        reason = "idle"
    elif clock.consecutive_failures >= 2:
        reason = "failure"
    elif (
        not clock.healthy
        and clock.healthy_at is not None
        and clock.unhealthy_since is not None
        and (now - clock.unhealthy_since) >= bounds["health_fail_s"]
    ):
        reason = "failure"
    elif not clock.healthy and clock.healthy_at is None and elapsed >= bounds["startup_s"]:
        reason = "failure"
    elif clock.manual_stop:
        reason = "manual"
    return {
        "stop": bool(reason),
        "reason": reason or "continue",
        "elapsed_s": round(elapsed, 3),
        "accrued_usd": round(accrued, 4),
        "cap_usd": cap,
        "rate_usd_per_hour": rate,
        "idle_s": round(idle_for if util_idle else 0, 3),
        "action": "delete" if reason else "continue",
    }


def delete_command(pod_id_env: str = "RUNPOD_POD_ID") -> str:
    """Terminate the pod. Stop is not used: a stopped pod keeps billing volume disk.

    Current RunPod docs (manage-pods, fetched 13 Oct 2026) name
    `runpodctl pod delete`. Older doc snapshots name `runpodctl remove pod`.
    The command tries the current verb, then the older one, then the REST
    DELETE. RUNPOD_POD_ID and a pod-scoped RUNPOD_API_KEY are injected by
    RunPod (docs.runpod.io/pods/templates/environment-variables).
    """
    return (
        f'runpodctl pod delete "${pod_id_env}" '
        f'|| runpodctl remove pod "${pod_id_env}" '
        f'|| curl --fail --request DELETE '
        f'--url "https://rest.runpod.io/v1/pods/${pod_id_env}" '
        f'--header "Authorization: Bearer $RUNPOD_API_KEY"'
    )


def self_terminate_preamble() -> str:
    """A backstop that lives inside the pod if the controller process dies."""
    lifetime = int(os.environ.get("AYVEN_POD_MAX_LIFETIME_S", str(MAX_LIFETIME_S)))
    return "\n".join([
        "#!/bin/sh",
        "set -u",
        f"lifetime={lifetime}",
        "(",
        "  sleep \"$lifetime\"",
        "  echo \"AYVEN lifetime cap reached; deleting pod\"",
        "  " + delete_command(),
        ") &",
        "echo $! > /tmp/ayven-lifetime.pid",
    ])
