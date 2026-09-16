"""Shared inline-image upload (POST /api/media/image-upload) + the sanitizer
contract it exists to satisfy.

Background — the bug this guards against:
    Quill embeds a pasted image as a base64 `data:` URI. bleach's default
    protocol allowlist is http/https/mailto, so it KEEPS the <img> and DELETES
    its src, leaving an empty tag. The image silently vanishes on save. The fix
    is to upload the blob and embed a hosted https:// URL.

These tests cover the server half (endpoint + sanitizers + template wiring).
The browser half — that a real paste/drop event is intercepted before Quill
inserts base64 — is NOT exercised here; it cannot be without a real browser.
"""

import base64
import io

import pytest
from starlette.testclient import TestClient

from app.constants import MEDIA_UPLOAD_ROLES
from app.main import app as fastapi_app
from app.newsletter_assets import (
    ALLOWED_IMAGE_MIMES,
    MAX_IMAGE_BYTES,
    image_ext_for_mime,
    sniff_image_mime,
)

from tests.conftest import make_session_cookie

pytestmark = pytest.mark.unit

ENDPOINT = "/api/media/image-upload"

# Real 1x1 PNG.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
GIF_BYTES = b"GIF89a" + b"\x01\x00\x01\x00\x00\x00\x00;"
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP_BYTES = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x00" * 16
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


# ─── Clients ─────────────────────────────────────────────────────────────────

def _client_with_roles(roles):
    """TestClient carrying a signed session for a user with `roles`."""
    cookie = make_session_cookie({
        "user": {
            "username": "test.uploader",
            "display_name": "SGT Uploader",
            "email": "up@13thlegion.org",
            "groups": [],
            "roles": list(roles),
        },
        "contact_verified": True,
    })
    c = TestClient(fastapi_app, raise_server_exceptions=False, follow_redirects=False)
    c.cookies.set("session", cookie)
    return c


def _csrf(c) -> str:
    tok = c.cookies.get("csrftoken")
    if not tok:
        c.get("/health")
        tok = c.cookies.get("csrftoken")
    assert tok, "no csrftoken cookie was issued"
    return tok


def _post(c, content, filename="shot.png", mime="image/png", with_csrf=True):
    tok = _csrf(c) if with_csrf else None
    return c.post(
        ENDPOINT,
        files={"file": (filename, io.BytesIO(content), mime)},
        data={"csrf_token": tok} if tok else None,
        headers={"X-CSRF-Token": tok} if tok else None,
    )


@pytest.fixture
def img_dir(tmp_path, monkeypatch):
    """Redirect stored uploads at a throwaway dir so the test owns the files."""
    d = tmp_path / "images"
    d.mkdir()
    monkeypatch.setattr("app.routes.media.NEWSLETTER_IMG_DIR", d)
    return d


# ─── Happy path ──────────────────────────────────────────────────────────────

def test_upload_happy_path_returns_hosted_url_and_writes_file(img_dir):
    c = _client_with_roles(["s3"])
    r = _post(c, PNG_BYTES)
    assert r.status_code == 200, r.text

    body = r.json()
    assert "error" not in body
    url = body["url"]

    # The client inserts this verbatim via insertEmbed(...,'image',url), and it
    # must be a plain http(s) URL or bleach will strip it right back out.
    assert url.startswith("http")
    assert "/nlmedia/images/" in url
    assert not url.startswith("data:")

    stored = url.rsplit("/", 1)[-1]
    assert (img_dir / stored).read_bytes() == PNG_BYTES


@pytest.mark.parametrize("content,mime,ext", [
    (PNG_BYTES, "image/png", ".png"),
    (JPEG_BYTES, "image/jpeg", ".jpg"),
    (GIF_BYTES, "image/gif", ".gif"),
    (WEBP_BYTES, "image/webp", ".webp"),
])
def test_upload_accepts_every_allowed_mime(img_dir, content, mime, ext):
    c = _client_with_roles(["command"])
    r = _post(c, content, filename="x", mime=mime)
    assert r.status_code == 200, r.text
    assert r.json()["url"].endswith(ext)


# ─── Rejections ──────────────────────────────────────────────────────────────

def test_upload_rejects_oversize(img_dir):
    c = _client_with_roles(["s3"])
    oversize = PNG_BYTES + b"\x00" * (MAX_IMAGE_BYTES + 1)
    r = _post(c, oversize)
    assert r.status_code == 400, r.text
    assert "5MB" in r.json()["error"]
    assert list(img_dir.iterdir()) == []


def test_upload_rejects_svg(img_dir):
    """SVG is excluded everywhere: image/svg+xml is a script vector."""
    assert "image/svg+xml" not in ALLOWED_IMAGE_MIMES
    c = _client_with_roles(["s3"])
    r = _post(c, SVG_BYTES, filename="evil.svg", mime="image/svg+xml")
    assert r.status_code == 400, r.text
    assert "Unsupported image type" in r.json()["error"]
    assert list(img_dir.iterdir()) == []


@pytest.mark.parametrize("mime", [
    "image/svg+xml", "text/html", "application/pdf",
    "application/octet-stream", "image/bmp", "",
])
def test_upload_rejects_disallowed_mimes(img_dir, mime):
    c = _client_with_roles(["s3"])
    r = _post(c, PNG_BYTES, filename="x.png", mime=mime)
    assert r.status_code == 400, r.text
    assert list(img_dir.iterdir()) == []


def test_upload_rejects_content_type_lying_about_bytes(img_dir):
    """Declared image/png but the body is SVG — must not be stored."""
    c = _client_with_roles(["s3"])
    r = _post(c, SVG_BYTES, filename="evil.png", mime="image/png")
    assert r.status_code == 400, r.text
    assert "do not match" in r.json()["error"]
    assert list(img_dir.iterdir()) == []


def test_upload_extension_comes_from_mime_not_filename(img_dir):
    """A caller must not choose the stored extension.

    /nlmedia is a StaticFiles mount and StaticFiles derives Content-Type from
    the extension on disk, so honouring an uploaded ".svg" name would let a
    caller serve image/svg+xml — script — from our own origin.
    """
    c = _client_with_roles(["s3"])
    r = _post(c, PNG_BYTES, filename="payload.svg", mime="image/png")
    assert r.status_code == 200, r.text

    stored = r.json()["url"].rsplit("/", 1)[-1]
    assert stored.endswith(".png")
    assert ".svg" not in stored
    assert [p.name for p in img_dir.iterdir()] == [stored]


def test_upload_rejects_missing_file(img_dir):
    c = _client_with_roles(["s3"])
    tok = _csrf(c)
    r = c.post(ENDPOINT, data={"csrf_token": tok}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 400, r.text
    assert "No file" in r.json()["error"]


# ─── AuthN / AuthZ ───────────────────────────────────────────────────────────

def test_upload_unauthenticated_rejected(img_dir):
    """Anonymous callers get a JSON 401, not a login redirect.

    The editor reads JSON; an HTML redirect body would surface as an opaque
    parse failure instead of a usable message.
    """
    c = TestClient(fastapi_app, raise_server_exceptions=False, follow_redirects=False)
    r = _post(c, PNG_BYTES)
    assert r.status_code == 401, r.text
    assert "error" in r.json()
    assert list(img_dir.iterdir()) == []


def test_upload_authenticated_but_unauthorized_role_rejected(img_dir):
    """An ordinary member who cannot author rich text cannot upload."""
    c = _client_with_roles(["enlisted"])
    r = _post(c, PNG_BYTES)
    assert r.status_code == 403, r.text
    assert "permission" in r.json()["error"].lower()
    assert list(img_dir.iterdir()) == []


@pytest.mark.parametrize("role", sorted(MEDIA_UPLOAD_ROLES))
def test_upload_allowed_for_every_media_upload_role(img_dir, role):
    c = _client_with_roles([role])
    r = _post(c, PNG_BYTES)
    assert r.status_code == 200, f"{role} was rejected: {r.text}"


def test_media_upload_roles_do_not_widen_beyond_existing_editors():
    """The endpoint must not invent a permission model.

    MEDIA_UPLOAD_ROLES is the union of role sets that ALREADY gate a rich-text
    editor. If someone adds a role here, this test should make them justify it.
    """
    from app.constants import S1_ROLES, UNIT_COMMS_ROLES

    expected = (
        UNIT_COMMS_ROLES
        | S1_ROLES
        | {"command", "s3", "admin", "leader"}   # events create/edit
        | {"command", "s3", "admin"}             # training_library TRADOC_MANAGE_ROLES
    )
    assert MEDIA_UPLOAD_ROLES == expected
    assert "enlisted" not in MEDIA_UPLOAD_ROLES
    assert "recruit" not in MEDIA_UPLOAD_ROLES


def test_upload_requires_csrf(img_dir):
    c = _client_with_roles(["s3"])
    _csrf(c)  # ensure the cookie exists, then deliberately omit the echo
    r = c.post(ENDPOINT, files={"file": ("a.png", io.BytesIO(PNG_BYTES), "image/png")})
    assert r.status_code == 403, r.text


# ─── Helpers ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("data,expected", [
    (PNG_BYTES, "image/png"),
    (JPEG_BYTES, "image/jpeg"),
    (GIF_BYTES, "image/gif"),
    (WEBP_BYTES, "image/webp"),
    (SVG_BYTES, None),
    (b"", None),
    (b"not an image at all", None),
])
def test_sniff_image_mime(data, expected):
    assert sniff_image_mime(data) == expected


def test_image_ext_for_mime_covers_every_allowed_mime():
    for mime in ALLOWED_IMAGE_MIMES:
        assert image_ext_for_mime(mime).startswith(".")
    assert image_ext_for_mime("image/svg+xml") == ".png"  # never ".svg"


# ─── The newsletter endpoint must keep working (it is in production) ─────────

def test_newsletter_image_upload_still_accepts_png(tmp_path, monkeypatch, patch_global_session):
    d = tmp_path / "nl-images"
    d.mkdir()
    monkeypatch.setattr("app.routes.newsletter.NEWSLETTER_IMG_DIR", d)

    c = _client_with_roles(["s1"])   # UNIT_COMMS_ROLES
    tok = _csrf(c)
    r = c.post(
        "/api/s1/newsletter/image-upload",
        files={"file": ("shot.png", io.BytesIO(PNG_BYTES), "image/png")},
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 200, r.text
    assert "/nlmedia/images/" in r.json()["url"]
    assert len(list(d.iterdir())) == 1


def test_newsletter_image_upload_rejects_svg_filename_trick(tmp_path, monkeypatch, patch_global_session):
    """Same extension hardening as the general endpoint."""
    d = tmp_path / "nl-images"
    d.mkdir()
    monkeypatch.setattr("app.routes.newsletter.NEWSLETTER_IMG_DIR", d)

    c = _client_with_roles(["s1"])
    tok = _csrf(c)
    r = c.post(
        "/api/s1/newsletter/image-upload",
        files={"file": ("payload.svg", io.BytesIO(PNG_BYTES), "image/png")},
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 200, r.text
    assert r.json()["url"].endswith(".png")


def test_newsletter_image_upload_still_requires_s1(tmp_path, monkeypatch):
    """The general endpoint must not have loosened the S1 gate."""
    c = _client_with_roles(["s3"])   # can use /api/media, NOT the S1 endpoint
    tok = _csrf(c)
    r = c.post(
        "/api/s1/newsletter/image-upload",
        files={"file": ("shot.png", io.BytesIO(PNG_BYTES), "image/png")},
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 403, r.text
