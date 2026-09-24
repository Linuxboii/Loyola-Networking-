"""The Android release channel.

The app is not on Play Store — a campus network for verified students of one
college has no business being publicly listable — so the server is the update
channel. This module owns the on-disk shape of that channel:

    var/releases/
      android/
        releases.json                     the manifest, newest release first
        7/loyola-networking-1.2.0-7.apk   one directory per build number

`releases.json` is the only source of truth. It is written atomically (temp
file, then replace) so a client polling mid-publish either sees the old
manifest or the new one, never half of one.

Nothing here touches the database. A release is a file on disk, which means
publishing works before the first migration, survives a database restore, and
can be rolled back with `git`-less tools an ops person already knows: delete
the entry, the old build is still sitting there.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger("loyola.releases")

ANDROID = "android"
MANIFEST_NAME = "releases.json"

# An APK filename we are willing to serve. Keeps path traversal and stray files
# in the release directory out of the download route entirely.
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._-]{1,120}\.apk$")


@dataclass(frozen=True)
class Release:
    """One published build."""

    build: int
    version: str
    filename: str
    size: int
    sha256: str
    published_at: str
    notes: str = ""
    # A mandatory release refuses to let the app continue until it is installed.
    # Reserve it for a change older builds cannot survive — an auth format
    # change, a privacy fix — because it locks a student out of the campus
    # network until they are on wifi long enough to download 40 MB.
    mandatory: bool = False
    # Builds older than this cannot talk to the current API at all.
    min_supported_build: int = 1
    abi: str = "universal"

    @property
    def directory(self) -> Path:
        return android_root() / str(self.build)

    @property
    def path(self) -> Path:
        return self.directory / self.filename

    def download_path(self) -> str:
        """The API path a client fetches the APK from."""
        return f"/api/v1/updates/android/download/{self.build}/{self.filename}"

    def to_public(self) -> dict[str, Any]:
        """The shape the app parses. Keep it additive — older builds read it."""
        return {
            "build": self.build,
            "version": self.version,
            "notes": self.notes,
            "size": self.size,
            "sha256": self.sha256,
            "published_at": self.published_at,
            "mandatory": self.mandatory,
            "min_supported_build": self.min_supported_build,
            "abi": self.abi,
            "download_url": self.download_path(),
            "filename": self.filename,
        }


def releases_root() -> Path:
    return Path(settings.release_root)


def android_root() -> Path:
    return releases_root() / ANDROID


def manifest_path() -> Path:
    return android_root() / MANIFEST_NAME


def _coerce(raw: dict[str, Any]) -> Release | None:
    try:
        return Release(
            build=int(raw["build"]),
            version=str(raw["version"]),
            filename=str(raw["filename"]),
            size=int(raw.get("size", 0)),
            sha256=str(raw.get("sha256", "")),
            published_at=str(raw.get("published_at", "")),
            notes=str(raw.get("notes", "")),
            mandatory=bool(raw.get("mandatory", False)),
            min_supported_build=int(raw.get("min_supported_build", 1)),
            abi=str(raw.get("abi", "universal")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("skipping malformed release entry: %s", exc)
        return None


def load_releases() -> list[Release]:
    """Every published build, newest first. Missing manifest means none yet."""
    path = manifest_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("release manifest unreadable (%s); treating as empty", exc)
        return []
    entries = raw.get("releases", []) if isinstance(raw, dict) else raw
    releases = [r for r in (_coerce(e) for e in entries if isinstance(e, dict)) if r]
    return sorted(releases, key=lambda r: r.build, reverse=True)


def latest_release() -> Release | None:
    releases = load_releases()
    return releases[0] if releases else None


def find_release(build: int) -> Release | None:
    for release in load_releases():
        if release.build == build:
            return release
    return None


def minimum_supported_build() -> int:
    """The highest floor any published release declares.

    Taking the maximum rather than the newest release's value means a floor,
    once raised, cannot be quietly lowered by forgetting the flag on the next
    publish — which would hand a broken build back to a student who is on it.
    """
    releases = load_releases()
    if not releases:
        return 1
    return max(r.min_supported_build for r in releases)


def resolve_apk(build: int, filename: str) -> Path | None:
    """The file to serve for a download request, or None if it is not ours.

    Both halves are checked against the manifest rather than the filesystem, so
    an APK dropped into the release directory by hand is never served and a
    crafted filename never escapes it.
    """
    if not SAFE_FILENAME.match(filename):
        return None
    release = find_release(build)
    if release is None or release.filename != filename:
        return None
    path = release.path
    if not path.is_file():
        log.error("manifest lists build %s but %s is missing", build, path)
        return None
    return path


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(releases: list[Release]) -> Path:
    """Replace the manifest atomically. Used by scripts/publish_release.py."""
    root = android_root()
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "releases": [asdict(r) for r in sorted(releases, key=lambda r: r.build, reverse=True)],
    }
    body = json.dumps(payload, indent=2) + "\n"
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=root, prefix=".manifest-", suffix=".tmp", delete=False
    )
    try:
        with handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, manifest_path())
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return manifest_path()


def update_payload(current_build: int | None) -> dict[str, Any]:
    """What `/api/v1/updates/android/latest` answers.

    `current_build` is what the caller says it is running. It is advisory: the
    app decides what to do, and an attacker lying about it only lies to itself.
    """
    latest = latest_release()
    floor = minimum_supported_build()
    payload: dict[str, Any] = {
        "platform": ANDROID,
        "min_supported_build": floor,
        "latest": latest.to_public() if latest else None,
    }
    if latest:
        payload["latest"]["min_supported_build"] = floor
    if current_build is not None:
        payload["current_build"] = current_build
        payload["update_available"] = bool(latest and latest.build > current_build)
        # Unsupported wins over merely-outdated: the app must block, not nudge.
        payload["unsupported"] = current_build < floor
        payload["mandatory"] = bool(
            current_build < floor or (latest and latest.build > current_build and latest.mandatory)
        )
    return payload
