# Intelligence components and licence audit

Updated 2026-09-28 for v2.0.0. Nothing in this tree was vendored from the projects below. AGPL code is not copied. `AYVEN_ALLOW_ESCALATION` stays 0.

“Maintained?” is a point-in-time judgement from public activity, not a support contract. Versions are what this repo pins or, when the package is not installed, “not pinned”.

## What actually executes

| Component | Decision | Executes in this build |
| --- | --- | --- |
| Ayven work packages, approvals, SQLite, Campus | product | Yes |
| Pydantic schemas + llguidance grammar | INTEGRATED | Yes, on every structured tool check |
| Qwen-Agent `FnCallAgent` | INTEGRATED behind `AgentRuntime` | Yes when the package imports and `AYVEN_AGENT_RUNTIME` is auto or qwen |
| Native / Pydantic runtime | INTEGRATED | Yes when Qwen-Agent is off or the call falls back |
| HTTP search and fetch | INTEGRATED | Yes |
| Entity targets, authority class, local rerank | INTEGRATED | Yes |
| Crawl4AI | OPTIONAL | Only if `crawl4ai` imports. It does not in the default install |
| Browser Use | INTEGRATED when installed | Read-only, after HTTP or a JavaScript wall |
| Calculator | INTEGRATED | Yes |
| Code sandbox (`unshare` / bubblewrap) | INTEGRATED when the kernel allows it | Yes only with `AYVEN_ALLOW_CODE=1`, approval, and real isolation. rlimit does not run code |
| MCP Python SDK | INTEGRATED when installed | Yes for configured servers. Tools are not given to every employee |
| SQLite memory | INTEGRATED | Yes |
| Mem0 | REJECTED | No |
| LiteLLM | REJECTED | No. `gateway.py` is the router |
| DSPy | OPTIONAL | Offline helper only. Not imported on the request path |
| Langfuse | OPTIONAL | No. Local JSON traces are the sink |
| Garak | OPTIONAL | `scripts/run_security_eval.sh` only |
| llm-guard | OPTIONAL | No |
| Rebuff | REJECTED | Archived. Not used |
| OpenHands, Aider, SWE-agent, mini-SWE-agent | REJECTED as hosts | No. The coding specialist is the Ayven sandbox |
| Daytona | REJECTED | Paid cloud. Not used |
| PyMuPDF | REJECTED | AGPL. Not imported |
| Firecrawl | REJECTED | AGPL. Adapter stays disabled |

## Licence table

| Project | Repo | Licence | Version / commit | Maintained? | Capability | Decision | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Qwen-Agent | QwenLM/Qwen-Agent | Apache-2.0 | 0.0.34 | Yes | Function-calling loop | INTEGRATED | Kept behind `QwenAgentRuntime`. Ayven still authorises every call |
| llguidance | guidance-ai/llguidance | MIT | 1.3.0 | Yes | Constrained decoding | INTEGRATED | Grammar mask. First legal byte of a tool request is `{`, not `<` |
| Pydantic | pydantic/pydantic | MIT | 2.13.5 | Yes | Typed models | INTEGRATED | Already required. Schemas are the tool boundary |
| Pydantic AI | pydantic/pydantic-ai | MIT | not pinned | Yes | Agent framework | ADAPTED | Patterns only (typed tools, structured output, approval-shaped requests). It is not the application runtime |
| Crawl4AI | unclecode/crawl4ai | Apache-2.0 | not pinned | Yes | Crawl and markdown extraction | OPTIONAL | Ladder step after HTTP. Not installed by default |
| Browser Use | browser-use/browser-use | MIT | 0.13.10 | Yes | Interactive browser | INTEGRATED | Escalation when the page needs interaction or JavaScript |
| MCP Python SDK | modelcontextprotocol/python-sdk | MIT | 2.1.1 | Yes | stdio and Streamable HTTP client | INTEGRATED | Pinned beside browser-use. Tools enter the registry after discovery |
| Mem0 | mem0ai/mem0 | Apache-2.0 | not pinned | Yes | Vector memory | REJECTED | Needs an embedder or a hosted service. SQLite with scope, provenance, threshold, and overlap ranking stays local |
| LiteLLM | BerriAI/litellm | MIT for non-enterprise; `enterprise/` is separate | not pinned | Yes | Provider router | REJECTED | Split licence and a large install. `gateway.py` records route, cooldown, and cost |
| DSPy | stanfordnlp/dspy | MIT | not pinned | Yes | Prompt optimisation | OPTIONAL | Offline candidate prompts only. Production does not rewrite prompts |
| Langfuse | langfuse/langfuse | MIT | not pinned | Yes | Hosted traces | OPTIONAL | Local traces are mandatory. Langfuse is a sink if a host is configured later |
| Garak | NVIDIA/garak | Apache-2.0 | not pinned | Yes | Security probes | OPTIONAL | Dev script. Not a unit test and not a paid call |
| llm-guard | protectai/llm-guard | MIT | not pinned | Yes, heavy | Scanners | OPTIONAL | Maintained, but it pulls models. Ayven fences untrusted web text instead |
| Rebuff | protectai/rebuff | — | — | No (archived) | Prompt injection firewall | REJECTED | Archived. Not used |
| Daytona | daytonaio/daytona | Apache-2.0 | not pinned | Yes | Cloud sandbox | REJECTED | A paid cloud sandbox is out of scope. Isolation here is local or it does not run |
| gVisor, nsjail, Firecracker | various | Apache-2.0 / BSD | not pinned | Yes | Harder sandboxes | OPTIONAL | Not present on this deployment. Documented, not faked |
| bubblewrap | containers/bubblewrap | LGPL-2.1 | system | Yes | User namespace sandbox | ADAPTED | Used when `unshare` is missing and `bwrap` works. Not vendored |
| OpenHands | All-Hands-AI/OpenHands | MIT | not pinned | Yes | Software agent | REJECTED | Host agent. Would skip permissions and the ledger |
| Aider | Aider-AI/aider | Apache-2.0 | not pinned | Yes | Repo editor | REJECTED | Owns the edit loop |
| SWE-agent | SWE-agent/SWE-agent | MIT | not pinned | Yes | Issue-to-patch agent | REJECTED | Docker-centric host |
| mini-SWE-agent | SWE-agent/mini-SWE-agent | MIT | not pinned | Yes | Small coding agent | REJECTED | Still its own shell loop |
| smolagents | huggingface/smolagents | Apache-2.0 | not pinned | Yes | Code agent | REJECTED | Duplicates the tool loop |
| Letta | letta-ai/letta | Apache-2.0 | not pinned | Yes | Memory server | REJECTED | Own server and model-written memory |
| Graphiti | getzep/graphiti | Apache-2.0 | not pinned | Yes | Temporal graph memory | DEFERRED | Another store. SQLite scopes are enough until a graph is actually queried |
| LangGraph | langchain-ai/langgraph | MIT | not pinned | Yes | Graph runtime | REJECTED | Would replace work packages |
| Firecrawl | firecrawl/firecrawl | AGPL-3.0 | not pinned | Yes | Crawl API | REJECTED | Network copyleft. Not vendored |
| Docling | docling-project/docling | MIT | not pinned | Yes | Document conversion | DEFERRED | Heavy. Stdlib CSV/HTML/text is the current backend |
| Unstructured | Unstructured-IO/unstructured | Apache-2.0 | not pinned | Yes | Document parsing | DEFERRED | Large install for formats the stdlib path does not need yet |
| MarkItDown | microsoft/markitdown | MIT | not pinned | Yes | Document to markdown | OPTIONAL | Acceptable licence. Not installed. PDF stays unavailable until it is |
| PyMuPDF | pymupdf/PyMuPDF | AGPL-3.0 / commercial | not pinned | Yes | PDF | REJECTED | AGPL. Not imported |
| FastAPI, HTTPX, Uvicorn, pytest | various | MIT / BSD | see requirements.txt | Yes | API and tests | INTEGRATED | Unchanged role |

## Install groups

`pyproject.toml` extras: `core`, `browser`, `gpu`, `memory`, `eval`, `sandbox`, `observability`, `frankenstein`.

The GPU extra must be installed **after** the constraints file so `typing-extensions` and `starlette` are not dragged apart by `llama-cpp-python` or `browser-use`:

```bash
pip install -r apps/api/requirements.txt -r apps/api/requirements-frankenstein.txt -c apps/api/constraints.txt
```

`validation/Dockerfile.gpu` uses a prebuilt CUDA wheel index for llama-cpp-python. This repo does not build or push that image.
