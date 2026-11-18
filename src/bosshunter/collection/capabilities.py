"""Server-side capability boundaries for platform-specific workflows."""

PLATFORM_CAPABILITIES: dict[str, frozenset[str]] = {
    "boss": frozenset({"collect", "score", "greet", "deliver", "monitor"}),
    # 四个平台都开放投递与监听。投递适配器见 executor/platform_delivery.py。
    "zhilian": frozenset({"collect", "score", "greet", "deliver", "monitor"}),
    "51job": frozenset({"collect", "score", "greet", "deliver", "monitor"}),
    "liepin": frozenset({"collect", "score", "greet", "deliver", "monitor"}),
}


def platform_supports(platform: str, capability: str) -> bool:
    return capability in PLATFORM_CAPABILITIES.get(str(platform), frozenset())
