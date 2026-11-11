import os
import time
from typing import Any

from openai import OpenAI


def run_streaming_test(
    model: str = "claude-sonnet-4-6",
    prompt: str = "Explain Kubernetes networking in 200 words.",
) -> dict[str, Any]:
    """Run original stream-latency check; return client-observed timings."""
    client = OpenAI(
        base_url="http://localhost:8000/v1",
        api_key=os.environ["KIRO_GATEWAY_API_KEY"],
        max_retries=0,
    )
    start = time.perf_counter()
    first_token = None
    output = []

    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
    )

    for chunk in stream:
        content = chunk.choices[0].delta.content if chunk.choices else None

        if content:
            if first_token is None:
                first_token = time.perf_counter()

            output.append(content)
            print(content, end="", flush=True)

    end = time.perf_counter()
    ttft = end - start if first_token is None else first_token - start

    print("\n\n--- Streaming Performance ---")
    if first_token is not None:
        print(f"Time to first token: {ttft:.3f}s")
    else:
        print("No content received")
    print(f"Total response time: {end - start:.3f}s")
    print(f"Output characters: {len(''.join(output))}")

    return {
        "model": model,
        "response": "".join(output),
        "time_to_first_content_seconds": ttft if first_token is not None else None,
        "total_latency_seconds": end - start,
        "content_chunks": len(output),
        "output_characters": len("".join(output)),
    }


if __name__ == "__main__":
    run_streaming_test()
