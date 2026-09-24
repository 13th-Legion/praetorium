# 13th Legion — S4 (Logistics) Dashboard Scope

**Status:** Scoped + approved by S4 (2026-09-24). Ready to build.
**Target:** Next sprint (after ribbon rack + S3 training-block dashboard).
**Last updated:** 2026-09-24

---

## Purpose
A dedicated S4 dashboard in the Portal (Praetorium) to manage unit logistics — meals, money, gear, and inventory — tied into existing FTX/RSVP and roster data.

---

## Proposed Feature Areas

### 1. Meals & Headcount
- Event meal planning tied directly to FTX attendance.
- Manage active meal slots per event: **Sat B/L/D, Sun B** (Sat dinner + Sun breakfast standard; Sat breakfast/lunch optional).
- **Auto-calculated headcount** based on RSVP data (headcount = RSVP count; rarely anyone opts out).
- No dietary tracking needed.

### 2. Expense Reimbursement
- Digital form for members to submit receipts for out-of-pocket unit expenses (FTX groceries, site fees, targets, etc.).
- S4 approval queue + reimbursement tracking.
- **FORMALIZED (S4 decision 2026-09-24):** members submit a receipt upload (photo/PDF) + amount, S4 reviews → approves → marks reimbursed when paid. Full flow below.
- Reimbursement is freeform today: Blackout (SGT Wall) buys and gets reimbursed via Zelle/cash. Bunker has donated groceries; Matos covered March FTX meals.

### 3. Purchase Requests
- Form for **Leaders and S4 staff** to request unit funds for new gear/supplies.
- Fields: item URL, estimated cost, quantity, justification.
- **Approval:** Command (CO/XO/1SG/PSG/PL) **or** S4 Head.
- Status flow: **Pending → Approved → Purchased → Received into Inventory.**

### 4. Equipment Donations
- Unit-wide form for members to **offer** personal gear to unit supply.
- S4 reviews/accepts → item enters **"Pending Acquisition."**
- Only added to the official books once S4 **physically receives** the item.

### 5. Supply Inventory
- Master inventory of unit property.
- **QR code tracking** + check-in/out system for gear issued to members.
- **APPROVED (S4 decision 2026-09-24):** build the QR tracker **plus a possession log** — track who has what, whether it's been turned back into supply, condition on return, etc.

---

## Design Notes / Cross-cutting
- **Approval roles** reuse existing RBAC: Command (CO/XO/1SG/PSG/PL) and S4 Head/staff. Verify billets against roster DB at build time.
- **Inventory pipeline ties together** Purchase Requests (#3 "Received into Inventory") and Equipment Donations (#4 "Pending Acquisition" → received) → both feed Supply Inventory (#5).
- **Meals (#1)** is the priority per April scoping; Expense Reimbursement (#2) is core. Equipment/inventory (#3–5) is the larger follow-on.

## Open Questions — RESOLVED (S4 feedback 2026-09-24)
1. **Scope** — ✅ Yes, aligns. Nothing missing or overkill.
2. **Reimbursement** — ✅ Formalize it. S4 asked for a concrete example (see below).
3. **QR inventory** — ✅ Build it, plus a possession log (who has what / returned to supply / etc.).

## Formalized Reimbursement Flow (what "formalize" means concretely)
1. Member submits an expense: title, amount, which event it was for, and a **receipt upload** (photo or PDF — grocery receipt, site fee, target receipt).
2. It lands in an S4 **review queue** as `pending`.
3. S4 (Wall) reviews the receipt, confirms the amount, and **approves**.
4. When actually paid out (Zelle/cash, as today), S4 marks it **reimbursed** with the date + who paid.
5. Or **rejects** with a reason (missing/illegible receipt, wrong amount, not a unit expense).

→ No part of this is new schema work: `s4_expenses` already has `receipt_url`, `amount`, `status` (pending/approved/reimbursed/rejected), `reimbursed_at`, `reimbursed_by_id`. The only new thing is the upload UI + the queue view.

## Possession Log = the existing `s4_checkouts` table
The "possession log" S4 described maps 1:1 onto `s4_checkouts`: `item_id`, `member_id`, `checked_out_at`/`checked_out_by`, `expected_return_at`, `checked_in_at`/`checked_in_by`, `return_condition`, `notes`. A possession log = list of rows where `checked_in_at IS NULL` (currently out), plus history of returns. And `s4_inventory_items` already has a `qr_code` column. So #5 is UI + QR generation on top of tables that already exist.

## History
- **2026-04-15:** First scoping with Cav — identified Meals + Expense as core, equipment as follow-on. Other functions named: site logistics, transportation, water/consumables, packing lists.
- **2026-06-01:** Expanded proposal (adds Purchase Requests, Equipment Donations, QR Inventory) posted to S4 chat for feedback before UI build.
- **2026-09-24:** S4 answered open questions: scope ✅, formalize reimbursement ✅, build QR + possession log ✅. Spec locked.
