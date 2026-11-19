"""Time-based card auth client (talks to the cloud relay).

Contract with the relay (both sides must match):
- HMAC key: the exe's relay App-Token (the X-App-Token value).
- Request signature: HMAC-SHA256 hex over ``machine_id + "|" + timestamp``
  (timestamp = Unix seconds). Sent as the ``X-Auth-Sign`` header AND
  the ``signature`` body field; the body also carries ``machine_id``
  and ``timestamp``. The relay rejects timestamps more than 300 s off.
- Response signature: HMAC-SHA256 hex over
  ``machine_id + "|" + expire_at + "|" + remaining_seconds + "|" + timestamp``
  (relay time). The client verifies it and distrusts any response that
  fails verification — a bad signature means "no auth", never the
  returned remaining time.
- POST /api/auth/info -> {machine_id, expire_at, remaining_seconds,
  status: "trial"|"active"|"expired", signature, server_time}.
  A new machine_id is auto-created with a 2-hour trial.
- POST /api/auth/activate {machine_id, card_code, timestamp, signature}
  -> 200 {expire_at, remaining_seconds, added_seconds, card_type};
  400 {error: invalid_card|card_used|card_bound_other|bad_signature|
  stale_timestamp}.

Countdown rules (anti-tamper):
- The authoritative remaining time always comes from the relay. While
  this process runs, the local countdown is driven by a monotonic
  clock (``time.monotonic``), so changing the system clock cannot
  extend it.
- When the relay is unreachable, the last successful sync is reused
  from a small cache file (data/auth_cache.json) for at most 24 hours
  counted from that sync. Past that, the client reports expired and
  the panel asks the user to check the network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from bosshunter.ai.credentials import get_relay_token, get_relay_url
from bosshunter.auth.hwid import get_machine_id

SYNC_TIMEOUT = 10.0
ACTIVATE_TIMEOUT = 15.0
TIMESTAMP_SKEW_SECONDS = 300
OFFLINE_GRACE_SECONDS = 24 * 3600

DEFAULT_BUY_URL = ""  # 故意留空：没有购买链接时状态栏不显示购买按钮
DEFAULT_WECHAT_ID = "jmlq2004"
DEFAULT_WECHAT_QR_PATH = "/wechat-qr.png"

ACTIVATE_ERROR_MESSAGES = {
    "invalid_card": "这个卡密不对，请检查有没有输错。",
    "card_used": "这张卡已经用过了。",
    "card_bound_other": "这张卡已经绑到别的电脑上了，一张卡只能用在一台电脑上。",
    "bad_signature": "验证没通过，稍后再试。",
    "stale_timestamp": "验证没通过，稍后再试。",
}


def sign_request(secret: str, machine_id: str, timestamp: int) -> str:
    """HMAC-SHA256 hex over ``machine_id|timestamp``."""
    message = f"{machine_id}|{int(timestamp)}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def sign_response(secret: str, machine_id: str, expire_at: int, remaining_seconds: int, timestamp: int) -> str:
    """HMAC-SHA256 hex over ``machine_id|expire_at|remaining_seconds|timestamp``."""
    message = f"{machine_id}|{int(expire_at)}|{int(remaining_seconds)}|{int(timestamp)}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_response_signature(secret: str, payload: dict) -> bool:
    """Check the relay's response signature (constant-time compare)."""
    try:
        machine_id = str(payload["machine_id"])
        expire_at = int(payload["expire_at"])
        remaining_seconds = int(payload["remaining_seconds"])
        timestamp = int(payload["server_time"])
        signature = str(payload["signature"])
    except (KeyError, TypeError, ValueError):
        return False
    expected = sign_response(secret, machine_id, expire_at, remaining_seconds, timestamp)
    return hmac.compare_digest(expected, signature)


def get_auth_display_config(config: dict) -> dict[str, str]:
    """Public display values for the auth bar (no secrets in here).

    Defaults apply when the [auth] section is missing, so installs
    whose config.yaml predates the auth feature still work. Change
    one line in config.yaml to swap the shop link, WeChat id or QR.
    """
    auth_cfg = config.get("auth") if isinstance(config, dict) else None
    if not isinstance(auth_cfg, dict):
        auth_cfg = {}
    buy_url = str(auth_cfg.get("buy_url") or "").strip() or DEFAULT_BUY_URL
    wechat_id = str(auth_cfg.get("wechat_id") or "").strip() or DEFAULT_WECHAT_ID
    qr_path = str(auth_cfg.get("wechat_qr_path") or "").strip() or DEFAULT_WECHAT_QR_PATH
    return {"buy_url": buy_url, "wechat_id": wechat_id, "wechat_qr_path": qr_path}


def normalize_card_code(raw: str) -> str:
    """Trim spaces and upper-case a card code; dashes are kept as typed."""
    return "".join(str(raw or "").split()).upper()


class AuthClient:
    """Holds the current auth state and talks to the relay.

    ``transport`` is an httpx transport for tests (a stub relay). In
    production it stays None and real HTTP is used.
    """

    def __init__(
        self,
        config: dict,
        cache_path: str | Path | None = None,
        transport: Any | None = None,
        machine_id: str | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._cache_path = Path(cache_path) if cache_path else None
        self._lock = threading.Lock()
        self.machine_id, self.dev_fallback = (
            (machine_id, False) if machine_id else get_machine_id()
        )
        # State below is only ever mutated under self._lock.
        self.status: str = "unknown"  # trial|active|expired|unknown
        self.expire_at: int = 0
        self._remaining_at_sync: int = 0
        self._anchor_monotonic: float = 0.0
        self._anchor_wall: float = 0.0
        self.server_time: int = 0
        self.offline: bool = False
        self.last_error: str = ""
        self._load_cache()

    # ─── Countdown (monotonic while running) ─────────────────

    def remaining_seconds(self) -> int | None:
        """Seconds left, or None when nothing was ever synced.

        Live state counts down on the monotonic clock; cached (grace)
        state counts down on wall time since the sync, capped at 24 h
        of staleness. Never goes below zero.
        """
        with self._lock:
            if self.status == "unknown":
                return None
            if self.offline:
                # Wall time can jump backwards; never let a jump invent time.
                elapsed = max(0.0, time.time() - self._anchor_wall)
                if elapsed > OFFLINE_GRACE_SECONDS:
                    return 0
                return max(0, self._remaining_at_sync - int(elapsed))
            # Monotonic only moves forward in production; clamp anyway so
            # a bogus anchor can never invent remaining time.
            elapsed = max(0.0, time.monotonic() - self._anchor_monotonic)
            return max(0, self._remaining_at_sync - int(elapsed))

    def is_active(self) -> bool:
        """True when the current state says there is time left."""
        remaining = self.remaining_seconds()
        return remaining is not None and remaining > 0

    def known_expired(self) -> bool:
        """True when a synced/cached state positively says expired."""
        remaining = self.remaining_seconds()
        return remaining is not None and remaining <= 0

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe state for the frontend (machine id is shortened)."""
        remaining = self.remaining_seconds()
        return {
            "machine_id": self.machine_id,
            "machine_id_short": self.machine_id[:12],
            "status": self.status if remaining is not None else "unknown",
            "remaining_seconds": remaining if remaining is not None else 0,
            "expire_at": self.expire_at,
            "offline": self.offline,
            "dev_fallback": self.dev_fallback,
            "error": self.last_error,
        }

    # ─── Cache (offline grace) ──────────────────────────────

    def _load_cache(self) -> None:
        if not self._cache_path:
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("machine_id") != self.machine_id:
            return
        try:
            synced_wall = float(data["synced_wall"])
            remaining = int(data["remaining_seconds"])
            expire_at = int(data["expire_at"])
        except (KeyError, TypeError, ValueError):
            return
        if time.time() - synced_wall > OFFLINE_GRACE_SECONDS:
            return
        self.status = str(data.get("status") or "expired")
        self.expire_at = expire_at
        self._remaining_at_sync = max(0, remaining)
        self._anchor_wall = synced_wall
        self._anchor_monotonic = time.monotonic()
        self.offline = True

    def _save_cache(self) -> None:
        if not self._cache_path:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "machine_id": self.machine_id,
                "status": self.status,
                "expire_at": self.expire_at,
                "remaining_seconds": self._remaining_at_sync,
                "synced_wall": self._anchor_wall,
                "server_time": self.server_time,
            }
            tmp = self._cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._cache_path)
        except OSError:
            pass

    # ─── Relay calls ────────────────────────────────────────

    def _relay_base(self) -> tuple[str, str]:
        url = get_relay_url(self._config).rstrip("/")
        token = get_relay_token(self._config) or ""
        return url, token

    def _post(self, path: str, body: dict, timeout: float) -> httpx.Response:
        url, token = self._relay_base()
        timestamp = int(time.time())
        signature = sign_request(token, self.machine_id, timestamp)
        payload = dict(body)
        payload.update(
            {"machine_id": self.machine_id, "timestamp": timestamp, "signature": signature}
        )
        headers = {
            "X-App-Token": token,
            "X-Auth-Sign": signature,
            "Content-Type": "application/json",
        }
        if self._transport is not None:
            with httpx.Client(transport=self._transport, trust_env=False) as client:
                return client.post(f"{url}{path}", json=payload, headers=headers, timeout=timeout)
        return httpx.post(f"{url}{path}", json=payload, headers=headers, timeout=timeout, trust_env=False)

    def _apply_info_payload(self, payload: dict) -> bool:
        """Verify a /api/auth/info payload and adopt it. False if untrusted."""
        _, token = self._relay_base()
        if not token:
            return False
        if not verify_response_signature(token, payload):
            return False
        try:
            server_time = int(payload["server_time"])
            expire_at = int(payload["expire_at"])
            remaining = int(payload["remaining_seconds"])
            status = str(payload["status"])
        except (KeyError, TypeError, ValueError):
            return False
        if abs(server_time - int(time.time())) > TIMESTAMP_SKEW_SECONDS:
            return False
        if status not in {"trial", "active", "expired"}:
            return False
        with self._lock:
            self.status = "expired" if remaining <= 0 else status
            self.expire_at = expire_at
            self._remaining_at_sync = max(0, remaining)
            self._anchor_monotonic = time.monotonic()
            self._anchor_wall = time.time()
            self.server_time = server_time
            self.offline = False
            self.last_error = ""
        self._save_cache()
        return True

    def sync(self, timeout: float = SYNC_TIMEOUT) -> bool:
        """Refresh the auth state from the relay. Never raises.

        Returns True on a verified fresh state. On any failure the
        previous state is kept (live countdown keeps running, or the
        offline grace cache keeps serving) and False is returned.
        """
        try:
            resp = self._post("/api/auth/info", {}, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            with self._lock:
                self.last_error = "连不上，再检查一下网络。"
            return False
        if not isinstance(payload, dict) or not self._apply_info_payload(payload):
            with self._lock:
                self.last_error = "这次没拿到有效的授权信息，稍后再试。"
            return False
        return True

    def activate(self, card_code: str, timeout: float = ACTIVATE_TIMEOUT) -> dict[str, Any]:
        """Activate a day/week card. Returns {"ok": True, ...} or {"ok": False, "error": ...}."""
        code = normalize_card_code(card_code)
        if not code:
            return {"ok": False, "error": "先把卡密填上。"}
        try:
            resp = self._post("/api/auth/activate", {"card_code": code}, timeout=timeout)
        except Exception:
            return {"ok": False, "error": "现在连不上，检查网络后重试。"}
        try:
            payload = resp.json()
        except Exception:
            payload = {}
        if resp.status_code == 200 and isinstance(payload, dict):
            try:
                expire_at = int(payload["expire_at"])
                remaining = int(payload["remaining_seconds"])
            except (KeyError, TypeError, ValueError):
                return {"ok": False, "error": "这次没拿到有效的授权信息，稍后再试。"}
            with self._lock:
                self.status = "active" if remaining > 0 else "expired"
                self.expire_at = expire_at
                self._remaining_at_sync = max(0, remaining)
                self._anchor_monotonic = time.monotonic()
                self._anchor_wall = time.time()
                self.offline = False
                self.last_error = ""
            self._save_cache()
            return {
                "ok": True,
                "expire_at": expire_at,
                "remaining_seconds": max(0, remaining),
                "added_seconds": int(payload.get("added_seconds") or 0),
                "card_type": str(payload.get("card_type") or ""),
            }
        error = payload.get("error") if isinstance(payload, dict) else None
        message = ACTIVATE_ERROR_MESSAGES.get(
            str(error), "这张卡用不了，请检查卡密或联系客服。"
        )
        return {"ok": False, "error": message}
