"""One model call through OpenRouter (OpenAI-compatible client), as in the lab. The graph only sees `chat()`'s dict, so
tests can replace the model with a scripted fake."""
import json
import os

from openai import OpenAI

from agent.config import BASE_URL, MODEL, ROOT

SETUP_HELP = "No OPENROUTER_API_KEY found: copy .env.example to .env and fill it in (the key is never printed)."
_client: OpenAI | None = None
PRICES_PATH = ROOT / "config" / "prices.json"


def price_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    """Fallback cost from config/prices.json (per-model entry, else \"default\") when the API gives no usage.cost."""
    prices = json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    p = prices.get(model) or prices["default"]
    return (tokens_in * p["input_per_mtok"] + tokens_out * p["output_per_mtok"]) / 1_000_000


def _openai() -> OpenAI:
    global _client
    if not os.getenv("OPENROUTER_API_KEY"):
        raise RuntimeError(SETUP_HELP)
    if _client is None:
        _client = OpenAI(base_url=BASE_URL, api_key=os.getenv("OPENROUTER_API_KEY"), max_retries=3, timeout=90)
    return _client


def chat(messages: list[dict], tools: list[dict], model: str | None = None) -> dict:
    """Returns {"content", "tool_calls": [{"name", "arguments"}], "tokens_in", "tokens_out", "cost_usd"}."""
    # One tool call per step (phase 7): ask for it here; the graph also handles a provider that ignores the flag.
    reply = _openai().chat.completions.create(model=model or MODEL, messages=messages, tools=tools, temperature=0,
                                              max_tokens=1500, parallel_tool_calls=False)
    msg, usage = reply.choices[0].message, reply.usage
    tokens_in = usage.prompt_tokens if usage else 0
    tokens_out = usage.completion_tokens if usage else 0
    cost = float(getattr(usage, "cost", None) or 0.0)  # OpenRouter's usage.cost (lab 6)
    return {
        "content": msg.content or "",
        "tool_calls": [{"name": c.function.name, "arguments": c.function.arguments or "{}"} for c in msg.tool_calls or []],
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost or price_usd(model or MODEL, tokens_in, tokens_out),
    }
