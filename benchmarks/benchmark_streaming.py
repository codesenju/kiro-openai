"""Streaming TTFT/chunk cadence benchmark, with five runs per advertised model."""
from __future__ import annotations

import argparse
import asyncio
import random
from datetime import UTC, datetime

from benchmark import (
    add_common_arguments,
    append_csv,
    create_client,
    load_config,
    load_existing_rows,
    model_kind,
    run_trial,
    verify_api,
    write_summary,
)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--models", nargs="*")
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--include-aliases", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.timeout:
        config.timeout_seconds = args.timeout
    if args.repetitions:
        config.repetitions = args.repetitions
    client = create_client(config)
    try:
        advertised = await verify_api(client)
        models = [m for m in (args.models or advertised) if m in advertised or args.models]
        if not args.include_aliases:
            models = [m for m in models if m not in config.routing_aliases]
        stream = config.scenarios["medium_streaming"]
        rows = []
        rng = random.Random(config.random_seed)
        for iteration in range(1, config.repetitions + 1):
            iteration_models = models.copy()
            rng.shuffle(iteration_models)
            for model in iteration_models:
                row = await run_trial(
                    client, model=model, prompt=str(stream["prompt"]),
                    scenario="medium_streaming", phase=args.phase, iteration=iteration,
                    streaming=True, max_tokens=config.request_max_tokens,
                )
                row["model_kind"] = model_kind(model, config.routing_aliases)
                rows.append(row)
        path = config.output_directory / f"{args.phase}.csv"
        append_csv(path, rows)
        write_summary(
            config.output_directory / "summary.json",
            load_existing_rows(path),
            run_info={
                "phase": args.phase,
                "timestamp_utc": datetime.now(UTC).isoformat(),
                "advertised_models": advertised,
                "tested_models": models,
                "repetitions": config.repetitions,
                "gateway_base_url": config.base_url,
            },
        )
        print(f"Recorded {len(rows)} streaming samples to {path}.")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
