"""Small, deterministic redaction helpers.

SolBeat stores only bounded, redacted event data and exposes an even smaller
summary-only view to an agent.  These helpers reduce accidental disclosure;
they are not a universal secret detector.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


_SENSITIVE_KEY_MARKERS = (
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "passwd",
    "password",
    "secret",
    "session",
    "token",
    "privatekey",
    "chatid",
    "senderid",
    "userid",
    "phonenumber",
    "homechannel",
    "allowedusers",
)
_ASSIGNMENT = re.compile(
    r"(?i)\b("
    r"[a-z0-9_.-]*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|private[_-]?key|authorization|cookie|credential|"
    r"passwd|password|secret|session(?:[_-]?(?:id|key))?|token|"
    r"phone[_-]?number(?:[_-]?id)?|chat[_-]?id|sender[_-]?id|user[_-]?id|"
    r"home[_-]?channel|allowed[_-]?users)"
    r")\s*[:=]\s*(?:(?:basic|bearer)\s+)?[^\s,;]+"
)
_SENSITIVE_HEADER = re.compile(
    r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie)\s*:\s*[^\r\n]+"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/:=-]+")
_OPENAI_STYLE_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_URL_PASSWORD = re.compile(r"(?i)(https?://[^:/\s]+:)[^@/\s]+@")
_WHATSAPP_ID = re.compile(r"\b\d{7,20}@(s\.whatsapp\.net|c\.us)\b", re.IGNORECASE)
_E164_PHONE = re.compile(r"(?<![\w+])\+\d{7,15}\b")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _is_sensitive_key(value: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", value.casefold())
    return any(marker in compact for marker in _SENSITIVE_KEY_MARKERS)


def redact_text(value: str, *, max_chars: int) -> str:
    """Normalize, redact common credential shapes, and bound a text value."""

    text = unicodedata.normalize("NFKC", str(value))
    text = _CONTROL.sub(" ", text).replace("```", "''' ")
    text = _URL_PASSWORD.sub(r"\1[REDACTED]@", text)
    text = _SENSITIVE_HEADER.sub(lambda match: f"{match.group(1)}: [REDACTED]", text)
    text = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _BEARER.sub(lambda match: f"{match.group(1)} [REDACTED]", text)
    text = _OPENAI_STYLE_TOKEN.sub("[REDACTED]", text)
    text = _JWT.sub("[REDACTED]", text)
    text = _WHATSAPP_ID.sub("[ACCOUNT_ID_REDACTED]", text)
    text = _E164_PHONE.sub("[PHONE_REDACTED]", text)
    text = " ".join(text.split())
    if len(text) > max_chars:
        text = text[: max(0, max_chars - 1)].rstrip() + "…"
    return text


def redact_value(value: Any, *, text_limit: int = 1_000, depth: int = 0) -> Any:
    """Recursively redact JSON-like data while preserving useful structure."""

    if depth >= 8:
        return "[MAX_DEPTH]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in list(value.items())[:100]:
            key = redact_text(str(raw_key), max_chars=120)
            result[key] = (
                "[REDACTED]"
                if _is_sensitive_key(key)
                else redact_value(child, text_limit=text_limit, depth=depth + 1)
            )
        if len(value) > 100:
            result["_truncated_keys"] = len(value) - 100
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result = [
            redact_value(item, text_limit=text_limit, depth=depth + 1)
            for item in list(value)[:100]
        ]
        if len(value) > 100:
            result.append({"_truncated_items": len(value) - 100})
        return result
    if isinstance(value, str):
        return redact_text(value, max_chars=text_limit)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value), max_chars=text_limit)


def bounded_payload_json(value: Any, *, max_bytes: int) -> str:
    """Return canonical redacted JSON or a non-reversible truncation marker."""

    redacted = redact_value(value)
    encoded = json.dumps(
        redacted, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(encoded.encode("utf-8")) <= max_bytes:
        return encoded
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return json.dumps(
        {"_truncated": True, "redacted_sha256": digest},
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint(value: str) -> str:
    """Store dedupe material as a one-way digest, never as caller input."""

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
