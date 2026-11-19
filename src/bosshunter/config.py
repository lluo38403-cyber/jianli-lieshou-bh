"""Configuration loader for BossHunter."""

import hashlib
import hmac
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

import yaml


# 管理员密码只存哈希（PBKDF2-HMAC-SHA256），明文密码永不落盘：
# 打包方在 config.example.yaml 里写 admin.password，启动时迁移成哈希。
ADMIN_PASSWORD_MIN_LENGTH = 6
_ADMIN_PASSWORD_HASH_ITERATIONS = 200_000


def hash_admin_password(password: str) -> str:
    """Hash an admin password for storage (never store the plaintext)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ADMIN_PASSWORD_HASH_ITERATIONS)
    return f"pbkdf2_sha256${_ADMIN_PASSWORD_HASH_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_admin_password(password: str, stored: str | None) -> bool:
    """Check a password against a stored hash with constant-time comparison."""
    if not password or not stored:
        return False
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def get_admin_password_hash(config: dict) -> str | None:
    """Return the stored admin password hash, if one is configured."""
    admin_cfg = config.get("admin") if isinstance(config, dict) else None
    if not isinstance(admin_cfg, dict):
        return None
    value = admin_cfg.get("password_hash")
    return str(value) if value else None


# BOSS直聘城市编码映射
CITY_CODES: dict[str, str] = {
    "北京": "101010100",
    "上海": "101020100",
    "深圳": "101280600",
    "广州": "101280100",
    "杭州": "101210100",
    "成都": "101270100",
    "武汉": "101200100",
    "南京": "101190100",
    "西安": "101110100",
    "苏州": "101190400",
    "天津": "101030100",
    "重庆": "101040100",
    "郑州": "101180100",
    "长沙": "101250100",
    "东莞": "101281600",
    "佛山": "101280800",
    "合肥": "101220100",
    "厦门": "101230200",
    "青岛": "101120200",
    "大连": "101070200",
}


SUPPORTED_AI_PROVIDERS = {"anthropic", "openai_compatible", "local", "relay"}
SUPPORTED_AI_SERVICES = {"anthropic", "deepseek", "doubao", "zhipu", "custom", "local", "relay"}
AI_SERVICE_PRESETS: dict[str, dict[str, str]] = {
    "anthropic": {
        "provider": "anthropic",
        "label": "Claude / Anthropic",
        "base_url": "",
        "key_env": "ANTHROPIC_API_KEY",
    },
    "deepseek": {
        "provider": "openai_compatible",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "doubao": {
        "provider": "openai_compatible",
        "label": "豆包 / 火山方舟",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "key_env": "ARK_API_KEY",
    },
    "zhipu": {
        "provider": "openai_compatible",
        "label": "智谱免费 (GLM-4.7-Flash)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "key_env": "ZHIPU_API_KEY",
    },
    "custom": {
        "provider": "openai_compatible",
        "label": "其他 OpenAI 兼容接口",
        "base_url": "",
        "key_env": "OPENAI_API_KEY",
    },
    "local": {
        "provider": "local",
        "label": "本地规则（不用 AI）",
        "base_url": "",
        "key_env": "",
    },
    "relay": {
        # 云端中转：Key 只在服务端，exe 不带 Key，只调中转 /api/ai/chat。
        # 直连服务商的代码保留，切回直连时改配置即可。
        "provider": "relay",
        "label": "云端中转",
        "base_url": "",
        "key_env": "",
    },
}

AI_CREDENTIAL_FIELDS = ("api_key", "auth_token")


DEFAULTS: dict[str, Any] = {
    "profile": {
        "resume_path": "./resume.md",
        "resume_output_dir": "./data/resumes",
        "target_cities": ["北京"],
        "education": "",
        "recruitment_type": "",
        "greeting_preference": "",
        "ai_greeting_enabled": False,
        "fixed_greeting": "您好，我对贵公司的这个岗位很感兴趣，简历已附上，期待与您进一步沟通，谢谢！",
        "job_types": ["实习", "全职", "兼职"],
        "posted_days": 7,
        "salary_min": 0,
        "salary_max": 0,
        "salary_ceil_ratio": 1.5,
        "filter_unparsed_salary": True,
        "allow_internship": False,
        "deal_breakers": [],
        "jd_deal_breakers": [],
        "blocked_companies": [],
    },
    "search": {
        "keywords": [],
        "cities": [],  # Empty = fallback to profile.target_cities
        "city_codes": {},
        "max_pages": 3,
        "sort": "default",
        "filters": {},
    },
    "collection": {
        "default_order": ["boss", "zhilian", "51job", "liepin"],
        "auto_score_default": False,
        "daily_search_page_limit": 60,
        "daily_detail_page_limit": 900,
        "max_consecutive_page_failures": 3,
        "risk_pause_min_minutes": 5,
        "risk_pause_max_minutes": 10,
        "collection_delay_multiplier": 1.5,
        "delivery_cooldown_min_minutes": 5,
        "delivery_cooldown_max_minutes": 15,
    },
    "platforms": {
        "boss": {
            "enabled": True,
            "search": {
                "keywords": [],
                "cities": [],
                "city_codes": {},
                "max_pages": 3,
                "sort": "default",
                "filters": {},
                "city_yield_policy": {"enabled": True, "warmup_minutes": 10, "window_minutes": 5, "min_new_jobs": 5},
            },
        },
        "zhilian": {
            "enabled": True,
            "search": {
                "keywords": [],
                "cities": [],
                "city_codes": {},
                "max_pages": 3,
                "sort": "default",
            },
        },
        "51job": {
            "enabled": True,
            "search": {
                "keywords": [],
                "cities": ["上海"],
                "city_codes": {"上海": "020000"},
                "max_pages": 1,
                "sort": "default",
            },
        },
        "liepin": {
            "enabled": True,
            "search": {
                "keywords": [],
                "cities": [],
                "city_codes": {},
                "max_pages": 1,
                "sort": "default",
            },
        },
    },
    "scoring": {
        "threshold": 60,
        "max_candidates": 20,
    },
    "throttle": {
        "daily_limit": 120,
        "interval_min": 60,
        "interval_max": 180,
        "browse_before_greet": True,
        "browse_duration_min": 15,
        "browse_duration_max": 30,
        "silent_mode": False,
    },
    "ai": {
        "provider": "anthropic",
        "service": "anthropic",
        "model": "claude-sonnet-4-6",
        "thinking": "auto",
        "thinking_budget": 2048,
        "timeout_seconds": 180,
        "scoring_max_tokens": 8192,
        "scoring_max_attempts": 2,
        "scoring_concurrency": 1,
        "scoring_second_review": False,
        "greeting_max_tokens": 8192,
        "greeting_review_max_tokens": 4096,
        "greeting_max_attempts": 2,
        "greeting_review_threshold": 7.0,
        "greeting_style_suggestions": False,
        "resume_tailor_enabled": False,
    },
    "auth": {
        # 鉴权展示配置（只有展示用的值，没有任何密钥）：
        # buy_url 发卡网购买链接、wechat_id 客服微信号、wechat_qr_path
        # 客服二维码图地址。改这一处，全端跟着换，不用改业务代码。
        # buy_url 故意留空：空着就不显示购买按钮，有链接再填。
        # wechat_id / 二维码图仍要 lucky 确认/替换。
        "buy_url": "",
        "wechat_id": "jmlq2004",
        "wechat_qr_path": "/wechat-qr.png",
    },
    "monitor": {
        "interval": 30,  # 分钟
        "initial_cooldown_minutes": 10,
        "chat_url": "https://www.zhipin.com/web/geek/chat",
        "max_conversations_per_cycle": 5,
        "max_consecutive_page_failures": 3,
        "max_resume_sends_per_cycle": 5,
        "auto_reply_hr_questions": False,
    },
    "follow_up": {
        "enabled": False,
        "interval_hours": 48,
        "skip_weekends": True,
    },
    "dedup": {
        "history_file": "./data/history.jsonl",
    },
    "safety": {
        "daily_platform_page_limit": 1100,
        "risk_lock_minutes": 10,
    },
    "browser": {
        "runtime": "builtin",
        "proxy_host": "127.0.0.1",
        "proxy_port": 3456,
        "chrome_ports": [9222, 9229, 9333],
        "auto_start_proxy": True,
        "enable_port_guard": True,
        "site_patterns": True,
    },
}


def credentials_path_for(config_path: Path | None = None) -> Path:
    """Return the local credentials file paired with a config file."""
    config_path = Path(config_path or "config.yaml")
    stem = config_path.stem.lstrip(".") or "config"
    return config_path.with_name(f".{stem}.credentials.yaml")


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load public settings plus local credentials, falling back to defaults."""
    cfg = _deep_copy_dict(DEFAULTS)
    config_path = Path(config_path or "config.yaml")

    # Load the private file first so a not-yet-migrated config.yaml remains the
    # source of truth until startup moves its legacy credential fields.
    credentials = _load_yaml_mapping(credentials_path_for(config_path))
    if credentials:
        _deep_merge(cfg, credentials)

    user_cfg = _load_yaml_mapping(config_path)
    if user_cfg:
        _deep_merge(cfg, user_cfg)
    _normalize_config_sections(cfg)
    _validate_ai_provider(cfg)
    validate_config_send_windows(cfg)
    return cfg


def save_config(config: dict[str, Any], config_path: Path | None = None) -> None:
    """Persist settings without writing AI credentials to config.yaml."""
    validate_config_send_windows(config)
    config_path = Path(config_path or "config.yaml")
    public_config = _deep_copy_dict(config)
    _normalize_internship_salary_filter(public_config)
    ai_cfg = public_config.get("ai")
    credentials: dict[str, Any] = {}
    if isinstance(ai_cfg, dict):
        for field in AI_CREDENTIAL_FIELDS:
            value = ai_cfg.pop(field, None)
            if value is not None and str(value).strip():
                credentials[field] = value
        # 分服务商凭证（ai.services.<服务>.api_key / auth_token）同样只进私密文件。
        services = ai_cfg.get("services")
        if isinstance(services, dict):
            service_creds: dict[str, Any] = {}
            for svc, entry in list(services.items()):
                if not isinstance(entry, dict):
                    continue
                kept: dict[str, Any] = {}
                for field in AI_CREDENTIAL_FIELDS:
                    value = entry.pop(field, None)
                    if value is not None and str(value).strip():
                        kept[field] = value
                if kept:
                    service_creds[svc] = kept
                if not entry:
                    services.pop(svc)
            if service_creds:
                credentials["services"] = service_creds
            if not services:
                ai_cfg.pop("services", None)

    # 管理员密码哈希只进私密文件；明文密码当场哈希，不落盘。
    admin_creds: dict[str, Any] = {}
    admin_cfg = public_config.get("admin")
    if isinstance(admin_cfg, dict):
        password_hash = admin_cfg.pop("password_hash", None)
        if password_hash is not None and str(password_hash).strip():
            admin_creds["password_hash"] = password_hash
        plain_password = admin_cfg.pop("password", None)
        if plain_password is not None and str(plain_password).strip():
            admin_creds["password_hash"] = hash_admin_password(str(plain_password))
        if not admin_cfg:
            public_config.pop("admin", None)

    private_path = credentials_path_for(config_path)
    private_data: dict[str, Any] = {}
    if credentials:
        private_data["ai"] = credentials
    if admin_creds:
        private_data["admin"] = admin_creds
    else:
        # 管理员密码哈希不在本次保存里：保留私密文件里已有的，
        # 用户端保存配置时不能把它清掉。
        existing_private = _load_yaml_mapping(private_path)
        existing_admin = existing_private.get("admin")
        if isinstance(existing_admin, dict) and existing_admin.get("password_hash"):
            private_data["admin"] = {"password_hash": existing_admin["password_hash"]}
    if private_data:
        _write_yaml_atomic(private_path, private_data)

    _write_yaml_atomic(config_path, public_config)

    if not private_data:
        try:
            private_path.unlink()
        except FileNotFoundError:
            pass


def validate_config_send_windows(config: dict[str, Any]) -> None:
    """Reject invalid windows before persisting settings or starting a task.

    投递时间窗已取消：配置里不再有 send_windows；只有旧配置里还留着
    该字段时才校验它，新配置直接通过。
    """
    from bosshunter.throttle import validate_send_windows

    throttle = config.get("throttle", {})
    if not isinstance(throttle, dict):
        raise ValueError("throttle 必须是配置对象")  # noqa: TRY004 - configuration validation reports ValueError
    windows = throttle.get("send_windows")
    if windows is None:
        return
    validate_send_windows(windows)


def migrate_legacy_credentials(config_path: Path | None = None) -> bool:
    """Move credentials out of an existing config.yaml on the next startup.

    包括 AI 凭证（legacy 字段与分服务商字段）和管理员密码：
    ``admin.password`` 明文当场哈希进私密文件，原文从 config.yaml 删除。
    """
    config_path = Path(config_path or "config.yaml")
    public_config = _load_yaml_mapping(config_path)
    _normalize_internship_salary_filter(public_config)
    moved = False

    ai_cfg = public_config.get("ai")
    if isinstance(ai_cfg, dict):
        legacy_values = {field: ai_cfg.pop(field) for field in AI_CREDENTIAL_FIELDS if field in ai_cfg}
        # 分服务商凭证（ai.services.<服务>.api_key / auth_token）同样挪进私密文件。
        services = ai_cfg.get("services")
        service_values: dict[str, dict[str, Any]] = {}
        if isinstance(services, dict):
            for svc, entry in list(services.items()):
                if not isinstance(entry, dict):
                    continue
                kept = {field: entry.pop(field) for field in AI_CREDENTIAL_FIELDS if field in entry}
                if kept:
                    service_values[svc] = kept
                if not entry:
                    services.pop(svc)
            if not services:
                ai_cfg.pop("services", None)
        if legacy_values or service_values:
            moved = True
            private_path = credentials_path_for(config_path)
            private_config = _load_yaml_mapping(private_path)
            private_ai = private_config.setdefault("ai", {})
            if not isinstance(private_ai, dict):
                private_ai = {}
                private_config["ai"] = private_ai
            for field, value in legacy_values.items():
                if value is not None and str(value).strip():
                    private_ai[field] = value
                else:
                    private_ai.pop(field, None)
            if service_values:
                private_services = private_ai.setdefault("services", {})
                if not isinstance(private_services, dict):
                    private_services = {}
                    private_ai["services"] = private_services
                for svc, kept in service_values.items():
                    merged = dict(private_services.get(svc, {})) if isinstance(private_services.get(svc), dict) else {}
                    for field, value in kept.items():
                        if value is not None and str(value).strip():
                            merged[field] = value
                        else:
                            merged.pop(field, None)
                    if merged:
                        private_services[svc] = merged
            _write_yaml_atomic(private_path, private_config)

    # 管理员密码：明文只应出现在打包方写的配置文件里，启动时哈希进私密文件。
    admin_cfg = public_config.get("admin")
    if isinstance(admin_cfg, dict):
        plain_password = admin_cfg.pop("password", None)
        if plain_password is not None and str(plain_password).strip():
            moved = True
            private_path = credentials_path_for(config_path)
            private_config = _load_yaml_mapping(private_path)
            private_admin = private_config.setdefault("admin", {})
            if not isinstance(private_admin, dict):
                private_admin = {}
                private_config["admin"] = private_admin
            private_admin["password_hash"] = hash_admin_password(str(plain_password))
            _write_yaml_atomic(private_path, private_config)
        if not admin_cfg:
            public_config.pop("admin", None)

    if not moved:
        return False

    _write_yaml_atomic(config_path, public_config)
    return True


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        content = yaml.safe_load(f) or {}
    return content if isinstance(content, dict) else {}


def _write_yaml_atomic(path: Path, data: dict[str, Any]) -> None:
    """Atomically write a local YAML file with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            yaml.safe_dump(data, temporary, allow_unicode=True, default_flow_style=False, sort_keys=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _normalize_internship_salary_filter(config: dict[str, Any]) -> None:
    """Daily internship pay must not be rejected as an unparsed monthly salary."""
    profile = config.get("profile")
    if isinstance(profile, dict) and profile.get("allow_internship") is True:
        profile["filter_unparsed_salary"] = False


def _normalize_config_sections(config: dict[str, Any]) -> dict[str, Any]:
    """Replace malformed sections and discard retired collection-count settings."""
    for section, defaults in DEFAULTS.items():
        if isinstance(defaults, dict) and not isinstance(config.get(section), dict):
            config[section] = _deep_copy_dict(defaults)

    _normalize_internship_salary_filter(config)
    return remove_retired_collection_settings(config)


def remove_retired_collection_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Remove collection-count settings that are no longer supported."""

    # These settings existed briefly, but a result-count limit is not a page-access
    # safety control. Ignore stale values so old config files cannot re-enable it or
    # make the removed fields reappear in the Web UI/API.
    collection = config.get("collection", {})
    collection.pop("daily_new_jobs_limit", None)
    collection.pop("default_target_count", None)
    if "delivery_cooldown_min_minutes" in collection or "delivery_cooldown_max_minutes" in collection:
        collection.pop("delivery_cooldown_minutes", None)
    search = config.get("search", {})
    search.pop("target_count", None)
    platforms = config.get("platforms", {})
    for platform_config in platforms.values():
        if isinstance(platform_config, dict):
            platform_search = platform_config.get("search", {})
            if isinstance(platform_search, dict):
                platform_search.pop("target_count", None)
    return config


def _validate_ai_provider(config: dict[str, Any]) -> None:
    """Fail fast when the configured AI provider is not supported."""
    ai_cfg = config.get("ai", {})
    provider = ai_cfg.get("provider", "anthropic")
    if provider not in SUPPORTED_AI_PROVIDERS:
        raise ValueError("当前版本支持 Claude、DeepSeek、豆包、智谱免费、自定义 OpenAI 兼容接口、云端中转或本地规则。")
    service = ai_cfg.get("service", "anthropic")
    if provider == "openai_compatible" and service == "anthropic":
        # Legacy configs only had `provider`; preserve them as custom OpenAI-compatible.
        ai_cfg["service"] = "custom"
        service = "custom"
    if service not in SUPPORTED_AI_SERVICES:
        raise ValueError("当前版本支持 Claude、DeepSeek、豆包、智谱免费、自定义 OpenAI 兼容接口、云端中转或本地规则。")
    expected_provider = AI_SERVICE_PRESETS[service]["provider"]
    if provider != expected_provider:
        ai_cfg["provider"] = expected_provider


def _deep_copy_dict(d: dict) -> dict:
    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = _deep_copy_dict(v)
        elif isinstance(v, list):
            result[k] = v[:]
        else:
            result[k] = v
    return result


def _deep_merge(base: dict, override: dict) -> None:
    """Recursively merge override into base."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
