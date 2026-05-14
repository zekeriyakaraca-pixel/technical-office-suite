from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

_DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
_TIMEOUT_SECONDS = 90


def _api_url() -> str:
    model = os.environ.get("GEMINI_MODEL", _DEFAULT_GEMINI_MODEL).strip()
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@dataclass
class GeminiRunResult:
    content: str
    ok: bool = True
    error: str | None = None


class GeminiBridge:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = httpx.Client(timeout=_TIMEOUT_SECONDS)

    def run(
        self,
        system_prompt: str,
        history: list[dict],
        user_message: str,
    ) -> GeminiRunResult:
        contents = []
        for item in history[-12:]:
            role = item.get("role", "")
            content = item.get("content", "")
            if role == "assistant":
                role = "model"
            if role in {"user", "model"} and isinstance(content, str) and content.strip():
                contents.append({"role": role, "parts": [{"text": content.strip()}]})
        contents.append({"role": "user", "parts": [{"text": user_message}]})

        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": 4096,
                "temperature": 0.4,
            },
        }

        try:
            resp = self._client.post(
                _api_url(),
                params={"key": self._api_key},
                json=payload,
            )
        except httpx.TimeoutException:
            return GeminiRunResult(
                content=(
                    "Gemini API yanit vermedi (60 sn zaman asimi). "
                    "Lutfen tekrar deneyin."
                ),
                ok=False,
                error="timeout",
            )
        except httpx.RequestError as exc:
            return GeminiRunResult(
                content=f"Gemini API baglanti hatasi: {exc}",
                ok=False,
                error=str(exc),
            )

        if resp.status_code == 429:
            return GeminiRunResult(
                content=(
                    "Gemini API ucretsiz kota limiti asildi (dakikada 5 istek / gunluk 20 istek). "
                    "Bir dakika bekleyip tekrar deneyin."
                ),
                ok=False,
                error="rate_limited",
            )

        if resp.status_code == 503:
            return GeminiRunResult(
                content=(
                    "Gemini API su an yogun talep altinda, gecici olarak kullanilamiyor. "
                    "Birka dakika bekleyip tekrar deneyin."
                ),
                ok=False,
                error="unavailable",
            )

        if resp.status_code != 200:
            return GeminiRunResult(
                content=f"Gemini API hatasi ({resp.status_code}). Lutfen tekrar deneyin.",
                ok=False,
                error=f"http_{resp.status_code}",
            )

        try:
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return GeminiRunResult(content=text.strip(), ok=True)
        except (KeyError, IndexError, ValueError) as exc:
            return GeminiRunResult(
                content="Gemini API yaniti beklenmeyen formatta.",
                ok=False,
                error=str(exc),
            )

    def close(self) -> None:
        self._client.close()


def get_gemini_bridge() -> GeminiBridge | None:
    """GEMINI_API_KEY env var ayarliysa GeminiBridge, yoksa None doner."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    return GeminiBridge(api_key)
