from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select, update, desc, or_, and_
from app.database import async_session
from app.models.notifications import Notification
from app.models.member import Member
from app.auth import require_auth
from typing import Optional

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

async def get_current_member_id(request: Request, db):
    user = request.session.get("user")
    if not user:
        return None
    username = user.get("username", "")
    if not username:
        return None
    
    result = await db.execute(select(Member.id).where(Member.nc_username == username))
    row = result.first()
    return row[0] if row else None


@router.get("/count", response_class=HTMLResponse)
@require_auth
async def get_unread_count(request: Request):
    async with async_session() as db:
        member_id = await get_current_member_id(request, db)
        if not member_id:
            return ""

        result = await db.execute(
            select(Notification).where(
                Notification.member_id == member_id,
                Notification.read_at.is_(None)
            )
        )
        count = len(result.scalars().all())

        if count > 0:
            return f'<span class="notification-badge">{count}</span>'
        return ""


@router.get("/dropdown", response_class=HTMLResponse)
@require_auth
async def get_dropdown(request: Request):
    async with async_session() as db:
        member_id = await get_current_member_id(request, db)
        if not member_id:
            return ""

        result = await db.execute(
            select(Notification)
            .where(Notification.member_id == member_id)
            .order_by(
                Notification.read_at.isnot(None),  # Unread first
                desc(Notification.created_at)
            )
            .limit(20)
        )
        notifications = result.scalars().all()

        html = '<div class="notification-dropdown-header" style="padding: 10px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center;">'
        html += '<span style="color: #e0e0e0; font-weight: bold;">Notifications</span>'
        if any(not n.read_at for n in notifications):
            html += '<button hx-post="/api/notifications/read-all" hx-target="#notification-panel" style="background: none; border: none; color: #d4a537; cursor: pointer; font-size: 12px;">Mark all read</button>'
        html += '</div>'
        html += '<div class="notification-dropdown-body" style="max-height: 400px; overflow-y: auto;">'

        if not notifications:
            html += '<div style="padding: 15px; color: #888; text-align: center;">No notifications</div>'
        else:
            for n in notifications:
                unread = not bool(n.read_at)
                bg_color = "rgba(212, 165, 55, 0.1)" if unread else "transparent"
                border_left = "3px solid #d4a537" if unread else "3px solid transparent"
                opacity = "1" if unread else "0.7"
                
                time_diff = datetime.utcnow() - n.created_at
                if time_diff.days > 0:
                    time_ago = f"{time_diff.days}d ago"
                elif time_diff.seconds > 3600:
                    time_ago = f"{time_diff.seconds // 3600}h ago"
                elif time_diff.seconds > 60:
                    time_ago = f"{time_diff.seconds // 60}m ago"
                else:
                    time_ago = "Just now"

                icon = n.icon or "🔔"
                
                html += f'<div class="notification-item" style="padding: 12px; border-bottom: 1px solid #333; background: {bg_color}; border-left: {border_left}; opacity: {opacity}; display: flex; gap: 10px; align-items: start; cursor: pointer;"'
                
                click_action = ""
                if unread:
                    click_action = f' hx-post="/api/notifications/{n.id}/read" hx-target="this" hx-swap="outerHTML"'
                    html += click_action
                else:
                    if n.link:
                        html += f' onclick="window.location.href=\'{n.link}\'"'
                        
                html += '>'
                html += f'<div style="font-size: 20px;">{icon}</div>'
                html += '<div style="flex: 1;">'
                html += f'<div style="color: #e0e0e0; font-size: 14px; font-weight: {"bold" if unread else "normal"}; margin-bottom: 4px;">{n.title}</div>'
                if n.body:
                    html += f'<div style="color: #888; font-size: 12px; margin-bottom: 4px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">{n.body}</div>'
                html += f'<div style="color: #666; font-size: 11px;">{time_ago}</div>'
                html += '</div>'
                
                if unread and n.link:
                    html += f'<a href="{n.link}" style="color: #d4a537; text-decoration: none; font-size: 12px; align-self: center;">View</a>'

                html += '</div>'

        html += '</div>'

        return HTMLResponse(content=html)


@router.post("/{notification_id}/read", response_class=HTMLResponse)
@require_auth
async def mark_read(request: Request, notification_id: int):
    async with async_session() as db:
        member_id = await get_current_member_id(request, db)
        if not member_id:
            return ""

        result = await db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.member_id == member_id
            )
        )
        n = result.scalar_one_or_none()
        
        if not n:
            return ""

        if not n.read_at:
            n.read_at = datetime.utcnow()
            await db.commit()
            
        # Return updated item HTML
        time_diff = datetime.utcnow() - n.created_at
        if time_diff.days > 0:
            time_ago = f"{time_diff.days}d ago"
        elif time_diff.seconds > 3600:
            time_ago = f"{time_diff.seconds // 3600}h ago"
        elif time_diff.seconds > 60:
            time_ago = f"{time_diff.seconds // 60}m ago"
        else:
            time_ago = "Just now"

        icon = n.icon or "🔔"
        
        html = f'<div class="notification-item" style="padding: 12px; border-bottom: 1px solid #333; background: transparent; border-left: 3px solid transparent; opacity: 0.7; display: flex; gap: 10px; align-items: start; cursor: pointer;"'
        if n.link:
            html += f' onclick="window.location.href=\'{n.link}\'"'
        html += '>'
        
        html += f'<div style="font-size: 20px;">{icon}</div>'
        html += '<div style="flex: 1;">'
        html += f'<div style="color: #e0e0e0; font-size: 14px; font-weight: normal; margin-bottom: 4px;">{n.title}</div>'
        if n.body:
            html += f'<div style="color: #888; font-size: 12px; margin-bottom: 4px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">{n.body}</div>'
        html += f'<div style="color: #666; font-size: 11px;">{time_ago}</div>'
        html += '</div>'
        html += '</div>'
        
        # We also need to trigger an update of the count badge. HTMX makes this easy via HX-Trigger
        return HTMLResponse(content=html, headers={"HX-Trigger": "updateNotificationCount"})


@router.post("/read-all", response_class=HTMLResponse)
@require_auth
async def mark_all_read(request: Request):
    async with async_session() as db:
        member_id = await get_current_member_id(request, db)
        if not member_id:
            return ""

        await db.execute(
            update(Notification)
            .where(
                Notification.member_id == member_id,
                Notification.read_at.is_(None)
            )
            .values(read_at=datetime.utcnow())
        )
        await db.commit()
        
    # Return updated dropdown by calling get_dropdown directly
    # Also trigger count update
    dropdown_response = await get_dropdown(request)
    return HTMLResponse(content=dropdown_response.body, headers={"HX-Trigger": "updateNotificationCount"})


# Helpers
async def create_notification(db, member_id: int, category: str, title: str, body: Optional[str] = None, link: Optional[str] = None, icon: Optional[str] = None):
    # Dedup: skip if identical notification for this member exists within last 60s
    cutoff = datetime.utcnow() - timedelta(seconds=60)
    existing = await db.execute(
        select(Notification.id).where(
            and_(
                Notification.member_id == member_id,
                Notification.title == title,
                Notification.created_at > cutoff,
            )
        ).limit(1)
    )
    if existing.first():
        return None

    new_notif = Notification(
        member_id=member_id,
        category=category,
        title=title,
        body=body,
        link=link,
        icon=icon
    )
    db.add(new_notif)
    await db.commit()
    return new_notif


async def create_notification_for_all(db, category: str, title: str, body: Optional[str] = None, link: Optional[str] = None, icon: Optional[str] = None, status_filter: Optional[str] = None):
    query = select(Member.id)
    if status_filter:
        query = query.where(Member.status == status_filter)
        
    result = await db.execute(query)
    member_ids = result.scalars().all()
    
    notifications = [
        Notification(
            member_id=mid,
            category=category,
            title=title,
            body=body,
            link=link,
            icon=icon
        )
        for mid in member_ids
    ]
    
    if notifications:
        db.add_all(notifications)
        await db.commit()


async def create_notification_for_roles(db, roles: list, category: str, title: str, body: Optional[str] = None, link: Optional[str] = None, icon: Optional[str] = None):
    """Send a notification to all members who have any of the specified portal roles."""
    import json as _json
    result = await db.execute(select(Member).where(Member.portal_roles.isnot(None)))
    members = result.scalars().all()

    target_ids = []
    for m in members:
        try:
            member_roles = _json.loads(m.portal_roles) if m.portal_roles else []
        except Exception:
            member_roles = []
        if any(r in member_roles for r in roles):
            target_ids.append(m.id)

    notifications = [
        Notification(
            member_id=mid,
            category=category,
            title=title,
            body=body,
            link=link,
            icon=icon
        )
        for mid in target_ids
    ]

    if notifications:
        db.add_all(notifications)
        await db.commit()
