"""Announcements API — pulls from NC Announcement Center, allows posting."""

import logging
import re
from datetime import datetime
from html import escape

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

from app.auth import require_auth, get_current_user
from config import get_settings

router = APIRouter(prefix="/api/announcements", tags=["announcements"])

# NC creds for posting (spooky has admin rights on NC)
from app.settings import NC_SVC_USER as NC_POST_USER, NC_SVC_PASS as NC_POST_PASS

from app.constants import S1_ROLES as POSTER_ROLES

log = logging.getLogger(__name__)

# Simple URL regex for auto-linking plain text messages
_URL_RE = re.compile(r'(https?://[^\s<>"\']+)')

# Basic HTML tag detection
_HAS_HTML_RE = re.compile(r'<(p|br|b|i|strong|em|ul|ol|li|a|h[1-6]|blockquote|div|span)[\s/>]', re.I)

# Whitelist of safe HTML tags and attributes for rich text
import bleach
ALLOWED_TAGS = [
    "p", "br", "b", "i", "u", "s", "strong", "em",
    "ul", "ol", "li", "a", "h1", "h2", "h3",
    "blockquote", "pre", "code", "span", "div", "img",
    "sub", "sup",
]
ALLOWED_ATTRS = {
    "a": ["href", "target", "rel"],
    "img": ["src", "alt", "width", "height"],
    "span": ["style", "class"],
    "p": ["style", "class"],
    "div": ["style", "class"],
}


def _render_message(message: str) -> str:
    """Render announcement body to safe HTML.

    If the message already contains HTML tags (from Quill), sanitize and pass through.
    Otherwise, escape plain text and auto-link URLs (legacy messages).
    """
    if not message:
        return ""

    if _HAS_HTML_RE.search(message):
        # Rich text from Quill — sanitize with bleach
        return bleach.clean(
            message,
            tags=ALLOWED_TAGS,
            attributes=ALLOWED_ATTRS,
            strip=True,
        )
    else:
        # Legacy plain text — escape and auto-link
        escaped = escape(message)
        linked = _URL_RE.sub(r'<a href="\1" target="_blank" style="color:#d4a537;">\1</a>', escaped)
        return linked.replace("\n", "<br>")


def _strip_html(html: str) -> str:
    """Strip HTML to plain text for plainMessage field."""
    if not html:
        return ""
    text = re.sub(r'<br\s*/?>', '\n', html, flags=re.I)
    text = re.sub(r'</p>\s*<p[^>]*>', '\n\n', text, flags=re.I)
    text = re.sub(r'</li>\s*<li[^>]*>', '\n• ', text, flags=re.I)
    text = re.sub(r'<li[^>]*>', '• ', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'&#x27;', "'", text)
    text = re.sub(r'&nbsp;', ' ', text)
    return text.strip()


def _can_post(user: dict) -> bool:
    """Command, admin, and S1 can post announcements."""
    roles = set(user.get("roles", []))
    return bool(roles & POSTER_ROLES)


def _parse_author(message: str, fallback_author: str) -> tuple[str, str]:
    """Extract real author from '[Posted by Name]' tag at end of message.
    Returns (clean_message, author_name).
    """
    if not message:
        return "", fallback_author
    # Check for the tag in both plain text and HTML contexts
    # Plain text: last line is [Posted by Name]
    # HTML: might be wrapped in <p>[Posted by Name]</p>
    import re as _re

    # Try HTML version first
    m = _re.search(r'<p>\[Posted by ([^\]]+)\]</p>\s*$', message)
    if m:
        clean = message[:m.start()].rstrip()
        return clean, m.group(1)

    # Plain text version
    lines = message.rsplit("\n", 1)
    if len(lines) == 2 and lines[1].startswith("[Posted by ") and lines[1].endswith("]"):
        return lines[0].rstrip(), lines[1][len("[Posted by "):-1]
    return message, fallback_author


# ─── Quill Editor HTML ──────────────────────────────────────────────────────

QUILL_CSS = """
<link href="https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.snow.css" rel="stylesheet">
<style>
.ql-toolbar.ql-snow {
    background: #2a2a3e;
    border: 1px solid #444 !important;
    border-radius: 4px 4px 0 0;
}
.ql-toolbar .ql-stroke { stroke: #ccc !important; }
.ql-toolbar .ql-fill { fill: #ccc !important; }
.ql-toolbar .ql-picker-label { color: #ccc !important; }
.ql-toolbar .ql-picker-options {
    background: #2a2a3e !important;
    border-color: #444 !important;
}
.ql-toolbar .ql-picker-item { color: #ccc !important; }
.ql-toolbar button:hover .ql-stroke,
.ql-toolbar .ql-picker-label:hover .ql-stroke { stroke: #d4a537 !important; }
.ql-toolbar button:hover .ql-fill,
.ql-toolbar .ql-picker-label:hover .ql-fill { fill: #d4a537 !important; }
.ql-toolbar button.ql-active .ql-stroke { stroke: #d4a537 !important; }
.ql-toolbar button.ql-active .ql-fill { fill: #d4a537 !important; }
.ql-container.ql-snow {
    background: #2a2a3e;
    border: 1px solid #444 !important;
    border-top: none !important;
    border-radius: 0 0 4px 4px;
    color: #eee;
    font-size: 13px;
    min-height: 120px;
}
.ql-editor { min-height: 120px; line-height: 1.5; }
.ql-editor.ql-blank::before { color: #888 !important; font-style: normal !important; }
.ql-editor a { color: #d4a537; }
.ql-snow .ql-tooltip {
    background: #1a1a2e !important;
    border-color: #444 !important;
    color: #eee !important;
    box-shadow: 0 2px 8px rgba(0,0,0,.4) !important;
}
.ql-snow .ql-tooltip input[type=text] {
    background: #2a2a3e !important;
    color: #eee !important;
    border-color: #555 !important;
}
.ql-snow .ql-tooltip a { color: #d4a537 !important; }
</style>
"""

QUILL_JS = '<script src="https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.js"></script>'


def _quill_editor_html(editor_id: str, toolbar_id: str, placeholder: str = "Message body...") -> str:
    """Return HTML for a Quill editor instance."""
    return f"""
    <div id="{toolbar_id}">
        <span class="ql-formats">
            <select class="ql-header">
                <option value="">Normal</option>
                <option value="1">Heading 1</option>
                <option value="2">Heading 2</option>
                <option value="3">Heading 3</option>
            </select>
        </span>
        <span class="ql-formats">
            <button class="ql-bold"></button>
            <button class="ql-italic"></button>
            <button class="ql-underline"></button>
            <button class="ql-strike"></button>
        </span>
        <span class="ql-formats">
            <button class="ql-list" value="ordered"></button>
            <button class="ql-list" value="bullet"></button>
            <button class="ql-blockquote"></button>
        </span>
        <span class="ql-formats">
            <button class="ql-link"></button>
            <button class="ql-image"></button>
        </span>
        <span class="ql-formats">
            <button class="ql-clean"></button>
        </span>
    </div>
    <div id="{editor_id}" style="min-height:120px;"></div>
    """


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@require_auth
async def get_announcements(request: Request):
    """Fetch recent announcements from NC Announcement Center and render HTML partial."""
    current_user = get_current_user(request)
    can_manage = _can_post(current_user)
    settings = get_settings()
    nc_url = settings.nc_url

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{nc_url}/ocs/v2.php/apps/announcementcenter/api/v1/announcements",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_POST_USER, NC_POST_PASS),
            )
            resp.raise_for_status()

        data = resp.json()
        items = data.get("ocs", {}).get("data", [])

        if not items:
            return HTMLResponse('<p class="text-muted">No announcements.</p>')

        html_parts = []
        for ann in items[:5]:
            subject = ann.get("subject", "")
            raw_message = ann.get("message", "")
            author_raw = ann.get("author", "Unknown")

            message, author = _parse_author(raw_message, author_raw)
            timestamp = ann.get("time", 0)

            try:
                dt = datetime.fromtimestamp(int(timestamp))
                date_str = dt.strftime("%b %d, %Y")
                time_ago = _time_ago(dt)
            except Exception:
                date_str = "Unknown date"
                time_ago = ""

            rendered = _render_message(message)
            ann_id = ann.get("id", 0)

            # Escape subject for safe HTML attribute use
            subject_escaped = escape(subject, quote=True)
            # For the edit form, we need raw message HTML as a JS string
            message_js = message.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$").replace("</", "<\\/")

            actions_html = ""
            if can_manage:
                actions_html = f"""
                <div style="display:flex;gap:6px;margin-top:6px;" id="ann-actions-{ann_id}">
                    <button onclick="startEdit({ann_id})"
                        style="padding:2px 8px;background:transparent;color:#d4a537;border:1px solid #d4a537;border-radius:3px;cursor:pointer;font-size:11px;">✏️ Edit</button>
                    <button hx-delete="/api/announcements/{ann_id}" hx-target="#announcements-content" hx-swap="innerHTML"
                        hx-confirm="Delete this announcement?"
                        style="padding:2px 8px;background:transparent;color:#c62828;border:1px solid #c62828;border-radius:3px;cursor:pointer;font-size:11px;">🗑️ Delete</button>
                </div>
                <div id="ann-edit-{ann_id}" style="display:none;margin-top:8px;">
                    <input type="text" id="edit-subject-{ann_id}" value="{subject_escaped}" required
                        style="width:100%;padding:4px 6px;font-size:13px;background:#2a2a3e;color:#eee;border:1px solid #444;border-radius:4px;margin-bottom:6px;box-sizing:border-box;">
                    {_quill_editor_html(f"edit-editor-{ann_id}", f"edit-toolbar-{ann_id}")}
                    <div style="display:flex;gap:6px;justify-content:flex-end;margin-top:6px;">
                        <button type="button" onclick="cancelEdit({ann_id})" style="padding:2px 10px;background:transparent;color:#888;border:1px solid #555;border-radius:3px;cursor:pointer;font-size:11px;">Cancel</button>
                        <button type="button" onclick="submitEdit({ann_id})" style="padding:2px 10px;background:#d4a537;color:#1a1a2e;border:none;border-radius:3px;font-weight:600;cursor:pointer;font-size:11px;">Save</button>
                    </div>
                    <script>
                    (function initEditQuill_{ann_id}() {{
                        if (typeof Quill === 'undefined') {{
                            setTimeout(initEditQuill_{ann_id}, 100);
                            return;
                        }}
                        var q = new Quill('#edit-editor-{ann_id}', {{
                            theme: 'snow',
                            modules: {{ toolbar: '#edit-toolbar-{ann_id}' }},
                            placeholder: 'Message body...'
                        }});
                        q.root.innerHTML = `{message_js}`;
                        window['editQuill_{ann_id}'] = q;
                    }})();
                    </script>
                </div>"""

            html_parts.append(f"""
            <div style="padding:12px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
                <div style="display:flex;justify-content:space-between;align-items:baseline;">
                    <div style="font-weight:600;font-size:15px;">📢 {escape(subject)}</div>
                    <div style="font-size:11px;color:#888;white-space:nowrap;margin-left:12px;">{time_ago}</div>
                </div>
                <div class="ann-body" style="font-size:13px;color:#ccc;margin-top:4px;line-height:1.5;">{rendered}</div>
                <div style="font-size:11px;color:#888;margin-top:6px;">— {escape(author)} · {date_str}</div>
                {actions_html}
            </div>""")

        js = ""
        if can_manage:
            js = f"""
            {QUILL_CSS}
            {QUILL_JS}
            <style>
            .ann-body a {{ color:#d4a537; text-decoration:underline; }}
            .ann-body a:hover {{ color:#e8c14a; }}
            .ann-body img {{ max-width:100%; border-radius:4px; margin:4px 0; }}
            .ann-body blockquote {{ border-left:3px solid #d4a537; padding-left:10px; color:#aaa; margin:8px 0; }}
            .ann-body h1,.ann-body h2,.ann-body h3 {{ color:#eee; margin:8px 0 4px; }}
            .ann-body ul,.ann-body ol {{ padding-left:20px; }}
            </style>
            <script>
            function startEdit(id) {{
                document.getElementById('ann-actions-' + id).style.display = 'none';
                document.getElementById('ann-edit-' + id).style.display = 'block';
            }}
            function cancelEdit(id) {{
                document.getElementById('ann-edit-' + id).style.display = 'none';
                document.getElementById('ann-actions-' + id).style.display = 'flex';
            }}
            function submitEdit(id) {{
                var q = window['editQuill_' + id];
                var subject = document.getElementById('edit-subject-' + id).value.trim();
                var message = q.root.innerHTML;
                if (!subject) {{ alert('Subject is required'); return; }}
                var btn = event.target;
                btn.disabled = true;
                btn.textContent = 'Saving...';
                var fd = new FormData();
                fd.append('subject', subject);
                fd.append('message', message);
                fetch('/api/announcements/' + id + '/edit', {{
                    method: 'POST',
                    body: fd
                }}).then(r => r.text()).then(html => {{
                    document.getElementById('announcements-content').innerHTML = html;
                    htmx.process(document.getElementById('announcements-content'));
                }}).catch(e => {{ alert('Failed: ' + e); btn.disabled = false; btn.textContent = 'Save'; }});
            }}
            </script>"""

        return HTMLResponse("".join(html_parts) + js)

    except Exception as e:
        return HTMLResponse(f'<p class="text-muted">⚠️ Could not load announcements: {e}</p>')


@router.get("/compose", response_class=HTMLResponse)
@require_auth
async def compose_form(request: Request):
    """Return the announcement compose form with Quill rich text editor."""
    user = get_current_user(request)
    if not _can_post(user):
        return HTMLResponse("")

    return HTMLResponse(f"""
    {QUILL_CSS}
    {QUILL_JS}
    <div id="announce-compose" style="margin-top:12px;padding:12px;background:rgba(255,255,255,0.03);border:1px solid #444;border-radius:6px;">
        <div style="margin-bottom:8px;">
            <input type="text" id="announce-subject" placeholder="Announcement subject" required
                style="width:100%;padding:6px 8px;font-size:13px;background:#2a2a3e;color:#eee;border:1px solid #444;border-radius:4px;box-sizing:border-box;">
        </div>
        <div style="margin-bottom:8px;">
            {_quill_editor_html("compose-editor", "compose-toolbar")}
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;">
            <label style="font-size:12px;color:#aaa;display:flex;align-items:center;gap:6px;">
                <input type="checkbox" id="announce-notify" checked> Send notification
            </label>
            <div style="display:flex;gap:8px;">
                <button type="button" onclick="document.getElementById('announce-compose').remove()"
                    style="padding:4px 12px;background:transparent;color:#888;border:1px solid #555;border-radius:4px;cursor:pointer;font-size:12px;">Cancel</button>
                <button type="button" id="announce-submit-btn" onclick="submitAnnouncement()"
                    style="padding:4px 14px;background:#d4a537;color:#1a1a2e;border:none;border-radius:4px;font-weight:600;cursor:pointer;font-size:12px;">📢 Post</button>
            </div>
        </div>
    </div>
    <script>
    var composeQuill = new Quill('#compose-editor', {{
        theme: 'snow',
        modules: {{ toolbar: '#compose-toolbar' }},
        placeholder: 'Message body...'
    }});

    function submitAnnouncement() {{
        var subject = document.getElementById('announce-subject').value.trim();
        var message = composeQuill.root.innerHTML;
        var notify = document.getElementById('announce-notify').checked ? '1' : '0';

        if (!subject) {{ alert('Subject is required'); return; }}

        // Check if editor is empty (Quill puts <p><br></p> when empty)
        var text = composeQuill.getText().trim();
        if (!text) {{ alert('Message body is required'); return; }}

        var btn = document.getElementById('announce-submit-btn');
        btn.disabled = true;
        btn.textContent = 'Posting...';

        var fd = new FormData();
        fd.append('subject', subject);
        fd.append('message', message);
        fd.append('notify', notify);

        fetch('/api/announcements/post', {{
            method: 'POST',
            body: fd
        }}).then(function(resp) {{
            return resp.text();
        }}).then(function(html) {{
            document.getElementById('announce-compose').remove();
            document.getElementById('announcements-content').innerHTML = html;
            htmx.process(document.getElementById('announcements-content'));
        }}).catch(function(e) {{
            alert('Failed to post: ' + e);
            btn.disabled = false;
            btn.textContent = '📢 Post';
        }});
    }}
    </script>
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

    poster_name = user.get("display_name", user.get("username", "Unknown"))
    author_tag = f"\n<p>[Posted by {poster_name}]</p>"
    message_with_author = f"{message}{author_tag}" if message else f"<p>[Posted by {poster_name}]</p>"

    # Generate plain text version
    plain_message = _strip_html(message)
    plain_with_author = f"{plain_message}\n[Posted by {poster_name}]" if plain_message else f"[Posted by {poster_name}]"

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
                    "plainMessage": plain_with_author,
                    "groups[]": "13th Legion",
                    "activities": "1",
                    "notifications": "1" if notify else "0",
                    "emails": "0",
                    "comments": "1",
                },
            )
            resp.raise_for_status()

        from app.routes.notifications import create_notification_for_all
        from app.database import async_session as notif_session
        async with notif_session() as ndb:
            await create_notification_for_all(
                ndb, "announcement", f"📢 {subject}",
                body=_strip_html(message)[:200] if message else None,
                link="/dashboard",
                icon="📢"
            )
    except Exception as e:
        return HTMLResponse(f'<p style="color:#c62828;">⚠️ Failed to post: {e}</p>')

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

    if not message:
        return HTMLResponse('<p style="color:#c62828;">⚠️ Message body cannot be empty.</p>')

    editor_name = user.get("display_name", user.get("username", "Unknown"))
    author_tag = f"\n<p>[Posted by {editor_name}]</p>"
    message_with_author = f"{message}{author_tag}"

    plain_message = _strip_html(message)
    plain_with_author = f"{plain_message}\n[Posted by {editor_name}]"

    settings = get_settings()
    nc_url = settings.nc_url

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.delete(
                f"{nc_url}/ocs/v2.php/apps/announcementcenter/api/v1/announcements/{ann_id}",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_POST_USER, NC_POST_PASS),
            )
            await client.post(
                f"{nc_url}/ocs/v2.php/apps/announcementcenter/api/v1/announcements",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_POST_USER, NC_POST_PASS),
                data={
                    "subject": subject,
                    "message": message_with_author,
                    "plainMessage": plain_with_author,
                    "groups[]": "13th Legion",
                    "activities": "1",
                    "notifications": "0",
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
