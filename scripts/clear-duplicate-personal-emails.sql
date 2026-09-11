-- Clear personal_email where it merely duplicates the official address.
--
-- Unit policy (Cav, 2026-09-10): personal_email may be anything EXCEPT the
-- member's own official Proton address. It exists so there is a SECOND way to
-- reach someone; a byte-for-byte copy of the official address makes it useless.
--
-- 27 rows were in that state when the policy was written -- legacy data entry,
-- not intent. app/services/email_policy now blocks new occurrences via
-- member_edit and contact_edit; this clears the existing ones.
--
-- Comparison is case-insensitive: Romanovtsm@proton.me and romanovtsm@proton.me
-- are the same mailbox, so both count as duplicates.
--
-- Run inside a transaction. The CONTROL count must not move.

\pset border 2
\echo '=== BEFORE ==='
SELECT
  count(*)                                                            AS members_total,
  count(*) FILTER (WHERE personal_email IS NOT NULL)                  AS have_personal,
  count(*) FILTER (WHERE personal_email IS NOT NULL
                     AND lower(personal_email) = lower(email))        AS duplicates_to_clear,
  count(*) FILTER (WHERE personal_email IS NOT NULL
                     AND lower(personal_email) <> lower(email))        AS control_genuine_personal
FROM members;

\echo '=== rows that WILL be cleared ==='
SELECT id, nc_username, email AS official, personal_email
FROM members
WHERE personal_email IS NOT NULL
  AND lower(personal_email) = lower(email)
ORDER BY nc_username;

BEGIN;

UPDATE members
   SET personal_email = NULL,
       updated_at     = now()
 WHERE personal_email IS NOT NULL
   AND lower(personal_email) = lower(email);

\echo '=== AFTER (inside txn) ==='
SELECT
  count(*)                                                            AS members_total,
  count(*) FILTER (WHERE personal_email IS NOT NULL)                  AS have_personal,
  count(*) FILTER (WHERE personal_email IS NOT NULL
                     AND lower(personal_email) = lower(email))        AS duplicates_remaining,
  count(*) FILTER (WHERE personal_email IS NOT NULL
                     AND lower(personal_email) <> lower(email))        AS control_genuine_personal
FROM members;

-- duplicates_remaining must be 0 and control_genuine_personal must equal the
-- BEFORE value. Verify both in the output, then COMMIT.
COMMIT;

\echo '=== AFTER COMMIT ==='
SELECT
  count(*)                                                            AS members_total,
  count(*) FILTER (WHERE personal_email IS NOT NULL)                  AS have_personal,
  count(*) FILTER (WHERE personal_email IS NOT NULL
                     AND lower(personal_email) = lower(email))        AS duplicates_remaining,
  count(*) FILTER (WHERE personal_email IS NOT NULL
                     AND lower(personal_email) <> lower(email))        AS control_genuine_personal
FROM members;
