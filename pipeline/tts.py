from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_DASHSCOPE_ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/"
    "services/aigc/multimodal-generation/generation"
)
RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class TTSError(RuntimeError):
    pass


def tts_cache_signature(config: dict[str, Any]) -> dict[str, Any]:
    """Return synthesis settings that affect audio, excluding credentials."""
    engine = config.get("engine", "macos")
    if engine == "dashscope_qwen3":
        return {
            "engine": engine,
            "model": config.get("model", "qwen3-tts-flash"),
            "voice": config.get("voice", config.get("name", "Cherry")),
            "language_type": config.get("language_type", "Chinese"),
            "endpoint": config.get("endpoint", DEFAULT_DASHSCOPE_ENDPOINT),
            "provider_version": 1,
        }
    return {
        "engine": "macos",
        "voice": config.get("name", "Tingting"),
        "rate": int(config.get("rate", 210)),
        "provider_version": 1,
    }


def _error_detail(body: bytes) -> str:
    if not body:
        return ""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body[:300].decode("utf-8", errors="replace")
    if not isinstance(payload, dict):
        return ""
    message = payload.get("message") or payload.get("code")
    request_id = payload.get("request_id")
    parts = [str(item) for item in (message, request_id) if item]
    return " | ".join(parts)


def _request(request: Request, *, timeout: float, attempts: int) -> bytes:
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            body = exc.read()
            if exc.code in RETRYABLE_STATUS_CODES and attempt + 1 < attempts:
                time.sleep(2**attempt)
                continue
            detail = _error_detail(body)
            suffix = f": {detail}" if detail else ""
            raise TTSError(f"DashScope request failed with HTTP {exc.code}{suffix}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
                continue
            raise TTSError(f"DashScope request failed: {exc}") from exc
    raise TTSError("DashScope request failed after retries")


def _write_atomic(output: Path, content: bytes) -> None:
    if not content:
        raise TTSError("DashScope returned an empty audio file")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.name}.", suffix=".part", dir=output.parent, delete=False
        ) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(output)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def synthesize_dashscope_qwen3(
    text: str,
    output: Path,
    config: dict[str, Any],
) -> None:
    """Generate a complete Qwen3-TTS clip through DashScope's HTTP API."""
    text = text.strip()
    if not text:
        raise TTSError("Qwen3-TTS text must not be empty")
    if len(text) > 600:
        raise TTSError("Qwen3-TTS text exceeds the 600-character API limit")

    api_key_env = str(config.get("api_key_env", "DASHSCOPE_API_KEY"))
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise TTSError(
            f"Missing DashScope API key. Set the {api_key_env} environment variable."
        )

    endpoint = str(config.get("endpoint", DEFAULT_DASHSCOPE_ENDPOINT))
    timeout = float(config.get("timeout", 90))
    attempts = int(config.get("max_retries", 3))
    payload = {
        "model": config.get("model", "qwen3-tts-flash"),
        "input": {
            "text": text,
            "voice": config.get("voice", config.get("name", "Cherry")),
            "language_type": config.get("language_type", "Chinese"),
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "StickMan-Video-Maker/0.4",
        },
        method="POST",
    )
    response_body = _request(request, timeout=timeout, attempts=attempts)
    try:
        response = json.loads(response_body.decode("utf-8"))
        audio = response["output"]["audio"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise TTSError("DashScope returned an invalid synthesis response") from exc

    encoded = audio.get("data") if isinstance(audio, dict) else None
    if encoded:
        try:
            _write_atomic(output, base64.b64decode(encoded, validate=True))
            return
        except (ValueError, TypeError) as exc:
            raise TTSError("DashScope returned invalid Base64 audio data") from exc

    audio_url = audio.get("url") if isinstance(audio, dict) else None
    if not isinstance(audio_url, str) or not audio_url:
        request_id = response.get("request_id") if isinstance(response, dict) else None
        suffix = f" (request_id={request_id})" if request_id else ""
        raise TTSError(f"DashScope response did not include audio data or a URL{suffix}")
    parsed = urlparse(audio_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise TTSError("DashScope returned an invalid audio URL")

    download = Request(audio_url, headers={"User-Agent": "StickMan-Video-Maker/0.4"})
    audio_bytes = _request(download, timeout=timeout, attempts=attempts)
    _write_atomic(output, audio_bytes)
