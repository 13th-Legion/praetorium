# Import all models so Alembic sees them
from app.models.member import Member
from app.models.training import TradocItem, MemberTradoc, Certification, MemberCertification, TrainingClaim
from app.models.awards import MemberAward  # noqa: F401
from app.models.enums import RankGrade, MemberStatus, TrainingBlock  # noqa: F401
from app.models.tlas import ThreatLevelEntry, ThreatLevel, TLAS_CONFIG  # noqa: F401
from app.models.recruiting import Recruiter, DocumentSignature, SeparationLog  # noqa: F401
from app.models.events import (  # noqa: F401
    Event, EventRSVP, EventDocument,
    EventGuest, EventBuddyPair, EventGuardSlot, EventGuardDuty,
    EventVexillation, EventVexillationAssignment,
)
from app.models.rank_history import RankHistory  # noqa: F401
from app.models.schedule import EventScheduleBlock  # noqa: F401
from app.models.elections import (  # noqa: F401
    Election, ElectionNomination, ElectionNominationReceipt,
    ElectionBallot, ElectionVoterRoll,
)
