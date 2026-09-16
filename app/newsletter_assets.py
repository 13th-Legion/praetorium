"""Seasonal crest catalog + storage paths for the Legionary Dispatch newsletter.

Two classes of assets:

1. Seasonal crests  — admin-curated, shipped in-repo under
   app/static/img/crests/, served by the public /static mount. Add a seasonal
   crest by dropping a PNG there and adding a catalog entry below.

2. User uploads (inline images + file attachments) — written to a mounted
   Docker volume at /app/data/newsletter (survives deploys, mirrors the Battle
   Library pattern) and served UNAUTHENTICATED via the /nlmedia mount so remote
   mail clients (Gmail image proxy, etc.) can fetch embedded images.
"""
from __future__ import annotations

import os
from pathlib import Path

from app.settings import PUBLIC_BASE_URL

# ── User-upload storage (mounted volume; deploy-safe) ────────────────────────
NEWSLETTER_DATA_DIR = Path(os.getenv("NEWSLETTER_DATA_DIR", "/app/data/newsletter"))
NEWSLETTER_IMG_DIR = NEWSLETTER_DATA_DIR / "images"
NEWSLETTER_ATTACH_DIR = NEWSLETTER_DATA_DIR / "attachments"

# Public (unauthenticated) URL base for inline images — must be absolute.
NLMEDIA_URL_BASE = f"{PUBLIC_BASE_URL}/nlmedia"

# ── Seasonal crests (in-repo, public static) ─────────────────────────────────
STATIC_ROOT = Path(__file__).resolve().parent / "static"
CREST_DIR = STATIC_ROOT / "img" / "crests"
CREST_URL_BASE = f"{PUBLIC_BASE_URL}/static/img/crests"

for _d in (NEWSLETTER_IMG_DIR, NEWSLETTER_ATTACH_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass  # volume may not be mounted at import time in some contexts

# Seasonal crest catalog. `file` is relative to CREST_DIR. `standard` is always
# present; seasonal entries fall back to standard until their PNG is uploaded.
SEASONAL_CRESTS: dict[str, dict] = {
    "standard":      {"label": "Standard", "file": "standard.png",     "emoji": "🛡️"},
    "christmas":     {"label": "Christmas",                   "file": "christmas.png",    "emoji": "🎄"},
    "halloween":     {"label": "Halloween",                   "file": "halloween.png",    "emoji": "🎃"},
    "easter":        {"label": "Easter",                      "file": "easter.png",       "emoji": "🐣"},
    "independence":  {"label": "Independence Day",            "file": "independence.png", "emoji": "🎆"},
    "thanksgiving":  {"label": "Thanksgiving",                "file": "thanksgiving.png", "emoji": "🦃"},
    "newyear":       {"label": "New Year",                    "file": "newyear.png",      "emoji": "🎉"},
}

DEFAULT_CREST = "standard"

# Upload limits
MAX_IMAGE_BYTES = 5 * 1024 * 1024          # 5 MB per inline image
MAX_ATTACH_BYTES = 15 * 1024 * 1024        # 15 MB per attachment
MAX_TOTAL_ATTACH_BYTES = 18 * 1024 * 1024  # 18 MB total payload (Proton Bridge ceiling)
ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

# Stored filename extension per allowed image MIME.
#
# SECURITY: the extension on disk MUST come from the validated MIME, never from
# the caller's filename. /nlmedia is a StaticFiles mount and StaticFiles derives
# the response Content-Type from the file extension, so honouring an uploaded
# name like "payload.svg" would let a caller have us serve image/svg+xml — i.e.
# script — from our own origin. SVG is excluded from ALLOWED_IMAGE_MIMES for the
# same reason; keep it that way.
IMAGE_MIME_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

# Magic-byte signatures for the allowed types, so a lying Content-Type header
# cannot get arbitrary bytes stored under an image extension.
_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
ALLOWED_ATTACH_MIMES = {
    "application/pdf",
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# Stored filename extension per allowed ATTACHMENT MIME.
#
# SECURITY: same rule as IMAGE_MIME_EXT, and for the same reason. Attachments
# land in NEWSLETTER_ATTACH_DIR, which sits inside the volume published by the
# unauthenticated /nlmedia StaticFiles mount, so the extension on disk chooses
# the Content-Type we serve. Taking it from the caller's filename let a caller
# upload bytes with an allowed Content-Type (say application/pdf) under the name
# "payload.html" and have us serve text/html — stored XSS on our own origin,
# reachable without a session. Every value here is inert when served.
ATTACH_MIME_EXT: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}

# Magic-byte signatures for the non-image attachment types. Checked so that a
# lying Content-Type header cannot get arbitrary bytes — HTML, SVG, script —
# stored under a document extension.
#
# application/msword accepts both OLE2 (Word 97-2003) and RTF, because Word
# writes RTF under a .doc name and browsers label both application/msword.
# The docx signature is the bare ZIP local-file header: every OOXML file is a
# zip, so this confirms "is a zip", not "is a valid Word document". That is
# enough for the job here, which is to keep markup out of the volume.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Per the PDF spec the header need only appear near the start of the file, and
# real-world PDFs sometimes carry a short prefix, so scan a window instead of
# demanding startswith and rejecting files that every reader accepts.
_PDF_HEADER_WINDOW = 1024


def crest_url(key: str) -> str:
    """Absolute URL of the crest for `key`, falling back to standard if the
    seasonal file hasn't been uploaded yet."""
    entry = SEASONAL_CRESTS.get(key) or SEASONAL_CRESTS[DEFAULT_CREST]
    if (CREST_DIR / entry["file"]).exists():
        return f"{CREST_URL_BASE}/{entry['file']}"
    std = SEASONAL_CRESTS[DEFAULT_CREST]
    if (CREST_DIR / std["file"]).exists():
        return f"{CREST_URL_BASE}/{std['file']}"
    return f"{PUBLIC_BASE_URL}/static/img/crest.png"  # last-resort


def crest_available(key: str) -> bool:
    entry = SEASONAL_CRESTS.get(key)
    return bool(entry and (CREST_DIR / entry["file"]).exists())


def image_url(filename: str) -> str:
    return f"{NLMEDIA_URL_BASE}/images/{filename}"


def image_ext_for_mime(mime: str) -> str:
    """Stored-file extension for a validated image MIME.

    Only ever call this with a MIME already checked against
    ALLOWED_IMAGE_MIMES; the .png fallback is a belt-and-braces default, not a
    licence to skip validation.
    """
    return IMAGE_MIME_EXT.get((mime or "").strip().lower(), ".png")


def attach_ext_for_mime(mime: str) -> str:
    """Stored-file extension for a validated attachment MIME.

    Only ever call this with a MIME already checked against
    ALLOWED_ATTACH_MIMES. Anything unrecognised falls back to ".bin", which
    StaticFiles serves as application/octet-stream — inert either way, and
    never the caller's own suffix.
    """
    return ATTACH_MIME_EXT.get((mime or "").strip().lower(), ".bin")


def sniff_image_mime(data: bytes) -> str | None:
    """Detect the real image type from magic bytes, or None if unrecognised.

    Covers exactly the four types in ALLOWED_IMAGE_MIMES. Used to confirm that
    the uploaded bytes match the declared Content-Type, so a caller cannot get
    SVG (or anything else) written out under a .png/.jpg name.
    """
    if not data:
        return None
    for signature, mime in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime
    # WebP is a RIFF container: "RIFF" <4-byte size> "WEBP"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def sniff_attachment_mime(data: bytes) -> str | None:
    """Detect the real type of uploaded attachment bytes, or None.

    Covers exactly the types in ALLOWED_ATTACH_MIMES: the four image types via
    sniff_image_mime, plus PDF and the two Word formats. None means "not a
    recognised attachment type", which is what HTML, SVG and script all return
    — so the callers reject on it rather than writing those bytes into the
    publicly served volume.

    The returned MIME is canonical, not the caller's claim, so a caller cannot
    pick the stored extension by mislabelling the upload.
    """
    if not data:
        return None

    img = sniff_image_mime(data)
    if img:
        return img

    if b"%PDF-" in data[:_PDF_HEADER_WINDOW]:
        return "application/pdf"

    if data.startswith(_ZIP_MAGICS):
        return _DOCX_MIME

    if data.startswith(_OLE2_MAGIC) or data.startswith(b"{\\rtf"):
        return "application/msword"

    return None
