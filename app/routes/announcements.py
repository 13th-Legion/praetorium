"""Announcements API — pulls from NC Announcement Center, allows posting."""

import re
from datetime import datetime

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

from app.auth import require_auth, get_current_user
from config import get_settings

router = APIRouter(prefix="/api/announcements", tags=["announcements"])

# NC creds for posting (spooky has admin rights on NC)
from app.settings import NC_SVC_USER as NC_POST_USER, NC_SVC_PASS as NC_POST_PASS

from app.constants import S1_ROLES as POSTER_ROLES

# Simple URL regex for auto-linking plain text messages
_URL_RE = re.compile(r'(https?://[^\s<>"\']+)')


def _render_message(message: str) -> str:
    """Render announcement message body. If it contains HTML tags, pass through.
    If plain text, auto-linkify URLs and convert newlines to <br>."""
    if not message:
        return ""
    # Check if it already has HTML (from Quill editor)
    if "<" in message and ("</p>" in message or "</a>" in message or "<br>" in message or "<strong>" in message):
        return message
    # Plain text — auto-linkify URLs and convert newlines
    escaped = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    linked = _URL_RE.sub(r'<a href="\1" target="_blank" style="color:#d4a537;">\1</a>', escaped)
    return linked.replace("\n", "<br>")

def _can_post(user: dict) -> bool:
    """Command, admin, and S1 can post announcements."""
    roles = set(user.get("roles", []))
    return bool(roles & POSTER_ROLES)


@router.get("", response_class=HTMLResponse)
@require_auth
async def get_announcements(request: Request):
    """Fetch recent announcements from NC Announcement Center and render HTML partial."""
    current_user = get_current_user(request)
    can_manage = _can_post(current_user)
    settings = get_settings()
    nc_url = settings.nc_url
    user = NC_POST_USER
    pw = NC_POST_PASS

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{nc_url}/ocs/v2.php/apps/announcementcenter/api/v1/announcements",
                headers={
                    "OCS-APIRequest": "true",
                    "Accept": "application/json",
                },
                auth=(user, pw),
            )
            resp.raise_for_status()

        data = resp.json()
        items = data.get("ocs", {}).get("data", [])

        if not items:
            return HTMLResponse('<p class="text-muted">No announcements.</p>')

        # Show up to 5 most recent
        html_parts = []
        for ann in items[:5]:
            subject = ann.get("subject", "")
            message = ann.get("message", "")
            author_raw = ann.get("author", "Unknown")
            # NC records spooky as author since we post via bot account.
            # Real author is embedded as last line: "[Posted by Display Name]"
            message_lines = message.rsplit("\n", 1) if message else [message]
            author = author_raw
            if len(message_lines) == 2 and message_lines[1].startswith("[Posted by ") and message_lines[1].endswith("]"):
                author = message_lines[1][len("[Posted by "):-1]
                message = message_lines[0].rstrip()
            timestamp = ann.get("time", 0)

            # Format timestamp
            try:
                dt = datetime.fromtimestamp(int(timestamp))
                date_str = dt.strftime("%b %d, %Y")
                time_ago = _time_ago(dt)
            except Exception:
                date_str = "Unknown date"
                time_ago = ""

            # Render message (HTML from Quill or auto-linkified plain text)
            message = _render_message(message)

            ann_id = ann.get("id", 0)
            full_message = ann.get("message", "")
            # Strip the [Posted by ...] tag from edit form too
            fm_lines = full_message.rsplit("\n", 1) if full_message else [full_message]
            if len(fm_lines) == 2 and fm_lines[1].startswith("[Posted by ") and fm_lines[1].endswith("]"):
                full_message = fm_lines[0].rstrip()
            edit_html = _render_message(full_message)

            # Action buttons for Command/S1 Lead
            actions_html = ""
            if can_manage:
                actions_html = f"""
                <div style="display:flex;gap:6px;margin-top:6px;" id="ann-actions-{ann_id}">
                    <button onclick="editAnnouncement({ann_id})"
                        style="padding:2px 8px;background:transparent;color:#d4a537;border:1px solid #d4a537;border-radius:3px;cursor:pointer;font-size:11px;">✏️ Edit</button>
                    <button hx-delete="/api/announcements/{ann_id}" hx-target="#announcements-content" hx-swap="innerHTML"
                        hx-confirm="Delete this announcement?"
                        style="padding:2px 8px;background:transparent;color:#c62828;border:1px solid #c62828;border-radius:3px;cursor:pointer;font-size:11px;">🗑️ Delete</button>
                </div>
                <div id="ann-edit-{ann_id}" style="display:none;margin-top:8px;">
                    <form id="edit-form-{ann_id}">
                        <input type="text" name="subject" id="edit-subject-{ann_id}" value="{subject}" required
                            style="width:100%;padding:4px 6px;font-size:13px;background:#2a2a3e;color:#eee;border:1px solid #444;border-radius:4px;margin-bottom:6px;box-sizing:border-box;">
                        <div id="edit-editor-{ann_id}" style="min-height:60px;background:#2a2a3e;color:#eee;font-size:13px;">{edit_html}</div>
                        <div style="display:flex;gap:6px;justify-content:flex-end;margin-top:6px;">
                            <button type="button" onclick="cancelEdit({ann_id})" style="padding:2px 10px;background:transparent;color:#888;border:1px solid #555;border-radius:3px;cursor:pointer;font-size:11px;">Cancel</button>
                            <button type="button" onclick="saveEdit({ann_id})" style="padding:2px 10px;background:#d4a537;color:#1a1a2e;border:none;border-radius:3px;font-weight:600;cursor:pointer;font-size:11px;">Save</button>
                        </div>
                    </form>
                </div>"""

            html_parts.append(f"""
            <div style="padding:12px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
                <div style="display:flex;justify-content:space-between;align-items:baseline;">
                    <div style="font-weight:600;font-size:15px;">📢 {subject}</div>
                    <div style="font-size:11px;color:#888;white-space:nowrap;margin-left:12px;">{time_ago}</div>
                </div>
                <div class="ann-body" style="font-size:13px;color:#ccc;margin-top:4px;line-height:1.5;">{message}</div>
                <div style="font-size:11px;color:#888;margin-top:6px;">— {author} · {date_str}</div>
                {actions_html}
            </div>""")

        # Add JS helpers for edit toggle (only once, for managers)
        js = ""
        if can_manage:
            js = """
            <link href="https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.snow.css" rel="stylesheet">
            <script src="https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.js"></script>
            <style>
            .ql-toolbar.ql-snow { background:#1a1a2e; border:1px solid #444; border-radius:4px 4px 0 0; }
            .ql-snow .ql-stroke { stroke:#aaa; }
            .ql-snow .ql-fill { fill:#aaa; }
            .ql-snow .ql-picker-label { color:#aaa; }
            .ql-snow .ql-picker-options { background:#2a2a3e; }
            .ql-snow .ql-editor { min-height:60px; }
            .ql-snow .ql-editor a { color:#d4a537; }
            [id^="edit-editor-"].ql-snow { border:1px solid #444; border-top:none; }
            .ann-body a { color:#d4a537; text-decoration:underline; }
            .ann-body a:hover { color:#e8c14a; }
            .ann-body p { margin:0 0 4px 0; }
            .ann-body ul, .ann-body ol { margin:4px 0; padding-left:20px; }
            </style>
            <script>
            var _editQuills = {};
            function editAnnouncement(id) {
                document.getElementById('ann-actions-' + id).style.display = 'none';
                document.getElementById('ann-edit-' + id).style.display = 'block';
                if (!_editQuills[id]) {
                    _editQuills[id] = new Quill('#edit-editor-' + id, {
                        theme: 'snow',
                        modules: { toolbar: [['bold', 'italic', 'underline'], ['link'], [{ list: 'ordered' }, { list: 'bullet' }], ['clean']] }
                    });
                    var form = document.getElementById('edit-form-' + id);
                    form.addEventListener('htmx:configRequest', function(e) {
                        document.getElementById('edit-message-' + id).value = _editQuills[id].root.innerHTML;
                    });
                    form.addEventListener('submit', function(e) {
                        document.getElementById('edit-message-' + id).value = _editQuills[id].root.innerHTML;
                    });
                }
            }
            function saveEdit(id) {
                var subject = document.getElementById('edit-subject-' + id).value;
                var message = _editQuills[id] ? _editQuills[id].root.innerHTML : '';
                var formData = new FormData();
                formData.append('subject', subject);
                formData.append('message', message);
                fetch('/api/announcements/' + id + '/edit', {
                    method: 'POST',
                    body: formData
                }).then(function(r) { return r.text(); }).then(function(html) {
                    document.getElementById('announcements-content').innerHTML = html;
                });
            }
            function cancelEdit(id) {
                document.getElementById('ann-edit-' + id).style.display = 'none';
                document.getElementById('ann-actions-' + id).style.display = 'flex';
            }
            </script>"""

        return HTMLResponse("".join(html_parts) + js)

    except Exception as e:
        return HTMLResponse(f'<p class="text-muted">⚠️ Could not load announcements: {e}</p>')


@router.get("/compose", response_class=HTMLResponse)
@require_auth
async def compose_form(request: Request):
    """Return the announcement compose form (HTMX partial)."""
    user = get_current_user(request)
    if not _can_post(user):
        return HTMLResponse("")

    return HTMLResponse("""
    <link href="https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.snow.css" rel="stylesheet">
    <div id="announce-compose" style="margin-top:12px;padding:12px;background:rgba(255,255,255,0.03);border:1px solid #444;border-radius:6px;">
        <form id="announce-form" hx-post="/api/announcements/post" hx-target="#announcements-content" hx-swap="innerHTML" hx-on::after-request="document.getElementById('announce-compose').remove()"
            hx-on::before-request="if(window._composeQuill)document.getElementById('compose-message').value=window._composeQuill.root.innerHTML;">
            <div style="margin-bottom:8px;">
                <input type="text" name="subject" placeholder="Announcement subject" required
                    style="width:100%;padding:6px 8px;font-size:13px;background:#2a2a3e;color:#eee;border:1px solid #444;border-radius:4px;box-sizing:border-box;">
            </div>
            <div style="margin-bottom:8px;">
                <div id="compose-editor" style="min-height:80px;background:#2a2a3e;color:#eee;border-radius:0 0 4px 4px;font-size:13px;"></div>
                <input type="hidden" name="message" id="compose-message">
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <label style="font-size:12px;color:#aaa;display:flex;align-items:center;gap:6px;">
                    <input type="checkbox" name="notify" value="1" checked> Send notification
                </label>
                <div style="display:flex;gap:8px;">
                    <button type="button" onclick="this.closest('#announce-compose').remove()" style="padding:4px 12px;background:transparent;color:#888;border:1px solid #555;border-radius:4px;cursor:pointer;font-size:12px;">Cancel</button>
                    <button type="submit" style="padding:4px 14px;background:#d4a537;color:#1a1a2e;border:none;border-radius:4px;font-weight:600;cursor:pointer;font-size:12px;">📢 Post</button>
                </div>
            </div>
        </form>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.js"></script>
    <script>
    (function() {
        window._composeQuill = new Quill('#compose-editor', {
            theme: 'snow',
            placeholder: 'Message body...',
            modules: { toolbar: [['bold', 'italic', 'underline'], ['link'], [{ list: 'ordered' }, { list: 'bullet' }], ['clean']] }
        });
    })();
    </script>
    <style>
    .ql-toolbar.ql-snow { background:#1a1a2e; border:1px solid #444; border-radius:4px 4px 0 0; }
    .ql-snow .ql-stroke { stroke:#aaa; }
    .ql-snow .ql-fill { fill:#aaa; }
    .ql-snow .ql-picker-label { color:#aaa; }
    .ql-snow .ql-picker-options { background:#2a2a3e; }
    #compose-editor.ql-snow { border:1px solid #444; border-top:none; }
    #compose-editor .ql-editor { min-height:80px; }
    #compose-editor .ql-editor a { color:#d4a537; }
    </style>
    """)


@router.post("/post", response_class=HTMLResponse)
@require_auth
async def post_announcement(request: Request):
    """Post a new announcement to NC Announcement Center, then return refreshed list."""
    user = get_current_user(request)
    if not _can_post(user):
        raise HTTPException(status_code=403, detail="Command or S1 Lead access required")

    form = await request.form()
    subject = form.get("subject", "").strip()
    message = form.get("message", "").strip()
    notify = form.get("notify") == "1"

    if not subject:
        raise HTTPException(status_code=400, detail="Subject is required")

    # Embed actual poster identity
    poster_name = user.get("display_name", user.get("username", "Unknown"))
    message_with_author = f"{message}\n[Posted by {poster_name}]"

    settings = get_settings()
    nc_url = settings.nc_url

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{nc_url}/ocs/v2.php/apps/announcementcenter/api/v1/announcements",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_POST_USER, NC_POST_PASS),
                data={
                    "subject": subject,
                    "message": message_with_author,
                    "plainMessage": message_with_author,
                    "groups[]": "13th Legion",
                    "activities": "1",
                    "notifications": "1" if notify else "0",
                    "emails": "0",
                    "comments": "1",
                },
            )
            resp.raise_for_status()
    except Exception as e:
        return HTMLResponse(f'<p style="color:#c62828;">⚠️ Failed to post: {e}</p>')

    # Return refreshed announcements list
    return await get_announcements(request)


@router.delete("/{ann_id}", response_class=HTMLResponse)
@require_auth
async def delete_announcement(request: Request, ann_id: int):
    """Delete an announcement from NC Announcement Center."""
    user = get_current_user(request)
    if not _can_post(user):
        raise HTTPException(status_code=403, detail="Command or S1 Lead access required")

    settings = get_settings()
    nc_url = settings.nc_url

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(
                f"{nc_url}/ocs/v2.php/apps/announcementcenter/api/v1/announcements/{ann_id}",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_POST_USER, NC_POST_PASS),
            )
            resp.raise_for_status()
    except Exception as e:
        return HTMLResponse(f'<p style="color:#c62828;">⚠️ Failed to delete: {e}</p>')

    # Return refreshed list
    return await get_announcements(request)


@router.post("/{ann_id}/edit", response_class=HTMLResponse)
@require_auth
async def edit_announcement(request: Request, ann_id: int):
    """Edit an announcement by deleting and reposting with updated content."""
    user = get_current_user(request)
    if not _can_post(user):
        raise HTTPException(status_code=403, detail="Command or S1 Lead access required")

    form = await request.form()
    subject = form.get("subject", "").strip()
    message = form.get("message", "").strip()

    if not subject:
        raise HTTPException(status_code=400, detail="Subject is required")

    # Strip empty Quill output
    import re
    msg_text = re.sub(r'<[^>]+>', '', message).strip()
    if not msg_text:
        return HTMLResponse(
            '<p style="color:#c62828;">⚠️ Message body cannot be empty.</p>'
        )

    # Embed editor identity
    editor_name = user.get("display_name", user.get("username", "Unknown"))
    message_with_author = f"{message}\n[Posted by {editor_name}]"

    settings = get_settings()
    nc_url = settings.nc_url

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Delete old
            await client.delete(
                f"{nc_url}/ocs/v2.php/apps/announcementcenter/api/v1/announcements/{ann_id}",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_POST_USER, NC_POST_PASS),
            )
            # Repost
            await client.post(
                f"{nc_url}/ocs/v2.php/apps/announcementcenter/api/v1/announcements",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_POST_USER, NC_POST_PASS),
                data={
                    "subject": subject,
                    "message": message_with_author,
                    "plainMessage": message_with_author,
                    "groups[]": "13th Legion",
                    "activities": "1",
                    "notifications": "0",  # Don't re-notify on edit
                    "emails": "0",
                    "comments": "1",
                },
            )
    except Exception as e:
        return HTMLResponse(f'<p style="color:#c62828;">⚠️ Failed to edit: {e}</p>')

    return await get_announcements(request)


def _time_ago(dt: datetime) -> str:
    """Human-friendly relative time."""
    now = datetime.now()
    delta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        m = seconds // 60
        return f"{m}m ago"
    elif seconds < 86400:
        h = seconds // 3600
        return f"{h}h ago"
    elif seconds < 604800:
        d = seconds // 86400
        return f"{d}d ago"
    elif seconds < 2592000:
        w = seconds // 604800
        return f"{w}w ago"
    else:
        return dt.strftime("%b %d")
