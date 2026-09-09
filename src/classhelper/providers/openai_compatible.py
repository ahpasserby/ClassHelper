"""Providers speaking the OpenAI chat-completions protocol.

Which is nearly all of them. DeepSeek, OpenAI, Moonshot, Qwen, SiliconFlow,
vLLM and Ollama's compatibility endpoint differ only in a base URL and a model
name, so one implementation covers them and "use my own provider" becomes a
setting rather than a code change.

Uses `urllib` rather than an HTTP library. That matters more than it looks:
this is a tool people install to read next week's lecture, and every dependency
is another way `pip install` can fail on someone's machine.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Iterator

from ..tls import create_context, explain
from .base import ProviderError, Usage

# Transient upstream conditions. Anything else is a bug or a bad key, and
# retrying those just wastes the user's time and money.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 4


class OpenAICompatibleProvider:
    def __init__(self, api_key: str, base_url: str, ca_bundle: str | None = None,
                 name: str = "openai-compatible", on_usage=None):
        self.name = name
        # Called with (model, kind, prompt, cached, completion) after every
        # billed request. The provider knows the model and the token counts and
        # nothing else does, so this is where the meter has to be read; what is
        # done with the reading is not its business.
        self._on_usage = on_usage
        self._key = api_key
        self._base = base_url.rstrip("/")
        # Built once: constructing an SSL context parses the whole CA bundle,
        # and the deck translator makes hundreds of calls.
        self._ssl = create_context(ca_bundle)
        self.usage = Usage()

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
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                with urllib.request.urlopen(
                    request, timeout=timeout, context=self._ssl
                ) as response:
                    data = json.loads(response.read().decode("utf-8"))
                self._meter(data.get("usage"), model, kind)
                return data["choices"][0]["message"]["content"]

            except urllib.error.HTTPError as exc:
                detail = _detail(exc)
                if exc.code in (401, 403):
                    raise ProviderError(
                        f"{self.name} 拒绝了这个 API key。请在设置里检查。"
                    ) from exc
                if exc.code == 402:
                    raise ProviderError(f"{self.name} 报告账户余额不足。") from exc
                if exc.code == 404:
                    raise ProviderError(
                        f"{self.name} 找不到模型或接口地址（404）：{detail}"
                    ) from exc
                if exc.code not in _RETRY_STATUS:
                    raise ProviderError(
                        f"{self.name} 返回 {exc.code}：{detail}"
                    ) from exc
                last = exc

            except urllib.error.URLError as exc:
                # Certificate problems never resolve by trying again, and the
                # raw OpenSSL message sends people down the wrong path.
                if isinstance(exc.reason, ssl.SSLCertVerificationError):
                    raise ProviderError(explain(exc.reason)) from exc
                last = exc

            except (TimeoutError, json.JSONDecodeError,
                    KeyError, IndexError) as exc:
                last = exc

            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(1.5 * (2 ** attempt))  # 1.5s, 3s, 6s

        raise ProviderError(
            f"重试 {_MAX_ATTEMPTS} 次后仍连不上 {self.name}：{last}",
            retryable=True,
        )


    def stream(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float = 0.4,
        timeout: float = 300.0,
    ) -> Iterator[str]:
        """Yield content fragments from a server-sent-event response.

        Not retried. A half-delivered answer cannot be resumed, and starting
        over silently would make text already on screen rewind -- better to end
        the stream and let the reader offer a retry.
        """
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            # Without this a streamed reply reports no usage at all, and every
            # question the user asked was billed to them and counted as free.
            "stream_options": {"include_usage": True},
        }
        request = urllib.request.Request(
            f"{self._base}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )

        try:
            response = urllib.request.urlopen(
                request, timeout=timeout, context=self._ssl
            )
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"{self.name} 返回 {exc.code}：{_detail(exc)}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                raise ProviderError(explain(exc.reason)) from exc
            raise ProviderError(f"连不上 {self.name}：{exc.reason}") from exc

        with response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue  # keep-alive or partial frame

                if used := chunk.get("usage"):
                    self._meter(used, model, "ask")
                # The frame carrying usage has an empty choices list.
                choices = chunk.get("choices") or [{}]
                delta = choices[0].get("delta") or {}
                # Reasoning models stream their thinking first, in a separate
                # field; the reader shows the answer, so only content is
                # forwarded.
                if text := delta.get("content"):
                    yield text


    def _meter(self, used: dict | None, model: str, kind: str) -> None:
        """Record one request's tokens, for the status bar and the ledger."""
        used = used or {}
        prompt = int(used.get("prompt_tokens") or 0)
        completion = int(used.get("completion_tokens") or 0)
        cached = _cached_tokens(used)
        self.usage.add(prompt, completion, cached)
        if self._on_usage is not None:
            try:
                self._on_usage(model, kind, prompt, cached, completion)
            except Exception:  # noqa: BLE001 - never fail a call over bookkeeping
                pass


def _cached_tokens(used: dict) -> int:
    """How many input tokens were a cache hit.

    Two spellings in the wild: DeepSeek reports `prompt_cache_hit_tokens` at the
    top level, OpenAI nests `cached_tokens` under `prompt_tokens_details`.
    """
    if (hit := used.get("prompt_cache_hit_tokens")) is not None:
        try:
            return max(0, int(hit))
        except (TypeError, ValueError):
            return 0
    details = used.get("prompt_tokens_details") or {}
    try:
        return max(0, int(details.get("cached_tokens") or 0))
    except (TypeError, ValueError):
        return 0


def _detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        return payload.get("error", {}).get("message", "")[:200] or exc.reason
    except (ValueError, OSError):
        return str(exc.reason)
