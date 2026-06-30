"""PP-232 seed: Advanced Qualifications & Tabs block + 4 tab subjects.

Idempotent: safe to re-run. Creates/updates the advanced-tier block and the
Sabre / Marksman / Sharpshooter (summary + Battle Library link) and Equites
(full authored markdown) subjects.
"""
import asyncio
from sqlalchemy import select, func
from app.database import async_session
from app.models.training import TradocBlock, TradocItem

ADVANCED_BLOCK_NUMBER = 100  # out of the 0-4 patching range; advanced tier
ADVANCED_BLOCK_NAME = "Advanced Qualifications & Tabs"

EQUITES_BODY = """\
## Equites

In ancient Rome an *Eques* was a knight in service to the Legions who occupied high ranks and positions due to their more rigorous training and skill level. The order of knights itself was referred to as the *Equites*.

Earning the **Equites** tab requires demonstrations of martial skill and physical fitness **beyond our patching requirements**. The program is broken into three categories. This is a **13th Legion internal** tab.

---

### Category 1 — Praecisio Teli
- Passed weapons qualification.

---

### Category 2 — Probatio Virium (Physical Fitness)

Choose **one** cardio event:

- **Option 1 — 1-mile run in 9:00.** Must take place on a track or outdoor route. **Treadmills are forbidden.**
- **Option 2 — 3-mile ruck in 60:00.** Full kit (uniform, load-bearing vest or plate carrier, ruck, rifle).

Plus **both** strength events:

- **Farmer's carry — 140 lbs** (50 lb hex bar with 2×45 lb plates, or homemade equivalent) over **50 m/yd (2×25)**. Time limit **1:30**.

![Farmer's carry equipment — loaded hex/trap bar with 45 lb plates](/static/tradoc/images/equites-farmers-carry.jpg)
*Example farmer's carry setup: hex/trap bar loaded with 45 lb plates. Your evidence video must show visible proof of weight like this.*

- **Casualty drag — 200 lbs** (50–60 lb sled with 2×45 lb and 2×25 lb plates, or homemade equivalent) over **50 m/yd (2×25)**. Time limit **2:00**. If using a sled it **cannot be wheeled**.

![Casualty drag equipment — non-wheeled sled with plates and pull strap](/static/tradoc/images/equites-casualty-drag.jpg)
*Example casualty drag setup: a non-wheeled sled loaded with plates and a pull strap. The sled cannot be wheeled.*

**Uniform:** normal gym clothes — athletic shoes, shorts, shirt.

**Evidence submission standards:**
- *Farmer's carry & casualty drag* — a video showing the measured distance, the appropriate weight on the bar/sled (legionary must provide visual proof of weight), and the legionary conducting the entire exercise from start to finish. Sled cannot be wheeled.
- *Run or ruck* — a photo of the legionary before the event **and** a screenshot from any running/fitness app that measures distance, speed, and time.

**Who to submit to:** All evidence goes to your **Team Leader**. Once the TL assesses it and concludes it meets the standards above, it is submitted to **S3 for final approval**.

---

### Category 3 — Via Equitis (Warrior Skills)
- Disassembly, reassembly, and function check of rifle
- Hand & arm signals
- Comms:
  - Radio familiarity — switch channels, set frequency
  - LACE report
  - SALUTE report
- Range cards
- Rank insignia identification & chain of command
- SMARCHE

---

### Standards Quick Reference

| Event | Standard |
|-------|----------|
| 1-mile run | 9:00 — track/outdoor only, no treadmills |
| 3-mile ruck | 60:00 — full kit (uniform, LBV/plate carrier, ruck, rifle) |
| Farmer's carry | 140 lbs, 50 m/yd (2×25), 1:30 |
| Casualty drag | 200 lbs, 50 m/yd (2×25), 2:00, non-wheeled sled |

*Approval chain: Team Leader → S3 (final).*
"""

# (name, sort_order, doc_type, doc_title, doc_url, doc_body, description)
SUBJECTS = [
    ("Sabre", 1, "external", "Sabre Course (TSM)", "/training/library/40/file", None,
     "TSM statewide course. Independent tab — no prerequisite."),
    ("Marksman", 2, "external", "Designated Marksman Course (TSM)", "/training/library/37/file", None,
     "TSM statewide course. Prerequisite for Sharpshooter."),
    ("Sharpshooter", 3, "external", "Sharpshooter Course (TSM)", "/training/library/38/file", None,
     "TSM statewide course. Prerequisite: Marksman."),
    ("Equites", 4, "markdown", "Equites Tab Program", None, EQUITES_BODY,
     "13th Legion internal elite tab. Martial skill + fitness beyond patching."),
]


async def main():
    async with async_session() as db:
        # ── Advanced-tier block ──
        blk = (await db.execute(
            select(TradocBlock).where(TradocBlock.number == ADVANCED_BLOCK_NUMBER)
        )).scalar_one_or_none()
        if not blk:
            blk = TradocBlock(
                number=ADVANCED_BLOCK_NUMBER,
                name=ADVANCED_BLOCK_NAME,
                description="Qualifications and tabs earned above and beyond patching.",
                sort_order=100,
                tier="advanced",
            )
            db.add(blk)
            await db.flush()
            print(f"created block {blk.number} '{blk.name}' tier={blk.tier}")
        else:
            blk.name = ADVANCED_BLOCK_NAME
            blk.tier = "advanced"
            if not blk.sort_order:
                blk.sort_order = 100
            print(f"updated block {blk.number} tier={blk.tier}")

        # ── Subjects ──
        for name, so, dtype, dtitle, durl, dbody, desc in SUBJECTS:
            item = (await db.execute(
                select(TradocItem).where(
                    TradocItem.block == ADVANCED_BLOCK_NUMBER, TradocItem.name == name
                )
            )).scalar_one_or_none()
            if not item:
                item = TradocItem(block=ADVANCED_BLOCK_NUMBER, block_name=ADVANCED_BLOCK_NAME, name=name)
                db.add(item)
            item.block_name = ADVANCED_BLOCK_NAME
            item.description = desc
            item.sort_order = so
            item.optional = False
            item.doc_type = dtype
            item.doc_title = dtitle
            item.doc_url = durl
            item.doc_body = dbody
            print(f"  upserted subject '{name}' ({dtype}) sort={so}")

        await db.commit()
        print("committed")


if __name__ == "__main__":
    asyncio.run(main())
