"""Model providers.

Adding one means writing a class with a `complete()` method and registering it
here. Nothing else in the codebase knows which vendor is in use.
"""

from __future__ import annotations

from ..config import Config
from .base import Provider, ProviderError
from .openai_compatible import OpenAICompatibleProvider


def build(cfg: Config, meter: bool = True) -> Provider:
    """Every supported provider speaks the OpenAI chat-completions protocol, so
    the only thing that varies is the base URL, which config already resolves.

    `meter` writes every call into the spending ledger. Off for the settings
    page's connection test, which is the program checking itself rather than
    work the user asked for.
    """
    if not cfg.base_url:
        raise ProviderError(f"{cfg.provider} 没有配置接口地址（base_url）。")

    on_usage = None
    if meter:
        from ..spend import record

        def on_usage(model, kind, prompt, cached, completion):  # noqa: F811
            record(cfg.provider, model, kind, prompt, cached, completion)

    return OpenAICompatibleProvider(
        cfg.api_key, cfg.base_url, cfg.ca_bundle or None, name=cfg.provider,
        on_usage=on_usage,
    )


__all__ = ["Provider", "ProviderError", "build", "OpenAICompatibleProvider"]
