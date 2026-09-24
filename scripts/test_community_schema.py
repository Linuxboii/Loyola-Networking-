"""Contract for additive community tables and super-admin role semantics."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import AdminGrant, CommunityProfile, Follow, User  # noqa: E402
from app.services.community_controls import can_administer  # noqa: E402


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok   {label}")


def main() -> int:
    check("follow table prevents duplicate follows", "uq_follow_once" in {c.name for c in Follow.__table__.constraints})
    check("follow requests have an explicit state", hasattr(Follow, "status"))
    check("community profile carries the OG badge", hasattr(CommunityProfile, "is_og"))
    check("admin grants carry actions and expiry", hasattr(AdminGrant, "actions") and hasattr(AdminGrant, "expires_at"))

    super_admin = SimpleNamespace(roles=["super_admin"])
    delegated = SimpleNamespace(roles=["admin"])
    now = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
    check("super admin can perform every scoped action", can_administer(super_admin, set(), "manage_og", now))
    check(
        "delegated admin is limited to its assigned action",
        can_administer(delegated, {"approve_verification"}, "approve_verification", now)
        and not can_administer(delegated, {"approve_verification"}, "manage_moderators", now),
    )
    check("user exposes super-admin role", User.is_super_admin.fget(super_admin) is True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
