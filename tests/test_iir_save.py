"""IIR save endpoint — publish must not 500 (regression: NULL report_number)."""

import pytest
from sqlalchemy import select

from app.models.s2_intel import IIR
from tests.factories import make_member

pytestmark = pytest.mark.integration


def _csrf(c) -> str:
    tok = c.cookies.get("csrftoken")
    if not tok:
        c.get("/health")
        tok = c.cookies.get("csrftoken")
    assert tok, "no csrftoken cookie was issued"
    return tok


class TestIirSave:
    async def test_publish_new_iir_assigns_report_number(
        self, auth_client, db_session, patch_global_session
    ):
        """Publishing a new IIR must INSERT with a report_number, not NULL.

        Regression: the create path called db.flush() before stamping
        "IIR-000N", and report_number is NOT NULL — so every publish 500'd.
        """
        member = await make_member(db_session, nc_username="test.soldier")
        await db_session.commit()

        tok = _csrf(auth_client)
        resp = auth_client.post(
            "/api/s2/iir/save",
            data={
                "subject": "Enemy contact report",
                "country_area": "DFW AO",
                "source": "Patrol observation",
                "reliability_rating": "B",
                "credibility": "Confirmed",
                "dissemination_tier": "command_only",
                "remarks": "test",
                "details": "<p>Two vehicles observed.</p>",
                "assessment": "<p>Low threat.</p>",
                "csrf_token": tok,
            },
            headers={"X-CSRF-Token": tok},
        )
        assert resp.status_code == 302, resp.text

        row = (await db_session.execute(
            select(IIR).order_by(IIR.id.desc()).limit(1)
        )).scalar_one()
        assert row.report_number == f"IIR-{row.id:04d}"
        assert row.author_id == member.id

    async def test_publish_without_required_fields_is_400(
        self, auth_client, db_session, patch_global_session
    ):
        await make_member(db_session, nc_username="test.soldier")
        await db_session.commit()

        tok = _csrf(auth_client)
        resp = auth_client.post(
            "/api/s2/iir/save",
            data={"subject": "", "country_area": "", "details": "", "csrf_token": tok},
            headers={"X-CSRF-Token": tok},
        )
        assert resp.status_code == 400
