-- Audit member email addresses against unit policy.
-- Keep the domain list in step with app/services/email_policy.PROTON_DOMAINS.
-- Run: docker cp scripts/audit-email-policy.sql praetorium-db:/tmp/a.sql &&
--      docker exec -i praetorium-db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -U praetorium -d praetorium -f /tmp/a.sql'
\pset border 2
\echo '=== A. OFFICIAL address is NOT a Proton address (policy violation) ==='
SELECT id, nc_username, email AS official, personal_email
FROM members
WHERE status IN ('active','recruit')
  AND ( email IS NULL OR email = ''
        OR ( lower(email) NOT LIKE '%@proton.me'
             AND lower(email) NOT LIKE '%@protonmail.com'
             -- @13thlegion.org is provisioned through Proton for Business, so it
             -- IS a Proton mailbox. Keep this in step with
             -- app/services/email_policy.PROTON_CUSTOM_DOMAINS.
             AND lower(email) NOT LIKE '%@13thlegion.org' ) )
ORDER BY nc_username;

\echo '=== B. personal_email equals the official address (policy violation) ==='
SELECT id, nc_username, email AS official, personal_email
FROM members
WHERE status IN ('active','recruit')
  AND personal_email IS NOT NULL
  AND lower(personal_email) = lower(email)
ORDER BY nc_username;

\echo '=== C. personal_email is a Proton address (worth eyeballing) ==='
SELECT id, nc_username, email AS official, personal_email
FROM members
WHERE status IN ('active','recruit')
  AND personal_email IS NOT NULL
  AND ( lower(personal_email) LIKE '%@proton.me'
        OR lower(personal_email) LIKE '%@protonmail.com'
        OR lower(personal_email) LIKE '%@13thlegion.org' )
ORDER BY nc_username;

\echo '=== D. coverage counts ==='
SELECT count(*) AS members,
       count(*) FILTER (WHERE personal_email IS NOT NULL) AS have_personal,
       count(*) FILTER (WHERE lower(email) LIKE '%@proton.me') AS proton_me,
       count(*) FILTER (WHERE lower(email) LIKE '%@protonmail.com') AS protonmail_com,
       count(*) FILTER (WHERE lower(email) LIKE '%@13thlegion.org') AS unit_domain
FROM members WHERE status IN ('active','recruit');
