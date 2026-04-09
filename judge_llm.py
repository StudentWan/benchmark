"""Claude-based judge LLM for evaluating agent execution traces.

Replaces browser-use's ChatGoogle wrapper with direct Anthropic API calls.
"""

import json
import os

import anthropic

from judge import JudgementResult

# Default judge model — use a capable but cost-effective model
JUDGE_MODEL = "claude-sonnet-4.6"


async def invoke_judge(
    system_prompt: str,
    user_content: list[dict],
    model: str | None = None,
    api_key: str | None = None,
) -> JudgementResult:
    """Evaluate an agent trace using Claude as judge.

    Args:
        system_prompt: System message with evaluation framework.
        user_content: List of Anthropic API content blocks (text + images).
        model: Claude model to use (default: claude-sonnet-4-6).
        api_key: Anthropic API key (default: ANTHROPIC_API_KEY env var).

    Returns:
        JudgementResult with verdict, reasoning, and metadata.
    """
    resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY")
    has_auth_token = bool(os.getenv("ANTHROPIC_AUTH_TOKEN"))
    has_proxy = bool(os.getenv("ANTHROPIC_BASE_URL"))
    if not resolved_key and not has_auth_token and not has_proxy:
        raise ValueError(
            "No Anthropic credentials found. Set ANTHROPIC_API_KEY, "
            "ANTHROPIC_AUTH_TOKEN, or ANTHROPIC_BASE_URL."
        )

    # Let SDK auto-detect env vars; only override if explicit key provided
    client = anthropic.AsyncAnthropic(
        **({"api_key": resolved_key} if resolved_key else {})
    )
    resolved_model = model or JUDGE_MODEL

    async with client.messages.stream(
        model=resolved_model,
        max_tokens=64000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        response = await stream.get_final_message()

    # Extract text from response
    response_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            response_text += block.text

    # Parse JSON from response — handle markdown code blocks
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        # Remove ```json ... ``` wrapper
        lines = cleaned.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        cleaned = "\n".join(lines)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        # Fallback: try to find JSON object in the response
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(response_text[start:end])
        else:
            raise ValueError(
                f"Could not parse judge response as JSON: {e}\n"
                f"Response: {response_text[:500]}"
            ) from e

    return JudgementResult(**data)
