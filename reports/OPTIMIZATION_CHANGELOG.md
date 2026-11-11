# Optimization Changelog

## 2026-11-11 — Inspection and benchmark harness

- Created gateway branch `perf/kiro-gateway-benchmark-optimization` from clean
  `main`; benchmark repo is a working directory without Git metadata.
- Started gateway bound to `127.0.0.1`, with `MCP_DISCOVERY=off`; health and
  model discovery responded successfully.
- Added async OpenAI-SDK benchmark runners, deterministic scenario configuration,
  CSV/JSON result output, optional process resource sampling, and chart
  generation. Retries are disabled; model order is randomized in the matrix.
- Refactored both original one-shot scripts into callable functions while
  preserving their command-line behavior and default prompt/model.
- No gateway implementation changes and no inference requests performed.
- Historical MCP comparison is recorded as user-provided, not independently
  reproduced. Initial measured CSVs remain schema-only until approved run.
- After explicit approval, installed benchmark dependencies, confirmed SDK model
  discovery (20 IDs), and ran one short Sonnet smoke request (3.746s client,
  2,779ms gateway turn, 0.4508 credits). Smoke is separate from baseline.
- Completed five-run model/scenario matrix: 568 rows saved, 506 success, 62
  failures confined to the two MiniMax IDs. The runner's summary step initially
  failed on blank CSV TTFT values; fixed numeric parsing and rebuilt summary from
  saved CSV without repeating inference. Corrected Matplotlib 3.11 label API.
- Completed gradual concurrency validation on `claude-sonnet-5-5`: 90/90 success
  at 1/2/5/10 clients. Latency and RSS rise with concurrency; CPU remains modest.
- No gateway optimization applied yet: findings need ACP-stage timing to separate
  session/model-selection/queueing from upstream generation.

## Optimization status

No optimization has been selected or applied. Session pooling, model-switch
changes, logging changes, and worker-count changes require more stage-level
timing and compatibility evidence. Initial matrix and concurrency approvals were
obtained and the runs completed; finalist runs remain separate.
