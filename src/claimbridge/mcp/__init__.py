"""
MCP (Model Context Protocol) integration.

`server.py` runs a read-only MCP server that reaches ClaimBridge through its
own /v1 HTTP API with an API key -- not through the database. See that module's
docstring for why, and for what is deliberately not exposed as a tool.

It is a separate process with its own dependencies (requirements-mcp.txt): the
MCP SDK needs anyio 4, and the API's FastAPI pin requires anyio 3, so the two
cannot share an environment today. That is a happy accident rather than the
reason for the split -- the split is about blast radius.
"""
