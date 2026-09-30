"""Block 3 comms lessons render in the BRM wrapper, with a PDF export."""

def test_comms_pages_anonymous_redirect(client):
    for slug in ("radio-familiarity", "net-etiquette", "reports"):
        resp = client.get(f"/training/tradoc/{slug}")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]


def test_comms_pages_match_the_brm_wrapper(auth_client):
    expect = {
        "radio-familiarity": ("Radio Familiarity", "SCUBADIVER", "/static/tradoc/radio-familiarity.pdf"),
        "net-etiquette": ("Net Etiquette", "Niner", "/static/tradoc/net-etiquette.pdf"),
        "reports": ("Reports", "MIST", "/static/tradoc/reports.pdf"),
    }
    for slug, (title, phrase, pdf) in expect.items():
        resp = auth_client.get(f"/training/tradoc/{slug}")
        assert resp.status_code == 200, slug
        assert title in resp.text
        assert "Block 3: Supplemental Skills" in resp.text
        assert phrase in resp.text
        assert f'href="{pdf}"' in resp.text
        assert "Download PDF" in resp.text
