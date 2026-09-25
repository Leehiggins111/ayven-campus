# Tools

Every tool returns the same shape: tool, status, query or action, source URL, source title, timestamp, extracted content, error, metadata.

The model may choose a tool. The tool does the deterministic work. Research roles cannot send email, buy, contact a prospect, write files, or change a database.

| Tool | Capability | State |
| --- | --- | --- |
| web_search | READ_WEB | REAL against DuckDuckGo HTML when mode is `live`. FIXTURE index when mode is `fixtures` |
| fetch_page | READ_WEB | REAL HTTP fetch in `live`. Fixture excerpt in `fixtures`. Invalid URLs are rejected |
| calculator | RUN_CALC | REAL. `+ - * /` on numeric literals only. Powers, names, and calls are rejected |
| browser | BROWSE_WEB | OPTIONAL. `browser-use` is not installed. JS shells are recorded as failures |
| firecrawl | READ_WEB | DISABLED. AGPL-3.0 source is not vendored and no request is sent |
| code_exec | RUN_CODE | DISABLED (`AYVEN_ALLOW_CODE=0`). Research roles do not have the capability. The studied patterns were smolagents and E2B; neither is installed. No Docker sandbox is started |
| send_email, purchase, external_contact, write_file, modify_database | gated | Denied for the research workforce |

MCP tools, when a server is configured later, are classified read versus action. Action calls require an approval. See `mcp_boundary.py`. Status today: **OPTIONAL, NOT INSTALLED**.

Snippets are not final evidence when the page can be opened. A failed fetch stays a failure.
