"""Newsletter save endpoint — must not 422 on a valid draft/send.

Regression: `app/routes/newsletter.py` had `from __future__ import annotations`.
Combined with the `@require_auth` decorator (which wraps the handler with
`functools.wraps` in `app/auth.py`), FastAPI resolved the string annotations
against the *wrapper's* globals — where `BackgroundTasks` and `AsyncSession`
are not imported — leaving them as unresolved `ForwardRef`s. FastAPI then
treated `background_tasks` as a required body param and every POST to
`/api/s1/newsletter/save` returned 422 Unprocessable Content, so no newsletter
could ever be saved or sent.
"""

import pytest

from app.models.newsletter import Newsletter

pytestmark = pytest.mark.integration


def _csrf(c) -> str:
    tok = c.cookies.get("csrftoken")
    if not tok:
        c.get("/health")
        tok = c.cookies.get("csrftoken")
    assert tok, "no csrftoken cookie was issued"
    return tok


class TestNewsletterSave:
    def test_save_draft_is_not_422(self, auth_client, patch_global_session):
        """A valid draft save must persist, not 422.

        The `background_tasks: BackgroundTasks` + `db: AsyncSession` params on
        `newsletter_save` must resolve to their real types (not ForwardRefs) or
        FastAPI rejects the request before the handler runs.
        """
        tok = _csrf(auth_client)
        resp = auth_client.post(
            "/api/s1/newsletter/save",
            data={
                "title": "Legionary Dispatch — Test",
                "subject": "Test Dispatch",
                "crest_key": "standard",
                "body_html": "<p>Hello world.</p>",
                "groups": "",
                "action": "draft",
                "csrf_token": tok,
            },
            headers={"X-CSRF-Token": tok},
        )
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body.get("id"), f"expected a newsletter id, got {body}"
        assert body.get("status") == "draft"

    def test_send_requires_groups(self, auth_client, patch_global_session):
        """Send without a recipient group is a clean 400, not a 422/500."""
        tok = _csrf(auth_client)
        resp = auth_client.post(
            "/api/s1/newsletter/save",
            data={
                "title": "Legionary Dispatch — Test",
                "subject": "Test Dispatch",
                "crest_key": "standard",
                "body_html": "<p>Hello world.</p>",
                "groups": "",
                "action": "send",
                "csrf_token": tok,
            },
            headers={"X-CSRF-Token": tok},
        )
        assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text}"

    def test_missing_title_is_400(self, auth_client, patch_global_session):
        tok = _csrf(auth_client)
        resp = auth_client.post(
            "/api/s1/newsletter/save",
            data={"title": "", "subject": "x", "action": "draft", "csrf_token": tok},
            headers={"X-CSRF-Token": tok},
        )
        assert resp.status_code == 400
