"""Behavioural tests for the Android release channel.

The update path is the one piece of this system that can brick every phone on
campus at once: a bad manifest, a filename that escapes the release directory,
or a minimum-build floor that lowers itself, and a student is either stuck on a
retired build or being offered somebody else's file. So it gets tested against a
real directory rather than mocks.

Everything runs inside a temporary release root — no database, no server, and
nothing written outside the temp directory.

    python scripts/test_updates.py
"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services import releases as rel  # noqa: E402

FAILURES: list[str] = []
COUNT = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global COUNT
    COUNT += 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}{'' if ok else '  -> ' + detail}")
    if not ok:
        FAILURES.append(label)


def make_release(build: int, *, body: bytes, **kwargs) -> rel.Release:
    """Write an APK and return the manifest entry describing it."""
    version = kwargs.pop("version", f"1.{build}.0")
    filename = kwargs.pop("filename", f"loyola-networking-{version}-{build}.apk")
    directory = rel.android_root() / str(build)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_bytes(body)
    return rel.Release(
        build=build,
        version=version,
        filename=filename,
        size=len(body),
        sha256=rel.sha256_of(path),
        published_at=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        **kwargs,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loyola-releases-") as tmp:
        settings.release_root = Path(tmp)

        print("\nan empty channel")
        check(rel.load_releases() == [], "no releases before anything is published")
        check(rel.latest_release() is None, "latest is None")
        check(rel.minimum_supported_build() == 1, "the floor defaults to 1")
        payload = rel.update_payload(1)
        check(payload["latest"] is None, "payload has no latest build")
        check(payload["update_available"] is False, "nothing to update to")
        check(payload["mandatory"] is False, "nothing is mandatory")

        print("\npublishing builds")
        first = make_release(1, body=b"apk-one", notes="First build.")
        second = make_release(2, body=b"apk-two-longer", notes="Second build.")
        rel.write_manifest([first, second])

        loaded = rel.load_releases()
        check([r.build for r in loaded] == [2, 1], "manifest reads back newest first")
        check(rel.latest_release().build == 2, "latest is the highest build")
        check(rel.find_release(1).version == "1.1.0", "an older build is still findable")
        check(rel.find_release(99) is None, "an unpublished build is not found")

        print("\nwhat a client is told")
        behind = rel.update_payload(1)
        check(behind["update_available"] is True, "a build behind is told to update")
        check(behind["latest"]["build"] == 2, "it is pointed at the newest build")
        check(
            behind["latest"]["download_url"] == f"/api/v1/updates/android/download/2/{second.filename}",
            "the download URL addresses the build and its filename",
        )
        check(behind["latest"]["sha256"] == second.sha256, "the hash travels with it")
        current = rel.update_payload(2)
        check(current["update_available"] is False, "the newest build is left alone")
        check(rel.update_payload(None).get("update_available") is None,
              "a client that does not say its build is not told it is behind")

        print("\nmandatory and retired builds")
        third = make_release(3, body=b"apk-three", notes="Security fix.", mandatory=True,
                             min_supported_build=3)
        rel.write_manifest([first, second, third])
        blocked = rel.update_payload(2)
        check(blocked["mandatory"] is True, "a mandatory release blocks an older build")
        check(blocked["unsupported"] is True, "build 2 is below the floor, so it is unsupported")
        check(rel.minimum_supported_build() == 3, "the floor rose with the release")

        # The floor must not fall when a later publish forgets the flag: a
        # student on the retired build would silently be let back in.
        fourth = make_release(4, body=b"apk-four", notes="Routine.")
        rel.write_manifest([first, second, third, fourth])
        check(rel.minimum_supported_build() == 3, "a later release cannot lower the floor")
        check(rel.update_payload(3)["unsupported"] is False, "build 3 is still supported")
        check(rel.update_payload(3)["mandatory"] is False,
              "a non-mandatory release above the floor only nudges")

        print("\nserving the file")
        resolved = rel.resolve_apk(4, fourth.filename)
        check(resolved is not None and resolved.read_bytes() == b"apk-four", "the published APK resolves")
        check(rel.resolve_apk(4, "not-the-one.apk") is None, "a filename the manifest does not list is refused")
        check(rel.resolve_apk(4, "../../../../etc/passwd") is None, "traversal is refused")
        check(rel.resolve_apk(4, "loyola.apk.exe") is None, "a non-APK name is refused")
        check(rel.resolve_apk(99, fourth.filename) is None, "an unpublished build is refused")

        # A file listed in the manifest but missing on disk must not 500 a
        # download or, worse, serve something else.
        stray = rel.android_root() / "4" / "planted.apk"
        stray.write_bytes(b"not ours")
        check(rel.resolve_apk(4, "planted.apk") is None, "a file dropped in by hand is not served")
        resolved.unlink()
        check(rel.resolve_apk(4, fourth.filename) is None, "a manifest entry with no file is refused")

        print("\nbad input")
        rel.manifest_path().write_text("{ not json", encoding="utf-8")
        check(rel.load_releases() == [], "an unreadable manifest reads as empty rather than crashing")
        rel.manifest_path().write_text('{"releases": [{"build": "x"}, {"version": "1.0"}]}', encoding="utf-8")
        check(rel.load_releases() == [], "malformed entries are skipped")

    print(f"\n{COUNT - len(FAILURES)}/{COUNT} update-channel checks passed")
    if FAILURES:
        print("\nFailures:")
        for failure in FAILURES:
            print("  -", failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
