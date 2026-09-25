# 13th Legion — S2 (Intelligence & Security) Portal Dashboard Scope

**Status:** Draft for S2 review. Questions dropped into S2 chat by Cav (2026-09-24).
**S2 Shop Head:** Harpoon (SGT Standlee) — replaced Romeo (Gonzalez) 2026-05-19.
**Target:** TBD (post S2 feedback).

---

## Purpose
An S2 dashboard inside Praetorium (the portal) covering S2's FTX responsibilities,
intel products, and a unit intel/alert reporting system — integrating the existing
standalone Texas Area Studies intel site (https://13thlegion.org/intel/) and adding
a reporting/alert layer.

---

## Known FTX responsibilities (from S2 chat, 2026-09-24)
S2 generally provides, per FTX:
- Rally point
- Route from RP to AO
- Weather report
- Code words
- **Challenge / password / running password** (Cav, 2026-09-24) — see below
- Coordinate early arrival and security
- **OPEN:** anything else?

**De-flocked routes (Harpoon, 2026-09-24):** routes are built in **Flock Hopper**
(navigation points) and then manually **transferred into Google Maps**. Pain
point: the manual re-entry. S2 may want the dashboard to hold the route
(nav points) so it doesn't have to be hand-copied — format/open question below.

### Challenge / Password / Running Password
Classic recognition/OPSEC signals, distinct from the code words above:
- **Challenge** — what the sentry asks (e.g. "Texas")
- **Password** — the correct reply (e.g. "Star")
- **Running password** — an alternate password for returning patrols / friendly
  elements to positively identify themselves

**Requirement (Cav, 2026-09-24):** set these in **multiple instances** that rotate
through intervals of time and/or missions — i.e. a schedule of challenge/password
sets valid per time window and/or per mission, not a single static set.

---

## Intel products
- **Area studies = own repo, kept standalone** (Cav decision 2026-09-24):
  https://13thlegion.org/intel/ stays its own thing and is **linked** into the S2
  dashboard, not folded into Praetorium. Whole state uses it today.
  - ⚠️ Last content update: **2026-03-02** (~7 months stale, all 7 cities).
  - ⚠️ Live site is **NOT under version control** (no git on web-prod at
    `/var/www/13thlegion.org/htdocs/intel/`; local copy at `~/clawd/projects/intel-deploy/`
    is itself stale). Get it into its own git repo + bring content current.

## Reporting system — IIR (Intelligence Information Report)
The finished, sourced, analytical product (not SALUTE — that's combat info, a field
report). Include in the dashboard.

**LOCKED (Cav, 2026-09-24):** dissemination = **BOTH tiers** (unit-wide front-page
alert + S2/Command-only), and include **all IIR fields**.

**Harpoon (2026-09-24):**
- **SALUTE-style combat info is OUT OF SCOPE** (Cav, 2026-09-24): this dashboard
  is not the place for field combat-info reports. IIR only.
- **Dissemination leans wide** — no current example of security intel that *shouldn't*
  go to the whole company; if it's important enough to share, it probably needs to
  be disseminated. Keep the tiered option (S2/Command-only) available, but default
  thinking is: share it.

Standard IIR fields to model (doctrine format defers to DIAM 58-12, which is
classified and not on hand; these are the well-established IIR sections):
1. **Report number / DTG** — unique id + date-time-group
2. **Country / area** — geographic scope (AO, city, region)
3. **Subject / title** — short summary line
4. **Source** — who/where the info came from (named or controlled source)
5. **Reliability rating** — source reliability (A–F scale, or high/med/low)
6. **Information credibility / confidence** — analytic confidence in the content
7. **Details / body** — the substantive reporting
8. **Assessment** — analyst evaluation of what it means
9. **Remarks / administrative** — dissemination, caveats ("unevaluated
   information, not finally evaluated intelligence"), links to requirements
10. **Dissemination tier** — unit-wide vs S2/Command-only (the portal's own field)

Doctrine note: the manual is explicit that a time-sensitive IIR passed to affected
units must carry the caveat **"unevaluated information, not finally evaluated
intelligence"** — this should render on unit-wide IIR alerts.

## Other S2 features — OPEN
Awaiting S2 feedback.

## Training Site Maps (NEW — Harpoon, 2026-09-24)
S2 should **own the training-site map library**:
- Add / remove **training sites**.
- **Upload maps** per training site.
- These then **populate the FTX event builder's "training site" dropdown**, and the
  selected site's map(s) **auto-populate into the FTX**. (Today there is no interface
  to manage training sites or upload maps — this is a gap.)

---

## Open questions (for S2 feedback, dropped 2026-09-24)
1. FTX responsibilities: anything beyond RP / RP→AO route / weather / code words /
   challenge/password/running-password / early-arrival+security coordination?
2. ~~De-flocked routes: preferred app?~~ → **Flock Hopper for nav points, then
   manually transferred to Google Maps.** OPEN: should the dashboard store the
   route (nav points / GPX / Google Maps link) so it isn't hand-copied?
3. Challenge/password/running-password: how should rotation work — per time
   interval, per mission, or both? Who consumes it (sentry roster, guard duty, all
   leaders)?
4. Intel products: area studies get their own repo, linked into the dashboard
   (not folded in). Content is ~7 months stale + not version-controlled — needs
   a git repo + refresh.
5. Reporting system: **IIR** (not SALUTE). LOCKED: both tiers + all IIR fields.
   Harpoon: dissemination leans wide. SALUTE-style combat info is OUT OF SCOPE
   (Cav 2026-09-24).
6. Other S2 features wanted in the dashboard? → **Training-site map library**
   (S2 manages sites + maps; populates FTX training-site dropdown + auto-maps).

---

## Notes / context
- PP-002 in the Praetorium backlog: "S2 intel products section (RBAC-restricted)" —
  the closest existing story, never specced.
- RBAC is flagged already (intel products are sensitive; will need rank/billet gating).
- Doctrine reference: `~/clawd/references/doctrine/INTELLIGENCE_FRAMEWORKS.md`
  (FM 2-0, FM 2-22.2, FM 3-12).
