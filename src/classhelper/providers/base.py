"""The provider interface.

Everything the rest of the program needs from a language model is one method.
Keeping the surface this small is what makes "swap in OpenAI, or Ollama, or
Claude" a small module rather than a rewrite -- and it keeps the translation
logic honest, since none of the prompt construction can quietly depend on one
vendor's extensions.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Iterator, Protocol


class ProviderError(Exception):
    """A call failed in a way the user should hear about."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Usage:
    """Running token total for one session.

    Surfaced in the reader's status bar. Not for billing accuracy -- it exists
    so that "how much is this costing me" is answerable at a glance instead of
    being a thing the user worries about silently.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Input tokens the vendor served from its own cache. Counted separately
    # because they are billed at a fraction of the rate -- a thirtieth of it on
    # DeepSeek -- so a total that ignores them is not close, it is wrong.
    cached_tokens: int = 0
    calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, prompt: int, completion: int, cached: int = 0) -> None:
        with self._lock:
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            self.cached_tokens += cached
            self.calls += 1

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class Provider(Protocol):
    name: str
    usage: Usage

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        json_mode: bool = False,
        temperature: float = 0.3,
        timeout: float = 120.0,
        kind: str = "translate",
    ) -> str:
        """Return the assistant's reply text for a single-turn exchange.

        `kind` is only ever bookkeeping: it labels the call in the spending
        ledger. Without it the weekly price check filed itself under
        "translate", and a user reading the breakdown would find calls there
        that translated nothing.
        """
        ...

    def stream(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float = 0.4,
        timeout: float = 300.0,
    ) -> Iterator[str]:
        """Yield the reply in fragments as they arrive.

        Translation does not need this -- a page is written into the document
        when it is complete. A question does: waiting in silence for fifteen
        seconds makes an answer feel broken even when it arrives.
        """
        ...
