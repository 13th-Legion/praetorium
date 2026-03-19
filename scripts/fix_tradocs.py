import sys
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.append("/app")
from app.models.training import MemberTradoc, TradocItem
from app.models.member import Member
from app.models.events import EventRSVP

DB_URL = os.environ.get("DATABASE_URL_SYNC")
if not DB_URL:
    print("ERROR: DATABASE_URL_SYNC environment variable is not set.", file=sys.stderr)
    sys.exit(1)
engine = create_engine(DB_URL)
Session = sessionmaker(bind=engine)
db = Session()

# 1. Get everyone who already had Comms (10) or Land Nav (12)
members_with_old = db.query(MemberTradoc.member_id).filter(MemberTradoc.item_id.in_([10, 12])).distinct().all()
member_ids = {m[0] for m in members_with_old}

# 2. Get everyone who attended event_id = 4
attendees = db.query(EventRSVP.member_id).filter(EventRSVP.event_id == 4, EventRSVP.attended == True).all()
member_ids.update({m[0] for m in attendees})

# 3. Add 53, 54, 57 to all of them if they don't have it
NEW_ITEMS = [53, 54, 57]
added = 0

for m_id in member_ids:
    for item_id in NEW_ITEMS:
        exists = db.query(MemberTradoc).filter_by(member_id=m_id, item_id=item_id).first()
        if not exists:
            mt = MemberTradoc(
                member_id=m_id,
                item_id=item_id,
                signed_off_by="System Migration",
                notes="Backfilled from Block 3 split"
            )
            db.add(mt)
            added += 1

db.commit()
print(f"Added {added} new TRADOC records to {len(member_ids)} members.")
