# Kiro Gateway LXC Setup

## Deployment profile

- Proxmox LXC: unprivileged Debian 13, 1 vCPU, 1 GiB RAM, 8 GiB root disk.
- Kiro CLI 3.2.x; gateway runs with Python 3.14 in a `uv` virtual environment.
- Gateway source: `codesenju/kiro-gateway` fork. The deployment was copied from
  the local working tree; future source updates should use the fork as source of
  truth.
- Gateway runs as Unix user `kiro`; working directory is `/home/kiro/workspace`.
- `MCP_DISCOVERY=off`; Kiro CLI's native MCP settings remain independent.
- Built-in tool permissions are enabled for this dedicated LXC. Kiro executes
  tools as the unprivileged `kiro` user, not as container root.
- The gateway API key is generated separately from Kiro authentication and kept
  in a root-only environment file. Never put it in this repository.

Deployment-specific IP addresses, hostnames, key material, and credentials are
intentionally omitted from this document.

## Authenticate Kiro CLI

Do not copy the host's `~/.kiro` authentication state into the container. Sign
in as the same Unix account that runs the gateway, using Kiro's remote device
flow:

```bash
pct exec <CTID> -- runuser -u kiro -- env \
  HOME=/home/kiro \
  PATH=/home/kiro/.local/bin:/usr/local/bin:/usr/bin:/bin \
  kiro-cli login --use-device-flow
```

Complete the displayed code in a browser. Verify the service account's auth
without printing credentials:

```bash
pct exec <CTID> -- runuser -u kiro -- env \
  HOME=/home/kiro \
  PATH=/home/kiro/.local/bin:/usr/local/bin:/usr/bin:/bin \
  kiro-cli whoami
```

## Gateway service

The systemd service is named `kiro-gateway`. Its environment file is
`/etc/kiro-gateway.env` and must remain mode `0600`; it is not tracked here.
Important settings:

```env
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
KIRO_CLI_PATH=/home/kiro/.local/bin/kiro-cli
KIRO_ACP_ENGINE=v2
MCP_DISCOVERY=off
ACP_WORKSPACE_DIR=/home/kiro/workspace
ACP_TRUST_TOOLS=true
KIRO_GATEWAY_API_KEY=<generate locally; never commit>
```

After Kiro login, start or restart the service:

```bash
pct exec <CTID> -- systemctl restart kiro-gateway
pct exec <CTID> -- systemctl status kiro-gateway --no-pager
pct exec <CTID> -- curl --fail http://127.0.0.1:8000/health
```

From an authorized client, use `http://<LXC-LAN-address>:8000/v1` as the
OpenAI-compatible base URL and send the gateway key in a Bearer header. Keep the
port on a trusted LAN or behind a TLS-terminating reverse proxy; do not expose
plain HTTP publicly.

## Tools and diagnostics

- `GET /v1/diagnostics/tools` requires the gateway API key. It reports sanitized
  operator MCP server names and recently observed tool use, not a complete list
  of all available Kiro tools.
- Standard OpenAI `tools=[{"type":"function", ...}]` declarations are forwarded
  but are not currently honored by Kiro CLI. Configure external tools as MCP
  servers instead, using `KIRO_MCP_SERVERS`, `KIRO_MCP_CONFIG`, or the
  per-request `mcp_servers` extension.
- `ACP_TRUST_TOOLS=true` permits Kiro's built-in/MCP tool permission requests.
  Use `false` for answer-only mode. Do not mount host secrets or sensitive
  directories into this container.
