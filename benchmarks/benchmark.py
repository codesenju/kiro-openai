"""Reusable async benchmark primitives for the Kiro OpenAI-compatible API.

The OpenAI SDK handles request/response compatibility. Retries are disabled so
failures stay visible in the measurements. Network inference is only initiated
by the command-line entrypoints, never at import time.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import statistics
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)

ROOT = Path(__file__).resolve().parent
RESULT_FIELDS = [
    "run_id", "timestamp_utc", "phase", "scenario", "model", "model_kind",
    "iteration", "concurrency", "streaming", "status", "http_status",
    "error_type", "error_message", "time_to_first_content_seconds",
    "total_latency_seconds", "chunk_interval_mean_seconds",
    "chunk_interval_p95_seconds", "content_chunks", "input_tokens",
    "output_tokens", "output_tokens_per_second", "token_source",
    "synthetic_context_cl100k_tokens",
    "gateway_turn_duration_ms", "credits", "requests_per_second",
    "gateway_cpu_percent_mean", "gateway_memory_rss_bytes_peak",
    "content_preview", "usage_metadata_json",
]


@dataclass
class BenchmarkConfig:
    """Validated subset of YAML settings used by the runner."""

    base_url: str = "http://127.0.0.1:8000/v1"
    timeout_seconds: float = 300.0
    max_retries: int = 0
    repetitions: int = 5
    warmup_per_model: int = 1
    random_seed: int = 20261111
    request_max_tokens: int = 512
    long_context_target_tokens: int = 4000
    concurrency_levels: list[int] = field(default_factory=lambda: [1, 2, 5, 10])
    concurrency_repetitions: int = 5
    concurrency_model: str = "claude-sonnet-5-5"
    stop_concurrency_on_failure: bool = True
    routing_aliases: list[str] = field(default_factory=lambda: ["auto", "claude-auto"])
    output_directory: Path = ROOT / "results"
    chart_directory: Path = ROOT.parent / "reports" / "charts"
    scenarios: dict[str, dict[str, Any]] = field(default_factory=dict)
    config_path: Path = ROOT / "benchmark_config.yaml"


def load_config(path: Path | str | None = None) -> BenchmarkConfig:
    """Load YAML settings and resolve output paths relative to config file."""
    config_path = Path(path or ROOT / "benchmark_config.yaml").expanduser().resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    api = raw.get("api", {})
    bench = raw.get("benchmark", {})
    base = config_path.parent
    return BenchmarkConfig(
        base_url=os.environ.get("KIRO_BENCHMARK_BASE_URL", api.get("base_url", "http://127.0.0.1:8000/v1")),
        timeout_seconds=float(api.get("timeout_seconds", 300)),
        max_retries=0,
        repetitions=int(bench.get("repetitions", 5)),
        warmup_per_model=int(bench.get("warmup_per_model", 1)),
        random_seed=int(bench.get("random_seed", 20261111)),
        request_max_tokens=int(bench.get("request_max_tokens", 512)),
        long_context_target_tokens=int(bench.get("long_context_target_tokens", 4000)),
        concurrency_levels=[int(v) for v in bench.get("concurrency_levels", [1, 2, 5, 10])],
        concurrency_repetitions=int(bench.get("concurrency_repetitions", 5)),
        concurrency_model=str(bench.get("concurrency_model", "claude-sonnet-5-5")),
        stop_concurrency_on_failure=bool(bench.get("stop_concurrency_on_failure", True)),
        routing_aliases=[str(v) for v in bench.get("routing_aliases", ["auto", "claude-auto"])],
        output_directory=(base / bench.get("output_directory", "results")).resolve(),
        chart_directory=(base / bench.get("chart_directory", "../reports/charts")).resolve(),
        scenarios=raw.get("scenarios", {}),
        config_path=config_path,
    )


def create_client(config: BenchmarkConfig) -> AsyncOpenAI:
    """Create official SDK client with explicit timeouts and no retries."""
    api_key = os.environ.get("KIRO_GATEWAY_API_KEY")
    if not api_key:
        raise RuntimeError("KIRO_GATEWAY_API_KEY is required; value is never logged")
    return AsyncOpenAI(
        base_url=config.base_url,
        api_key=api_key,
        timeout=config.timeout_seconds,
        max_retries=0,
    )


def discover_models(models_response: Any) -> list[str]:
    """Return all advertised model IDs from an OpenAI SDK model-list response."""
    ids = {str(item.id) for item in models_response.data if getattr(item, "id", None)}
    return sorted(ids)


def model_kind(model: str, aliases: Iterable[str]) -> str:
    """Mark routing aliases separately from concrete model IDs."""
    return "routing_alias" if model in set(aliases) else "model"


def _dump_model(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return value
    return {}


def _usage_and_metadata(response: Any) -> tuple[int | None, int | None, dict[str, Any]]:
    usage_obj = getattr(response, "usage", None)
    usage = _dump_model(usage_obj)
    extra = getattr(usage_obj, "model_extra", None)
    if isinstance(extra, dict):
        usage.update(extra)
    response_extra = getattr(response, "model_extra", None)
    if isinstance(response_extra, dict):
        usage.update(response_extra)
    metadata = usage.get("kiro_metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    return usage.get("prompt_tokens"), usage.get("completion_tokens"), metadata


def _percentile(samples: list[float], percentile: float) -> float | None:
    """Nearest-rank percentile; returns null for empty samples."""
    if not samples:
        return None
    values = sorted(samples)
    rank = max(1, math.ceil(percentile * len(values)))
    return values[rank - 1]


def percentile_summary(samples: list[float]) -> dict[str, float | int | None]:
    """Summarize sample count and latency percentiles without overstating confidence."""
    return {
        "sample_count": len(samples),
        "p50": _percentile(samples, 0.50),
        "p90": _percentile(samples, 0.90),
        "p95": _percentile(samples, 0.95),
        "p99": _percentile(samples, 0.99),
        "confidence_note": (
            "P99 is not a reliable tail estimate with fewer than 100 observations; "
            "five-run screening is directional only."
            if len(samples) < 100 else "Percentiles are empirical; inspect sample count and workload mix."
        ),
    }


def build_long_context(target_tokens: int) -> tuple[str, int | None]:
    """Build deterministic synthetic context, tokenized with cl100k when installed."""
    try:
        import tiktoken

        encoder = tiktoken.get_encoding("cl100k_base")
        fragments = [
            f"Record {i:04d}: Kubernetes service shard-{i % 31} uses zone-{i % 3}, "
            f"network policy revision {i % 17}, and deployment epoch {2020 + i % 7}. "
            "The platform team should preserve availability, auditability, and least privilege.\n"
            for i in range(max(target_tokens, 1))
        ]
        encoded: list[int] = []
        for fragment in fragments:
            encoded.extend(encoder.encode(fragment))
            if len(encoded) >= target_tokens:
                break
        encoded = encoded[:target_tokens]
        context = encoder.decode(encoded)
        return context + "\nSummarize key operational themes in no more than 100 words.", len(encoded)
    except ImportError:
        # Fixed content fallback remains reproducible, but exact token length is
        # intentionally reported as unavailable rather than guessed from chars.
        paragraph = (
            "Kubernetes network record: service discovery, policy enforcement, "
            "routing, observability, and availability must remain consistent. "
            "Use least privilege and preserve an auditable change history.\n"
        )
        repeated = paragraph * max(1, target_tokens // 25)
        return repeated + "Summarize key operational themes in no more than 100 words.", None


@dataclass
class ResourceSampler:
    """Best-effort sampler for gateway/ACP process CPU and resident memory."""

    interval_seconds: float = 0.25
    cpu_samples: list[float] = field(default_factory=list)
    rss_peak: int | None = None
    _task: asyncio.Task | None = None
    _enabled: bool = False

    async def start(self) -> None:
        try:
            import psutil  # noqa: F401
        except ImportError:
            return
        self._enabled = True
        self._task = asyncio.create_task(self._sample_loop())

    async def stop(self) -> tuple[float | None, int | None]:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        mean_cpu = statistics.fmean(self.cpu_samples) if self.cpu_samples else None
        return mean_cpu, self.rss_peak

    async def _sample_loop(self) -> None:
        import psutil

        known: dict[int, Any] = {}
        for proc in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
            try:
                cmd = " ".join(proc.info.get("cmdline") or []).lower()
                name = (proc.info.get("name") or "").lower()
                if "kiro-cli" in name or "kiro-cli acp" in cmd or "main.py" in cmd:
                    known[proc.info["pid"]] = proc
                    proc.cpu_percent(None)
            except (psutil.Error, KeyError):
                continue
        while True:
            await asyncio.sleep(self.interval_seconds)
            cpu_total = 0.0
            rss_total = 0
            seen = 0
            for pid, proc in list(known.items()):
                try:
                    if not proc.is_running():
                        known.pop(pid, None)
                        continue
                    cpu_total += proc.cpu_percent(None)
                    rss_total += proc.memory_info().rss
                    seen += 1
                except psutil.Error:
                    known.pop(pid, None)
            if seen:
                self.cpu_samples.append(cpu_total)
                self.rss_peak = max(self.rss_peak or 0, rss_total)


async def run_trial(
    client: AsyncOpenAI,
    *,
    model: str,
    prompt: str,
    scenario: str,
    phase: str = "baseline",
    iteration: int = 1,
    concurrency: int = 1,
    streaming: bool = False,
    max_tokens: int = 512,
    monitor_resources: bool = True,
) -> dict[str, Any]:
    """Run one request and return timing, usage, metadata, resources, or failure."""
    run_id = uuid.uuid4().hex
    timestamp = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    first_content: float | None = None
    content_chunks = 0
    previous_chunk_time: float | None = None
    chunk_intervals: list[float] = []
    text: list[str] = []
    input_tokens = output_tokens = None
    usage_metadata: dict[str, Any] = {}
    status_code = None
    error_type = error_message = None
    resources = ResourceSampler() if monitor_resources else None
    if resources:
        await resources.start()
    status = "success"
    try:
        request = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        if streaming:
            request["stream"] = True
            request["stream_options"] = {"include_usage": True}
            stream = await client.chat.completions.create(**request)
            status_code = 200
            async for chunk in stream:
                now = time.perf_counter()
                if chunk.usage is not None:
                    input_tokens, output_tokens, usage_metadata = _usage_and_metadata(chunk)
                for choice in chunk.choices:
                    content = choice.delta.content
                    if isinstance(content, str) and content:
                        if first_content is None:
                            first_content = now - started
                        if previous_chunk_time is not None:
                            chunk_intervals.append(now - previous_chunk_time)
                        previous_chunk_time = now
                        content_chunks += 1
                        text.append(content)
        else:
            response = await client.chat.completions.create(**request)
            status_code = 200
            input_tokens, output_tokens, usage_metadata = _usage_and_metadata(response)
            if response.choices:
                content = response.choices[0].message.content
                if isinstance(content, str):
                    text.append(content)
    except APITimeoutError as exc:
        status = "timeout"
        error_type = type(exc).__name__
        error_message = str(exc)
    except APIStatusError as exc:
        status = "http_error"
        status_code = exc.status_code
        error_type = type(exc).__name__
        error_message = str(exc)[:1000]
    except APIConnectionError as exc:
        status = "connection_error"
        error_type = type(exc).__name__
        error_message = str(exc)[:1000]
    except Exception as exc:  # Keep unexpected failures as measured samples.
        status = "client_error"
        error_type = type(exc).__name__
        error_message = str(exc)[:1000]
    finally:
        total_latency = time.perf_counter() - started
        cpu_mean = rss_peak = None
        if resources:
            cpu_mean, rss_peak = await resources.stop()

    duration_after_first = (
        total_latency - first_content
        if first_content is not None and total_latency > first_content else None
    )
    token_rate = (
        output_tokens / duration_after_first
        if output_tokens is not None and duration_after_first and duration_after_first > 0
        else None
    )
    intervals_mean = statistics.fmean(chunk_intervals) if chunk_intervals else None
    metadata = usage_metadata
    return {
        "run_id": run_id,
        "timestamp_utc": timestamp,
        "phase": phase,
        "scenario": scenario,
        "model": model,
        "model_kind": "routing_alias" if model in {"auto", "claude-auto"} else "model",
        "iteration": iteration,
        "concurrency": concurrency,
        "streaming": streaming,
        "status": status,
        "http_status": status_code,
        "error_type": error_type,
        "error_message": error_message,
        "time_to_first_content_seconds": first_content,
        "total_latency_seconds": total_latency,
        "chunk_interval_mean_seconds": intervals_mean,
        "chunk_interval_p95_seconds": _percentile(chunk_intervals, 0.95),
        "content_chunks": content_chunks,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "output_tokens_per_second": token_rate,
        "token_source": "gateway usage metadata; may be estimated upstream" if output_tokens is not None else None,
        "gateway_turn_duration_ms": metadata.get("turn_duration_ms"),
        "credits": metadata.get("credits"),
        "requests_per_second": None,
        "gateway_cpu_percent_mean": cpu_mean,
        "gateway_memory_rss_bytes_peak": rss_peak,
        "content_preview": "".join(text)[:300],
        "usage_metadata_json": json.dumps(metadata, sort_keys=True),
    }


def append_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Append results to CSV, creating a header once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def write_summary(path: Path, rows: list[dict[str, Any]], *, run_info: dict[str, Any]) -> dict[str, Any]:
    """Write grouped latency and reliability summaries from observed rows only."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("phase") == "warmup" or row.get("scenario") == "warmup":
            continue
        groups.setdefault((str(row.get("model")), str(row.get("scenario"))), []).append(row)
    report: dict[str, Any] = {
        "run_info": run_info,
        "total_samples": sum(len(items) for items in groups.values()),
        "results": {},
        "limitations": [
            "Five repetitions support screening, not robust P99 conclusions.",
            "Non-streaming TTFT is unavailable; only streaming requests have observed TTFT.",
            "Token throughput is emitted only when gateway returns usage counts; gateway counts may be estimates.",
            "Gateway CPU/RSS remain null when psutil is unavailable or no gateway process is identified.",
        ],
    }
    for (model, scenario), samples in sorted(groups.items()):
        successful = [item for item in samples if item.get("status") == "success"]
        timeouts = sum(item.get("status") == "timeout" for item in samples)
        errors = sum(item.get("status") != "success" for item in samples)
        http_errors = sum(item.get("status") == "http_error" for item in samples)

        def values(field_name: str) -> list[float]:
            output = []
            for item in successful:
                try:
                    value = item.get(field_name)
                    if value not in (None, ""):
                        output.append(float(value))
                except (TypeError, ValueError):
                    continue
            return output

        latency = values("total_latency_seconds")
        ttft = values("time_to_first_content_seconds")
        credits = values("credits")
        memory = values("gateway_memory_rss_bytes_peak")
        report["results"].setdefault(model, {})[scenario] = {
            "sample_count": len(samples),
            "success_count": len(successful),
            "http_error_rate": http_errors / len(samples) if samples else None,
            "request_failure_rate": errors / len(samples) if samples else None,
            "timeout_rate": timeouts / len(samples) if samples else None,
            "total_latency_seconds": percentile_summary(latency),
            "ttft_seconds": percentile_summary(ttft),
            "gateway_turn_duration_ms": percentile_summary(values("gateway_turn_duration_ms")),
            "output_tokens_per_second": percentile_summary(values("output_tokens_per_second")),
            "requests_per_second": percentile_summary(values("requests_per_second")),
            "gateway_cpu_percent_mean": (
                statistics.fmean(values("gateway_cpu_percent_mean"))
                if values("gateway_cpu_percent_mean") else None
            ),
            "gateway_memory_rss_bytes_peak": max(memory) if memory else None,
            "credits_total": sum(credits) if credits else None,
            "credits_sample_count": len(credits),
            "input_tokens_total": sum(values("input_tokens")) if values("input_tokens") else None,
            "output_tokens_total": sum(values("output_tokens")) if values("output_tokens") else None,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def load_existing_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_empty_result_files(config: BenchmarkConfig) -> None:
    """Create schema-only result artifacts when no approved run exists yet."""
    for name in ("baseline.csv", "optimized.csv"):
        path = config.output_directory / name
        if not path.exists():
            append_csv(path, [])
    summary = config.output_directory / "summary.json"
    if not summary.exists():
        summary.write_text(json.dumps({
            "status": "awaiting approved inference benchmark",
            "measured_samples": 0,
            "note": "No inference results recorded; no measurements fabricated.",
        }, indent=2) + "\n", encoding="utf-8")


def generate_charts(baseline_csv: Path, optimized_csv: Path, output_dir: Path) -> None:
    """Generate requested charts when actual benchmark samples are available."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return
    baseline = load_existing_rows(baseline_csv)
    optimized = load_existing_rows(optimized_csv)
    output_dir.mkdir(parents=True, exist_ok=True)

    def numeric(rows: list[dict[str, Any]], field_name: str) -> dict[str, list[float]]:
        result: dict[str, list[float]] = {}
        for row in rows:
            if row.get("phase") == "warmup" or row.get("scenario") == "warmup":
                continue
            try:
                result.setdefault(row["model"], []).append(float(row[field_name]))
            except (KeyError, TypeError, ValueError):
                continue
        return result

    for field_name, filename, title, ylabel in [
        ("time_to_first_content_seconds", "ttft_by_model.png", "TTFT by model", "Seconds"),
        ("total_latency_seconds", "total_latency_by_model.png", "Total latency by model", "Seconds"),
        ("output_tokens_per_second", "output_token_throughput_by_model.png", "Output token throughput by model", "Tokens/second"),
    ]:
        values = numeric(baseline, field_name)
        if values:
            labels = list(values)
            plt.figure(figsize=(max(8, len(labels) * 0.65), 5))
            plt.boxplot([values[label] for label in labels], tick_labels=labels)
            plt.xticks(rotation=65, ha="right")
            plt.ylabel(ylabel)
            plt.title(title)
            plt.tight_layout()
            plt.savefig(output_dir / filename, dpi=160)
            plt.close()

    concurrency_groups: dict[int, list[float]] = {}
    for row in baseline:
        try:
            if row.get("scenario") != "concurrency_short":
                continue
            level = int(row["concurrency"])
            concurrency_groups.setdefault(level, []).append(
                float(row["total_latency_seconds"])
            )
        except (KeyError, TypeError, ValueError):
            continue
    if concurrency_groups:
        plt.figure(figsize=(8, 5))
        levels = sorted(concurrency_groups)
        medians = [statistics.median(concurrency_groups[level]) for level in levels]
        plt.plot(levels, medians, marker="o")
        plt.xticks(levels)
        plt.xlabel("Concurrent requests")
        plt.ylabel("Seconds")
        plt.title("Latency versus concurrency")
        plt.tight_layout()
        plt.savefig(output_dir / "latency_vs_concurrency.png", dpi=160)
        plt.close()

    if baseline and optimized:
        comparison: dict[str, tuple[list[float], list[float]]] = {}
        for model in {str(row.get("model")) for row in baseline + optimized}:
            before = [float(row["total_latency_seconds"]) for row in baseline if row.get("phase") != "warmup" and row.get("model") == model and row.get("total_latency_seconds")]
            after = [float(row["total_latency_seconds"]) for row in optimized if row.get("phase") != "warmup" and row.get("model") == model and row.get("total_latency_seconds")]
            if before and after:
                comparison[model] = (before, after)
        if comparison:
            labels = list(comparison)
            plt.figure(figsize=(max(8, len(labels) * 0.65), 5))
            x = list(range(len(labels)))
            width = 0.38
            plt.bar([v - width / 2 for v in x], [statistics.median(comparison[m][0]) for m in labels], width, label="Baseline")
            plt.bar([v + width / 2 for v in x], [statistics.median(comparison[m][1]) for m in labels], width, label="Optimized")
            plt.xticks(x, labels, rotation=65, ha="right")
            plt.ylabel("Median total latency (seconds)")
            plt.title("Before/after optimization comparison")
            plt.legend()
            plt.tight_layout()
            plt.savefig(output_dir / "before_after_optimization.png", dpi=160)
            plt.close()


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=str(ROOT / "benchmark_config.yaml"))
    parser.add_argument("--phase", choices=("baseline", "optimized"), default="baseline")
    parser.add_argument("--timeout", type=float, help="Override request timeout in seconds")


async def verify_api(client: AsyncOpenAI) -> list[str]:
    """Fetch advertised model IDs; this is metadata discovery, not inference."""
    response = await client.models.list()
    return discover_models(response)
