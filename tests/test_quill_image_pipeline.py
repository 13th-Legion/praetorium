"""End-to-end contract for Quill inline images: sanitizers + template wiring.

Part C of the upload-on-paste work is an audit: every bleach allowlist that
handles Quill output must let a hosted <img> through, and none of them may
admit `data:`. These tests pin both halves of that so a future allowlist edit
cannot silently reintroduce "the image vanished on save".

Also pins the template wiring, because the client side has no other automated
coverage — see the note at the bottom of this file.
"""

from pathlib import Path

import pytest

from app.routes.announcements import _render_message
from app.routes.events import _clean_description
from app.routes.newsletter import _sanitize
from app.routes.training_library import render_markdown

pytestmark = pytest.mark.unit

APP_DIR = Path(__file__).resolve().parent.parent / "app"
TEMPLATES = APP_DIR / "templates"

HOSTED_URL = "https://portal.13thlegion.org/nlmedia/images/abc123.png"
HOSTED_IMG = (
    f'<p>before</p><p><img src="{HOSTED_URL}" alt="map" width="400" height="300">'
    f"</p><p>after</p>"
)
DATA_URI_IMG = '<p><img src="data:image/png;base64,iVBORw0KGgo=" width="200"></p>'

# Every sanitizer that receives editor HTML. Keep this list in step with the
# editors; a new rich-text surface with its own allowlist belongs here.
SANITIZERS = [
    ("events._clean_description", _clean_description),
    ("announcements._render_message", _render_message),
    ("newsletter._sanitize", _sanitize),
    ("training_library.render_markdown", render_markdown),
]


@pytest.mark.parametrize("name,fn", SANITIZERS, ids=[s[0] for s in SANITIZERS])
def test_hosted_image_survives_every_sanitizer(name, fn):
    """The whole fix depends on this: a hosted https img must round-trip."""
    out = fn(HOSTED_IMG)
    assert "<img" in out, f"{name} dropped the <img> element"
    assert HOSTED_URL in out, f"{name} dropped the src"
    assert 'alt="map"' in out, f"{name} dropped alt"
    assert 'width="400"' in out, f"{name} dropped width"
    assert 'height="300"' in out, f"{name} dropped height"


@pytest.mark.parametrize("name,fn", SANITIZERS, ids=[s[0] for s in SANITIZERS])
def test_data_uri_src_is_stripped_by_every_sanitizer(name, fn):
    """Reproduces the original bug, and pins the decision not to 'fix' it here.

    bleach keeps the <img> but deletes a `data:` src, which is exactly why a
    pasted image vanished. The fix is upload-on-paste, NOT adding `data:` to a
    protocol allowlist — that bloats the stored row and re-admits
    data:image/svg+xml as a script vector.
    """
    out = fn(DATA_URI_IMG)
    assert "data:" not in out, f"{name} now admits data: URIs — do not do this"


@pytest.mark.parametrize("name,fn", SANITIZERS, ids=[s[0] for s in SANITIZERS])
def test_script_and_svg_never_survive(name, fn):
    out = fn('<p>hi<script>alert(1)</script><svg onload="alert(1)"></svg></p>')
    assert "<script" not in out
    assert "<svg" not in out
    assert "onload" not in out


def test_no_sanitizer_declares_the_data_protocol():
    """Belt to the round-trip suspenders, checked against the real allowlists.

    Only newsletter passes an explicit `protocols=`; the other three rely on
    bleach's default (http/https/mailto). Both routes to allowing `data:` are
    pinned here.
    """
    import bleach

    from app.routes.newsletter import ALLOWED_PROTOCOLS

    assert "data" not in ALLOWED_PROTOCOLS
    assert set(ALLOWED_PROTOCOLS) == {"http", "https", "mailto"}
    assert "data" not in set(bleach.sanitizer.ALLOWED_PROTOCOLS)


# ─── Client wiring ───────────────────────────────────────────────────────────
#
# NOTE ON COVERAGE: these are static assertions over the shipped templates and
# JS. They prove the module is loaded and invoked for every editor, and that it
# contains the paste/drop interception. They do NOT prove that a real browser
# paste is intercepted before Quill inserts base64 — that needs a real browser
# and was not performed.

SHARED_JS = APP_DIR / "static" / "js" / "quill-image-upload.js"

# Templates that construct a Quill editor, and the endpoint each should target.
EDITOR_TEMPLATES = {
    "pages/events.html": "/api/media/image-upload",
    "pages/event_detail.html": "/api/media/image-upload",
    "pages/newsletter_edit.html": "/api/s1/newsletter/image-upload",
    "pages/s1_email_blast.html": "/api/s1/newsletter/image-upload",
}


def test_shared_module_exists_and_is_loaded_by_base_template():
    assert SHARED_JS.is_file(), "shared Quill upload module is missing"
    base = (TEMPLATES / "base.html").read_text()
    assert "/static/js/quill-image-upload.js" in base, (
        "base.html must load the shared module so every editor page has it"
    )


@pytest.mark.parametrize("template,endpoint", sorted(EDITOR_TEMPLATES.items()))
def test_every_quill_editor_attaches_the_shared_upload_module(template, endpoint):
    src = (TEMPLATES / template).read_text()
    assert "new Quill(" in src, f"{template} no longer builds a Quill editor"
    assert "attachQuillImageUpload(" in src, (
        f"{template} builds a Quill editor but never attaches image upload — "
        f"paste would fall back to base64 and vanish on save"
    )
    assert endpoint in src, f"{template} should upload to {endpoint}"


@pytest.mark.parametrize("template", sorted(EDITOR_TEMPLATES))
def test_no_template_keeps_a_bespoke_image_upload_handler(template):
    """One implementation only — the old per-template handlers are gone."""
    src = (TEMPLATES / template).read_text()
    assert "addHandler('image'" not in src, (
        f"{template} still has a bespoke toolbar image handler; it should use "
        f"the shared module instead"
    )


def test_every_template_with_a_quill_editor_is_covered_by_this_test():
    """Fails when a new Quill editor is added without image upload wiring."""
    found = {
        str(p.relative_to(TEMPLATES))
        for p in TEMPLATES.rglob("*.html")
        if "new Quill(" in p.read_text()
    }
    assert found == set(EDITOR_TEMPLATES), (
        f"Quill editors changed. Found {sorted(found)}, "
        f"expected {sorted(EDITOR_TEMPLATES)}. Wire up any new editor with "
        f"attachQuillImageUpload() and add it to EDITOR_TEMPLATES."
    )


def test_shared_module_intercepts_paste_drop_toolbar_and_quill_uploader():
    js = SHARED_JS.read_text()
    assert 'addEventListener("paste"' in js, "no paste interception"
    assert 'addEventListener("drop"' in js, "no drop interception"
    assert "clipboardData" in js
    assert "dataTransfer" in js
    assert "preventDefault()" in js, "base64 could still reach the document"
    # Quill's own uploader module is what inserts the base64; it must be
    # overridden too, in both Quill 1.3.x and 2.x.
    assert 'getModule("uploader")' in js, "Quill's base64 uploader not overridden"
    assert 'addHandler("image"' in js, "toolbar image button not handled"
    # Listeners must capture, or Quill's root-bound handlers run first.
    assert "}, true);" in js, "paste/drop listeners must use the capture phase"


def test_shared_module_never_fails_silently():
    """Silent failure is the exact bug being fixed."""
    js = SHARED_JS.read_text()
    assert "function fail(" in js
    assert ".catch(" in js, "upload rejections must be handled"
    assert "console.error" in js


def test_shared_module_client_limits_match_the_server():
    """Client-side pre-checks must not drift from the server's rules.

    Parses the actual ALLOWED_MIMES array rather than searching the whole file,
    so prose in the header comment cannot satisfy or break the assertion.
    """
    import re

    from app.newsletter_assets import ALLOWED_IMAGE_MIMES, MAX_IMAGE_BYTES

    js = SHARED_JS.read_text()

    match = re.search(r"var ALLOWED_MIMES = \[(.*?)\];", js, re.S)
    assert match, "could not find the client ALLOWED_MIMES array"
    client_mimes = set(re.findall(r'"([^"]+)"', match.group(1)))

    assert client_mimes == set(ALLOWED_IMAGE_MIMES), (
        "client image allowlist has drifted from ALLOWED_IMAGE_MIMES"
    )
    assert "image/svg+xml" not in client_mimes, "SVG must stay excluded"
    assert f"{MAX_IMAGE_BYTES // (1024 * 1024)} * 1024 * 1024" in js
