from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone

from . import events
from .db import connect
from .distribution import route, update_package
from .llm import complete
from .tools import fetch_page, web_search

AGENT = "web-researcher"
DEPT = "research"
MAX_SEARCHES = 4
MAX_FETCHES = 5
MAX_SECONDS = 75


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_agent(**fields) -> None:
    conn = connect()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE agents SET {sets} WHERE id=?", [*fields.values(), AGENT])
    conn.commit()
    conn.close()


def _emit(type_: str, package, summary: str, status: str, progress: float, tool: str | None = None) -> None:
    events.emit(
        type_,
        project_id=package["project_id"],
        task_id=package.get("task_id"),
        agent_id=AGENT,
        department_id=DEPT,
        status=status,
        tool=tool,
        summary=summary,
        progress=progress,
    )


def _questions(objective: str) -> list[str]:
    text, _ = complete(
        "You plan research questions only. Return 3 to 5 short search queries, one per line. No preamble.",
        f"Objective:\n{objective}",
        max_tokens=220,
    )
    lines = [re.sub(r"^[\-\d\.\)\s]+", "", ln).strip() for ln in text.splitlines() if ln.strip()]
    lines = [ln for ln in lines if 8 < len(ln) < 140]
    if len(lines) < 3:
        base = objective[:80]
        lines = [
            base,
            f"{base} manufacturer trade account UK",
            f"{base} delivery Scotland minimum order",
            f"{base} custom specification contact",
        ]
    return lines[:5]


def _useful(hit: dict) -> bool:
    url = (hit.get("url") or "").lower()
    title = (hit.get("title") or "").lower()
    if not url.startswith("http"):
        return False
    bad = ("pinterest.", "facebook.com", "instagram.com", "tiktok.", "youtube.com", "search_error")
    return not any(b in url or b in title for b in bad)


def run_package(package_id: str) -> None:
    started = time.time()
    conn = connect()
    pkg = conn.execute("SELECT * FROM work_packages WHERE id=?", (package_id,)).fetchone()
    conn.close()
    if not pkg:
        return
    package = dict(pkg)
    _set_agent(status="working", current_task_id=package.get("task_id"), last_summary="Planning research", progress=0.1)
    update_package(package_id, stage="research", status="researching")
    _emit("agent.started_task", package, "Planning research", "working", 0.1)
    _emit("package.arrived", package, "Work package arrived at Research Lab", "researching", 0.1)
    questions = _questions(package["objective"])
    _emit("agent.planning", package, "Planning research questions", "working", 0.15)
    notes: list[str] = []
    searches = fetches = 0
    seen = set()
    for q in questions:
        if searches >= MAX_SEARCHES or time.time() - started > MAX_SECONDS:
            break
        searches += 1
        _set_agent(status="researching", current_tool="web_search", last_summary=f"Searching: {q[:80]}", progress=0.2 + searches * 0.08)
        _emit("agent.using_tool", package, f"Searching: {q}", "researching", 0.25, "web_search")
        hits = [h for h in web_search(q, limit=6) if _useful(h)]
        _emit("agent.researching", package, f"Received {len(hits)} usable hits for: {q}", "researching", 0.3, "web_search")
        for hit in hits[:4]:
            if not hit.get("url"):
                continue
            conn = connect()
            conn.execute(
                "INSERT INTO sources(id,package_id,url,title,snippet,note,created_at) VALUES(?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), package_id, hit.get("url") or "", hit.get("title") or "", hit.get("snippet") or "", "search_hit", now()),
            )
            conn.commit()
            conn.close()
            notes.append(f"{hit.get('title')}\n{hit.get('url')}\n{hit.get('snippet') or ''}")
        for hit in hits[:2]:
            if fetches >= MAX_FETCHES or time.time() - started > MAX_SECONDS:
                break
            url = hit["url"]
            if url in seen:
                continue
            seen.add(url)
            fetches += 1
            _set_agent(status="researching", current_tool="fetch_page", last_summary=f"Reading {hit.get('title') or url}"[:160], progress=0.45)
            _emit("agent.using_tool", package, f"Reading source: {hit.get('title') or url}", "researching", 0.5, "fetch_page")
            page = fetch_page(url)
            snippet = (page.get("text") or hit.get("snippet") or "")[:700]
            if snippet:
                notes.append(f"{page.get('title') or hit.get('title')}\n{url}\n{snippet}")
            conn = connect()
            conn.execute(
                "INSERT INTO sources(id,package_id,url,title,snippet,note,created_at) VALUES(?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), package_id, page.get("url") or url, page.get("title") or hit.get("title"), snippet[:500], page.get("error") or "", now()),
            )
            conn.commit()
            conn.close()
    evidence = "\n\n---\n\n".join(notes)[:8000] or "No page text captured; search titles only."
    synth, tokens = complete(
        "You are Ayven Research. Write a source-backed briefing. Use only the evidence. Mark unknowns. If trade price, MOQ, delivery or custom spec is missing, draft an enquiry and require approval. Do not invent prices.",
        f"Objective:\n{package['objective']}\n\nEvidence:\n{evidence}",
        max_tokens=900,
    )
    conn = connect()
    conn.execute("UPDATE agents SET tokens=tokens+?, cost_usd=cost_usd+? WHERE id=?", (tokens, tokens / 1000 * 0.002, AGENT))
    conn.commit()
    conn.close()
    needs_enquiry = any(k in synth.lower() for k in ("enquiry", "approval", "not publicly", "contact the", "trade account"))
    next_action = "Lee to approve supplier enquiry" if needs_enquiry else "Milo to present findings"
    update_package(package_id, findings=synth, next_action=next_action, requires_approval=1 if needs_enquiry else 0, status="routing", stage="distribution")
    _set_agent(status="idle", current_tool=None, last_summary="Findings prepared", progress=1, current_task_id=None)
    _emit("agent.completed", package, "Preparing findings", "idle", 1)
    _emit("package.completed_research", package, "Research complete — sending to Distribution", "routing", 1)
    dest = route(package_id)
    if dest == "approval":
        aid = str(uuid.uuid4())
        conn = connect()
        conn.execute(
            """INSERT INTO approvals(id,task_id,project_id,agent_id,summary,status,created_at) VALUES(?,?,?,?,?,?,?)""",
            (aid, package.get("task_id") or package_id, package["project_id"], AGENT, "Approve prepared supplier enquiry before any send.", "pending", now()),
        )
        if package.get("task_id"):
            conn.execute("UPDATE tasks SET status=?, result=?, approval_status=? WHERE id=?", ("needs_approval", synth, "pending", package["task_id"]))
        conn.commit()
        conn.close()
        _set_agent(status="needs_approval", last_summary="Waiting for Lee")
        events.emit("approval.requested", project_id=package["project_id"], task_id=package.get("task_id"), agent_id=AGENT, department_id="command", status="pending", summary="Approve prepared supplier enquiry")
    elif package.get("task_id"):
        conn = connect()
        conn.execute("UPDATE tasks SET status=?, result=? WHERE id=?", ("complete", synth, package["task_id"]))
        conn.commit()
        conn.close()
