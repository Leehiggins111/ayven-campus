"""Capability checks. Research work cannot send, buy, or mutate production."""

from __future__ import annotations

READ_WEB = "READ_WEB"
BROWSE_WEB = "BROWSE_WEB"
RUN_CALC = "RUN_CALC"
RUN_CODE = "RUN_CODE"
READ_FILES = "READ_FILES"
WRITE_FILES = "WRITE_FILES"
SEND_EMAIL = "SEND_EMAIL"
MODIFY_DATABASE = "MODIFY_DATABASE"
PURCHASE = "PURCHASE"
EXTERNAL_CONTACT = "EXTERNAL_CONTACT"

ALL = (
    READ_WEB,
    BROWSE_WEB,
    RUN_CALC,
    RUN_CODE,
    READ_FILES,
    WRITE_FILES,
    SEND_EMAIL,
    MODIFY_DATABASE,
    PURCHASE,
    EXTERNAL_CONTACT,
)

# High-impact actions. Research roles never receive these.
GATED = {WRITE_FILES, SEND_EMAIL, MODIFY_DATABASE, PURCHASE, EXTERNAL_CONTACT, RUN_CODE}

TOOL_CAPS = {
    "web_search": READ_WEB,
    "fetch_page": READ_WEB,
    "calculator": RUN_CALC,
    "browser": BROWSE_WEB,
    "firecrawl": READ_WEB,
    "code_exec": RUN_CODE,
    "send_email": SEND_EMAIL,
    "purchase": PURCHASE,
    "external_contact": EXTERNAL_CONTACT,
    "write_file": WRITE_FILES,
    "modify_database": MODIFY_DATABASE,
}

# Employees do not get every tool. Supervisor may re-fetch sources.
# Nobody in the research workforce can email, buy, or contact a customer.
ROLE_CAPS = {
    "research-e1": {READ_WEB, RUN_CALC},
    "research-e2": {READ_WEB, RUN_CALC},
    "research-e3": {READ_WEB, RUN_CALC},
    "research-sup": {READ_WEB, RUN_CALC, BROWSE_WEB},
    "research-mgr": {READ_WEB, RUN_CALC},
    "web-researcher": {READ_WEB, RUN_CALC},
    "milo": {READ_WEB},
}


class PermissionDenied(Exception):
    def __init__(self, tool: str, capability: str, agent_id: str):
        self.tool = tool
        self.capability = capability
        self.agent_id = agent_id
        super().__init__(f"{agent_id} lacks {capability} for {tool}")


def capabilities_for(agent_id: str) -> set[str]:
    return set(ROLE_CAPS.get(agent_id, set()))


def authorize(agent_id: str, tool: str, *, approved: bool = False) -> str:
    """Return the capability used, or raise PermissionDenied."""
    cap = TOOL_CAPS.get(tool)
    if cap is None:
        raise PermissionDenied(tool, "UNKNOWN_TOOL", agent_id)
    granted = capabilities_for(agent_id)
    if cap not in granted:
        raise PermissionDenied(tool, cap, agent_id)
    if cap in GATED and not approved:
        raise PermissionDenied(tool, f"{cap}_APPROVAL_REQUIRED", agent_id)
    return cap
