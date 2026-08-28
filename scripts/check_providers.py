"""Connectivity smoke check for the configured LLM providers.

Run this before starting a pipeline to confirm your configuration works:

    python scripts/check_providers.py

This resolves everything through ``core.config`` and ``llm.registry``, so what it
reports is exactly what the agents will use -- same provider order, same models,
same credentials.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import (  # noqa: E402
    LLMProvider,
    Purpose,
    env_file,
    get_settings,
    shadowed_env_keys,
)
from llm.registry import get_chat_model, model_name_for, usable_providers  # noqa: E402

PING = "Reply with exactly: Connection Successful"

KEY_VARIABLE = {
    LLMProvider.GOOGLE: "GOOGLE_API_KEY",
    LLMProvider.GROQ: "GROQ_API_KEY",
    LLMProvider.OPENAI: "OPENAI_API_KEY",
    LLMProvider.CEREBRAS: "CEREBRAS_API_KEY",
    LLMProvider.OLLAMA: "",  # local, no key
}


def warn_about_shadowed_keys(path: str) -> None:
    for name in shadowed_env_keys(path):
        shell_value = os.environ[name]
        print(
            f"[warn] {name} is set in your shell environment ({shell_value[:7]}...) and overrides "
            f"{path}. The shell value is the one in use, which is very likely your 401."
        )
        print("       Unset it and restart this shell:  Remove-Item Env:\\" + name)


def check(provider: LLMProvider, settings) -> bool:
    model = model_name_for(provider, Purpose.TEXT, settings)
    print(f"\n--- {provider.value} ---")

    key = settings.api_key_for(provider)
    if key:
        print(f"[ok]   {KEY_VARIABLE[provider]} found ({key[:7]}...)")
    elif provider is LLMProvider.OLLAMA:
        print(f"[ok]   no key needed, base_url={settings.ollama_base_url}")

    for purpose in Purpose:
        print(f"       {purpose.value:<11} -> {model_name_for(provider, purpose, settings)}")

    try:
        response = get_chat_model(Purpose.TEXT, provider, settings).invoke(PING)
        print(f"       ping ({model}) -> {response.content}")
        return True
    except Exception as exc:
        print(f"[fail] {provider.value}: {exc}")
        return False


def main() -> int:
    warn_about_shadowed_keys(env_file())
    settings = get_settings()

    configured = settings.configured_providers()
    usable = usable_providers(settings)

    print(f"provider : {settings.llm_provider.value}")
    print(f"chain    : {', '.join(p.value for p in configured)}")

    missing = [p for p in configured if p not in usable]
    for provider in missing:
        print(f"\n--- {provider.value} ---")
        print(f"[skip] {KEY_VARIABLE[provider]} not set")

    if not usable:
        print("\nNo usable provider. Copy .env.example to .env and add the key for LLM_PROVIDER.")
        return 1

    reachable = sum(check(provider, settings) for provider in usable)

    print(f"\n{reachable}/{len(configured)} configured provider(s) reachable.")
    return 0 if reachable else 1


if __name__ == "__main__":
    raise SystemExit(main())
