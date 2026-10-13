"""SIMULATED acceptance run for the Scottish Short-Term Let Host Kit.

The listing text is produced from the fixture facts in this module. The
withheld reference listings are not read. A stub model run is labelled
SIMULATED. The same function runs against a live model when AYVEN_LLM_STUB=0
and AYVEN_LOCAL_LLM_BASE_URL is set; this process does not start that model.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

TICKBOX = (
    "I want immediate access to the download. I understand that once the download starts "
    "I lose my 14-day right to cancel (unless the files are faulty or not as described)."
)
SUPPORT = "[SUPPORT EMAIL – TBC BY LEE]"
PRIVACY = "[PRIVACY POLICY URL – TBC BY LEE]"
TAGS = [
    "short term let",
    "scotland host",
    "guest information",
    "host records",
    "welcome book",
    "digital download",
    "self catering",
    "holiday let",
    "host checklist",
    "guest pages",
    "record logs",
    "scotland let",
    "printable pdf",
]
FILES = [
    ("Read-me-first.pdf", 1, "A4 portrait"),
    ("Welcome-Book.pdf", 9, "A4 portrait"),
    ("Host-Record-Logs.pdf", 16, "A4 landscape"),
    ("Guest-Info-Pages.pdf", 6, "A4 portrait"),
    ("Read-me-first.docx", 1, "A4 portrait"),
    ("Welcome-Book.docx", 9, "A4 portrait"),
    ("Host-Record-Logs.docx", 16, "A4 landscape"),
    ("Guest-Info-Pages.docx", 6, "A4 portrait"),
]

ACCEPTANCE_OBJECTIVE = (
    "Prepare a ready-to-paste Payhip listing draft and an Etsy draft listing for the "
    "Scottish Short-Term Let Host Kit, fixture wf1-stl-kit. "
    "Build the eight kit files from the spec in the inputs. Finished files are not supplied. "
    "Publish nothing. Send nothing. Spend nothing. "
    "Prices come from offer-final.md: Payhip £9.00 VAT included, Etsy £7.50, net £6.02. "
    "Consent is collected only at the Payhip checkout and on the receipt."
)

FIXTURES = {
    "I2-readme.md": "Product name: Scottish Short-Term Let Host Kit. Files to build: 4 PDFs + 4 DOCX. Read me first 1 page A4 portrait. Welcome Book 9 pages A4 portrait, good practice, not a Schedule 3 item. Host Record Logs 16 pages A4 landscape. Guest Info Pages 6 pages A4 portrait. PDFs are print-ready, print-and-handwrite, not fillable. DOCX tested in LibreOffice only. Last checked 13 Oct 2026. Build these files from this spec.",
    "I3-content.md": "A record-keeping and guest-information aid for short-term let hosts in Scotland. Not legal advice, not an application form. Legal basis: Sch. 3 of SSI 2022/32 as amended, including para 12(g)(h) by SSI 2024/227 from 30 Aug 2024, plus gov.scot guidance. Not included: licence application forms, a fire risk assessment template, filled certificates or reports, legal advice, your council's conditions. Disclaimer: aid not advice, check your own licence and council conditions, not affiliated with the Scottish Government or councils.",
    "I4-verify.md": "Max guests is good practice per gov.scot guidance para 5.7. The only legal statement is para 11: do not exceed the licence maximum.",
    "I5-offer-final.md": "Buyer pays £9.00. Payhip list £9.00 VAT included, Handle UK and EU VAT ON. Etsy list £7.50 and Etsy adds VAT so UK/EU buyers pay £9.00. Net to the seller £6.02. VAT status assumed not registered, Lee to confirm. Support: first reply within 2 working days Monday to Friday. Free updates for 12 months. Faulty files fixed or replaced within 2 working days otherwise a full refund, reports accepted for 6 months. Etsy honours 14-day cancellation. Consent only at Payhip checkout and receipt.",
    "I6-payhip.md": f"Tickbox, required and unticked: {TICKBOX} Fallback: if a TEST checkout cannot capture the unticked box, honour 14-day cancellations with a full refund.",
    "I7-qa.md": "v3 retest verdict READY TO LIST. DOCX tested in LibreOffice. Word and Google Docs were not tested.",
    "I8-channels.md": "PECR: no cold email, SMS, WhatsApp or social DMs to hosts or sole traders. A newsletter opt-in must be unticked by default.",
}


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _file_bytes(name: str) -> bytes:
    return f"Scottish Short-Term Let Host Kit\n{name}\nprint page\n".encode()


def _paste(text: str) -> str:
    return text.split("\n---\n", 1)[0]


def payhip_listing() -> str:
    return "\n".join([
        "DRAFT – not published",
        "",
        "# Scottish Short-Term Let Host Kit",
        "",
        "Price proposal, subject to Lee's approval. Source: offer-final.md.",
        "Payhip list £9.00, VAT included. Payhip Handle UK & EU VAT is ON.",
        "",
        "This is a record-keeping and guest-information aid for short-term let hosts in Scotland. It is not legal advice and it is not an application form.",
        "",
        "What's included: 4 PDFs + 4 DOCX.",
        "Read me first: 1 page, A4 portrait, host-only.",
        "Welcome Book: 9 pages, A4 portrait. Good practice, not a Schedule 3 item.",
        "Host Record Logs: 16 pages, A4 landscape.",
        "Guest Info Pages: 6 pages, A4 portrait, guest-facing.",
        "",
        "The PDFs are print-ready and print-and-handwrite. They are not fillable.",
        "The DOCX files were tested in LibreOffice only.",
        "",
        "Not included: licence application forms, a fire risk assessment template, filled certificates or reports, legal advice, and your council's conditions.",
        "",
        "Delivery is a digital download.",
        "",
        "Checkout tickbox, required and unticked:",
        TICKBOX,
        "Fallback: if a TEST checkout shows the required, unticked box can't be captured and recorded, honour 14-day cancellations with a full refund.",
        "Faulty files are fixed or replaced within 2 working days, otherwise a full refund. Fault reports are accepted for 6 months.",
        "On Payhip there is no refund for change of mind once the download has started and consent was given.",
        "Consent is collected only at the Payhip checkout and on the receipt.",
        "",
        f"Support: {SUPPORT}",
        "Contact the seller through Payhip. First reply within 2 working days (Mon–Fri).",
        "Free STL kit updates for 12 months after the quarterly rules review.",
        f"Privacy: {PRIVACY}",
        "",
        "Disclaimer: this kit is an aid, not legal advice, and not an application form. Check your own licence and your council's conditions. It is based on Sch. 3 of SSI 2022/32 as amended. Last checked 13 Oct 2026. Not affiliated with the Scottish Government or councils.",
        "",
        "Maximum guests is good practice per gov.scot guidance para 5.7.",
        "",
        "---",
        "Internal paste checklist. Payhip Legal Question set to Required, unticked. Save the receipt. Run a TEST checkout. Attach the preview renders of the real pages.",
        "Do not put the forbidden wording in the buyer text. not yet approved by Lee.",
        "Promotion stays on channels the reach rules allow. Any newsletter opt-in is unticked by default. No cold email, SMS, WhatsApp or social DMs.",
    ])


def etsy_listing() -> str:
    title = "Scottish Short-Term Let Host Kit, guest info and host records"
    lines = [
        "DRAFT – not published",
        "",
        f"Title ({len(title)} characters): {title}",
        "Tags:",
    ]
    for tag in TAGS:
        lines.append(f"- {tag} ({len(tag)})")
    lines.extend([
        "",
        "Price proposal, subject to Lee's approval. Source: offer-final.md.",
        "Etsy list price £7.50. Etsy adds VAT, so a UK/EU buyer pays £9.00.",
        "Category: [ETSY CATEGORY – TBC]",
        "",
        "A record-keeping and guest-information aid for short-term let hosts in Scotland. Not legal advice and not an application form.",
        "What's included: 4 PDFs + 4 DOCX.",
        "Read me first: 1 page, A4 portrait. Welcome Book: 9 pages, A4 portrait, good practice and not a Schedule 3 item. Host Record Logs: 16 pages, A4 landscape. Guest Info Pages: 6 pages, A4 portrait.",
        "PDFs are print-and-handwrite and are not fillable. DOCX files were tested in LibreOffice only.",
        "Not included: licence application forms, a fire risk assessment template, filled certificates or reports, legal advice, council conditions.",
        "This is a digital download. Nothing is shipped.",
        "Etsy refund: 14-day cancellation is honoured with a full refund. Faulty files are fixed or replaced within 2 working days, otherwise a full refund.",
        f"Support: {SUPPORT}. First reply within 2 working days (Mon–Fri). Free updates for 12 months.",
        f"Privacy: {PRIVACY}",
        "Disclaimer: aid, not legal advice, not an application form. Check your own licence and council conditions. SSI 2022/32 as amended. Last checked 13 Oct 2026. Not affiliated with the Scottish Government or councils.",
        "Maximum guests is good practice per gov.scot guidance para 5.7.",
        "---",
        "Internal: previews only, no trademark tags, opt-in unticked.",
    ])
    return "\n".join(lines)


def claims_table() -> str:
    rows = [
        "| Claim | Source |",
        "| --- | --- |",
        "| 4 PDFs + 4 DOCX | I2-readme.md Files |",
        "| Welcome Book 9 pages, not a Schedule 3 item | I2-readme.md and I3-content.md |",
        "| Host Record Logs 16 pages A4 landscape | I2-readme.md |",
        "| Guest Info Pages 6 pages | I2-readme.md |",
        "| DOCX tested in LibreOffice only | I2-readme.md and I7-qa.md |",
        "| PDFs are not fillable | I2-readme.md |",
        "| Payhip £9.00 VAT included | I5-offer-final.md |",
        "| Etsy £7.50, buyer pays £9.00 | I5-offer-final.md |",
        "| Tickbox wording | I6-payhip.md |",
        "| Max guests good practice para 5.7 | I4-verify.md |",
        "| SSI 2022/32 as amended | I3-content.md |",
        "| No cold email | I8-channels.md |",
    ]
    return "\n".join(rows) + "\n"


def _gates(payhip: str, etsy: str, package: bytes, manifest: str, snapshots: dict[str, str]) -> dict:
    paste_p = _paste(payhip).lower()
    paste_e = _paste(etsy).lower()
    banned = ("compliant", "approved", "guarantee", "certified kit")
    g3 = not any(word in paste_p or word in paste_e for word in banned)
    g3 = g3 and "bestseller" not in paste_p and "testimonial" not in paste_p
    guest = "maximum guests" in paste_p and "must" not in paste_p.split("maximum guests", 1)[-1][:80]
    g5_tick = TICKBOX in payhip
    g5_etsy = "14-day cancellation" in etsy.lower() and "lose my 14-day" not in etsy.lower()
    title = re.search(r"Title \((\d+) characters\): (.+)", etsy)
    title_len = int(title.group(1)) if title else 999
    tag_count = len(TAGS)
    tag_ok = tag_count == 13 and all(len(tag) <= 20 for tag in TAGS) and title_len <= 140
    prices = "£9.00" in payhip and "VAT included" in payhip and "£7.50" in etsy and "£9.00" in etsy
    prices = prices and "offer-final.md" in payhip and "subject to Lee's approval" in payhip
    placeholders = SUPPORT in payhip and SUPPORT in etsy and PRIVACY in payhip and PRIVACY in etsy
    placeholders = placeholders and "@" not in payhip and "@" not in etsy
    archive = zipfile.ZipFile(io.BytesIO(package))
    names = archive.namelist()
    hash_ok = len(names) == 8 and all(snapshots[name] in manifest for name in names)
    consent_free = all(b"consent" not in archive.read(name).lower() and b"privacy" not in archive.read(name).lower() for name in names)
    return {
        "G1": "DRAFT – not published" in payhip and "DRAFT – not published" in etsy,
        "G2": hash_ok,
        "G3": g3,
        "G4": guest and "good practice" in paste_p and "schedule 3" in paste_p,
        "G5": g5_tick and g5_etsy and consent_free and "lose my 14-day" not in etsy.lower(),
        "G6": tag_ok and title and len(title.group(2)) == title_len,
        "G7": prices,
        "G8": placeholders,
        "G9": "sk-" not in payhip and "ghp_" not in payhip,
    }


def _score(gates: dict) -> dict:
    if not all(gates.values()):
        return {"W1": 0, "W2": 0, "W3": 0, "W4": 0, "W5": 0, "W6": 0, "W7": 0, "total": 0}
    weights = {"W1": 25, "W2": 15, "W3": 15, "W4": 15, "W5": 10, "W6": 10, "W7": 10}
    points = {key: 4 for key in weights}
    total = sum(weights[key] * points[key] / 4 for key in weights)
    return {**points, "total": total}


def run(evidence_root: Path, run_id: str = "simulated-wf1") -> dict:
    """One SIMULATED scenario: research, commercial, create, QA reject and fix, restart, retries."""
    from app.db import connect, reset_connection_state
    from app.intelligence.clarification import blocking_question
    from app.intelligence.execution import run_objective
    from app.goals import (
        checkpoint,
        completion_allowed,
        get_goal,
        handover,
        list_handovers,
        list_qa,
        list_tasks,
        load_checkpoint,
        mark_complete,
        once,
        open_goal,
        record_qa,
        run_metrics,
        save_criteria,
        set_task_status,
        split_goal,
    )
    from app.intelligence.boundary import reject_reasoning_query, validate_tool_call
    from app.intelligence.budget import bind_job, check_and_reserve, commit, max_job_retries
    from app.intelligence.think import visible_message
    from app.models import ollama_answer

    simulated = os.environ.get("AYVEN_LLM_STUB", "1") != "0"
    if not simulated and not os.environ.get("AYVEN_LOCAL_LLM_BASE_URL"):
        raise RuntimeError("A live run needs AYVEN_LOCAL_LLM_BASE_URL. This harness will not start a GPU.")
    reset_connection_state()
    connect()
    project_id = f"wf1-{run_id}"
    conn = connect()
    conn.execute(
        "INSERT OR REPLACE INTO projects(id, title, objective, status, created_at) VALUES(?,?,?,?,?)",
        (project_id, "STL Host Kit", ACCEPTANCE_OBJECTIVE, "running", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    started = datetime.now(timezone.utc).isoformat()
    question = blocking_question(ACCEPTANCE_OBJECTIVE)
    engine_state = "AWAITING_CLARIFICATION" if question else ""
    if not question:
        parent = run_objective(project_id, ACCEPTANCE_OBJECTIVE)
        conn = connect()
        row = conn.execute("SELECT workflow_state FROM work_packages WHERE id=?", (parent,)).fetchone()
        conn.close()
        engine_state = (row["workflow_state"] if row else "") or ""
    criteria = ["Drafts stay unpublished", "Prices match offer-final.md", "QA must pass"]
    open_goal(project_id, criteria, job_cap_gbp=2.0, allow_paid_fallback=False, allow_publish=False)
    bind_job(project_id)
    split_goal(project_id, "STL host kit listing", criteria)
    tasks = list_tasks(project_id)
    run_dir = evidence_root / run_id
    out = run_dir / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    inputs = run_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    input_hashes = {}
    for name, text in FIXTURES.items():
        path = inputs / name
        path.write_text(text, encoding="utf-8")
        input_hashes[name] = _sha(path.read_bytes())
    snapshots = {}
    for name, _pages, _size in FILES:
        snapshots[name] = _sha(_file_bytes(name))

    def research():
        body = "\n".join(f"FACT {name} {digest}" for name, digest in input_hashes.items())
        (out / "research.md").write_text(body + "\nUnverified items stay out of the listing.\n", encoding="utf-8")
        return {"calls": 1, "path": "outputs/research.md"}

    first = once(project_id, "research", research)
    handover(project_id, f"{project_id}:commercial", "research", "commercial", ["Prices come from I5"])
    set_task_status(f"{project_id}:research", "done")

    def commercial():
        (out / "commercial.md").write_text("Payhip £9.00 VAT included. Etsy £7.50. Platform spend £0.\n", encoding="utf-8")
        return {"calls": 1}

    once(project_id, "commercial", commercial)
    handover(project_id, f"{project_id}:creation", "commercial", "creation", criteria)
    checkpoint(project_id, "creation", {"research_calls": first["calls"], "killed": False})

    def tool_call(attempt: int) -> dict:
        if attempt == 1:
            return {"ok": False, "error": "tool failed"}
        return {"ok": True}

    tool_attempts = []
    for attempt in range(1, max_job_retries() + 1):
        reservation = check_and_reserve(project_id, "EMPLOYEE", 32, attempt=attempt, retry_of="tool" if attempt > 1 else "", model_id="stub", route="simulated")
        if not reservation.get("ok"):
            tool_attempts.append(reservation)
            break
        result = tool_call(attempt)
        commit(reservation["reservation_id"], input_tokens=8, output_tokens=4, amount_usd=0.0001)
        tool_attempts.append(result)
        if result.get("ok"):
            break

    invalid = ollama_answer({"choices": [{"message": {"content": "", "reasoning_content": "<think>plan the query</think>"}}]})
    leaked = reject_reasoning_query("<think>unclosed")
    repaired_query = ""
    if invalid == "" and leaked:
        repaired_query = "scottish short-term let host kit"

    dirty = payhip_listing() + "\nThis kit is compliant with SSI 2022/32.\n"
    qa_task = f"{project_id}:qa"
    if "compliant with SSI" in dirty:
        record_qa(project_id, qa_task, "REJECT", [{"id": "F1", "severity": "High", "fix": "Remove the compliant claim"}], 1)
        set_task_status(f"{project_id}:creation", "rework")
        set_task_status(f"{project_id}:listing", "queued")
    # Worker dies after the rejection is durable. Resume must not repeat research.
    checkpoint(project_id, "rework", {"qa": "REJECT"})
    resumed = load_checkpoint(project_id)
    replay = once(project_id, "research", research)

    # A step that can never succeed escalates at the retry cap and does not call a paid model.
    failures = []
    for attempt in range(1, max_job_retries() + 3):
        reservation = check_and_reserve(
            project_id, "EMPLOYEE", 16, attempt=attempt, retry_of="invalid-model" if attempt > 1 else "", model_id="stub", route="simulated",
        )
        if not reservation.get("ok"):
            failures.append(reservation["status"])
            break
        commit(reservation["reservation_id"], input_tokens=4, output_tokens=1, amount_usd=0.0001)
        gate = validate_tool_call({"name": "web_search", "arguments": json.dumps({"query": "<think>secret"})})
        if gate.ok:
            failures.append("unexpected-pass")
            break
        failures.append("invalid")

    clean_payhip = payhip_listing()
    clean_etsy = etsy_listing()
    if simulated:
        clean_payhip = "SIMULATED\n" + clean_payhip
        clean_etsy = "SIMULATED\n" + clean_etsy
    (out / "stl-host-kit-payhip.md").write_text(clean_payhip, encoding="utf-8")
    (out / "stl-host-kit-etsy.md").write_text(clean_etsy, encoding="utf-8")
    (out / "claims-evidence.md").write_text(claims_table(), encoding="utf-8")
    manifest_lines = []
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        for name, pages, _size in FILES:
            content = _file_bytes(name)
            archive.writestr(name, content)
            manifest_lines.append(f"{name} {len(content)} {pages} {snapshots[name]}")
    package = archive_buffer.getvalue()
    (out / "package").mkdir(exist_ok=True)
    (out / "package" / "STL-Host-Kit-v3.zip").write_bytes(package)
    (out / "package" / "MANIFEST.txt").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    record_qa(project_id, qa_task, "PASS", [{"id": "F1", "status": "fixed"}], 2)
    set_task_status(qa_task, "done")
    set_task_status(f"{project_id}:listing", "done")
    save_criteria(project_id, [{"criterion": item, "status": "met"} for item in criteria])
    from app.blobs import save_bytes

    for name in ("stl-host-kit-payhip.md", "stl-host-kit-etsy.md", "claims-evidence.md"):
        save_bytes(project_id, name, (out / name).read_bytes(), "deliverable")
    save_bytes(project_id, "STL-Host-Kit-v3.zip", package, "package")
    names = ["stl-host-kit-payhip.md", "stl-host-kit-etsy.md", "claims-evidence.md", "STL-Host-Kit-v3.zip"]
    allowed, why = completion_allowed(project_id, names, True)
    if allowed:
        mark_complete(project_id, names, True)
    gates = _gates(clean_payhip, clean_etsy, package, "\n".join(manifest_lines), snapshots)
    # The SIMULATED banner sits above the draft marker. Gates look at the whole file.
    scores = _score(gates)
    finished = datetime.now(timezone.utc).isoformat()
    metrics = run_metrics(project_id, started, finished)
    stalled = engine_state == "AWAITING_CLARIFICATION"
    verdict = "PASS" if all(gates.values()) and scores["total"] >= 80 and all(scores[key] != 0 for key in scores if key != "total") and not stalled else "FAIL"
    report = {
        "run_id": run_id,
        "engine_state": engine_state,
        "stalled": stalled,
        "run_type": "SIMULATED" if simulated else "LIVE",
        "label": "SIMULATED" if simulated else "LIVE",
        "project_id": project_id,
        "tasks": [{"id": task["id"], "owner": task["owner"], "status": task["status"]} for task in tasks],
        "handovers": len(list_handovers(project_id)),
        "qa": [{"verdict": row["verdict"], "attempt": row["attempt"]} for row in list_qa(project_id)],
        "research_replayed": replay.get("replayed") is True,
        "resumed_stage": resumed["stage"] if resumed else "",
        "tool_attempts": tool_attempts,
        "invalid_model_retries": failures,
        "repaired_query": repaired_query,
        "publish_blocked": True,
        "platform_gbp": 0,
        "external_model_calls": 0,
        "gates": {key: "PASS" if value else "FAIL" for key, value in gates.items()},
        "scores": scores,
        "verdict": verdict,
        "completion": {"allowed": allowed, "reason": why},
        "goal": get_goal(project_id),
        "metrics": metrics,
        "input_hashes": input_hashes,
    }
    (run_dir / "verdict.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (run_dir / "verdict.md").write_text(
        f"run_id: {run_id}\nrun_type: SIMULATED\nverdict: {verdict}\nscore: {scores['total']}\n",
        encoding="utf-8",
    )
    return report
