"""Verifier: deterministic checks that evidence and arithmetic hold."""

from __future__ import annotations

from .calc import to_pence
from .quoting import quote_internal_doors
from .research import is_availability_text


def verify(objective: str, task_class: str, claims: list[dict], research: dict) -> dict:
    checked = 0
    passed = 0
    notes = []
    arithmetic_ok = True
    calc_claims = [c for c in claims if c["claim_type"] == "CALCULATION"]
    if task_class == "internal_door_quote" and calc_claims:
        fresh = quote_internal_doors(objective)
        arithmetic_ok = fresh is not None
        if fresh:
            expected = {
                "labour_per_door": fresh["scenarios"]["labour_per_door"]["total_ex_vat"],
                "labour_per_job": fresh["scenarios"]["labour_per_job"]["total_ex_vat"],
            }
            if to_pence(expected["labour_per_door"]) != fresh["pence_check"]["labour_per_door"]:
                arithmetic_ok = False
                notes.append("pence check diverged from the expression total")
            blob = " ".join(c["claim_text"] for c in calc_claims)
            for total in expected.values():
                checked += 1
                if total in blob:
                    passed += 1
                else:
                    notes.append(f"missing scenario total {total}")
                    arithmetic_ok = False
    for claim in claims:
        if claim["claim_type"] in ("MISSING_INFORMATION", "AMBIGUITY", "ASSUMPTION"):
            checked += 1
            if claim["evidence_text"]:
                passed += 1
            else:
                notes.append(f"claim {claim['id']} has no evidence text")
            continue
        if claim["claim_type"] == "CALCULATION":
            continue
        checked += 1
        evidence = claim.get("evidence_text") or ""
        if claim["status"] == "STALE":
            passed += 1
            continue
        if not evidence:
            notes.append(f"material claim {claim['id']} lacks evidence")
            continue
        if claim["status"] in ("SUPPORTED", "PARTIALLY_SUPPORTED"):
            if is_availability_text(claim["claim_text"]) and claim.get("freshness") != "LIVE":
                notes.append(f"availability claim {claim['id']} is not live")
                continue
            passed += 1
        else:
            notes.append(f"claim {claim['id']} status {claim['status']}")
    failures = research.get("failures") or []
    rate = round(passed / checked, 2) if checked else 1.0
    return {
        "stage": "verifier",
        "arithmetic_ok": arithmetic_ok,
        "claims_checked": checked,
        "claims_passed": passed,
        "pass_rate": rate,
        "retrieval_failures": len(failures),
        "notes": notes[:20],
        "current_live_web": research.get("mode") == "live" and not failures,
    }
