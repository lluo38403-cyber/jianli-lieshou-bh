"""Shared page-access budgets and persistent safety locks for platform workflows."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from bosshunter.db import (
    add_platform_access,
    count_platform_access_today,
    get_active_platform_safety_lock,
    set_platform_safety_lock,
)

class PlatformSafetyStop(RuntimeError):
    """Raised before another platform page is opened when a safety guard stops work."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class PlatformAccessGuard:
    conn: sqlite3.Connection
    config: dict
    stage: str
    platform: str = "boss"

    def ensure_unlocked(self) -> None:
        lock = get_active_platform_safety_lock(self.conn)
        if lock:
            raise PlatformSafetyStop("persistent_risk_lock")

    def check_available(self, action: str, *, daily_limit: int | None = None) -> None:
        """Check original access limits without consuming a page attempt."""
        self.ensure_unlocked()
        safety_cfg = self.config.get("safety", {})
        global_limit = _positive_int(safety_cfg.get("daily_platform_page_limit", 1100), 1100)
        if count_platform_access_today(self.conn, platform=self.platform) >= global_limit:
            raise PlatformSafetyStop("daily_platform_page_limit")
        if daily_limit is not None:
            stage_count = count_platform_access_today(
                self.conn,
                platform=self.platform,
                stage=self.stage,
                action=action,
            )
            if stage_count >= max(int(daily_limit), 1):
                raise PlatformSafetyStop(f"daily_{action}_limit")

    def reserve(self, action: str, *, daily_limit: int | None = None) -> None:
        """Reserve one page-open attempt before navigation."""
        self.check_available(action, daily_limit=daily_limit)
        add_platform_access(self.conn, self.stage, action, platform=self.platform)

    def lock(self, reason: str, *, minutes: int | None = None) -> None:
        safety_cfg = self.config.get("safety", {})
        lock_minutes = (
            _positive_int(minutes, 1)
            if minutes is not None
            else _positive_int(safety_cfg.get("risk_lock_minutes", 10), 10)
        )
        set_platform_safety_lock(self.conn, reason, minutes=lock_minutes)


@dataclass
class TransientPlatformAccessGuard:
    """Open a short-lived DB connection for workflows without a cycle-long connection."""

    config: dict
    stage: str
    db_factory: Callable[[], sqlite3.Connection]
    platform: str = "boss"

    def ensure_unlocked(self) -> None:
        conn = self.db_factory()
        try:
            PlatformAccessGuard(conn, self.config, self.stage, self.platform).ensure_unlocked()
        finally:
            conn.close()

    def reserve(self, action: str, *, daily_limit: int | None = None) -> None:
        conn = self.db_factory()
        try:
            PlatformAccessGuard(conn, self.config, self.stage, self.platform).reserve(
                action,
                daily_limit=daily_limit,
            )
        finally:
            conn.close()


def _positive_int(value: object, default: int) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default


def collection_guard_for(safety_conn: sqlite3.Connection | None, config: dict, platform: str) -> PlatformAccessGuard | None:
    """给一个平台的采集建守卫；没有真实数据库连接时返回 None（不限额）。

    采集器测试里常传 object() 占位（db 函数都被 patch 掉），那种连接
    根本跑不了计数——这时不限额、保持旧行为；编排器传的是 get_db 的
    真连接，守卫正常生效。
    """
    if not isinstance(safety_conn, sqlite3.Connection):
        return None
    return PlatformAccessGuard(safety_conn, config, "collection", platform)


def collection_access_limits(config: dict) -> tuple[int, int]:
    """采集页数限额（搜索页 / 详情页）：取 collection 配置，缺省 60 / 900。

    v36：以前只有 BOSS 采集读这两个值；现在四个平台共用同一对限额，
    各自按自己的平台名计数（见 PlatformAccessGuard 的 platform 参数）。
    """
    collection_cfg = config.get("collection", {}) if isinstance(config.get("collection"), dict) else {}
    return (
        _positive_int(collection_cfg.get("daily_search_page_limit", 60), 60),
        _positive_int(collection_cfg.get("daily_detail_page_limit", 900), 900),
    )


def quota_stop_message(platform_label: str, reason: str) -> str:
    """超限停采的提示文案：把原因码翻成用户能看懂的话。

    reason 码是给程序判断用的（orchestrator 只认 completed_with_shortage
    + 原因码），message 只给用户看，别把 daily_search_page_limit 这类码
    原样甩给用户。
    """
    if reason == "persistent_risk_lock":
        return f"{platform_label}采集暂时暂停：此前检测到风险提示，请稍后重试。"
    detail = {
        "daily_search_page_limit": "今天翻页已到上限",
        "daily_detail_page_limit": "今天打开岗位详情已到上限",
        "daily_platform_page_limit": "今天打开页面总数已到上限",
    }.get(reason, reason)
    return f"{platform_label}采集已达安全上限：{detail}，明天再继续。"
