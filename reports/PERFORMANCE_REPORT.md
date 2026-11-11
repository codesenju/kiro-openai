# Kiro Gateway Performance Report

## Status

The 568-row model/scenario matrix and 90-request gradual concurrency phase
completed. The matrix runner hit a summary-aggregation bug after writing its
CSV; summary and charts were recovered from saved data without repeating
inference. Gateway ran locally on `127.0.0.1:8000` with `MCP_DISCOVERY=off`. No
credentials are included in this repository or report.

## Architecture and request path

```text
OpenAI SDK client
  -> FastAPI / Uvicorn
  -> auth + model validation + request conversion
  -> ShimService (fresh session per request)
  -> shared ACPClient subprocess (one kiro-cli acp process per gateway)
  -> ACP JSON-RPC over stdio
  -> Kiro CLI / Kiro backend
```

- `main.py` starts one `ACPClient`, launches one `kiro-cli acp` subprocess,
  performs one ACP initialize handshake and a warm-up session for model discovery.
- `kiro/routes_openai_shim.py` handles model discovery, auth, request conversion,
  response shaping, and OpenAI SSE. Its stream generator yields translated
  events without intentional response buffering and disables proxy buffering.
- `kiro/shim_service.py` opens a new ACP session for each request, then forwards
  `prompt`/`prompt_stream` calls. Conversation history is serialized into that
  request; sessions are intentionally not reused between independent requests.
- `kiro/acp_client.py` multiplexes JSON-RPC through the shared process: a single
  stdout reader resolves request futures and per-session event queues; a write
  lock protects stdio. Completed/cancelled prompts clean up queue and usage
  routing maps. ACP sessions themselves have no per-request close call here and
  live-process resource growth remains a validation item, not a confirmed leak.
- Model selection occurs after `session/new`, with `session/set_model` when the
  requested ID differs from the client object's cached current model. This is a
  separate ACP round trip. The cache is process-wide even though sessions are
  independent; concurrent model-switch behavior warrants measurement before any
  change.
- `kiro/harness_mcp.py` resolves request MCP configuration in route handling.
  `MCP_DISCOVERY=off` prevents harness config discovery; no MCP servers were
  used in this benchmark. Kiro's built-in tools remain active when permissions
  allow them. MCP discovery default is `all`, so keep the explicit off override
  for comparable runs.

## Latency attribution and likely bottlenecks

1. **Historical MCP startup/request delay:** supplied prior observations were
   33.728s TTFT / 37.655s total with MCP enabled versus 2.683s / 6.304s with MCP
   disabled. These imply ~92% lower TTFT with discovery disabled, but are
   user-provided and not reproduced here. MCP server initialization happens
   during ACP `session/new`; a registered server can block session setup.
2. **Per-request ACP setup:** every inference opens `session/new`. If a model
   switch is needed, `session/set_model` adds another request before the prompt.
   `session/new`, optional mode/system-prompt setup, and model selection precede
   first output. The public OpenAI response does not expose separate timings for
   those steps.
3. **Upstream generation:** prompt generation and first model output are likely
   the dominant remaining latency once MCP is off, but cannot be isolated until
   measured alongside gateway-reported `kiro_metadata.turn_duration_ms`.
4. **Authentication:** OpenAI Bearer auth is a local string comparison before
   route work (`kiro/auth.py`); expected cost is negligible relative to ACP.
5. **Streaming:** OpenAI SSE serializes each translated delta with `json.dumps`
   and yields immediately; first content is observable at the SDK. No gateway
   buffering bottleneck is established by code inspection alone.
6. **Async/concurrency:** FastAPI handlers and ACP I/O are async. Writes are
   serialized only while writing a JSON-RPC line; response routing uses IDs and
   per-session queues. There is no explicit gateway request semaphore. Actual
   kiro-cli prompt serialization/head-of-line behavior is unknown until gradual
   concurrency tests run.
7. **Logging:** normal level is INFO. ACP stdout/stderr are logged at DEBUG;
   enabling DEBUG can add serialization and I/O cost, especially with large
   updates. No logging changes are justified absent profile evidence.
8. **HTTP connection management:** the OpenAI client will reuse its async HTTP
   transport. The gateway's legacy `kiro/http_client.py` is not used by this ACP
   request path; it is not evidence of a current per-request HTTP bottleneck.

## Baseline observations (inspection only)

| Check | Observation |
|---|---|
| Initial localhost health | Unavailable before gateway startup |
| Gateway version | `2.4.1.post1.dev17+g5b9da783d` |
| Loopback `/health` after startup | HTTP 200; 0.000854s observed client total |
| `/v1/models` after startup | HTTP 200; 0.001427s observed client total |
| Advertised IDs | 20, including `auto` and injected `claude-auto` aliases |
| MCP | `MCP_DISCOVERY=off`; no MCP servers used |
| Approved SDK smoke | `claude-sonnet-5-5`, short prompt: 3.746s client total, 2,779ms Kiro turn, 20 input / 1 output token, 0.4508 credits |
| Model/scenario baseline | 568 CSV rows: 506 successful, 62 failed; all failures were the two MiniMax IDs |
| Concurrency | 90/90 successful; levels 1, 2, 5, and 10 |
| Historical inference | User-provided values above; not part of generated CSV |

The single smoke result is recorded in `benchmarks/results/smoke.csv`; it is not
part of baseline model rankings or repeated-sample statistics. The smoke's
client-observed time and Kiro-reported turn duration are distinct measures.

Model discovery is unauthenticated metadata. Inference routes require the
gateway key. The local shell has a key configured through the gateway's local
environment; the value was never read into output or recorded.

## Baseline plan and request budget

The live catalogue contains 18 concrete model IDs and two routing aliases. The
suite screens concrete models on five non-streaming scenarios (5 measured
iterations each) plus medium streaming (5 each), and does one separate
short-prompt warm-up per concrete model. Aliases receive five short routing
checks each and are excluded from concrete-model rankings. Gradual concurrency
uses the configured Sonnet representative, five repetitions per level, and
levels 1, 2, 5, then 10.

| Work | Requests |
|---|---:|
| 18 models × 5 scenarios × 5 iterations | 450 |
| 18 models × medium streaming × 5 | 90 |
| 18 warm-ups | 18 |
| 2 aliases × 5 short routing checks | 10 |
| Concurrency: 5 × (1 + 2 + 5 + 10) | 90 |
| **Initial full-run estimate** | **658** |

No automatic retries. One smoke call was added after approval. CSV contains 658
rows (568 model-matrix rows including warm-ups, plus 90 concurrency requests);
with the separate smoke call, 659 inference requests were sent. Concurrency
escalated gradually and did not stop early. Finalist confidence runs are
additional and excluded.

## Baseline findings

- 16 of 18 concrete models succeeded in every measured scenario (5/5 each).
- `minimax-m2-1` and `minimax-m2-5` failed every request: 52 HTTP 502s and 10
  streaming API errors across their warm-ups and scenario runs. Treat them as
  unavailable on this gateway until independently diagnosed.
- Fastest short-prompt median was `claude-haiku-4-5` at 1.099s. It also had the
  lowest medium-stream TTFT median at 1.235s.
- Lowest short-prompt median credits was `claude-haiku-5-5` at 0.0067; its
  medium-stream TTFT median was slower at 6.580s. Credits are Kiro-reported
  metadata for this exact short prompt, not a universal model price.
- Five samples per scenario are screening evidence only. P99 estimates are not
  reliable at this sample size; no technical-quality scoring was performed.

## Concurrency results

Representative model: `claude-sonnet-5-5`, short prompt, five batches per level.
Values are per-request median total latency and median batch throughput:

| Concurrency | Successful requests | Median latency | Median requests/second | Mean CPU | Peak sampled RSS |
|---:|---:|---:|---:|---:|---:|
| 1 | 5/5 | 1.400s | 0.700 | 9.0% | 258.4 MiB |
| 2 | 10/10 | 1.909s | 0.854 | 9.2% | 304.1 MiB |
| 5 | 25/25 | 2.884s | 1.315 | 11.9% | 330.6 MiB |
| 10 | 50/50 | 4.867s | 1.796 | 12.9% | 402.0 MiB |

Throughput rose with concurrency, while request latency and RSS also rose. CPU
remained modest; this does not look CPU-saturated. Queueing may be in the shared
ACP/kiro-cli path or upstream, but API metadata does not split session,
model-switch, queue, and generation time, so attribution remains unproven.
Four charts are present under `reports/charts/`. Before/after chart is absent
because no optimization was applied and `optimized.csv` has no measurements.

## Benchmark side effect

`kiro-gateway/nginx-deployment.yaml`, `nginx-configmap.yaml`, and
`nginx-service.yaml` appeared during the code-generation scenario; content and
timestamps match the benchmark request. With Kiro built-in tools trusted, they
were likely written by the agent into gateway's working directory. All three are
untracked and were left untouched. Direct future code-generation runs at a
temporary workspace or explicitly deny tool execution when file writes are not
desired.

## Measurement limits

- Stream TTFT is first non-empty SDK content chunk. Non-stream TTFT is null,
  because the non-stream response hides first-token timing.
- Total latency is client-observed and includes connection, gateway setup,
  ACP, and upstream generation. `turn_duration_ms` is recorded separately when
  Kiro reports it; it does not include all gateway setup.
- Gateway usage token counts may be estimates (Kiro CLI often reports no token
  counts over ACP). Throughput is left null when output token counts are absent;
  characters are never converted to tokens.
- CPU/RSS collection is best effort via optional `psutil`. Missing dependency or
  unidentified gateway/ACP process yields null values.
- Five samples are directional screening only. P99 is included as a descriptive
  nearest-rank value but explicitly marked unreliable at this sample size.
- The planned per-model warm-up is recorded separately from measured requests;
  it captures first use of each requested model on an already-started gateway.
  Full gateway process cold-start timing is not included: it requires a
  controlled restart and startup instrumentation.
- Model technical quality requires human review against a rubric. No automated
  judge requests (and extra credit usage) are included.

## Optimization decision

No gateway source optimization is applied yet. One ACP process is already reused
across requests; pooling ACP sessions would violate the documented fresh-session
isolation contract and risks context leakage. Optimize only after baseline data
shows a gateway-controlled cost. Next candidate investigation: time `session/new`
and `session/set_model` independently, then validate whether the shared cached
model default causes redundant or skipped set-model calls under concurrency.

## Reproduction

From `kiro-openai/`, after installing `requirements.txt` and exporting
`KIRO_GATEWAY_API_KEY`:

```bash
cd ~/codesenju/repos/kiro-gateway
MCP_DISCOVERY=off SERVER_HOST=127.0.0.1 uv run main.py

cd ~/codesenju/repos/kiro-openai
.venv/bin/python benchmarks/benchmark_models.py --phase baseline
.venv/bin/python benchmarks/benchmark_concurrency.py --phase baseline
```

Run only after approving the request budget. Start with a smaller scoped test by
passing `--models claude-sonnet-5-5 --repetitions 1`; keep concurrency at level 1
until explicitly approved. Store outputs in `benchmarks/results/` and generated
charts in `reports/charts/`.
