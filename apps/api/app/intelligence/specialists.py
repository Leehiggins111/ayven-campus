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
        "reason": "Mem0's gain is embeddings. There is no local embedding model in this build, and the hosted path is a remote service. Token-overlap retrieval on Ayven's existing rows covers the required include/exclude behaviour without that dependency.",
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
)


def matrix() -> list[dict]:
    return [dict(row) for row in MATRIX]


def consider(task_class: str) -> dict:
    if task_class != "software_engineering":
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
