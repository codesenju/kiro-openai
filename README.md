# OpenAI client for Kiro Gateway

OpenAI-compatible endpoint for the Kiro Gateway deployed in the lab. Gateway
source fork: `codesenju/kiro-gateway`.

## Connection

```text
Base URL: http://192.168.0.17:8000/v1
Auth:     Authorization: Bearer $KIRO_GATEWAY_API_KEY
```

The gateway API key is stored on the LXC in `/etc/kiro-gateway.env` with
root-only permissions. Load it from a trusted local secret store; do not commit
it or paste it into chat. Endpoint is plain HTTP on the private LAN; do not
expose port 8000 publicly.

## OpenAI-compatible endpoints

| Method | Path | Purpose | Auth |
|---|---|---|---|
| `GET` | `/v1/models` | List models | Not required |
| `POST` | `/v1/chat/completions` | Chat completions; streaming supported | Bearer key |
| `POST` | `/v1/responses` | Responses API; send full input each request | Bearer key |
| `GET` | `/v1/diagnostics/tools` | Sanitized configured/observed tool summary | Bearer key |
| `POST` | `/v1/embeddings` | Unsupported by Kiro CLI/ACP | Bearer key |

The gateway is stateless: `previous_response_id` chaining is unsupported. Send
the full conversation in each request.

## Quick tests

List advertised models:

```bash
curl --fail-with-body http://192.168.0.17:8000/v1/models
```

Send one short completion (inference consumes Kiro credits):

```bash
curl --fail-with-body http://192.168.0.17:8000/v1/chat/completions \
  -H "Authorization: Bearer $KIRO_GATEWAY_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "Reply exactly: gateway-ok"}]
  }'
```

Streaming:

```bash
curl -N http://192.168.0.17:8000/v1/chat/completions \
  -H "Authorization: Bearer $KIRO_GATEWAY_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "Reply exactly: stream-ok"}],
    "stream": true
  }'
```

OpenAI Python SDK:

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="http://192.168.0.17:8000/v1",
    api_key=os.environ["KIRO_GATEWAY_API_KEY"],
)

response = client.chat.completions.create(
    model="claude-haiku-4-5",
    messages=[{"role": "user", "content": "Reply exactly: gateway-ok"}],
)
print(response.choices[0].message.content)
```

## Tools

Kiro CLI built-in tools are enabled on this LXC and run as the unprivileged
`kiro` user in `/home/kiro/workspace`. `ACP_TRUST_TOOLS=true` allows Kiro to run
them; use only with trusted callers and prompts. `MCP_DISCOVERY=off` disables
harness-config discovery, but does not disable Kiro CLI-native MCP config.

OpenAI `tools=[{"type":"function", ...}]` definitions are accepted but are
not currently honored as client-side function tools by Kiro CLI. Configure
external tools through MCP (`KIRO_MCP_SERVERS`, `KIRO_MCP_CONFIG`, or a
per-request `mcp_servers` extension). See the tool diagnostic endpoint for
sanitized configuration and observed calls; it is not a full availability list.
