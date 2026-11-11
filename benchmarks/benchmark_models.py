"""Five-scenario, randomized multi-model screening runner."""
from __future__ import annotations

import argparse
import asyncio
import platform
import random
from datetime import UTC, datetime

from benchmark import (
    BenchmarkConfig,
    add_common_arguments,
    append_csv,
    build_long_context,
    create_client,
    load_config,
    load_existing_rows,
    model_kind,
    run_trial,
    verify_api,
    write_summary,
)


def make_prompt(scenario: str, config: BenchmarkConfig, long_prompt: str) -> str:
    spec = config.scenarios[scenario]
    return long_prompt if scenario == "long_context" else str(spec.get("prompt", ""))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--models", nargs="*", help="Optional explicit model IDs")
    parser.add_argument("--repetitions", type=int, help="Override measured runs per model/scenario")
    parser.add_argument("--include-aliases", action="store_true", help="Run all scenarios for routing aliases too")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.timeout:
        config.timeout_seconds = args.timeout
    if args.repetitions:
        config.repetitions = args.repetitions
    client = create_client(config)
    try:
        advertised = await verify_api(client)
        if not advertised:
            raise RuntimeError("/v1/models returned no model IDs")
        models = [m for m in (args.models or advertised) if m in advertised or args.models]
        if not args.include_aliases:
            concrete = [m for m in models if model_kind(m, config.routing_aliases) != "routing_alias"]
            aliases = [m for m in models if model_kind(m, config.routing_aliases) == "routing_alias"]
            models = concrete
        else:
            aliases = []
        rng = random.Random(config.random_seed)
        rng.shuffle(models)
        long_prompt, context_tokens = build_long_context(config.long_context_target_tokens)
        results_path = config.output_directory / f"{args.phase}.csv"
        rows = []
        # Warm-ups are separate, explicitly labelled rows; they are not included
        # in measured repetition summaries by the report scripts.
        for model in models:
            for index in range(config.warmup_per_model):
                warmup = await run_trial(
                    client, model=model, prompt=config.scenarios["short"]["prompt"],
                    scenario="warmup", phase="warmup", iteration=index + 1,
                    max_tokens=config.request_max_tokens,
                )
                warmup["model_kind"] = model_kind(model, config.routing_aliases)
                warmup["content_preview"] = "warmup; excluded from measured runs"
                rows.append(warmup)
        for scenario in config.scenarios:
            if scenario == "medium_streaming":
                continue
            for iteration in range(1, config.repetitions + 1):
                scenario_models = models.copy()
                rng.shuffle(scenario_models)
                for model in scenario_models:
                    row = await run_trial(
                        client, model=model, prompt=make_prompt(scenario, config, long_prompt),
                        scenario=scenario, phase=args.phase, iteration=iteration,
                        streaming=False, max_tokens=config.request_max_tokens,
                    )
                    row["model_kind"] = model_kind(model, config.routing_aliases)
                    if scenario == "long_context":
                        row["synthetic_context_cl100k_tokens"] = context_tokens
                    rows.append(row)
        stream_spec = config.scenarios.get("medium_streaming", {})
        for iteration in range(1, config.repetitions + 1):
            scenario_models = models.copy()
            rng.shuffle(scenario_models)
            for model in scenario_models:
                row = await run_trial(
                    client, model=model, prompt=str(stream_spec.get("prompt", "")),
                    scenario="medium_streaming", phase=args.phase, iteration=iteration,
                    streaming=True, max_tokens=config.request_max_tokens,
                )
                row["model_kind"] = model_kind(model, config.routing_aliases)
                rows.append(row)
        # Alias behavior gets a small dedicated routing screen, not a duplicate
        # underlying-model ranking across all five expensive prompts.
        for model in aliases:
            for iteration in range(1, config.repetitions + 1):
                row = await run_trial(
                    client, model=model, prompt=config.scenarios["short"]["prompt"],
                    scenario="alias_short", phase=args.phase, iteration=iteration,
                    max_tokens=config.request_max_tokens,
                )
                row["model_kind"] = "routing_alias"
                rows.append(row)
        append_csv(results_path, rows)
        all_rows = load_existing_rows(results_path)
        write_summary(
            config.output_directory / "summary.json", all_rows,
            run_info={
                "phase": args.phase,
                "timestamp_utc": datetime.now(UTC).isoformat(),
                "gateway_base_url": config.base_url,
                "advertised_models": advertised,
                "measured_models_randomized_order": models,
                "routing_aliases_screened": aliases,
                "repetitions": config.repetitions,
                "warmup_per_model": config.warmup_per_model,
                "synthetic_context_target_tokens": config.long_context_target_tokens,
                "synthetic_context_cl100k_tokens": context_tokens,
                "python": platform.python_version(),
                "config_path": str(config.config_path),
            },
        )
        from benchmark import generate_charts
        generate_charts(
            config.output_directory / "baseline.csv",
            config.output_directory / "optimized.csv",
            config.chart_directory,
        )
        print(f"Recorded {len(rows)} samples to {results_path}; failures retained in CSV.")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
