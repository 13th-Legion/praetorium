"""Phase 3 — migration integrity.

Cheap structural guarantees that don't need a live Postgres:
  * the Alembic history has exactly ONE head (no accidental branch/merge)
  * every revision defines a non-empty downgrade()
  * the new webhook_events migration up/downgrades cleanly on a scratch DB
"""

import importlib.util
import pathlib

import pytest
from sqlalchemy import create_engine, inspect
from alembic.config import Config
from alembic.script import ScriptDirectory

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


def test_webhook_migration_roundtrip(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "wh_mig", REPO / "migrations/versions/k9l0m1n2o3p4_webhook_events.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from unittest import mock

    db = tmp_path / "m.db"
    eng = create_engine(f"sqlite:///{db}")

    with eng.begin() as conn:
        op = Operations(MigrationContext.configure(conn))
        with mock.patch.object(mod, "op", op):
            mod.upgrade()
    with eng.connect() as conn:
        insp = inspect(conn)
        assert "webhook_events" in insp.get_table_names()
        uq = insp.get_unique_constraints("webhook_events")
        assert any(u["column_names"] == ["provider", "transaction_id"] for u in uq)

    with eng.begin() as conn:
        op = Operations(MigrationContext.configure(conn))
        with mock.patch.object(mod, "op", op):
            mod.downgrade()
    with eng.connect() as conn:
        assert "webhook_events" not in inspect(conn).get_table_names()
