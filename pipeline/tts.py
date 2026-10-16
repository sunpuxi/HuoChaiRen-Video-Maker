from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


USER_AGENT = "StickMan-Video-Maker/0.4"
DEFAULT_DASHSCOPE_ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/"
    "services/aigc/multimodal-generation/generation"
)
DEFAULT_MINIMAX_ENDPOINT = "https://api.minimax.cn/v1/t2a_v2"
MINIMAX_TEXT_LIMIT = 10_000
# MiniMax reports business failures inside an HTTP 200 body via base_resp.
# 1001 timeout / 1002 rate limit / 1039 TPM rate limit are worth another try.
MINIMAX_RETRYABLE_STATUS_CODES = {1001, 1002, 1039}
# 1002 is a per-minute rate limit and 1039 a per-minute token limit: both need
# to wait out a window, not just back off a couple of seconds.
MINIMAX_RATE_LIMIT_STATUS_CODES = {1002, 1039}
MINIMAX_RATE_LIMIT_BACKOFF = 20.0
MINIMAX_DEFAULT_MODEL = "speech-2.8-hd"
MINIMAX_DEFAULT_VOICE = "male-qn-qingse"
MINIMAX_INTERJECTIONS = {
    "laughs",
    "chuckle",
    "coughs",
    "clear-throat",
    "groans",
    "breath",
    "pant",
    "inhale",
    "exhale",
    "gasps",
    "sniffs",
    "sighs",
    "snorts",
    "burps",
    "lip-smacking",
    "humming",
    "hissing",
    "emm",
    "sneezes",
}
_PAUSE_MARKER = re.compile(r"<#\d+(?:\.\d{1,2})?#>")
_INTERJECTION_MARKER = re.compile(
    r"\((" + "|".join(sorted(MINIMAX_INTERJECTIONS)) + r")\)", re.IGNORECASE
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
    if engine == "minimax":
        return {
            "engine": engine,
            "model": config.get("model", MINIMAX_DEFAULT_MODEL),
            "voice_id": config.get("voice_id", config.get("voice", MINIMAX_DEFAULT_VOICE)),
            "speed": float(config.get("speed", 1.0)),
            "vol": float(config.get("vol", 1.0)),
            "pitch": int(config.get("pitch", 0)),
            "emotion": config.get("emotion"),
            "language_boost": config.get("language_boost", "auto"),
            "text_normalization_mode": config.get("text_normalization_mode"),
            "format": config.get("format", "mp3"),
            "sample_rate": int(config.get("sample_rate", 32000)),
            "bitrate": int(config.get("bitrate", 128000)),
            "channel": int(config.get("channel", 1)),
            "pronunciation_dict": config.get("pronunciation_dict"),
            "endpoint": config.get("endpoint", DEFAULT_MINIMAX_ENDPOINT),
            "provider_version": 1,
        }
    return {
        "engine": "macos",
        "voice": config.get("name", "Tingting"),
        "rate": int(config.get("rate", 210)),
        "provider_version": 1,
    }


def strip_control_markers(text: str) -> str:
    """Drop speech-only control markers so they never reach a subtitle.

    MiniMax accepts inline pause markers such as ``<#0.4#>`` and interjection
    tags such as ``(sighs)``. Both steer the performance but are not spoken
    words, so on-screen captions derived from narration must not show them.
    Texts without markers are returned unchanged.
    """
    cleaned = _PAUSE_MARKER.sub("", text)
    cleaned = _INTERJECTION_MARKER.sub("", cleaned)
    if cleaned == text:
        return text
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([，。！？；：、])", r"\1", cleaned)
    return cleaned.strip()


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


def _request(
    request: Request, *, timeout: float, attempts: int, provider: str = "DashScope"
) -> bytes:
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
            raise TTSError(f"{provider} request failed with HTTP {exc.code}{suffix}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
                continue
            raise TTSError(f"{provider} request failed: {exc}") from exc
    raise TTSError(f"{provider} request failed after retries")


def _write_atomic(output: Path, content: bytes, *, provider: str = "DashScope") -> None:
    if not content:
        raise TTSError(f"{provider} returned an empty audio file")
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


def _download_audio(
    url: str, output: Path, *, timeout: float, attempts: int, provider: str
) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise TTSError(f"{provider} returned an invalid audio URL")
    download = Request(url, headers={"User-Agent": USER_AGENT})
    audio = _request(download, timeout=timeout, attempts=attempts, provider=provider)
    _write_atomic(output, audio, provider=provider)


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
            "User-Agent": USER_AGENT,
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

    _download_audio(
        audio_url, output, timeout=timeout, attempts=attempts, provider="DashScope"
    )


def minimax_payload(text: str, config: dict[str, Any]) -> dict[str, Any]:
    """Build the non-streaming ``/v1/t2a_v2`` request body."""
    payload: dict[str, Any] = {
        "model": config.get("model", MINIMAX_DEFAULT_MODEL),
        "text": text,
        "stream": False,
        "output_format": "hex",
        "voice_setting": {
            "voice_id": config.get("voice_id", config.get("voice", MINIMAX_DEFAULT_VOICE)),
            "speed": float(config.get("speed", 1.0)),
            "vol": float(config.get("vol", 1.0)),
            "pitch": int(config.get("pitch", 0)),
        },
        "audio_setting": {
            "sample_rate": int(config.get("sample_rate", 32000)),
            "format": config.get("format", "mp3"),
            "channel": int(config.get("channel", 1)),
        },
    }
    if payload["audio_setting"]["format"] == "mp3":
        payload["audio_setting"]["bitrate"] = int(config.get("bitrate", 128000))

    emotion = config.get("emotion")
    if emotion:
        payload["voice_setting"]["emotion"] = emotion

    language_boost = config.get("language_boost", "auto")
    if language_boost:
        payload["language_boost"] = language_boost

    text_normalization = config.get("text_normalization_mode")
    if text_normalization:
        payload["text_normalization_mode"] = text_normalization

    pronunciation = config.get("pronunciation_dict")
    if isinstance(pronunciation, dict) and pronunciation:
        payload["pronunciation_dict"] = pronunciation

    return payload


def _minimax_status(response: Any) -> tuple[int, str]:
    base_resp = response.get("base_resp") if isinstance(response, dict) else None
    if not isinstance(base_resp, dict):
        return 0, ""
    code = base_resp.get("status_code", 0)
    return (code if isinstance(code, int) else 0), str(base_resp.get("status_msg") or "")


def synthesize_minimax(text: str, output: Path, config: dict[str, Any]) -> None:
    """Generate one MiniMax clip through the non-streaming T2A HTTP endpoint.

    The pipeline renders offline, so a single request that returns the complete
    clip is simpler and equally accurate than the WebSocket stream; both
    endpoints return the same audio. Configure ``endpoint`` to use
    ``https://api-bj.minimaxi.com/v1/t2a_v2`` for lower latency in north China.
    """
    text = text.strip()
    if not text:
        raise TTSError("MiniMax text must not be empty")
    if len(text) > MINIMAX_TEXT_LIMIT:
        raise TTSError(f"MiniMax text exceeds the {MINIMAX_TEXT_LIMIT}-character API limit")

    api_key_env = str(config.get("api_key_env", "MINIMAX_API_KEY"))
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise TTSError(
            f"Missing MiniMax API key. Set the {api_key_env} environment variable."
        )

    endpoint = str(config.get("endpoint", DEFAULT_MINIMAX_ENDPOINT))
    timeout = float(config.get("timeout", 90))
    attempts = max(1, int(config.get("max_retries", 3)))
    request = Request(
        endpoint,
        data=json.dumps(minimax_payload(text, config), ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    response: Any = None
    for attempt in range(attempts):
        try:
            body = _request(request, timeout=timeout, attempts=1, provider="MiniMax")
        except TTSError:
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
                continue
            raise
        try:
            response = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TTSError("MiniMax returned an invalid synthesis response") from exc

        status_code, status_msg = _minimax_status(response)
        if status_code == 0:
            break
        if status_code in MINIMAX_RETRYABLE_STATUS_CODES and attempt + 1 < attempts:
            if status_code in MINIMAX_RATE_LIMIT_STATUS_CODES:
                # exponential backoff off a 20s base clears a one-minute window
                time.sleep(MINIMAX_RATE_LIMIT_BACKOFF * (attempt + 1))
            else:
                time.sleep(2**attempt)
            continue
        trace_id = response.get("trace_id") if isinstance(response, dict) else None
        suffix = f" (trace_id={trace_id})" if trace_id else ""
        raise TTSError(
            f"MiniMax synthesis failed with status_code {status_code}: {status_msg}{suffix}"
        )

    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        trace_id = response.get("trace_id") if isinstance(response, dict) else None
        suffix = f" (trace_id={trace_id})" if trace_id else ""
        raise TTSError(f"MiniMax response did not include audio data{suffix}")

    audio = data.get("audio")
    if not isinstance(audio, str) or not audio.strip():
        trace_id = response.get("trace_id") if isinstance(response, dict) else None
        suffix = f" (trace_id={trace_id})" if trace_id else ""
        raise TTSError(f"MiniMax response did not include audio data{suffix}")

    audio = audio.strip()
    if audio.startswith(("http://", "https://")):
        _download_audio(
            audio, output, timeout=timeout, attempts=attempts, provider="MiniMax"
        )
        return
    try:
        _write_atomic(output, bytes.fromhex(audio), provider="MiniMax")
    except ValueError as exc:
        raise TTSError("MiniMax returned invalid hex audio data") from exc
