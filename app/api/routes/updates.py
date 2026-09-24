"""In-app updates for the Android client.

Three endpoints, all deliberately unauthenticated:

* `GET /updates/android/latest` — what the newest build is.
* `GET /updates/android/download/{build}/{filename}` — the APK itself.
* `GET /updates/android/manifest` — the whole history, for the admin console.

They are open because an app that is *too old to sign in* still has to be able
to update itself, and because the APK is the same file we hand out at a fresher
stall. The verification wall guards campus data; it cannot guard the client
binary without making a broken build unrecoverable in the field.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.services import releases as release_service

router = APIRouter(prefix="/updates", tags=["api:updates"])

APK_MEDIA_TYPE = "application/vnd.android.package-archive"


@router.get("/android/latest")
async def android_latest(
    build: int | None = Query(
        default=None,
        ge=0,
        description="The build number the caller is running, so the answer can say whether it is behind.",
    ),
):
    return release_service.update_payload(build)


@router.get("/android/manifest")
async def android_manifest():
    """Every published build, newest first."""
    return {
        "min_supported_build": release_service.minimum_supported_build(),
        "releases": [r.to_public() for r in release_service.load_releases()],
    }


@router.get("/android/download/{build}/{filename}")
async def android_download(build: int, filename: str):
    """Serve one published APK.

    Only files the manifest lists are served, so this cannot be turned into a
    file-read primitive by a crafted filename.
    """
    path = release_service.resolve_apk(build, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="No such build.")
    return FileResponse(
        path,
        media_type=APK_MEDIA_TYPE,
        filename=filename,
        headers={
            # Builds are immutable: a given build number is published once, so a
            # phone that retries a failed download resumes from cache instead of
            # pulling 40 MB again.
            "Cache-Control": "public, max-age=31536000, immutable",
            "Accept-Ranges": "bytes",
        },
    )
