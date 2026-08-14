"""Telegram DM channel (plan §14) — per-batch topline + daily payment
report with attached sheet. Plain urllib; the token never appears in logs.
Build/testing messages carry a TEST prefix and are kept few (guardrail).
"""
from __future__ import annotations

import json
import urllib.request
import uuid
from pathlib import Path

from . import config as C

_API = "https://api.telegram.org"


class TelegramError(Exception):
    pass


def _prefix(cfg: C.Config, text: str) -> str:
    return f"TEST {text}" if cfg.test_mode else text


def send_message(cfg: C.Config, text: str) -> None:
    if not cfg.tg_token or not cfg.tg_chat:
        raise TelegramError("telegram token/chat id not configured")
    body = json.dumps({"chat_id": cfg.tg_chat,
                       "text": _prefix(cfg, text)}).encode()
    req = urllib.request.Request(
        f"{_API}/bot{cfg.tg_token}/sendMessage", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.load(r)
    except Exception as e:
        raise TelegramError(f"sendMessage failed: {type(e).__name__}") from e
    if not resp.get("ok"):
        raise TelegramError(f"sendMessage rejected: "
                            f"{str(resp)[:200].replace(cfg.tg_token, '***')}")


def send_document(cfg: C.Config, path: Path, caption: str = "") -> None:
    if not cfg.tg_token or not cfg.tg_chat:
        raise TelegramError("telegram token/chat id not configured")
    path = Path(path)
    boundary = uuid.uuid4().hex
    parts = []

    def field(name: str, value: str):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                     f"name=\"{name}\"\r\n\r\n{value}\r\n".encode())

    field("chat_id", cfg.tg_chat)
    if caption:
        field("caption", _prefix(cfg, caption))
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; "
        f"name=\"document\"; filename=\"{path.name}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n".encode())
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{_API}/bot{cfg.tg_token}/sendDocument", data=body,
        headers={"Content-Type":
                 f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.load(r)
    except Exception as e:
        raise TelegramError(f"sendDocument failed: {type(e).__name__}") from e
    if not resp.get("ok"):
        raise TelegramError("sendDocument rejected")
