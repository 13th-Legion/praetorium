"""attach_submission_files() — Deck attachment upload.

INCIDENT (2026-08-04 .. 2026-09-03): every attachment upload 400'd for a
month and nobody noticed.

Deck's AttachmentApiController::create is
    create(int $cardId, string $type, string $data)
with NO default on $data. Omitting `data` makes Nextcloud's AppFramework
reject the request with a bare HTTP 400 and an EMPTY body *before the
controller runs* -- nothing reaches nextcloud.log.

test_sends_the_required_data_argument is the regression guard. If it fails,
attachments are silently broken again.
"""

import logging

from conftest import FakeResponse, propfind_response

FORM_HREF = (
    "/remote.php/dav/files/spooky/Forms/"
    "3%20-%20Texas%20State%20Militia%20%e2%80%94%20Application%20%26%20"
    "Background%20Check%20Release/7/"
    "30%20-%20Please%20upload%20copies/dd214.pdf"
)

SUBMISSION = {"id": 7, "answers": [{"fileId": 123, "questionId": 30}]}


def _happy_path(http, attach_status=200):
    """PROPFIND -> download -> attach."""
    http.queue(
        propfind_response(FORM_HREF.rsplit("/", 1)[0] + "/", FORM_HREF),
        FakeResponse(200, b"%PDF-1.4 fake"),
        FakeResponse(attach_status, b'{"id": 99}' if attach_status < 300 else b""),
    )


# ─── THE regression guard ────────────────────────────────────────────────────

def test_sends_the_required_data_argument(daemon, http):
    """`data` must be present and must be the filename. Incident 2 guard."""
    _happy_path(http)
    attached = daemon.attach_submission_files(SUBMISSION, card_id=55, stack_id=11)

    assert attached == 1
    upload = http.calls[-1]
    assert upload.method == "POST"
    assert upload.url.endswith("/cards/55/attachments")

    sent = upload.kwargs["data"]
    assert "data" in sent, (
        "Deck's AttachmentApiController::create has no default on $data; "
        "omitting it produces a bare empty-bodied 400 before the controller runs"
    )
    assert sent["data"] == "dd214.pdf"
    assert sent["type"] == "deck_file"
    assert upload.kwargs["files"]["file"][0] == "dd214.pdf"


def test_filename_is_url_decoded_for_deck(daemon, http):
    href = FORM_HREF.rsplit("/", 1)[0] + "/DD%20214%20%26%20LTC.pdf"
    http.queue(
        propfind_response(href),
        FakeResponse(200, b"data"),
        FakeResponse(200, b"{}"),
    )
    daemon.attach_submission_files(SUBMISSION, card_id=55, stack_id=11)
    assert http.calls[-1].kwargs["data"]["data"] == "DD 214 & LTC.pdf"


# ─── Failure is never silent ─────────────────────────────────────────────────

def test_a_failed_upload_logs_the_body(daemon, http, caplog):
    _happy_path(http, attach_status=400)
    with caplog.at_level(logging.WARNING):
        attached = daemon.attach_submission_files(SUBMISSION, card_id=55, stack_id=11)
    assert attached == 0
    assert "400" in caplog.text
    assert "<empty body>" in caplog.text, (
        "the exact month-long failure mode: a 400 with no body at all"
    )


def test_a_failed_download_is_logged_and_skipped(daemon, http, caplog):
    http.queue(
        propfind_response(FORM_HREF),
        FakeResponse(507, b"insufficient storage"),
    )
    with caplog.at_level(logging.WARNING):
        assert daemon.attach_submission_files(SUBMISSION, card_id=55, stack_id=11) == 0
    assert "insufficient storage" in caplog.text


def test_missing_forms_folder_is_reported_with_the_path(daemon, http, caplog):
    http.queue(FakeResponse(404, b""))
    with caplog.at_level(logging.WARNING):
        assert daemon.attach_submission_files(SUBMISSION, card_id=55, stack_id=11) == 0
    assert "No Forms folder" in caplog.text
    assert "3%20-%20" in caplog.text, "log the path so the prefix bug is visible"


# ─── Listing behaviour ───────────────────────────────────────────────────────

def test_uses_depth_3_because_forms_nests_files_under_a_question_folder(daemon, http):
    """Depth 1 finds only the per-question subfolder, never the files."""
    _happy_path(http)
    daemon.attach_submission_files(SUBMISSION, card_id=55, stack_id=11)
    propfind = http.calls[0]
    assert propfind.method == "PROPFIND"
    assert propfind.kwargs["headers"]["Depth"] == "3"
    assert propfind.url == daemon.forms_submission_url(7)


def test_directories_are_not_treated_as_files(daemon, http):
    http.queue(propfind_response("/dav/sub/7/", "/dav/sub/7/question/"))
    assert daemon.attach_submission_files(SUBMISSION, card_id=55, stack_id=11) == 0


def test_no_file_answers_makes_no_http_calls(daemon, http):
    assert daemon.attach_submission_files({"id": 7, "answers": []}, 55, 11) == 0
    assert daemon.attach_submission_files({"id": 7, "answers": {}}, 55, 11) == 0
    assert http.calls == []


def test_counts_only_the_uploads_that_succeeded(daemon, http):
    a = FORM_HREF.rsplit("/", 1)[0] + "/a.pdf"
    b = FORM_HREF.rsplit("/", 1)[0] + "/b.pdf"
    http.queue(
        propfind_response(a, b),
        FakeResponse(200, b"A"), FakeResponse(201, b"{}"),   # a.pdf ok
        FakeResponse(200, b"B"), FakeResponse(400, b""),     # b.pdf fails
    )
    assert daemon.attach_submission_files(SUBMISSION, card_id=55, stack_id=11) == 1
