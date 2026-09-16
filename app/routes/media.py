"""Shared rich-text media uploads (PP-327).

Why this exists
---------------
Quill pastes an image into the document as a base64 ``data:`` URI. Every
rich-text field in the portal is sanitized with bleach, and bleach's default
protocol allowlist is ``http, https, mailto`` — ``data:`` is NOT in it. So
bleach keeps the ``<img>`` element and silently deletes its ``src``, leaving an
empty tag. To the user the image simply vanishes on save, with no error.

Allowing ``data:`` would be the wrong fix: it bloats every stored row, re-ships
the image inline on every page load, and ``data:image/svg+xml`` is an XSS
vector. Instead we upload the blob here and hand back a normal ``https://`` URL,
which already passes every sanitizer unchanged.

The newsletter editor already had this shape at
``POST /api/s1/newsletter/image-upload``, but it is ``_require_s1``-gated, which
is wrong for events and announcements (different roles edit those). This is the
same validation and the same storage, reachable by any role that can actually
author rich text.
"""
from __future__ import annotations

import logging
import os
import uuid

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

from app.auth import require_auth
from app.constants import UNIT_COMMS_ROLES
from app.newsletter_assets import (
    ALLOWED_IMAGE_MIMES,
    MAX_IMAGE_BYTES,
    NEWSLETTER_IMG_DIR,
    image_url,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/media", tags=["media"])

# Roles that can author rich text somewhere in the portal: unit comms
# (newsletter / announcements) plus the ops roles that edit event descriptions.
# Deliberately the UNION of existing role sets — this widens nothing, it just
# stops the upload path being narrower than the editors that need it.
MEDIA_UPLOAD_ROLES = tuple(sorted(set(UNIT_COMMS_ROLES) | {"s1", "s3", "command", "admin"}))

# Defence in depth. SVG is excluded on purpose: it can carry script, and it is
# not in ALLOWED_IMAGE_MIMES either. Keep both lists raster-only.
_SAFE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


@router.post("/image-upload", response_model=None)
@require_auth
async def media_image_upload(request: Request):
    """Store a pasted/dropped/picked image and return its hosted URL.

    Always returns JSON. The client surfaces ``error`` to the user — a silent
    failure here would reproduce the very bug this endpoint fixes.
    """
    user = request.session.get("user", {}) or {}
    roles = set(user.get("roles") or [])
    if not roles.intersection(MEDIA_UPLOAD_ROLES):
        return JSONResponse({"error": "Not authorized to upload images."}, status_code=403)

    form = await request.form()
    file: UploadFile = form.get("file")
    if not file or not getattr(file, "filename", None):
        return JSONResponse({"error": "No file provided."}, status_code=400)

    data = await file.read()
    if not data:
        return JSONResponse({"error": "Empty file."}, status_code=400)
    if len(data) > MAX_IMAGE_BYTES:
        return JSONResponse(
            {"error": f"Image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)}MB limit."},
            status_code=400,
        )

    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_IMAGE_MIMES:
        return JSONResponse(
            {"error": "Unsupported image type. Use PNG, JPEG, GIF or WebP."},
            status_code=400,
        )

    # Trust the validated MIME for the extension rather than the client-supplied
    # filename, so a hostile name like "x.svg" or "x.php" cannot pick the ext.
    ext = _MIME_EXT.get(mime) or os.path.splitext(file.filename or "")[1].lower()
    if ext not in _SAFE_EXTS:
        return JSONResponse({"error": "Unsupported image type."}, status_code=400)

    stored = f"{uuid.uuid4().hex}{ext}"
    try:
        NEWSLETTER_IMG_DIR.mkdir(parents=True, exist_ok=True)
        with open(NEWSLETTER_IMG_DIR / stored, "wb") as fh:
            fh.write(data)
    except Exception:
        logger.exception("media image upload failed to write %s", stored)
        return JSONResponse({"error": "Could not save image."}, status_code=500)

    return JSONResponse({"url": image_url(stored)})
