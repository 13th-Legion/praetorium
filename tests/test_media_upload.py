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
from app.models.newsletter import Newsletter
from app.newsletter_assets import (
    ALLOWED_ATTACH_MIMES,
    ALLOWED_IMAGE_MIMES,
    ATTACH_MIME_EXT,
    MAX_IMAGE_BYTES,
    attach_ext_for_mime,
    image_ext_for_mime,
    sniff_attachment_mime,
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

# Attachment fixtures: minimal but signature-valid bodies for each allowed type.
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< >>\nendobj\ntrailer\n%%EOF\n"
DOC_BYTES = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64   # OLE2 compound file
RTF_BYTES = b"{\\rtf1\\ansi hello}"                                # Word also writes this as .doc
DOCX_BYTES = b"PK\x03\x04" + b"\x00" * 64                          # OOXML is a zip
HTML_PAYLOAD = b"<!doctype html><html><script>alert(document.domain)</script></html>"


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


# ─── Newsletter ATTACHMENTS ──────────────────────────────────────────────────
#
# Background — the bug this guards against:
#     The attachment handler validated Content-Type against ALLOWED_ATTACH_MIMES
#     but took the stored extension from the caller's filename. Attachments land
#     in NEWSLETTER_ATTACH_DIR, inside the volume published by the
#     UNAUTHENTICATED /nlmedia StaticFiles mount, and StaticFiles types its
#     responses by file extension. So an upload sent as `Content-Type:
#     application/pdf` named `payload.html` was stored as `<uuid>.html` and
#     served back as text/html from our own origin — stored XSS, no session
#     needed. Identical class to the image-upload bug fixed on 2026-09-15.
#
# The extension is now derived from the validated MIME, the bytes are
# signature-checked, and the mount forces a download for everything outside
# `images/`.

# Extensions StaticFiles would serve as something a browser executes.
SCRIPTABLE_NAMES = [
    "payload.html", "payload.htm", "payload.svg", "payload.js",
    "payload.mjs", "payload.xhtml", "payload.xml", "payload.shtml",
]


@pytest.fixture
def attach_dir(tmp_path, monkeypatch):
    d = tmp_path / "attachments"
    d.mkdir()
    monkeypatch.setattr("app.routes.newsletter.NEWSLETTER_ATTACH_DIR", d)
    return d


async def _new_draft(db_sessionmaker) -> int:
    async with db_sessionmaker() as s:
        nl = Newsletter(
            title="Legionary Dispatch — Test", subject="Test",
            body_html="<p>x</p>", crest_key="standard",
            status="draft", created_by_name="SGT Uploader",
        )
        s.add(nl)
        await s.commit()
        await s.refresh(nl)
        return nl.id


def _post_attach(c, nl_id, content, filename, mime):
    tok = _csrf(c)
    return c.post(
        f"/api/s1/newsletter/{nl_id}/attachment",
        files={"file": (filename, io.BytesIO(content), mime)},
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )


@pytest.mark.parametrize("filename", SCRIPTABLE_NAMES)
async def test_attachment_extension_comes_from_mime_not_filename(
    attach_dir, db_sessionmaker, patch_global_session, filename
):
    """A caller must not choose the stored extension.

    The bytes here are a genuine PDF and the declared type is the allowed
    application/pdf, so the upload is accepted — but it must land as .pdf, not
    under the active extension the caller asked for.
    """
    nl_id = await _new_draft(db_sessionmaker)
    c = _client_with_roles(["s1"])

    r = _post_attach(c, nl_id, PDF_BYTES, filename, "application/pdf")
    assert r.status_code == 200, r.text

    stored = [p.name for p in attach_dir.iterdir()]
    assert len(stored) == 1, stored
    assert stored[0].endswith(".pdf"), stored
    suffix = "." + filename.rsplit(".", 1)[-1]
    assert suffix not in stored[0], stored


async def test_attachment_stored_name_never_carries_an_active_extension(
    attach_dir, db_sessionmaker, patch_global_session
):
    """Whatever the filename, the extension on disk resolves to an inert type."""
    import mimetypes

    nl_id = await _new_draft(db_sessionmaker)
    c = _client_with_roles(["s1"])

    for filename in SCRIPTABLE_NAMES:
        _post_attach(c, nl_id, PDF_BYTES, filename, "application/pdf")

    dangerous = {"text/html", "image/svg+xml", "text/javascript",
                 "application/xhtml+xml", "application/xml"}
    for p in attach_dir.iterdir():
        served, _ = mimetypes.guess_type(p.name)
        assert served not in dangerous, f"{p.name} would be served as {served}"


@pytest.mark.parametrize("content,mime,ext", [
    (PDF_BYTES, "application/pdf", ".pdf"),
    (PNG_BYTES, "image/png", ".png"),
    (JPEG_BYTES, "image/jpeg", ".jpg"),
    (GIF_BYTES, "image/gif", ".gif"),
    (WEBP_BYTES, "image/webp", ".webp"),
    (DOC_BYTES, "application/msword", ".doc"),
    (RTF_BYTES, "application/msword", ".doc"),
    (DOCX_BYTES,
     "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
     ".docx"),
])
async def test_attachment_accepts_every_allowed_type(
    attach_dir, db_sessionmaker, patch_global_session, content, mime, ext
):
    """Hardening must not cost S1 any type they could legitimately attach."""
    nl_id = await _new_draft(db_sessionmaker)
    c = _client_with_roles(["s1"])

    r = _post_attach(c, nl_id, content, "report" + ext, mime)
    assert r.status_code == 200, r.text

    stored = [p.name for p in attach_dir.iterdir()]
    assert len(stored) == 1 and stored[0].endswith(ext), stored


@pytest.mark.parametrize("mime", sorted(ALLOWED_ATTACH_MIMES))
async def test_attachment_rejects_markup_under_every_allowed_mime(
    attach_dir, db_sessionmaker, patch_global_session, mime
):
    """HTML bytes must not reach the volume behind ANY allowed Content-Type.

    This is the belt to the extension fix's braces: even though a stored .pdf
    is inert, HTML that a browser never renders still has no business in a
    publicly readable directory.
    """
    nl_id = await _new_draft(db_sessionmaker)
    c = _client_with_roles(["s1"])

    r = _post_attach(c, nl_id, HTML_PAYLOAD, "payload.pdf", mime)
    assert r.status_code == 400, r.text
    assert "do not match" in r.text
    assert list(attach_dir.iterdir()) == []


async def test_attachment_rejects_svg_bytes(attach_dir, db_sessionmaker, patch_global_session):
    """SVG is script. It is in neither allowlist and must not sneak in as an image."""
    assert "image/svg+xml" not in ALLOWED_ATTACH_MIMES
    nl_id = await _new_draft(db_sessionmaker)
    c = _client_with_roles(["s1"])

    r = _post_attach(c, nl_id, SVG_BYTES, "logo.svg", "image/svg+xml")
    assert r.status_code == 400, r.text
    assert list(attach_dir.iterdir()) == []

    # ...and not by mislabelling it as an allowed image type either.
    r = _post_attach(c, nl_id, SVG_BYTES, "logo.png", "image/png")
    assert r.status_code == 400, r.text
    assert list(attach_dir.iterdir()) == []


async def test_attachment_original_filename_is_escaped_in_the_htmx_row(
    attach_dir, db_sessionmaker, patch_global_session
):
    """The uploaded name is echoed into a hand-built HTML fragment.

    _attachment_row_html assembles its response with an f-string rather than
    Jinja, so nothing escapes it automatically and a filename carrying markup
    would execute in the S1 editor on upload.
    """
    nl_id = await _new_draft(db_sessionmaker)
    c = _client_with_roles(["s1"])

    r = _post_attach(
        c, nl_id, PDF_BYTES,
        '<img src=x onerror=alert(document.domain)>.pdf', "application/pdf",
    )
    assert r.status_code == 200, r.text

    # The payload may appear as text, but never as a tag: no unescaped '<'
    # from the filename, so the browser parses it as content, not an element.
    assert "<img" not in r.text
    assert "&lt;img src=x onerror=alert(document.domain)&gt;.pdf" in r.text


def test_attachment_row_html_escapes_quotes_in_the_original_name():
    """Attribute-breakout half of the same escaping contract.

    Driven against the fragment builder directly: httpx percent-encodes a bare
    '"' in a multipart filename, so a raw quote cannot be delivered end-to-end
    through TestClient even though a hand-rolled HTTP client can send one.
    """
    from types import SimpleNamespace

    from app.routes.newsletter import _attachment_row_html

    att = SimpleNamespace(
        id=7, size=2048, filename="deadbeef.pdf",
        orig_name='a" onmouseover="alert(1)',
    )
    out = _attachment_row_html(att, nl_id=3)

    assert 'onmouseover="alert(1)"' not in out
    assert "&quot;" in out


async def test_attachment_requires_csrf(attach_dir, db_sessionmaker, patch_global_session):
    nl_id = await _new_draft(db_sessionmaker)
    c = _client_with_roles(["s1"])
    _csrf(c)  # prime the cookie, then deliberately omit the echo
    r = c.post(
        f"/api/s1/newsletter/{nl_id}/attachment",
        files={"file": ("a.pdf", io.BytesIO(PDF_BYTES), "application/pdf")},
    )
    assert r.status_code == 403, r.text
    assert list(attach_dir.iterdir()) == []


async def test_attachment_requires_s1(attach_dir, db_sessionmaker, patch_global_session):
    nl_id = await _new_draft(db_sessionmaker)
    c = _client_with_roles(["s3"])   # /api/media yes, S1 newsletter no
    r = _post_attach(c, nl_id, PDF_BYTES, "a.pdf", "application/pdf")
    assert r.status_code == 403, r.text
    assert list(attach_dir.iterdir()) == []


# ─── Attachment helpers ─────────────────────────────────────────────────────

def test_attach_ext_for_mime_covers_every_allowed_mime():
    """Every allowed attachment MIME has an explicit, inert extension."""
    import mimetypes

    dangerous = {"text/html", "image/svg+xml", "text/javascript",
                 "application/xhtml+xml", "application/xml"}
    for mime in ALLOWED_ATTACH_MIMES:
        ext = attach_ext_for_mime(mime)
        assert ext.startswith("."), mime
        assert mime in ATTACH_MIME_EXT, f"{mime} has no explicit mapping"
        assert mimetypes.guess_type("x" + ext)[0] not in dangerous, (mime, ext)


@pytest.mark.parametrize("mime", ["text/html", "image/svg+xml", "", "application/octet-stream"])
def test_attach_ext_for_mime_never_honours_an_unknown_type(mime):
    assert attach_ext_for_mime(mime) == ".bin"


@pytest.mark.parametrize("data,expected", [
    (PDF_BYTES, "application/pdf"),
    (b"\n\n" + PDF_BYTES, "application/pdf"),          # tolerated leading junk
    (DOC_BYTES, "application/msword"),
    (RTF_BYTES, "application/msword"),
    (DOCX_BYTES,
     "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    (PNG_BYTES, "image/png"),
    (JPEG_BYTES, "image/jpeg"),
    (GIF_BYTES, "image/gif"),
    (WEBP_BYTES, "image/webp"),
    (HTML_PAYLOAD, None),
    (SVG_BYTES, None),
    (b"alert(1)", None),
    (b"", None),
])
def test_sniff_attachment_mime(data, expected):
    assert sniff_attachment_mime(data) == expected


def test_sniff_attachment_mime_recognises_every_allowed_mime():
    """No allowed type may be un-sniffable, or uploading it would 400."""
    samples = {
        "application/pdf": PDF_BYTES,
        "application/msword": DOC_BYTES,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": DOCX_BYTES,
        "image/png": PNG_BYTES,
        "image/jpeg": JPEG_BYTES,
        "image/gif": GIF_BYTES,
        "image/webp": WEBP_BYTES,
    }
    assert set(samples) == ALLOWED_ATTACH_MIMES
    for mime, data in samples.items():
        assert sniff_attachment_mime(data) == mime


# ─── The /nlmedia mount itself ──────────────────────────────────────────────

def _nlmedia_write(subdir, name, content):
    """Drop a file into the real mounted directory (fixed at import time)."""
    import os
    from app.main import _nlmedia_dir

    d = os.path.join(_nlmedia_dir, subdir)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p, "wb") as fh:
        fh.write(content)
    return p


def test_nlmedia_serves_inline_images_inline_and_unauthenticated():
    """Gmail's image proxy has to be able to fetch these and render them.

    No session, no Content-Disposition. If this test starts failing because
    something added a download header here, embedded images stop rendering in
    delivered mail — that is the constraint the whole mount exists for.
    """
    import os

    p = _nlmedia_write("images", "inline_probe.png", PNG_BYTES)
    try:
        c = TestClient(fastapi_app, raise_server_exceptions=False, follow_redirects=False)
        r = c.get("/nlmedia/images/inline_probe.png")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/png"
        assert "content-disposition" not in r.headers
        assert r.headers["x-content-type-options"] == "nosniff"
    finally:
        os.unlink(p)


def test_nlmedia_forces_download_for_attachments():
    """Attachments are emailed as MIME parts and previewed through an auth-gated
    route; their reachability here is an accident of sharing the volume.

    Forcing a download neutralises the whole filename/extension bug class, so
    even a file that somehow lands with an active extension cannot execute.
    """
    import os

    p = _nlmedia_write("attachments", "download_probe.pdf", PDF_BYTES)
    try:
        c = TestClient(fastapi_app, raise_server_exceptions=False, follow_redirects=False)
        r = c.get("/nlmedia/attachments/download_probe.pdf")
        assert r.status_code == 200, r.text
        assert r.headers["content-disposition"] == "attachment"
        assert r.headers["x-content-type-options"] == "nosniff"
    finally:
        os.unlink(p)


def test_nlmedia_stray_html_cannot_execute():
    """Regression net for the bug class, independent of the upload handlers.

    Writing .html directly into the volume simulates any future handler that
    forgets to derive its extension. The mount must still refuse to let a
    browser render it.
    """
    import os

    p = _nlmedia_write("attachments", "stray_probe.html", HTML_PAYLOAD)
    try:
        c = TestClient(fastapi_app, raise_server_exceptions=False, follow_redirects=False)
        r = c.get("/nlmedia/attachments/stray_probe.html")
        assert r.status_code == 200, r.text
        # StaticFiles still types it text/html from the extension — which is
        # exactly why the download header, not the type, is the control here.
        assert r.headers["content-disposition"] == "attachment"
        assert r.headers["x-content-type-options"] == "nosniff"
    finally:
        os.unlink(p)


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
