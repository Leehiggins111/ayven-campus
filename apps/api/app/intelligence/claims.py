"""Claim ledger. Confidence comes from evidence quality, not model self-belief."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ..db import connect
from .research import is_availability_text
from .think import strip_think

STATUSES = ("SUPPORTED", "PARTIALLY_SUPPORTED", "UNVERIFIED", "CONTRADICTED", "STALE")

_RANK_CONFIDENCE = {
    "PRIMARY_OFFICIAL": 0.82,
    "HIGH_QUALITY_SECONDARY": 0.66,
    "OTHER_SECONDARY": 0.48,
    "COMMUNITY": 0.3,
    "UNKNOWN": 0.18,
    "DETERMINISTIC": 0.9,
    "INPUT": 0.86,
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def confidence_for(source_type: str, freshness: str, evidence_level: str) -> tuple[float, str]:
    base = _RANK_CONFIDENCE.get(source_type, 0.2)
    status = "SUPPORTED"
    if evidence_level == "snippet":
        base = min(base, 0.4)
        status = "PARTIALLY_SUPPORTED"
    if freshness == "FIXTURE_SNAPSHOT":
        base = min(base, 0.74)
    if freshness == "STALE":
        return 0.15, "STALE"
    if source_type in ("UNKNOWN", "COMMUNITY") and evidence_level != "deterministic":
        status = "PARTIALLY_SUPPORTED" if status == "SUPPORTED" else status
    return round(base, 2), status


def add_claim(
    package_id: str,
    agent_id: str,
    claim_text: str,
    claim_type: str,
    *,
    evidence_text: str = "",
    source_url: str = "",
    source_type: str = "UNKNOWN",
    retrieved_at: str = "",
    freshness: str = "UNKNOWN",
    evidence_level: str = "page",
    source_title: str = "",
    status: str | None = None,
    supersedes: str | None = None,
) -> dict:
    text = strip_think(claim_text)
    evidence = strip_think(evidence_text)
    if is_availability_text(text) and freshness != "LIVE":
        freshness = "STALE"
        status = "STALE"
    conf, auto = confidence_for(source_type, freshness, evidence_level)
    if status is None:
        status = auto
    if status not in STATUSES:
        status = "UNVERIFIED"
    if not evidence and source_type not in ("DETERMINISTIC", "INPUT") and status == "SUPPORTED":
        status = "UNVERIFIED"
        conf = min(conf, 0.2)
    claim_id = str(uuid.uuid4())
    ts = now()
    row = {
        "id": claim_id,
        "package_id": package_id,
        "agent_id": agent_id,
        "claim_text": text,
        "claim_type": claim_type,
        "source_id": "",
        "evidence_text": evidence,
        "source_url": source_url,
        "source_type": source_type,
        "retrieved_at": retrieved_at or ts,
        "freshness": freshness,
        "verification_status": "PENDING",
        "confidence": conf,
        "challenged_by": "",
        "challenge_reason": "",
        "supersedes": supersedes or "",
        "status": status,
        "created_at": ts,
        "updated_at": ts,
    }
    conn = connect()
    conn.execute(
        """INSERT INTO claims(
            id,package_id,agent_id,claim_text,claim_type,source_id,evidence_text,source_url,
            source_type,retrieved_at,freshness,verification_status,confidence,challenged_by,
            challenge_reason,supersedes,status,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [row[k] for k in (
            "id", "package_id", "agent_id", "claim_text", "claim_type", "source_id", "evidence_text",
            "source_url", "source_type", "retrieved_at", "freshness", "verification_status", "confidence",
            "challenged_by", "challenge_reason", "supersedes", "status", "created_at", "updated_at",
        )],
    )
    if evidence or source_url:
        conn.execute(
            """INSERT INTO claim_evidence(
                id,claim_id,package_id,source_url,source_title,source_type,evidence_text,
                retrieved_at,rank,supports,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), claim_id, package_id, source_url, source_title, source_type,
                evidence[:4000], row["retrieved_at"], source_type, status, ts,
            ),
        )
    conn.commit()
    conn.close()
    return row


def list_claims(package_id: str | None = None, project_package_ids: list[str] | None = None) -> list[dict]:
    conn = connect()
    if package_id:
        rows = conn.execute("SELECT * FROM claims WHERE package_id=? ORDER BY created_at", (package_id,)).fetchall()
    elif project_package_ids:
        marks = ",".join("?" * len(project_package_ids))
        rows = conn.execute(f"SELECT * FROM claims WHERE package_id IN ({marks}) ORDER BY created_at", project_package_ids).fetchall()
    else:
        rows = conn.execute("SELECT * FROM claims ORDER BY created_at").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def challenge(claim_id: str, challenger: str, reason: str, new_status: str) -> None:
    conn = connect()
    conn.execute(
        "UPDATE claims SET challenged_by=?, challenge_reason=?, status=?, verification_status=?, updated_at=? WHERE id=?",
        (challenger, strip_think(reason)[:500], new_status, "CHALLENGED", now(), claim_id),
    )
    conn.commit()
    conn.close()


def mark_verified(claim_id: str, status: str) -> None:
    conn = connect()
    conn.execute(
        "UPDATE claims SET verification_status=?, status=?, updated_at=? WHERE id=?",
        ("CHECKED", status, now(), claim_id),
    )
    conn.commit()
    conn.close()


def claims_from_evidence(package_id: str, agent_id: str, evidence: list[dict]) -> list[dict]:
    created = []
    for item in evidence:
        passage = (item.get("extracted_content") or "").strip()
        if not passage:
            continue
        meta = item.get("metadata") or {}
        freshness = meta.get("freshness") or "UNKNOWN"
        source_type = meta.get("source_rank") or "UNKNOWN"
        claim = add_claim(
            package_id,
            agent_id,
            passage[:500],
            "ROUTE" if meta.get("channel") else "FACT",
            evidence_text=passage[:1200],
            source_url=item.get("source_url") or "",
            source_type=source_type,
            retrieved_at=item.get("timestamp") or "",
            freshness=freshness,
            evidence_level=meta.get("evidence_level") or "page",
            source_title=item.get("source_title") or "",
        )
        created.append(claim)
    return created


def claims_from_quote(package_id: str, agent_id: str, quote: dict) -> list[dict]:
    created = []
    per_door = quote["scenarios"]["labour_per_door"]["total_ex_vat"]
    per_job = quote["scenarios"]["labour_per_job"]["total_ex_vat"]
    created.append(add_claim(
        package_id, agent_id,
        f"If labour is per door, the provisional ex-VAT total is £{per_door}. This is not a final quote.",
        "CALCULATION",
        evidence_text=f"expression totals labour_per_door={per_door} pence_check={quote['pence_check']['labour_per_door']}",
        source_type="DETERMINISTIC",
        freshness="INPUT",
        evidence_level="deterministic",
        source_title="ayven-calculator",
    ))
    created.append(add_claim(
        package_id, agent_id,
        f"If labour is per job, the provisional ex-VAT total is £{per_job}. This is not a final quote.",
        "CALCULATION",
        evidence_text=f"expression totals labour_per_job={per_job} pence_check={quote['pence_check']['labour_per_job']}",
        source_type="DETERMINISTIC",
        freshness="INPUT",
        evidence_level="deterministic",
        source_title="ayven-calculator",
    ))
    created.append(add_claim(
        package_id, agent_id,
        f"The figure £{quote['per_door_ex_delivery']} per door appears only inside the per-door labour scenario, excluding delivery, and is not a final price.",
        "CALCULATION",
        evidence_text=f"per_door_ex_delivery={quote['per_door_ex_delivery']}",
        source_type="DETERMINISTIC",
        freshness="INPUT",
        evidence_level="deterministic",
    ))
    created.append(add_claim(
        package_id, agent_id,
        "Labour of the stated amount is AMBIGUOUS: the input does not prove per-door or per-job. Neither scenario is selected.",
        "AMBIGUITY",
        evidence_text=f"labour_unit={quote['labour_unit']}",
        source_type="INPUT",
        freshness="INPUT",
        evidence_level="deterministic",
        status="SUPPORTED",
    ))
    created.append(add_claim(
        package_id, agent_id,
        "VAT was not stated. No VAT rate was applied.",
        "AMBIGUITY",
        evidence_text="vat=UNKNOWN_NOT_APPLIED",
        source_type="INPUT",
        freshness="INPUT",
        evidence_level="deterministic",
        status="SUPPORTED",
    ))
    created.append(add_claim(
        package_id, agent_id,
        "Hinges are assumed once per door for the scenarios only. Hinge count and positions were not given, so that assumption is not proven.",
        "ASSUMPTION",
        evidence_text=quote["hinges_unit"],
        source_type="INPUT",
        freshness="INPUT",
        evidence_level="deterministic",
        status="PARTIALLY_SUPPORTED",
    ))
    for field in quote["missing_fields"]:
        created.append(add_claim(
            package_id, agent_id,
            f"Missing before any quote: {field}",
            "MISSING_INFORMATION",
            evidence_text="Required by the internal-door-quoting skill because the objective does not state it.",
            source_type="INPUT",
            freshness="INPUT",
            evidence_level="deterministic",
            status="SUPPORTED",
        ))
    return created


def material_supported(claims: list[dict]) -> list[dict]:
    return [c for c in claims if c["status"] in ("SUPPORTED", "PARTIALLY_SUPPORTED") and c["claim_type"] not in ("MISSING_INFORMATION",)]
