"""archive_applicant_files() — moving applicant documents to [S-1] Admin.

Replaces move_applicant_files(), which had THREE defects and had therefore
never once succeeded (20 'No Forms folder yet' log lines back to 2026-03-07,
zero successful moves, empty Applications/ folder):

  1. built the Forms path without the '3 - ' form-id prefix -> instant 404
  2. iterated EVERY submission folder under the form root, using the
     applicant name only for the DESTINATION -- had the path ever resolved,
     the first onboarding would have swept all 39+ applicants' documents into
     one person's folder
  3. used Depth 1, but Forms nests uploads inside a per-question subfolder,
     so it would have found zero files even with the path fixed

test_only_touches_the_given_submission is the guard for defect 2 -- the one
that would have been a data-mixing incident rather than a no-op.
"""

import logging

from conftest import FakeResponse, propfind_response

SUB = 7
BASE = (
    "/remote.php/dav/files/spooky/Forms/"
    "3%20-%20Texas%20State%20Militia%20%e2%80%94%20Application%20%26%20"
    "Background%20Check%20Release"
)
FILE_A = f"{BASE}/{SUB}/30%20-%20uploads/dd214.pdf"
FILE_B = f"{BASE}/{SUB}/30%20-%20uploads/ltc.jpg"


# ─── Scoping: the dangerous bug ──────────────────────────────────────────────

def test_only_touches_the_given_submission(daemon, http):
    """Never widen to the form root: that is everyone else's documents."""
    http.queue(
        propfind_response(FILE_A),
        FakeResponse(201, b""),   # MKCOL
        FakeResponse(201, b""),   # COPY
    )
    daemon.archive_applicant_files("John Garcia", SUB)

    propfind = http.calls[0]
    assert propfind.method == "PROPFIND"
    assert propfind.url == daemon.forms_submission_url(SUB)
    assert propfind.url.endswith(f"/{SUB}")
    assert propfind.url != daemon.forms_root_url(), (
        "listing the form root would sweep in every other applicant"
    )


def test_uses_depth_3_to_reach_files_under_the_question_folder(daemon, http):
    http.queue(propfind_response(FILE_A), FakeResponse(201, b""), FakeResponse(201, b""))
    daemon.archive_applicant_files("John Garcia", SUB)
    assert http.calls[0].kwargs["headers"]["Depth"] == "3"


def test_a_missing_submission_id_does_nothing_at_all(daemon, http, caplog):
    with caplog.at_level(logging.WARNING):
        assert daemon.archive_applicant_files("John Garcia", None) == 0
    assert http.calls == [], "no submission id means no idea whose files these are"
    assert "submission id" in caplog.text.lower()


# ─── Happy path ──────────────────────────────────────────────────────────────

def test_archives_every_file_into_the_applicant_folder(daemon, http):
    http.queue(
        propfind_response(FILE_A, FILE_B),
        FakeResponse(201, b""),   # MKCOL
        FakeResponse(201, b""),   # file A
        FakeResponse(204, b""),   # file B (overwrote)
    )
    assert daemon.archive_applicant_files("John Garcia", SUB) == 2

    dest = daemon.applications_folder_url("John Garcia")
    assert http.calls[1].method == "MKCOL"
    assert http.calls[1].url == dest
    for call in http.calls[2:]:
        assert call.kwargs["headers"]["Destination"].startswith(dest + "/")


def test_defaults_to_move_matching_the_backfill(daemon, http):
    """MOVE preserves the Nextcloud fileId, and Forms references uploads by
    fileId, so archived files keep resolving. Verified on production: all 17
    backfilled files kept their fileIds and are still referenced by Forms.

    It must also match scripts/backfill_s1_archive.py, which MOVEd the
    backlog -- a COPYing live path would leave a duplicate behind for every
    new applicant.
    """
    http.queue(propfind_response(FILE_A), FakeResponse(201, b""), FakeResponse(201, b""))
    daemon.archive_applicant_files("John Garcia", SUB)
    assert daemon.ARCHIVE_VERB == "MOVE"
    assert http.calls[-1].method == "MOVE"


def test_destination_header_keeps_the_url_encoded_filename(daemon, http):
    href = f"{BASE}/{SUB}/30%20-%20uploads/DD%20214%20%26%20LTC.pdf"
    http.queue(propfind_response(href), FakeResponse(201, b""), FakeResponse(201, b""))
    daemon.archive_applicant_files("John Garcia", SUB)
    dest = http.calls[-1].kwargs["headers"]["Destination"]
    assert dest.endswith("/DD%20214%20%26%20LTC.pdf"), "must not double-encode or decode"


def test_existing_destination_folder_is_fine(daemon, http, caplog):
    """MKCOL on an existing collection returns 405; that is not an error."""
    http.queue(
        propfind_response(FILE_A),
        FakeResponse(405, b"method not allowed"),   # MKCOL, already exists
        FakeResponse(201, b""),
    )
    with caplog.at_level(logging.WARNING):
        assert daemon.archive_applicant_files("John Garcia", SUB) == 1
    assert "405" not in caplog.text, "405 from MKCOL must not be logged as a failure"


# ─── Failures are visible ────────────────────────────────────────────────────

def test_missing_forms_folder_is_reported_with_the_path(daemon, http, caplog):
    http.queue(FakeResponse(404, b""))
    with caplog.at_level(logging.WARNING):
        assert daemon.archive_applicant_files("John Garcia", SUB) == 0
    assert "3%20-%20" in caplog.text


def test_a_failed_copy_logs_the_body_and_is_not_counted(daemon, http, caplog):
    http.queue(
        propfind_response(FILE_A, FILE_B),
        FakeResponse(201, b""),
        FakeResponse(201, b""),                        # A ok
        FakeResponse(507, b"quota exceeded"),          # B fails
    )
    with caplog.at_level(logging.WARNING):
        assert daemon.archive_applicant_files("John Garcia", SUB) == 1
    assert "quota exceeded" in caplog.text


def test_a_submission_with_no_files_is_not_an_error(daemon, http, caplog):
    http.queue(propfind_response(f"{BASE}/{SUB}/"))
    with caplog.at_level(logging.WARNING):
        assert daemon.archive_applicant_files("John Garcia", SUB) == 0
    assert caplog.text == ""


def test_move_applicant_files_is_gone(daemon):
    """All three legacy path builders were deleted, not left lying around."""
    assert not hasattr(daemon, "move_applicant_files")
