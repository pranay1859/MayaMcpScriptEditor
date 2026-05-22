# Connecting Ollama to the Maya MCP Server

This guide sets up the bridge so your local Ollama LLM can call the same Maya MCP tools
that Claude Desktop uses.

```
Maya (running the MCP server on :6275)
    │
    │  streamable-http
    ▼
mcp-remote  ←── bridges streamable-http → stdio
    │
    │  stdio
    ▼
mcpo proxy  ←── wraps MCP tools as OpenAPI REST endpoints on :8000
    │
    │  HTTP tool calls
    ▼
Ollama  (your local LLM at http://localhost:4318)
```

---

## Prerequisites

- Maya is running with the MCP server started (check Script Editor for `[maya-mcp] listening on http://127.0.0.1:6275/mcp`)
- Node.js is installed (for `npx`)
- Python 3.10+ is installed

---

## Step 1 — Install mcpo

```bash
pip install mcpo
```

Or via `uv` (faster):

```bash
pip install uv
```

---

## Step 2 — Start the mcpo proxy

This command wraps the Maya MCP server and exposes all its tools as OpenAPI REST endpoints:

```bash
uvx mcpo --port 8000 -- npx -y mcp-remote http://127.0.0.1:6275/mcp
```

If Maya is on a **different machine** (remote setup), replace `127.0.0.1:6275` with the Maya
machine's IP:

```bash
uvx mcpo --port 8000 -- npx -y mcp-remote http://<MAYA_MACHINE_IP>:6275/mcp
```

Verify it's running by opening: **http://localhost:8000/docs**
You should see the Maya MCP tools listed as OpenAPI endpoints.

---

## Step 3 — Configure Ollama to use the tools

### Option A: Open WebUI (recommended — web UI for Ollama)

Open WebUI v0.6.31+ has native MCP/OpenAPI tool support.

1. Open Open WebUI in your browser
2. Go to **Settings → Tools**
3. Add a new tool server with URL: `http://localhost:8000`
4. Select a model that supports tool calling (e.g. `llama3.1`, `qwen2.5`, `mistral-nemo`)
5. Enable tools in the chat session

That's it — the model can now call `check_scene_health`, `run_python_snippet`, and all other
Maya tools directly from the chat.

### Option B: Direct Ollama API (developer mode)

If you're calling the Ollama API at `http://localhost:4318` directly from code, pass the tools
from the mcpo OpenAPI schema:

```python
import requests, json

# Fetch available tools from mcpo
tools_schema = requests.get("http://localhost:8000/openapi.json").json()
tools = tools_schema.get("x-mcp-tools", [])  # mcpo exposes these

# Call Ollama with tools
response = requests.post("http://localhost:4318/api/chat", json={
    "model": "llama3.1",
    "messages": [{"role": "user", "content": "Check the scene health"}],
    "tools": tools,
    "stream": False,
})
print(response.json())
```

For tool calls returned by Ollama, forward them to mcpo:

```python
# When Ollama returns a tool_call, execute it via mcpo
tool_call = response.json()["message"]["tool_calls"][0]
tool_name = tool_call["function"]["name"]
tool_args = tool_call["function"]["arguments"]

result = requests.post(
    f"http://localhost:8000/{tool_name}",
    json=tool_args,
)
print(result.json())
```

---

## Recommended models for TD work

| Model | Why |
|---|---|
| `llama3.1` | Strong tool-calling, good code reasoning |
| `qwen2.5` | Excellent tool use, multilingual |
| `mistral-nemo` | Fast, reliable tool calling |
| `qwen2.5-coder` | Best for Python/MEL code generation |

Pull a model:
```bash
ollama pull llama3.1
```

---

## System prompt for Ollama

Paste the content of `assets/td_agent_system_prompt.md` as the system prompt in your Ollama
session. In Open WebUI: **Settings → Models → Edit model → System prompt**.

Fill in the placeholders (Maya version, naming convention, etc.) before saving.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| mcpo fails to connect | Confirm Maya MCP server is running: Script Editor should show `[maya-mcp] listening` |
| `http://localhost:8000/docs` shows no tools | Restart mcpo; check that `npx mcp-remote` can reach `:6275` |
| Ollama doesn't call tools | Switch to a model that explicitly supports tool use (`llama3.1`, `qwen2.5`) |
| Tool result is an error | Maya may have crashed or the scene state changed — check Script Editor |
| Port 8000 already in use | Change mcpo port: `uvx mcpo --port 8001 -- ...` and update your Ollama config |
