"""Shared inline-image upload for the portal's Quill editors.

Why this exists
---------------
`POST /api/s1/newsletter/image-upload` already uploads an image and returns a
hosted URL, but it is gated behind `_require_s1`. Events, announcements and the
training library are edited by *different* roles, so those editors could not
use it. Rather than widen the S1 gate, this module exposes the same upload
behaviour behind the union of the role sets that already gate a rich-text
editor (`MEDIA_UPLOAD_ROLES`).

Files land in the existing `NEWSLETTER_DATA_DIR` mounted volume so they survive
deploys, and are served by the existing `/nlmedia` StaticFiles mount. No new
Docker volume, no new model, no migration.

⚠️ OPERATOR NOTE: `/nlmedia` is served UNAUTHENTICATED on purpose, because
Gmail's image proxy must be able to fetch newsletter images. Anything written
here is therefore readable by anyone who has (or guesses, or is forwarded) the
URL. A uuid4 filename is obscurity, not access control. See the Security note
in the project report before routing sensitive operational imagery through it.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
# Starlette's UploadFile, deliberately: `await request.form()` yields THAT
# class, and fastapi.UploadFile is a subclass of it — so an isinstance() check
# against the FastAPI one never matches a parsed form field.
from starlette.datastructures import UploadFile

from app.auth import get_current_user
from app.constants import MEDIA_UPLOAD_ROLES
from app.newsletter_assets import (
    ALLOWED_IMAGE_MIMES,
    MAX_IMAGE_BYTES,
    NEWSLETTER_IMG_DIR,
    image_ext_for_mime,
    image_url,
    sniff_image_mime,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/media", tags=["media"])

_MAX_MB = MAX_IMAGE_BYTES // (1024 * 1024)


@router.post("/image-upload", response_model=None)
async def media_image_upload(request: Request):
    """Store an inline editor image and return its hosted URL.

    Returns ``{"url": ...}`` — the exact shape the Quill client expects — or
    ``{"error": ...}`` with a 4xx status. Errors are JSON (not a login
    redirect) so the editor can surface the reason to the user instead of
    failing silently, which is the bug this whole feature exists to fix.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Sign in to upload images."}, status_code=401)
    if not (set(user.get("roles", [])) & MEDIA_UPLOAD_ROLES):
        return JSONResponse(
            {"error": "You do not have permission to upload images."},
            status_code=403,
        )

    form = await request.form()
    file = form.get("file")
    if not isinstance(file, UploadFile) or not getattr(file, "filename", None):
        return JSONResponse({"error": "No file provided."}, status_code=400)

    # Bounded read: one byte past the limit is enough to reject an oversize
    # upload without pulling an arbitrarily large body into memory.
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        return JSONResponse(
            {"error": f"Image exceeds the {_MAX_MB}MB limit."}, status_code=400
        )
    if not data:
        return JSONResponse({"error": "Uploaded file is empty."}, status_code=400)

    mime = (file.content_type or "").strip().lower()
    if mime not in ALLOWED_IMAGE_MIMES:
        return JSONResponse(
            {"error": "Unsupported image type. Allowed: PNG, JPEG, GIF, WebP."},
            status_code=400,
        )

    # The declared Content-Type is attacker-controlled; confirm it against the
    # actual bytes so nothing else can be stored under an image extension.
    sniffed = sniff_image_mime(data)
    if sniffed is None or sniffed != mime:
        return JSONResponse(
            {"error": "File contents do not match a supported image type."},
            status_code=400,
        )

    # Extension from the VALIDATED mime, never the caller's filename — see the
    # note on IMAGE_MIME_EXT in app/newsletter_assets.py.
    stored = f"{uuid.uuid4().hex}{image_ext_for_mime(mime)}"
    try:
        NEWSLETTER_IMG_DIR.mkdir(parents=True, exist_ok=True)
        with open(NEWSLETTER_IMG_DIR / stored, "wb") as fh:
            fh.write(data)
    except OSError:
        logger.exception("media image upload: failed writing %s", stored)
        return JSONResponse(
            {"error": "Could not store the image. Try again."}, status_code=500
        )

    logger.info(
        "media image upload: %s (%s, %d bytes) by %s",
        stored, mime, len(data), user.get("username", "?"),
    )
    return JSONResponse({"url": image_url(stored)})
