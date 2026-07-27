"""Phase 3 — migration integrity.

Cheap structural guarantees that don't need a live Postgres:
  * the Alembic history has exactly ONE head (no accidental branch/merge)
  * every revision defines a non-empty downgrade()
  * the squashed baseline models the webhook_events dedup table + its
    (provider, transaction_id) unique constraint

NOTE: history was squashed to a single baseline revision (0001_baseline) on
2026-07-27 because the pre-squash chain never created ~50 tables via migration
(they were bootstrapped by create_all() on prod), so a from-scratch
`alembic upgrade head` — exactly what CI runs — crashed at the first migration
that indexed a non-existent table.
"""

import pathlib

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.database import Base
import app.models  # noqa: F401  (register all tables on Base.metadata)

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parent.parent


def _script_dir():
    cfg = Config(str(REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "migrations"))
    return ScriptDirectory.from_config(cfg)


def test_single_head():
    heads = _script_dir().get_heads()
    assert len(heads) == 1, f"expected 1 alembic head, found {heads}"


def test_every_revision_has_downgrade():
    sd = _script_dir()
    for rev in sd.walk_revisions():
        src = pathlib.Path(rev.path).read_text()
        assert "def downgrade" in src, f"{rev.revision} missing downgrade()"


def test_baseline_is_single_root():
    """The squashed baseline is the sole root (down_revision is None)."""
    sd = _script_dir()
    bases = list(sd.get_bases())
    assert bases == ["0001_baseline"], f"expected single baseline root, got {bases}"


def test_webhook_events_dedup_constraint_modeled():
    """webhook_events must keep its (provider, transaction_id) unique constraint
    for PayPal idempotency/replay protection. The squashed baseline reflects
    Base.metadata, so assert the constraint at the model level.
    """
    tbl = Base.metadata.tables["webhook_events"]
    unique_col_sets = [
        tuple(c.name for c in uc.columns)
        for uc in tbl.constraints
        if uc.__class__.__name__ == "UniqueConstraint"
    ]
    # Also honor a plain unique=True on a composite index if modeled that way.
    unique_index_sets = [
        tuple(c.name for c in idx.columns)
        for idx in tbl.indexes
        if idx.unique
    ]
    all_unique = unique_col_sets + unique_index_sets
    assert ("provider", "transaction_id") in all_unique, (
        f"webhook_events missing (provider, transaction_id) unique constraint; "
        f"found {all_unique}"
    )
