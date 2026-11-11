"""Gradual short-prompt concurrency test; stops escalation after failures by default."""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from benchmark import (
    ResourceSampler,
    add_common_arguments,
    append_csv,
    create_client,
    load_config,
    load_existing_rows,
    run_trial,
    verify_api,
    write_summary,
)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--model", help="Concurrency model; defaults to configured representative")
    parser.add_argument("--levels", nargs="+", type=int)
    parser.add_argument("--repetitions", type=int)
    args = parser.parse_args()
    config = load_config(args.config)
    if args.timeout:
        config.timeout_seconds = args.timeout
    levels = args.levels or config.concurrency_levels
    repetitions = args.repetitions or config.concurrency_repetitions
    model = args.model or config.concurrency_model
    client = create_client(config)
    try:
        advertised = await verify_api(client)
        if model not in advertised:
            raise RuntimeError(f"Concurrency model {model!r} is not advertised")
        rows = []
        stopped_at: int | None = None
        for level in levels:
            level_failed = False
            for iteration in range(1, repetitions + 1):
                started = asyncio.get_running_loop().time()
                resources = ResourceSampler()
                await resources.start()
                tasks = [
                    run_trial(
                        client, model=model,
                        prompt=config.scenarios["short"]["prompt"],
                        scenario="concurrency_short", phase=args.phase,
                        iteration=iteration, concurrency=level,
                        max_tokens=config.request_max_tokens,
                        monitor_resources=False,
                    )
                    for _ in range(level)
                ]
                batch = await asyncio.gather(*tasks)
                cpu_mean, rss_peak = await resources.stop()
                elapsed = asyncio.get_running_loop().time() - started
                throughput = level / elapsed if elapsed > 0 else None
                for row in batch:
                    row["requests_per_second"] = throughput
                    row["gateway_cpu_percent_mean"] = cpu_mean
                    row["gateway_memory_rss_bytes_peak"] = rss_peak
                    rows.append(row)
                if any(row["status"] != "success" for row in batch):
                    level_failed = True
                    if config.stop_concurrency_on_failure:
                        break
            if level_failed and config.stop_concurrency_on_failure:
                stopped_at = level
                break
        path = config.output_directory / f"{args.phase}.csv"
        append_csv(path, rows)
        write_summary(
            config.output_directory / "summary.json",
            load_existing_rows(path),
            run_info={
                "phase": args.phase,
                "timestamp_utc": datetime.now(UTC).isoformat(),
                "gateway_base_url": config.base_url,
                "advertised_models": advertised,
                "concurrency_model": model,
                "levels_requested": levels,
                "repetitions_per_level": repetitions,
                "stopped_after_level": stopped_at,
                "no_retry": True,
            },
        )
        from benchmark import generate_charts
        generate_charts(
            config.output_directory / "baseline.csv",
            config.output_directory / "optimized.csv",
            config.chart_directory,
        )
        print(f"Recorded {len(rows)} concurrency samples to {path}; stopped level={stopped_at}.")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
