"""Feedback form submissions become one Deck card, without the raw user id."""

from app.services.feedback_deck import card_from_submission


QUESTIONS = [
    {"id": 1, "text": "Unit name / metro"},
    {"id": 2, "text": "Type"},
    {"id": 3, "text": "Severity"},
    {"id": 4, "text": "What happened"},
]


def test_feedback_page_is_public(client):
    resp = client.get("/feedback")
    assert resp.status_code == 200
    assert "Do not paste" in resp.text
    assert "apps/forms/s/7dcrKqMENnCjdjaG" in resp.text
    assert 'class="feedback-fab"' in resp.text


def test_card_names_the_unit_and_keeps_the_marker():
    title, body = card_from_submission({
        "id": 44,
        "userId": "anon-user-secret",
        "answers": [
            {"questionId": 1, "text": "DFW"},
            {"questionId": 2, "text": "Bug"},
            {"questionId": 3, "text": "Annoying"},
            {"questionId": 4, "text": "Roster page 500s"},
        ],
    }, QUESTIONS)
    assert title == "Bug: DFW (Annoying)"
    assert body.startswith("forms-submission:9:44")
    assert "Roster page 500s" in body
    assert "anon-user-secret" not in title
    assert "anon-user-secret" not in body
