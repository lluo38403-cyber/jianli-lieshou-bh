"""Silent machine code (HWID) for the time-based card auth.

The client collects a few stable hardware features (motherboard serial,
CPU id, disk serial), joins them and hashes with SHA-256. Only the
hash ever leaves this machine: raw hardware values are never returned
to callers, never logged and never sent anywhere.

Windows is the real target: ``wmic`` first, PowerShell
``Get-CimInstance`` as fallback (newer Windows builds may drop wmic).
On any other platform the collector falls back to /etc/machine-id or
the network adapter address and marks the result as a dev fallback —
it exists so the panel can be developed and tested on Linux, and must
never be mistaken for a production fingerprint.

The collector functions are injectable (``collectors={...}``) so tests
can pin exact inputs without touching real hardware.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import uuid
from typing import Callable

Collector = Callable[[], str]


def _run_capture(argv: list[str], timeout: float = 10.0) -> str:
    """Run a command and return its stdout, or "" on any failure."""
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return ""
    return proc.stdout or ""


def _pick_value(output: str) -> str:
    """Take the first non-empty, non-header line of a wmic-style table."""
    for line in output.splitlines():
        text = line.strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in {"serialnumber", "processorid", "serial number", "processor id"}:
            continue
        if lowered.startswith(("serialnumber", "processorid")):
            continue
        return text
    return ""


def _wmic_value(query: str, field: str) -> str:
    return _pick_value(_run_capture(["wmic", query, "get", field]))


def _powershell_value(cim_class: str, prop: str) -> str:
    script = (
        f"Get-CimInstance {cim_class} | "
        f"Select-Object -First 1 -ExpandProperty {prop} | Out-String"
    )
    return _run_capture(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    ).strip()


def _wmic_collectors() -> dict[str, Collector]:
    return {
        "baseboard": lambda: _wmic_value("baseboard", "serialnumber"),
        "cpu": lambda: _wmic_value("cpu", "processorid"),
        "disk": lambda: _wmic_value("diskdrive", "serialnumber"),
    }


def _powershell_collectors() -> dict[str, Collector]:
    return {
        "baseboard": lambda: _powershell_value("Win32_BaseBoard", "SerialNumber"),
        "cpu": lambda: _powershell_value("Win32_Processor", "ProcessorId"),
        "disk": lambda: _powershell_value("Win32_DiskDrive", "SerialNumber"),
    }


def _dev_fallback_collectors() -> dict[str, Collector]:
    def _machine_id_file() -> str:
        try:
            with open("/etc/machine-id", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    def _mac_address() -> str:
        node = uuid.getnode()
        return f"{node:012x}" if node else ""

    return {"baseboard": _machine_id_file, "cpu": _machine_id_file, "disk": _mac_address}


def _normalize(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def collect_hardware_values(
    collectors: dict[str, Collector] | None = None,
) -> dict[str, str]:
    """Collect raw hardware values (never leaves this module's callers).

    On Windows tries wmic first, then PowerShell. Anywhere else uses
    the dev fallback. Returns the values keyed by feature name plus a
    ``dev_fallback`` flag ("1" when the dev fallback was used).
    """
    if collectors is not None:
        chosen = collectors
        dev_fallback = False
    elif os.name == "nt":
        chosen = _wmic_collectors()
        dev_fallback = False
    else:
        chosen = _dev_fallback_collectors()
        dev_fallback = True

    values: dict[str, str] = {}
    for key in ("baseboard", "cpu", "disk"):
        fn = chosen.get(key)
        try:
            values[key] = _normalize(fn() if fn else "")
        except Exception:
            values[key] = ""
    # On Windows, if wmic came back empty across the board, retry once
    # with PowerShell before giving up.
    if os.name == "nt" and not any(values.values()):
        for key, fn in _powershell_collectors().items():
            try:
                values[key] = _normalize(fn())
            except Exception:
                values[key] = ""
    values["dev_fallback"] = "1" if dev_fallback else "0"
    return values


def machine_id(values: dict[str, str] | None = None) -> str:
    """SHA-256 of the joined hardware values (64 lowercase hex chars).

    Same input always yields the same id; different hardware yields a
    different id. The raw values are never part of the output.
    """
    collected = values if values is not None else collect_hardware_values()
    parts = [_normalize(collected.get(key, "")) for key in ("baseboard", "cpu", "disk")]
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def get_machine_id() -> tuple[str, bool]:
    """Return (machine_id, is_dev_fallback) for this machine."""
    collected = collect_hardware_values()
    return machine_id(collected), collected.get("dev_fallback") == "1"
