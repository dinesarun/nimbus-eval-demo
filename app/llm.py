"""The one place that talks to an LLM. Both the bot and the judge go through here.

Provider is picked from whichever key is set (in the shell or in eval-demo/.env):
    AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT -> Azure OpenAI (model = deployment name)
    OPENAI_API_KEY                               -> OpenAI Chat Completions (default: gpt-4o)
    ANTHROPIC_API_KEY                            -> Claude (default: claude-opus-5)
Override models with BOT_MODEL / JUDGE_MODEL.
"""
import json
import os
from pathlib import Path


def _load_dotenv():
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

if os.environ.get("AZURE_OPENAI_API_KEY"):
    PROVIDER = "azure"
elif os.environ.get("OPENAI_API_KEY"):
    PROVIDER = "openai"
else:
    PROVIDER = "anthropic"
DEFAULT_MODEL = {"azure": "gpt-4o", "openai": "gpt-4o", "anthropic": "claude-opus-5"}[PROVIDER]
BOT_MODEL = os.environ.get("BOT_MODEL", DEFAULT_MODEL)
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", DEFAULT_MODEL)


class LLMError(Exception):
    """Any provider failure (bad key, network, refusal), in one exception type."""


def has_key() -> bool:
    return any(os.environ.get(k) for k in ("AZURE_OPENAI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"))


_client = None


def _get_client():
    global _client
    if _client is None:
        if not has_key():
            raise LLMError("No API key. Copy .env.example to .env, add a key, then restart.")
        if PROVIDER == "azure":
            import openai
            _client = openai.AzureOpenAI(
                api_key=os.environ["AZURE_OPENAI_API_KEY"],
                azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
            )
        elif PROVIDER == "openai":
            import openai
            _client = openai.OpenAI()
        else:
            import anthropic
            _client = anthropic.Anthropic()
    return _client


def chat(model: str, system: str, user: str, temperature: float = 0.0, schema: dict | None = None):
    """Send one system+user message. Returns a string, or a dict if a JSON schema is given."""
    client = _get_client()
    try:
        if PROVIDER in ("openai", "azure"):  # same Chat Completions API
            return _openai(client, model, system, user, temperature, schema)
        return _anthropic(client, model, system, user, schema)
    except LLMError:
        raise
    except Exception as e:  # openai.APIError / anthropic.APIError / network errors
        raise LLMError(f"{type(e).__name__}: {e}") from e


def _openai(client, model, system, user, temperature, schema):
    kwargs = {}
    if schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "result", "strict": True, "schema": schema},
        }
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=1500,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        **kwargs,
    )
    message = response.choices[0].message
    if message.refusal:
        raise LLMError(f"model refused: {message.refusal}")
    return json.loads(message.content) if schema else message.content.strip()


def _anthropic(client, model, system, user, schema):
    output_config = {"effort": "medium" if schema else "low"}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    response = client.beta.messages.create(
        model=model,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config=output_config,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise LLMError("model refused this request")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return json.loads(text) if schema else text
