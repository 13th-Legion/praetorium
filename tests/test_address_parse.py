"""One-line address parsing — used by the recruit daemon AND the portal.

The daemon used to comma-split 'Street, City, State ZIP'. Application
addresses are a single text line with no commas, so city/state/zip landed
empty on every onboard (Garcia, Corsello, and the rest of the 2026-09
pipeline). These tests pin the real production strings.
"""
from app.address_parse import parse_oneline_address


def _p(raw):
    return parse_oneline_address(raw)


class TestCommaForm:
    def test_standard_us(self):
        d = _p("3637 East Trinity Mills Road, Dallas, TX 75287")
        assert d["street"] == "3637 East Trinity Mills Road"
        assert d["city"] == "Dallas"
        assert d["state"] == "TX"
        assert d["zip"] == "75287"

    def test_apt_and_two_word_city(self):
        d = _p("520 Samuels Avenue, APT 3211, Fort Worth, TX 76102")
        assert d["city"] == "Fort Worth"
        assert d["zip"] == "76102"
        assert "Samuels" in d["street"]

    def test_comma_before_zip(self):
        d = _p("700 Ipswich Avenue, APT 54108, Fort Worth, TX, 76131")
        assert d["city"] == "Fort Worth"
        assert d["state"] == "TX"
        assert d["zip"] == "76131"


class TestNoCommaProduction:
    """The actual strings sitting in members.address today."""

    def test_garcia_lovers_ln(self):
        d = _p("7821 Lovers Ln Dallas Tx 75225")
        assert d["street"] == "7821 Lovers Ln"
        assert d["city"] == "Dallas"
        assert d["state"] == "TX"
        assert d["zip"] == "75225"

    def test_corsello_unit_then_city(self):
        d = _p("5734 Moore Park Rd unit 2 Abilene TX 79601")
        assert d["street"] == "5734 Moore Park Rd unit 2"
        assert d["city"] == "Abilene"
        assert d["state"] == "TX"
        assert d["zip"] == "79601"

    def test_miller_style_dr_then_city(self):
        d = _p("719 Cougar Dr Allen TX 75013")
        assert d["street"] == "719 Cougar Dr"
        assert d["city"] == "Allen"
        assert d["state"] == "TX"
        assert d["zip"] == "75013"

    def test_two_word_city_after_suffix(self):
        d = _p("1400 Brimwood Dr Fort Worth TX 76131")
        assert d["street"] == "1400 Brimwood Dr"
        assert d["city"] == "Fort Worth"
        assert d["zip"] == "76131"


class TestConservative:
    def test_no_state_no_zip_left_as_street(self):
        d = _p("14000 Aston Falls Dr")
        assert d["street"] == "14000 Aston Falls Dr"
        assert d["city"] is None
        assert d["zip"] is None

    def test_no_suffix_does_not_steal_street_name_as_city(self):
        """Bergener: last-token-as-city turned '4315 Woodmeadow TX' into
        street=4315, city=Woodmeadow. A street suffix is the only safe break."""
        d = _p("4315 Woodmeadow TX")
        assert d["street"] == "4315 Woodmeadow"
        assert d["city"] is None
        assert d["state"] == "TX"

    def test_street_suffix_at_end_is_not_a_city(self):
        d = _p("324 Eugenia St. TX")
        assert d["city"] is None
        assert "Eugenia" in d["street"]

    def test_empty(self):
        d = _p("")
        assert d == {"street": None, "city": None, "state": None, "zip": None}

    def test_geo_reexport(self):
        from app.address_parse import parse_oneline_address as ap
        from app.geo import parse_oneline_address as geo_parse
        assert geo_parse is ap
        d = geo_parse("7821 Lovers Ln Dallas Tx 75225")
        assert d["city"] == "Dallas"
        assert d["zip"] == "75225"
