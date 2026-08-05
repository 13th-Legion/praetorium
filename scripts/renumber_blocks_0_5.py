"""Swap TRADOC Block 0 <-> Block 5 numbering + rename the field-tasks block.

Cav request: In-Processing (currently Block 5, the new-member onboarding block)
should be Block 0 (the FIRST thing a member does), and the recurring field
tasks (currently Block 0 "Every FTX": FOB Setup, Guard Duty, Stand-To) should
be Block 5 and get a better name.

IMPORTANT: block number 0 has historically been the "always-credited field
tasks" semantic flag in events.py finalize logic. That semantic is being moved
to FIELD_TASKS_BLOCK (=5) in code in the SAME deploy. This script only handles
the DB side:
  - TradocBlock.number swap (0<->5) via a temp number to dodge the unique index
  - TradocItem.block + block_name denorm swap
  - TradocBlock.sort_order so 0 sorts first / 5 sorts last
  - rename the field-tasks block
  - re-tag NMO-series events: training_blocks "5" -> "0"

Idempotent-ish: safe to re-run (detects already-swapped state by name).
Dry-run by default; pass --commit to write.
"""
import asyncio
import sys
from sqlalchemy import select, update
from app.database import async_session
from app.models.training import TradocBlock, TradocItem
from app.models.events import Event

FIELD_TASKS_NAME = "Field Standing Tasks"
TEMP = 999


async def main(commit: bool):
    async with async_session() as db:
        blocks = {b.number: b for b in (await db.execute(select(TradocBlock))).scalars().all()}
        b0 = blocks.get(0)
        b5 = blocks.get(5)
        if not b0 or not b5:
            print(f"Expected blocks 0 and 5; found numbers {sorted(blocks)}. Abort.")
            return

        print(f"BEFORE: block0 name={b0.name!r} sort={b0.sort_order}  |  block5 name={b5.name!r} sort={b5.sort_order}")

        # Guard against double-run: if block 0 is already In-Processing, bail.
        if (b0.name or "").strip().lower() == "in-processing":
            print("Block 0 already = In-Processing; looks already swapped. Nothing to do.")
            return

        # --- swap TradocBlock.number 0 <-> 5 via temp ---
        b0.number = TEMP
        await db.flush()
        b5.number = 0
        await db.flush()
        b0.number = 5
        await db.flush()

        # --- rename the field-tasks block (old block 0, now number 5) ---
        old_field_name = b0.name
        b0.name = FIELD_TASKS_NAME

        # --- sort order: In-Processing (now 0) first, field-tasks (now 5) last ---
        b5.sort_order = 0      # In-Processing shows first
        b0.sort_order = 5      # Field Standing Tasks shows last (after 1-4)

        # --- swap TradocItem.block for the two blocks (temp dance) ---
        # items currently block 0 -> 5 ; items currently block 5 -> 0
        await db.execute(update(TradocItem).where(TradocItem.block == 0).values(block=TEMP))
        await db.execute(update(TradocItem).where(TradocItem.block == 5).values(block=0, block_name="In-Processing"))
        await db.execute(update(TradocItem).where(TradocItem.block == TEMP).values(block=5, block_name=FIELD_TASKS_NAME))

        # --- re-tag NMO-series events: training_blocks that reference "5" -> "0" ---
        # NMO events were tagged training_blocks="5" (PP-290). In-Processing is now block 0.
        evs = (await db.execute(select(Event).where(Event.training_blocks.isnot(None)))).scalars().all()
        retagged = 0
        for ev in evs:
            parts = [p.strip() for p in (ev.training_blocks or "").split(",") if p.strip()]
            if "5" in parts:
                # This tag pointed at old block 5 (In-Processing) -> now block 0.
                new = ["0" if p == "5" else p for p in parts]
                # de-dupe while preserving order
                seen = set(); new = [x for x in new if not (x in seen or seen.add(x))]
                ev.training_blocks = ",".join(new)
                if ev.training_block == 5:
                    ev.training_block = 0
                retagged += 1
        print(f"Re-tagged {retagged} event(s) training_blocks 5 -> 0 (NMO series).")

        print(f"AFTER:  block(number=0) name='In-Processing' sort=0  |  block(number=5) name={FIELD_TASKS_NAME!r} sort=5 (was {old_field_name!r})")

        if commit:
            await db.commit()
            print("COMMITTED.")
        else:
            await db.rollback()
            print("DRY-RUN (no changes). Re-run with --commit.")


if __name__ == "__main__":
    asyncio.run(main("--commit" in sys.argv))
