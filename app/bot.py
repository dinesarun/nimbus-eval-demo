"""The AI feature under test: a refund-support bot for a made-up product, Nimbus CRM.

This is deliberately tiny. In a real product this would sit behind an API and a UI;
for the eval, all that matters is: question in -> answer out.
"""
from pathlib import Path

from app import llm

APP_DIR = Path(__file__).resolve().parent
BOT_MODEL = llm.BOT_MODEL


def list_prompt_versions() -> list[str]:
    return sorted(p.stem for p in (APP_DIR / "prompts").glob("*.txt"))


def system_prompt(version: str) -> str:
    template = (APP_DIR / "prompts" / f"{version}.txt").read_text()
    knowledge_base = (APP_DIR / "knowledge_base.md").read_text()
    return template.replace("{knowledge_base}", knowledge_base)


def ask(version: str, question: str) -> str:
    # A little temperature, like a real chat product: answers vary run to run.
    return llm.chat(BOT_MODEL, system_prompt(version), question, temperature=0.3)


if __name__ == "__main__":
    # Quick manual poke: python -m app.bot v1 "Can I get a refund after 45 days?"
    import sys

    version, question = sys.argv[1], " ".join(sys.argv[2:])
    try:
        print(ask(version, question))
    except llm.LLMError as e:
        sys.exit(f"Error: {e}")
