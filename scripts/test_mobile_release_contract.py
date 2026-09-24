"""Static release contract for the Android connection and community entry points."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def need(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok   {label}")


def main() -> int:
    config = (ROOT / "mobile/lib/core/config.dart").read_text(encoding="utf-8")
    auth = (ROOT / "mobile/lib/screens/auth_screen.dart").read_text(encoding="utf-8")
    settings = (ROOT / "mobile/lib/screens/settings_screen.dart").read_text(encoding="utf-8")
    groups = (ROOT / "mobile/lib/screens/groups_screen.dart").read_text(encoding="utf-8")
    update_prompt = (ROOT / "mobile/lib/widgets/update_prompt.dart").read_text(encoding="utf-8")
    main = (ROOT / "mobile/lib/main.dart").read_text(encoding="utf-8")
    profile = (ROOT / "mobile/lib/screens/profile_screen.dart").read_text(encoding="utf-8")
    admin = (ROOT / "mobile/lib/screens/admin_screen.dart").read_text(encoding="utf-8")
    deps = (ROOT / "app/deps.py").read_text(encoding="utf-8")
    api = (ROOT / "mobile/lib/core/api_client.dart").read_text(encoding="utf-8")
    image_cache = (ROOT / "mobile/lib/core/image_cache.dart").read_text(encoding="utf-8")
    post_card = (ROOT / "mobile/lib/widgets/post_card.dart").read_text(encoding="utf-8")
    common = (ROOT / "mobile/lib/widgets/common.dart").read_text(encoding="utf-8")
    session = (ROOT / "mobile/lib/core/session.dart").read_text(encoding="utf-8")
    pubspec = (ROOT / "mobile/pubspec.yaml").read_text(encoding="utf-8")

    need("production HTTPS host is the app default", "defaultValue: 'https://loyola.avlokai.com'" in config)
    need("legacy local overrides are discarded", "_isLegacyLocal" in config)
    need("sign-in does not expose a server editor", "_editServer" not in auth and "Server address" not in auth)
    need("server address is staff-only", "final canManageServer" in settings and "if (canManageServer)" in settings)
    need("verified members can create communities", '"create_group": "Newcomer"' in deps)
    need("community creation is clearly labelled", "Create community" in groups)
    need("timeouts do not expose a server address", "Connection timed out." in api and "server address in Settings" not in api)
    need(
        "returning from Android installer does not immediately reopen the update prompt",
        "didChangeAppLifecycleState" not in update_prompt,
    )
    need("theme choice is persisted and drives MaterialApp", "ThemeController" in main and "themeMode:" in main)
    need("settings can change username and theme", "Change username" in settings and "AMOLED dark" in settings)
    need("profile exposes follower management", "Followers" in profile and "Follow requests" in profile)
    need("in-app admin can review protected ID images", "Review ID images" in admin)
    need("post images use the bounded disk cache", "CampusCachedImage" in post_card)
    need("avatars use the bounded disk cache", "CampusCachedImageProvider" in common)
    need("cached media keeps protected request headers", "httpHeaders:" in image_cache)
    need("private media cache is cleared on logout", "CampusImageCache.clear()" in session)
    need("release includes the image cache dependency", "cached_network_image:" in pubspec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
