"""Load .env (stdlib only, fallback to environment variables).

Priority: process env > .env file > defaults.
Searches for .env in the package root and two parents up.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path


def _load_dotenv() -> None:
    for candidate in [
        Path(__file__).resolve().parents[1] / ".env",   # human-connector/.env
        Path(__file__).resolve().parents[2] / ".env",   # one level up
        Path.cwd() / ".env",
    ]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
            break


_load_dotenv()


def get_port() -> int:
    return int(os.environ.get("URIRUN_HUMAN_PORT", "8797"))


def get_host() -> str:
    return os.environ.get("URIRUN_HUMAN_HOST", "0.0.0.0")


def get_node() -> str:
    return os.environ.get("URIRUN_HUMAN_NODE", "cell-a")


def get_lan_ip() -> str:
    raw = os.environ.get("URIRUN_HUMAN_LAN_IP", "auto")
    if raw and raw != "auto":
        return raw
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_lan_url(port: int | None = None) -> str:
    p = port if port is not None else get_port()
    return f"http://{get_lan_ip()}:{p}"
