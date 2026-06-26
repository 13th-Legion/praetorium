"""Seed catalog for newsletter section templates.

Derived from the header inventory of all 5 past Legionary Dispatches, with links
updated to Praetorium and Discord removed (Nextcloud Talk now). Run idempotently
(insert-if-missing by key) so re-running never duplicates or clobbers admin edits.

PUBLIC_BASE_URL is substituted at seed time so links point at the live portal.
"""
from __future__ import annotations

from app.settings import PUBLIC_BASE_URL

P = PUBLIC_BASE_URL  # e.g. https://portal.13thlegion.org

# Each entry: key, title, category, default_order, preload, dynamic_source, body_html
SECTION_TEMPLATES = [
    # ── PER-ISSUE (header + prompt; creator fills in) ──────────────────────────
    dict(key="commander_message", title="Message from the Commander", category="per_issue",
         default_order=10, body_html="<p><em>CO/1SG note to the unit goes here…</em></p>"),
    dict(key="ftx_wrapup", title="FTX Wrap-Up", category="per_issue",
         default_order=20, body_html="<p><em>Recap of the most recent FTX — turnout, highlights, standouts…</em></p>"),
    dict(key="upcoming_ftx", title="Upcoming FTX", category="per_issue",
         default_order=30, body_html="<p><em>Next FTX — dates, location, focus, what to bring…</em></p>"),
    dict(key="patches_promotions", title="Patches & Promotions", category="per_issue",
         default_order=40, body_html="<p><em>New patched members and promotions this period…</em></p>"),
    dict(key="course_graduates", title="Course Graduates", category="per_issue",
         default_order=50, body_html="<p><em>Congratulate graduates of DMR, GSAR/FAST, or other courses…</em></p>"),
    dict(key="unit_financials", title="Unit Financials", category="per_issue",
         default_order=60, body_html="<p><em>Current balance, income, and expenditures for the period…</em></p>"),
    dict(key="seasonal_greeting", title="Seasonal Greeting", category="per_issue",
         default_order=5, body_html="<p><em>Holiday / seasonal message to the unit…</em></p>"),
    dict(key="special_event", title="Special Event", category="per_issue",
         default_order=55, body_html="<p><em>Leadership Summit, Family Day, or other special event…</em></p>"),

    # ── RECURRING (pre-filled boilerplate; preload into every draft) ───────────
    dict(key="recruiting", title="Recruiting", category="recurring",
         default_order=70, preload=True,
         body_html=(
             "<p>We're always looking for help at recruiting events. If you'd like to "
             f'pitch in, sign up under <strong>S1: Admin</strong> in the portal: '
             f'<a href="{P}/shops/s1">{P}/shops/s1</a></p>'
         )),
    dict(key="training_calendar", title="Training Calendar", category="recurring",
         default_order=80, preload=True, dynamic_source="events_calendar",
         body_html=(
             "<p><em>(Live training calendar — pulled from the portal's events when the "
             "newsletter is composed.)</em></p>"
             f'<p>View the full events schedule any time at '
             f'<a href="{P}/events">{P}/events</a>.</p>'
         )),
    dict(key="alternate_uniform", title="Alternate Uniform", category="recurring",
         default_order=90, preload=True,
         body_html=(
             "<p>Command would like everyone to assemble a pair of combat clothes "
             "(top and pants) in M81 woodland, meant to be worn under your kit. The "
             "purpose is to have a uniform that visually distinguishes us from various "
             "government organizations and provides OPFOR options. This is not an "
             "immediate priority — procure at your own pace.</p>"
         )),
    dict(key="essential_links", title="Essential Links", category="recurring",
         default_order=100, preload=True,
         body_html=(
             "<p>Everything you need lives in the portal:</p>"
             "<ul>"
             f'<li>Battle Library: <a href="{P}/library">{P}/library</a></li>'
             f'<li>Events &amp; Calendar: <a href="{P}/events">{P}/events</a></li>'
             f'<li>Roster &amp; Chain of Command: <a href="{P}/roster">{P}/roster</a></li>'
             f'<li>Donate: <a href="{P}/donate">{P}/donate</a></li>'
             "</ul>"
         )),
    dict(key="online_training", title="Weekly Online Training", category="recurring",
         default_order=110, preload=True,
         body_html=(
             "<p>We conduct online training every week in <strong>Nextcloud Talk</strong> "
             "on Tuesdays at 2000. The schedule:</p>"
             "<ul>"
             "<li>Training Night A — 1st Tuesday — Varied Subjects</li>"
             "<li>Training Night B — 2nd Tuesday — FTX AAR</li>"
             "<li>Training Night C — 3rd Tuesday — Land Nav</li>"
             "<li>Training Night D — 4th Tuesday — Comms</li>"
             "</ul>"
         )),
    dict(key="uniform_patches", title="Uniform Patches", category="recurring",
         default_order=120, preload=True,
         body_html=(
             "<p>Legion patches, state patches, and TSM nametapes are available for "
             "sale at FTXs — $5.00 each. Cash accepted on-site.</p>"
         )),
    dict(key="volunteer", title="Volunteer Opportunities", category="recurring",
         default_order=130, preload=True,
         body_html=(
             "<p>We're always looking for volunteer opportunities for the Legion. If you "
             "have a suggestion, bring it up with your team leader or post it in the "
             "volunteering channel on Nextcloud Talk.</p>"
         )),
    dict(key="donations", title="Donations", category="recurring",
         default_order=140, preload=True,
         body_html=(
             "<p>If you'd like to help cover the cost of chow, training supplies, or save "
             "toward big purchases like a trailer or HF radio, you can donate to the unit "
             f'here: <a href="{P}/donate">{P}/donate</a>. Every cent helps.</p>'
         )),
]


async def seed_section_templates(db) -> int:
    """Insert-if-missing by key. Returns count inserted. Never clobbers existing
    rows (preserves admin edits)."""
    from sqlalchemy import select
    from app.models.newsletter_section import NewsletterSectionTemplate

    existing = set((await db.execute(select(NewsletterSectionTemplate.key))).scalars().all())
    added = 0
    for spec in SECTION_TEMPLATES:
        if spec["key"] in existing:
            continue
        db.add(NewsletterSectionTemplate(**spec))
        added += 1
    if added:
        await db.commit()
    return added
