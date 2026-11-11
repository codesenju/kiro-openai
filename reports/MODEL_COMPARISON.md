# Model Comparison

## Current state

The 568-row baseline matrix and 90-request concurrency phase are complete. The
local `/v1/models` endpoint advertised 20 IDs on 2026-11-11: 18 concrete model
IDs and routing aliases `auto` / `claude-auto`. Five repetitions per
model/scenario support directional screening, not robust P99 or quality
conclusions.

| Model | Short median latency | Medium streaming TTFT median | Short median credits | Short success |
|---|---:|---:|---:|---:|
| `claude-haiku-4-5` | **1.099s** | **1.235s** | 0.0282 | 5/5 |
| `claude-haiku-5-5` | 1.693s | 6.580s | **0.0067** | 5/5 |
| `claude-opus-4-5` | 2.148s | 2.224s | 0.1550 | 5/5 |
| `claude-opus-4-6` | 1.970s | 2.207s | 0.1553 | 5/5 |
| `claude-opus-4-7` | 1.448s | 1.651s | 0.2124 | 5/5 |
| `claude-opus-4-8` | 1.447s | 2.956s | 0.2118 | 5/5 |
| `claude-opus-5` | 1.577s | 4.535s | 0.2128 | 5/5 |
| `claude-opus-5-5` | 1.804s | 3.579s | 0.1936 | 5/5 |
| `claude-sonnet-4-5` | 2.412s | 2.640s | 0.0916 | 5/5 |
| `claude-sonnet-4-6` | 1.473s | 1.720s | 0.0915 | 5/5 |
| `claude-sonnet-5` | 1.568s | 1.962s | 0.1255 | 5/5 |
| `claude-sonnet-5-5` | 1.458s | 2.757s | 0.1254 | 5/5 |
| `gpt-5.6-luna` | 2.033s | 1.911s | 0.3170 | 5/5 |
| `gpt-5.6-sol` | 1.829s | 2.004s | 2.3246 | 5/5 |
| `gpt-5.6-terra` | 2.552s | 2.386s | 1.1623 | 5/5 |
| `minimax-m2-1` | — | — | — | 0/5; all scenarios failed |
| `minimax-m2-5` | — | — | — | 0/5; all scenarios failed |
| `qwen3-coder-next` | 1.564s | 1.795s | 0.0499 | 5/5 |
| Routing aliases `auto`, `claude-auto` | 1.878s / 1.929s | Not streamed | 0.0969 / 0.0969 | 5/5 each |

Latency values are medians over five observed successful samples per scenario.
Credit values are median Kiro-reported credits for the same short prompt, not
provider list prices; output differences and metering variance can affect them.
The two MiniMax IDs returned HTTP 502 or streaming errors in every run. Model
technical quality was not scored; no accuracy recommendation is made.
