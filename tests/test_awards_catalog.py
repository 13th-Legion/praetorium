"""Awards catalog helpers and the Personnel menu link."""

from pathlib import Path

import pytest

from app.routes.awards_catalog import image_path

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def test_image_path_uses_catalog_art_when_set():
    assert image_path("dona", "phalerae", "dona/phalerae.png") == "dona/phalerae.png"


def test_image_path_falls_back_by_section():
    assert image_path("rack", "ftx", None) == "rack/ftx.png"
    assert image_path("tab", "sabre", "") == "tabs/sabre.png"


def test_tenure_art_lives_with_the_discs():
    assert image_path("tenure", "anni_gold", None) == "discs/anni_gold.png"


def test_personnel_menu_links_the_catalog():
    nav = (ROOT / "app/templates/base.html").read_text()
    personnel = nav.split("Personnel ▾", 1)[1].split("Events", 1)[0]
    assert 'href="/awards"' in personnel


def test_ribbons_are_one_tagged_surface():
    """The whole decorations block is the future per-unit creation boundary."""
    from app.models.ribbons import UNIT_RIBBON_SURFACE

    profile = (ROOT / "app/templates/pages/profile.html").read_text()
    awards = (ROOT / "app/templates/pages/awards_catalog.html").read_text()
    marker = f'data-unit-surface="{UNIT_RIBBON_SURFACE}"'
    assert profile.count(marker) == 1
    assert 'id="unit-ribbons"' in profile
    assert marker in awards
    assert "What these are" in profile
    assert "Decorations & Service <a" in profile
