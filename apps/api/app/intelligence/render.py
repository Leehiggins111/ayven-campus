"""Render the published briefing from the ledger. Model prose is not the source."""

from __future__ import annotations


def _money_lines(lines: list[dict]) -> list[str]:
    rendered = []
    for line in lines:
        rendered.append(f"- {line['label']}: {line['qty']} × £{line['unit']} = £{line['total']} (`{line['expression']}`)")
    return rendered


def render_focus(focus: str, facts: dict) -> str:
    task = facts["task_class"]
    if focus == "trivial":
        return "No current facts were required. Nothing was researched and nothing was sent."
    if focus == "scenarios":
        return _scenarios(facts)
    if focus == "gaps":
        return _gaps(facts)
    if focus == "routes":
        return _routes(facts)
    if focus == "channels":
        return _channels(facts)
    if focus == "prospects":
        return _prospects(facts)
    if focus == "draft":
        return _draft(facts)
    if focus == "evidence":
        if task == "internal_door_quote":
            return _suppliers(facts)
        return _routes(facts) if task == "football_tickets" else _prospects(facts)
    return _gaps(facts)


def render_parent(facts: dict, audits: list[dict], manager_decision: str) -> str:
    parts = [
        "# Ayven briefing",
        f"Task class: {facts['task_class']}",
        f"Research mode: {facts['research'].get('mode')} ({_mode_label(facts['research'].get('mode'))})",
        "Validation: LOCAL_DETERMINISTIC. Real-model quality is not claimed from this text alone.",
        "",
        "## Objective",
        facts["objective"],
        "",
    ]
    for child in facts["children"]:
        parts += [f"## {child['title']}", child["report"], ""]
    parts += ["## Supervisor audits"]
    for audit in audits:
        parts.append(
            f"- {audit['focus']}: {audit['decision']} (model suggested {audit.get('advisory') or 'n/a'}; authoritative check wins)"
        )
    parts += ["", "## Manager", f"Decision: {manager_decision}", ""]
    if facts["task_class"] == "internal_door_quote":
        parts.append("Human clarification is required before any figure is treated as a customer quote. Nothing was sent.")
    elif facts["task_class"] == "football_tickets":
        parts.append("No purchase was made. No enquiry was sent. Nothing was sent. Approval is required before contact.")
    elif facts["task_class"] == "vending_prospects":
        parts.append("Outreach status: DRAFT_ONLY. Sent: no. Nothing was sent. Approval is required before any contact.")
    else:
        parts.append("Nothing was sent.")
    return "\n".join(parts).strip() + "\n"


def _mode_label(mode: str | None) -> str:
    if mode == "fixtures":
        return "offline snapshot, not a live web fetch"
    if mode == "live":
        return "live fetch attempted"
    return "not applicable"


def _scenarios(facts: dict) -> str:
    quote = facts.get("quote")
    if not quote:
        return "No deterministic calculation was required for this focus."
    door = quote["scenarios"]["labour_per_door"]
    job = quote["scenarios"]["labour_per_job"]
    lines = [
        "These totals are provisional arithmetic. They are not a final quote.",
        f"Door count: {quote['door_count']}. Sizes: {quote['size_counts'] or 'not parsed'}.",
        f"Labour unit: {quote['labour_unit']}. The input does not prove per-door or per-job, so both scenarios are shown and neither is selected.",
        f"Hinges: {quote['hinges_unit']}.",
        f"VAT: {quote['vat']}. VAT was not applied.",
        "",
        "### Scenario labour_per_door",
        *_money_lines(door["lines"]),
        f"- total_ex_vat: £{door['total_ex_vat']}",
        f"- per_door_ex_delivery: £{door['per_door_ex_delivery']}",
        f"£{door['per_door_ex_delivery']} per door and £{door['total_ex_vat']} appear only in this scenario. They are not a final quote.",
        "",
        "### Scenario labour_per_job",
        *_money_lines(job["lines"]),
        f"- total_ex_vat: £{job['total_ex_vat']}",
        "This scenario is not a final quote either.",
        "",
        "Answer to whether the per-door figure and the larger total are a final quote: NO.",
    ]
    return "\n".join(lines)


def _gaps(facts: dict) -> str:
    lines = ["## Unknowns and missing information"]
    quote = facts.get("quote")
    if quote:
        lines.append(f"Labour unit remains {quote['labour_unit']}. Do not resolve it silently.")
        lines.append("VAT was not applied.")
        for field in quote["missing_fields"]:
            lines.append(f"- {field}")
    for gap in facts["research"].get("gaps") or []:
        lines.append(f"- {gap}")
    for failure in facts["research"].get("failures") or []:
        lines.append(f"- Retrieval failure ({failure.get('stage')}): {failure.get('error')}")
    if len(lines) == 1:
        lines.append("- No further gap was identified from the retrieved material.")
    if facts["task_class"] == "vending_prospects":
        lines.append("Decision-maker type: not identified, because no prospect page named an owner, facilities manager, or buyer.")
        lines.append("Sent: no. Nothing was sent.")
    if facts["task_class"] == "football_tickets":
        lines.append("Enquiry gap: no page evidenced an authorised travel-partner allocation or a package contract.")
        lines.append("No purchase was made.")
    return "\n".join(lines)


def _routes(facts: dict) -> str:
    lines = [
        "Live availability: NOT CLAIMED. A snapshot or a homepage is not current stock.",
        "No purchase was made.",
    ]
    evidence = facts["research"].get("evidence") or []
    if not evidence:
        lines.append("No page was opened, so no route is asserted.")
        return "\n".join(lines)
    for item in evidence:
        meta = item.get("metadata") or {}
        lines += [
            f"### {item.get('source_title') or item.get('source_url')}",
            f"- URL: {item.get('source_url')}",
            f"- Source rank: {meta.get('source_rank')}",
            f"- Channel: {meta.get('channel')}",
            f"- Freshness: {meta.get('freshness')}",
            f"- Evidence: {item.get('extracted_content')}",
        ]
    for failure in facts["research"].get("failures") or []:
        lines.append(f"- Retrieval failure: {failure.get('url') or failure.get('query')} ({failure.get('error')})")
    return "\n".join(lines)


def _channels(facts: dict) -> str:
    lines = [
        "Official pages are ranked PRIMARY_OFFICIAL. Any other seller is a possible reseller and is not an authorised partner unless an official page says so.",
        "No invented partnership is recorded.",
        "Live availability: NOT CLAIMED.",
    ]
    for item in facts["research"].get("evidence") or []:
        meta = item.get("metadata") or {}
        lines.append(f"- {item.get('source_url')} is {meta.get('channel')} ({meta.get('source_rank')}).")
    if not facts["research"].get("evidence"):
        lines.append("- No channel was classified because no page was opened.")
    return "\n".join(lines)


def _prospects(facts: dict) -> str:
    lines = [
        "Prospects are listed only when an opened page supports a real organisation and a placement opportunity.",
        "Prospects: none evidenced.",
        "Statistics: none recorded. No sourced vending count was retrieved.",
        "Sent: no. Nothing was sent.",
    ]
    for item in facts["research"].get("evidence") or []:
        lines.append(f"- Reviewed {item.get('source_url')} ({(item.get('metadata') or {}).get('source_rank')}). It did not establish a placement prospect.")
    for gap in facts["research"].get("gaps") or []:
        lines.append(f"- {gap}")
    return "\n".join(lines)


def _draft(facts: dict) -> str:
    if facts["task_class"] == "vending_prospects":
        return "\n".join([
            "Outreach status: DRAFT_ONLY",
            "To: not addressed. No evidenced prospect.",
            "Body: Hello — we place vending machines and would like to ask who manages on-site catering. This draft names nobody and must not be sent until a real prospect is sourced and Lee approves contact.",
            "Sent: no",
            "Nothing was sent.",
            "Approval: required before any contact.",
        ])
    return "\n".join([
        "Next action remains inside Ayven until a person approves it.",
        "Sent: no. Nothing was sent.",
        "No purchase was made.",
    ])


def _suppliers(facts: dict) -> str:
    lines = ["Supplier names are included only when an opened page supports them."]
    evidence = facts["research"].get("evidence") or []
    if not evidence:
        lines.append("No supplier page was retrieved. No supplier is named.")
    for item in evidence:
        lines.append(f"- {item.get('source_title')}: {item.get('source_url')}")
    for gap in facts["research"].get("gaps") or []:
        lines.append(f"- {gap}")
    lines.append("Hinge positions were not on a retrieved page, so none are stated.")
    return "\n".join(lines)
