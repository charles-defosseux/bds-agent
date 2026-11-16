"""One model call through OpenRouter (OpenAI-compatible client), as in the lab. The graph only sees `chat()`'s dict, so
tests can replace the model with a scripted fake."""
import os

from openai import OpenAI

from agent.config import BASE_URL, MODEL

SETUP_HELP = "No OPENROUTER_API_KEY found: copy .env.example to .env and fill it in (the key is never printed)."
_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if not os.getenv("OPENROUTER_API_KEY"):
        raise RuntimeError(SETUP_HELP)
    if _client is None:
        _client = OpenAI(base_url=BASE_URL, api_key=os.getenv("OPENROUTER_API_KEY"), max_retries=3, timeout=90)
    return _client


def chat(messages: list[dict], tools: list[dict], model: str | None = None) -> dict:
    """Returns {"content", "tool_calls": [{"name", "arguments"}], "tokens_in", "tokens_out", "cost_usd"}."""
    reply = _openai().chat.completions.create(model=model or MODEL, messages=messages, tools=tools, temperature=0,
                                              max_tokens=1500)
    msg, usage = reply.choices[0].message, reply.usage
    return {
        "content": msg.content or "",
        "tool_calls": [{"name": c.function.name, "arguments": c.function.arguments or "{}"} for c in msg.tool_calls or []],
        "tokens_in": usage.prompt_tokens if usage else 0,
        "tokens_out": usage.completion_tokens if usage else 0,
        "cost_usd": float(getattr(usage, "cost", None) or 0.0),  # OpenRouter's usage.cost (lab 6)
    }
