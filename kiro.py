import os
import time
from typing import Any

from openai import OpenAI


def run_non_streaming_test(
    model: str = "claude-sonnet-4-6",
    prompt: str = "Are you ready?",
) -> dict[str, Any]:
    """Run the original single-turn SDK check and return its measurements."""
    client = OpenAI(
        base_url="http://localhost:8000/v1",
        api_key=os.environ["KIRO_GATEWAY_API_KEY"],
        max_retries=0,
    )
    start = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = time.perf_counter() - start
    content = response.choices[0].message.content
    usage = response.usage
    metadata = usage.model_extra or {} if usage else {}
    kiro = metadata.get("kiro_metadata", {})

    print("\nResponse:")
    print(content)

    print("\n--- Performance Metrics ---")
    print(f"Response time: {elapsed:.3f} seconds")
    if usage:
        print(f"Input tokens: {usage.prompt_tokens}")
        print(f"Output tokens: {usage.completion_tokens}")
        print(f"Total tokens: {usage.total_tokens}")
    if kiro:
        print(f"Kiro processing time: {kiro.get('turn_duration_ms', 0) / 1000:.3f} seconds")
        print(f"Credits consumed: {kiro.get('credits', 'N/A')}")

    return {
        "model": model,
        "response": content,
        "total_latency_seconds": elapsed,
        "input_tokens": usage.prompt_tokens if usage else None,
        "output_tokens": usage.completion_tokens if usage else None,
        "total_tokens": usage.total_tokens if usage else None,
        "gateway_metadata": kiro,
    }


if __name__ == "__main__":
    run_non_streaming_test()
