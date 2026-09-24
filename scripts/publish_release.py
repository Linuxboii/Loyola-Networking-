"""Publish an Android build so the app can update itself.

One command between "flutter build apk" and every phone on campus seeing the
update:

    python scripts/publish_release.py --apk mobile/build/app/outputs/flutter-apk/app-release.apk \
        --notes "Faster feed, fixes the crash when a post has no image."

The version name and build number are read out of mobile/pubspec.yaml (the
`version: 1.2.0+7` line) unless you pass --version / --build, so the thing
students see and the thing the build system produced cannot disagree.

What it does:

1. Copies the APK to var/releases/android/<build>/<name>.apk
2. Hashes it (SHA-256) and records its size — the app verifies both before it
   hands the file to the package installer, so a truncated download fails loudly
   instead of installing.
3. Rewrites var/releases/android/releases.json atomically.

Useful flags:

    --mandatory          older builds must update before they can be used
    --min-supported N    builds below N are refused outright (raise, never lower)
    --dry-run            print what would happen and touch nothing
    --list               show what is currently published, then exit

Rollback is `--remove <build>`: the entry leaves the manifest, the file stays on
disk, and the previous build becomes "latest" again on the next poll.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import releases as rel  # noqa: E402

PUBSPEC = ROOT / "mobile" / "pubspec.yaml"
VERSION_LINE = re.compile(r"^version:\s*([0-9]+(?:\.[0-9]+)*)\+([0-9]+)\s*$", re.MULTILINE)


def read_pubspec_version() -> tuple[str, int]:
    """The version name and build number the APK was built with.

    Only consulted when --version / --build are not given: a server checkout has
    the Python app and no Flutter project, so deploy.sh reads the pubspec on the
    machine that has one and passes both across.
    """
    if not PUBSPEC.exists():
        raise SystemExit(
            f"{PUBSPEC} is not here, so the version cannot be read — pass "
            "--version and --build explicitly (this is how deploy.sh publishes "
            "on a server, which has no copy of the Flutter project)."
        )
    match = VERSION_LINE.search(PUBSPEC.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"could not find a `version: x.y.z+n` line in {PUBSPEC}")
    return match.group(1), int(match.group(2))


def human(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB"


def show(releases: list[rel.Release]) -> None:
    if not releases:
        print("nothing published yet")
        return
    floor = max(r.min_supported_build for r in releases)
    print(f"minimum supported build: {floor}")
    for r in releases:
        flags = " [mandatory]" if r.mandatory else ""
        here = "" if r.path.is_file() else "  ** file missing **"
        print(f"  build {r.build:>4}  v{r.version:<10} {human(r.size):>9}  {r.published_at}{flags}{here}")


def do_remove(build: int, dry_run: bool) -> int:
    releases = rel.load_releases()
    remaining = [r for r in releases if r.build != build]
    if len(remaining) == len(releases):
        print(f"build {build} is not published")
        return 1
    if dry_run:
        print(f"would drop build {build} from the manifest (the APK stays on disk)")
        return 0
    rel.write_manifest(remaining)
    latest = remaining[0].build if remaining else None
    print(f"dropped build {build}; latest is now {latest if latest else 'nothing'}")
    return 0


def do_publish(args: argparse.Namespace) -> int:
    apk = Path(args.apk).resolve()
    if not apk.is_file():
        raise SystemExit(f"no APK at {apk}")

    # The pubspec is only read for whatever was not given on the command line,
    # so publishing works on a machine that has no Flutter project at all.
    version, build = args.version, args.build
    if version is None or build is None:
        pubspec_version, pubspec_build = read_pubspec_version()
        version = version or pubspec_version
        build = build or pubspec_build

    existing = rel.load_releases()
    clash = next((r for r in existing if r.build == build), None)
    if clash and not args.force:
        raise SystemExit(
            f"build {build} is already published (v{clash.version}). Bump the +n in "
            f"mobile/pubspec.yaml, or pass --force to replace it."
        )

    # A build number that goes backwards would make every phone think it is
    # ahead of the server and never update again.
    if existing and build < existing[0].build and not args.force:
        raise SystemExit(
            f"build {build} is older than the published {existing[0].build}; "
            "pass --force if you really mean to roll the channel back"
        )

    floor = args.min_supported or (max((r.min_supported_build for r in existing), default=1))
    if floor > build:
        raise SystemExit(f"--min-supported {floor} is above this build ({build}); it would reject itself")

    size = apk.stat().st_size
    digest = rel.sha256_of(apk)
    filename = args.filename or f"loyola-networking-{version}-{build}.apk"
    if not rel.SAFE_FILENAME.match(filename):
        raise SystemExit(f"{filename!r} is not a servable APK name")

    release = rel.Release(
        build=build,
        version=version,
        filename=filename,
        size=size,
        sha256=digest,
        published_at=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        notes=args.notes.strip(),
        mandatory=args.mandatory,
        min_supported_build=floor,
        abi=args.abi,
    )

    print(f"apk        {apk}")
    print(f"version    {version} (build {build}){' [mandatory]' if args.mandatory else ''}")
    print(f"size       {human(size)}")
    print(f"sha256     {digest}")
    print(f"target     {release.path}")
    print(f"min build  {floor}")
    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    release.directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(apk, release.path)

    kept = [r for r in existing if r.build != build]
    rel.write_manifest([release, *kept])

    print(f"\npublished. manifest: {rel.manifest_path()}")
    print(f"clients poll: GET /api/v1/updates/android/latest?build=<their build>")
    print(f"apk served at: {release.download_path()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apk", help="path to the built APK")
    parser.add_argument("--notes", default="", help="what changed, in a sentence a student would read")
    parser.add_argument("--version", help="override the version name from pubspec.yaml")
    parser.add_argument("--build", type=int, help="override the build number from pubspec.yaml")
    parser.add_argument("--filename", help="override the published filename")
    parser.add_argument("--abi", default="universal", help="which ABI this APK carries")
    parser.add_argument("--mandatory", action="store_true", help="older builds must update to keep working")
    parser.add_argument("--min-supported", type=int, help="refuse builds below this number outright")
    parser.add_argument("--force", action="store_true", help="replace an already published build")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", action="store_true", help="show what is published and exit")
    parser.add_argument("--remove", type=int, metavar="BUILD", help="unpublish a build (rollback)")
    args = parser.parse_args()

    if args.list:
        show(rel.load_releases())
        return 0
    if args.remove is not None:
        return do_remove(args.remove, args.dry_run)
    if not args.apk:
        parser.error("--apk is required (or use --list / --remove)")
    return do_publish(args)


if __name__ == "__main__":
    raise SystemExit(main())
