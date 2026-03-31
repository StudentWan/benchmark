"""LLM cost calculation for Anthropic Claude models."""

# Pricing per million tokens (USD) — updated 2026-03
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4.6": {"input": 5.00, "output": 25.00},
    "claude-sonnet-4.6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4.5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4.5": {"input": 3.00, "output": 15.00},
    "claude-opus-4.5": {"input": 5.00, "output": 25.00},
}


def calculate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """Calculate cost in USD from token counts and model name.

    Returns 0.0 for unknown models rather than raising.
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return 0.0
    return (input_tokens / 1_000_000) * pricing["input"] + (
        output_tokens / 1_000_000
    ) * pricing["output"]
