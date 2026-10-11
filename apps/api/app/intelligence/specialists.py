"""Specialist engines stay behind Ayven. None of them becomes the product.

A software work package would go: Ayven package → software-engineering skill →
this boundary → the sandbox (the only specialist that is light enough to run
here) → evidence → supervisor. OpenHands, Aider, and SWE-agent are not installed.
"""

from __future__ import annotations

MATRIX = (
    {
        "project": "All-Hands-AI/OpenHands",
        "licence": "MIT",
        "capability": "full software-engineering agent loop",
        "decision": "REJECTED",
        "reason": "It is a host agent with its own planner, shell, and browser. Running it would bypass Ayven packages, permissions, and the claim ledger, and it implies a software department this benchmark does not have.",
    },
    {
        "project": "Aider-AI/aider",
        "licence": "Apache-2.0",
        "capability": "repo editing pair programmer",
        "decision": "REJECTED",
        "reason": "Aider owns the edit loop and git. Ayven has no software department and the frozen exams do not edit a repository. Installing it would add a second orchestrator.",
    },
    {
        "project": "SWE-agent/SWE-agent",
        "licence": "MIT",
        "capability": "issue-to-patch agent",
        "decision": "REJECTED",
        "reason": "Same host-agent problem as OpenHands, plus a Docker-centric runtime this VM does not have.",
    },
    {
        "project": "SWE-agent/mini-SWE-agent",
        "licence": "MIT",
        "capability": "small coding agent",
        "decision": "REJECTED",
        "reason": "Lighter than SWE-agent, but it still runs model code through its own shell loop. Ayven's sandbox is the coding specialist so permissions stay in one place.",
    },
    {
        "project": "huggingface/smolagents",
        "licence": "Apache-2.0",
        "capability": "code agent and local execution ideas",
        "decision": "STUDIED",
        "reason": "The useful idea is local code execution with limits. Ayven uses unshare rather than smolagents' interpreter, because the benchmark already has a calculator and smolagents would duplicate the Qwen-Agent tool loop.",
    },
    {
        "project": "letta-ai/letta",
        "licence": "Apache-2.0",
        "capability": "stateful agent memory server",
        "decision": "REJECTED",
        "reason": "Letta wants its own server and model-backed memory. Ayven already stores scoped SQLite memories with provenance. Adding Letta would not rank or scope better without an embedding model, and an embedding API would be a paid or GPU call.",
    },
    {
        "project": "mem0ai/mem0",
        "licence": "Apache-2.0",
        "capability": "memory extraction and vector recall",
        "decision": "REJECTED",
        "reason": "Mem0's default embedder is a hosted OpenAI-compatible call, and its store is a second memory server. Ayven already ranks SQLite rows with a local fastembed model (lexical fallback) under the same scopes and provenance rules. Adding Mem0 would duplicate that and can spend a paid API if the default provider is left on.",
    },
    {
        "project": "BerriAI/litellm",
        "licence": "MIT for non-enterprise code; enterprise/ is separate",
        "capability": "multi-provider gateway",
        "decision": "REJECTED",
        "reason": "Ayven already speaks OpenAI-compatible HTTP to a local server. LiteLLM is a large gateway and its enterprise tree is a different licence. It does not add a local capability the harness lacks.",
    },
    {
        "project": "langchain-ai/langgraph",
        "licence": "MIT",
        "capability": "graph orchestrator",
        "decision": "REJECTED",
        "reason": "Using LangGraph as the task graph would move work packages and approvals out of Ayven. ADR-001 keeps that graph in Ayven.",
    },
    {
        "project": "microsoft/agent-framework",
        "licence": "MIT",
        "capability": "multi-agent workflow host",
        "decision": "REJECTED",
        "reason": "It would replace the employee/supervisor/manager loop. The loop is the product boundary, not a missing library.",
    },
    {
        "project": "guidance-ai/llguidance",
        "licence": "MIT",
        "capability": "JSON-schema constrained decoding",
        "decision": "INTEGRATED",
        "reason": "Tool arguments are accepted only when the schema grammar allows them. A reasoning prefix cannot be the first token.",
    },
    {
        "project": "pydantic/pydantic-ai",
        "licence": "MIT",
        "capability": "typed agent runtime",
        "decision": "INTEGRATED",
        "reason": "Optional runtime only. A pydantic-ai Agent with FunctionModel and TestModel runs, then Ayven's validate_tool_call accepts or rejects the output. It does not own packages, approvals, or the ledger.",
    },
    {
        "project": "unclecode/crawl4ai",
        "licence": "Apache-2.0",
        "capability": "markdown extraction and crawling",
        "decision": "INTEGRATED",
        "reason": "Rung 4. DefaultMarkdownGenerator extracts a local HTML page to markdown. Browser Use remains the interactive step. LiteLLM ships inside Crawl4AI and is not used as Ayven's gateway.",
    },
)


def run_coding(agent_id: str, source: str, approved: bool = False) -> dict:
    """The one coding specialist: Ayven's sandbox, behind the same permission gate."""
    from .code_sandbox import run_code

    result = run_code(agent_id, source, approved=approved)
    return {
        "engine": "ayven-code-sandbox",
        "skill": "software-engineering",
        "status": result.status,
        "evidence": result.as_dict(),
        "supervisor_sees": "stdout, stderr, exit code",
    }


def matrix() -> list[dict]:
    return [dict(row) for row in MATRIX]


def consider(task_class: str) -> dict:
    if task_class not in {"software_engineering", "software_build"}:
        return {"engine": None, "status": "not_applicable", "evidence": [], "reason": "This work package is not a software task."}
    from .code_sandbox import isolation_level

    return {
        "engine": "ayven-code-sandbox",
        "status": "ACTIVE",
        "skill": "software-engineering",
        "isolation": isolation_level(),
        "evidence": ["stdout", "stderr", "exit code"],
        "reason": "The sandbox is the coding specialist. Results return as tool evidence for the supervisor. No software department is created.",
    }
