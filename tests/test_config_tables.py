"""Wave C — app_settings + event_categories + member_statuses services."""

import pytest

from app.services import settings_store as ss
from app.services import taxonomies as tax

pytestmark = pytest.mark.unit


class TestSettingsFallback:
    def test_geo_center_default(self):
        ss.invalidate()
        assert ss.geo_center() == (32.7512, -97.0457)

    def test_scalars(self):
        ss.invalidate()
        assert ss.max_shops() == 2
        assert ss.warno_talk_room() == "atnd3vgf"
        assert ss.tenure_points() == {"gold": 15, "silver": 9, "bronze": 3}
        assert ss.ftx_device_thresholds() == [5, 10, 25, 50]

    def test_typed_getters(self):
        ss.invalidate()
        assert ss.get_int("nonexistent_key", 7) == 7
        assert ss.get_str("nonexistent_key", "x") == "x"
        assert ss.get_json("nonexistent_key", [1]) == [1]


class TestEventCategoriesFallback:
    def test_valid_categories(self):
        tax.invalidate()
        cats = tax.valid_categories()
        assert "ftx" in cats and "mcftx" in cats and len(cats) == 9

    def test_warno_lead(self):
        tax.invalidate()
        assert tax.warno_lead_days()["ftx"] == 14
        assert tax.warno_lead_days()["mcftx"] == 28

    def test_rsvp_defaults(self):
        tax.invalidate()
        r = tax.rsvp_categories()
        assert "ftx" in r and "meeting" not in r

    def test_icons_labels(self):
        tax.invalidate()
        assert tax.category_labels()["ftx"] == "FTX"
        assert tax.category_icons()["ftx"]


class TestMemberStatusesFallback:
    def test_status_options(self):
        tax.invalidate()
        assert set(tax.status_options()) == {"recruit", "active", "inactive", "separated", "blacklisted"}

    def test_lifecycle_buckets(self):
        tax.invalidate()
        assert tax.left_statuses() == {"inactive", "separated", "blacklisted"}
        assert tax.stayed_statuses() == {"active"}
        assert tax.in_progress_statuses() == {"recruit"}

    def test_status_meta_colors(self):
        tax.invalidate()
        assert tax.status_meta()["active"]["color"] == "#4caf50"
