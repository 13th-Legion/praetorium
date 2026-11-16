"""Status-change audit logging for members.

A SQLAlchemy before_update listener records every change to Member.status into
member_status_history. The acting username is captured from a ContextVar set
per-request by AuditContextMiddleware.
"""
import contextvars
from datetime import datetime

from sqlalchemy import event, inspect
from starlette.middleware.base import BaseHTTPMiddleware

from app.models.member import Member
from app.models.member_status_history import MemberStatusHistory

current_username: contextvars.ContextVar = contextvars.ContextVar("current_username", default=None)


class AuditContextMiddleware(BaseHTTPMiddleware):
    """Set the acting username for the duration of each request."""

    async def dispatch(self, request, call_next):
        user = request.session.get("user")
        token = current_username.set(user.get("username") if user else None)
        try:
            return await call_next(request)
        finally:
            current_username.reset(token)


@event.listens_for(Member, "before_update")
def _audit_member_status(mapper, connection, target):
    hist = inspect(target).attrs.status.history
    if not hist.has_changes():
        return
    old_status = hist.deleted[0] if hist.deleted else None
    new_status = hist.added[0] if hist.added else None
    connection.execute(
        MemberStatusHistory.__table__.insert().values(
            member_id=target.id,
            old_status=old_status,
            new_status=new_status,
            changed_by=current_username.get(),
            changed_at=datetime.utcnow(),
        )
    )
