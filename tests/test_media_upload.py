"""PP-327 — shared rich-text image upload endpoint.

Context: Quill pastes images as base64 ``data:`` URIs. bleach's default protocol
allowlist is http/https/mailto, so it keeps ``<img>`` and deletes the ``src``,
and the image silently vanishes on save. The fix is to upload the blob and
insert a hosted ``https://`` URL. These tests cover the upload endpoint that
makes that possible.
"""
import io
import os

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FAKE_USER, make_session_cookie

# 1x1 transparent PNG.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)

URL = "/api/media/image-upload"


def _csrf(c) -> str:
    """Mirror tests/test_ops_console.py — POSTs are CSRF-protected."""
    tok = c.cookies.get("csrftoken")
    if not tok:
        c.get("/health")
        tok = c.cookies.get("csrftoken")
    assert tok, "no csrftoken cookie was issued"
    return tok


def _client_with_roles(roles):
    from app.main import app as fastapi_app

    user = dict(FAKE_USER)
    user["roles"] = roles
    cookie = make_session_cookie({"user": user, "contact_verified": True})
    c = TestClient(fastapi_app, raise_server_exceptions=False, follow_redirects=False)
    # No explicit domain: http.cookiejar drops domain-specified cookies for the
    # dotless "testserver" host (see conftest.auth_client).
    c.cookies.set("session", cookie)
    return c


def _upload(c, filename, content, mime):
    tok = _csrf(c)
    return c.post(
        URL,
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
        files={"file": (filename, io.BytesIO(content), mime)},
        follow_redirects=False,
    )


@pytest.fixture(autouse=True)
def _tmp_media_dir(tmp_path, monkeypatch):
    """Point image storage at a temp dir so tests never touch the real volume."""
    import app.newsletter_assets as na
    import app.routes.media as media

    d = tmp_path / "images"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(na, "NEWSLETTER_IMG_DIR", d, raising=False)
    monkeypatch.setattr(media, "NEWSLETTER_IMG_DIR", d, raising=False)
    return d


def test_upload_happy_path_returns_url(_tmp_media_dir):
    c = _client_with_roles(["s1"])
    r = _upload(c, "shot.png", PNG_1PX, "image/png")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("url")
    # A hosted URL — never a data: URI, which is the entire point of the fix.
    assert not body["url"].startswith("data:")
    written = list(_tmp_media_dir.iterdir())
    assert len(written) == 1
    assert written[0].suffix == ".png"


def test_upload_rejects_oversize():
    from app.newsletter_assets import MAX_IMAGE_BYTES

    c = _client_with_roles(["s1"])
    big = b"\x89PNG" + b"0" * (MAX_IMAGE_BYTES + 1024)
    r = _upload(c, "big.png", big, "image/png")
    assert r.status_code == 400, r.text
    assert "exceeds" in r.json().get("error", "").lower()


def test_upload_rejects_svg_xss_vector():
    """SVG can carry script; it must never be accepted."""
    c = _client_with_roles(["s1"])
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    r = _upload(c, "x.svg", svg, "image/svg+xml")
    assert r.status_code == 400, r.text
    assert "unsupported" in r.json().get("error", "").lower()


def test_upload_rejects_disallowed_mime():
    c = _client_with_roles(["s1"])
    r = _upload(c, "x.pdf", b"%PDF-1.4", "application/pdf")
    assert r.status_code == 400, r.text


def test_upload_rejects_empty_file():
    c = _client_with_roles(["s1"])
    r = _upload(c, "empty.png", b"", "image/png")
    assert r.status_code == 400, r.text


def test_upload_requires_authentication():
    """Unauthenticated callers are bounced, not served."""
    from app.main import app as fastapi_app

    c = TestClient(fastapi_app, raise_server_exceptions=False, follow_redirects=False)
    r = _upload(c, "shot.png", PNG_1PX, "image/png")
    assert r.status_code in (302, 307, 401, 403), r.status_code


def test_upload_rejects_role_without_authoring_rights():
    """Authenticated but non-authoring roles get 403, not an upload slot."""
    c = _client_with_roles(["recruit"])
    r = _upload(c, "shot.png", PNG_1PX, "image/png")
    assert r.status_code == 403, r.text
    assert "authorized" in r.json().get("error", "").lower()


@pytest.mark.parametrize("role", ["s1", "s3", "command", "admin"])
def test_authoring_roles_allowed(role, _tmp_media_dir):
    """Event editors (s3/command) must be able to upload, not just S1.

    The pre-existing newsletter endpoint was _require_s1-gated, which is exactly
    why a general endpoint was needed for event/announcement descriptions.
    """
    c = _client_with_roles([role])
    r = _upload(c, "shot.png", PNG_1PX, "image/png")
    assert r.status_code == 200, f"{role}: {r.text}"


def test_filename_extension_comes_from_mime_not_client():
    """A hostile filename must not choose the stored extension."""
    c = _client_with_roles(["s1"])
    r = _upload(c, "evil.php", PNG_1PX, "image/png")
    assert r.status_code == 200, r.text
    assert r.json()["url"].endswith(".png")


def test_static_js_module_is_shipped():
    """The shared client module must exist, or every editor silently regresses."""
    path = os.path.join("app", "static", "js", "quill-image-upload.js")
    assert os.path.exists(path), path
    src = open(path, encoding="utf-8").read()
    # Toolbar was already handled before PP-327; paste and drop were not.
    assert "'paste'" in src
    assert "'drop'" in src
    assert "addHandler" in src


@pytest.mark.parametrize("tpl,needle", [
    ("app/templates/pages/event_detail.html", "quill-image-upload.js"),
    ("app/templates/pages/events.html", "quill-image-upload.js"),
    ("app/templates/pages/newsletter_edit.html", "quill-image-upload.js"),
    ("app/templates/pages/s1_email_blast.html", "quill-image-upload.js"),
])
def test_every_quill_editor_loads_the_module(tpl, needle):
    """All four Quill editors must wire the shared module.

    Missing one means paste silently keeps failing in that editor — the exact
    failure mode being fixed.
    """
    src = open(tpl, encoding="utf-8").read()
    assert needle in src, f"{tpl} does not load {needle}"
    assert "attachQuillImageUpload" in src, f"{tpl} never calls attachQuillImageUpload"
