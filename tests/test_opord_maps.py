"""Training-site maps must reach the published OPORD.

The maps were already chosen in the S3 event builder — `s3_ops` renders them
via `get_site_maps` — but nothing carried them into the OPORD that actually
goes out. So the people who needed them, everyone driving to the site, got an
operations order with no map in it. Reported 2026-09-11, the day of an FTX at
Site Dog.

Links are absolute and point at /static, which is served without auth, so they
open straight from the email instead of bouncing the reader through a login.
"""

import types

import pytest

from app.routes.events import _site_maps_html, _site_maps_text
from app.training_sites import TRAINING_SITES, get_site_maps

pytestmark = pytest.mark.unit

PORTAL = "https://portal.13thlegion.org"


def _event(site):
    return types.SimpleNamespace(training_site=site)


@pytest.mark.parametrize("site_key", sorted(TRAINING_SITES))
def test_every_site_renders_all_of_its_maps(site_key):
    """Whatever the builder offers for a site must appear in the OPORD."""
    html = _site_maps_html(_event(site_key))
    text = _site_maps_text(_event(site_key))
    expected = get_site_maps(site_key)
    assert expected, f"{site_key} has no maps configured"
    for m in expected:
        assert m["url"] in html, f"{m['label']} missing from OPORD html"
        assert m["url"] in text, f"{m['label']} missing from Talk message"
        assert m["label"] in html
        assert m["label"] in text


@pytest.mark.parametrize("site_key", sorted(TRAINING_SITES))
def test_links_are_absolute(site_key):
    """A relative /static path is useless in an email client."""
    html = _site_maps_html(_event(site_key))
    text = _site_maps_text(_event(site_key))
    for m in get_site_maps(site_key):
        assert f'{PORTAL}{m["url"]}' in html
        assert f'{PORTAL}{m["url"]}' in text
    assert 'href="/static' not in html, "relative link would not resolve in email"


def test_site_name_and_address_are_shown():
    html = _site_maps_html(_event("dog"))
    assert "Dog" in html
    assert TRAINING_SITES["dog"]["nickname"] in html
    assert TRAINING_SITES["dog"]["address"] in html


def test_dog_is_the_ftx_site_and_carries_both_scales():
    """Event 73 (11 SEP FTX, Hico) is site 'dog'. Regression anchor."""
    urls = [m["url"] for m in get_site_maps("dog")]
    assert any("Dog_10000" in u for u in urls)
    assert any("Dog_25000" in u for u in urls)
    html = _site_maps_html(_event("dog"))
    assert html.count("<li") == len(urls)


@pytest.mark.parametrize("site", [None, "", "nonexistent"])
def test_no_site_renders_nothing(site):
    """An online meeting or an unset site must not emit an empty MAPS heading."""
    assert _site_maps_html(_event(site)) == ""
    assert _site_maps_text(_event(site)) == ""


def test_marked_variants_are_labelled_distinctly():
    """Baker and Easy ship 'marked' overlays; the labels must differ so the
    reader can tell which sheet is which."""
    labels = [m["label"] for m in get_site_maps("baker")]
    assert len(labels) == len(set(labels)), "duplicate map labels"
    assert any("Marked" in l for l in labels)
