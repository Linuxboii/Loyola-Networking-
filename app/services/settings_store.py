"""Runtime-editable settings, cached in-process.

The OCR template lives here. That is the point: nobody has handed us a specimen
Loyola Academy card, so the roll-number pattern, the label aliases and the
issuer tokens must be correctable from the admin console the first time a real
card comes through — not in a redeploy.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting, utcnow

_CACHE: dict[str, tuple[float, Any]] = {}
_TTL = 30.0


async def get_setting(db: AsyncSession, key: str, default: Any = None) -> Any:
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _TTL:
        return hit[1]
    row = await db.get(AppSetting, key)
    value = row.value if row else default
    if value is None:
        value = default
    _CACHE[key] = (time.time(), value)
    return value


async def set_setting(db: AsyncSession, key: str, value: Any, actor_id: int | None = None) -> None:
    stmt = (
        pg_insert(AppSetting)
        .values(key=key, value=value, updated_at=utcnow(), updated_by=actor_id)
        .on_conflict_do_update(
            index_elements=[AppSetting.key],
            set_={"value": value, "updated_at": utcnow(), "updated_by": actor_id},
        )
    )
    await db.execute(stmt)
    _CACHE[key] = (time.time(), value)


def invalidate(key: str | None = None) -> None:
    if key is None:
        _CACHE.clear()
    else:
        _CACHE.pop(key, None)


async def ocr_template(db: AsyncSession) -> dict[str, Any]:
    from app.ocr import DEFAULT_TEMPLATE

    stored = await get_setting(db, "ocr_template", {}) or {}
    template = {**DEFAULT_TEMPLATE, **stored}
    if "labels" in stored:
        template["labels"] = {**DEFAULT_TEMPLATE["labels"], **stored["labels"]}
    return template
