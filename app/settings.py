"""Centralized settings — all credentials from env vars."""

import os

# Nextcloud service account (general NC API calls — announcements, calendar, file uploads)
NC_SVC_USER = os.getenv("NC_SVC_USER", "spooky")
NC_SVC_PASS = os.getenv("NC_SVC_PASS", "")

# Nextcloud portal service account (S1 admin operations — user provisioning, group management)
NC_PORTAL_SVC_USER = os.getenv("NC_PORTAL_SVC_USER", "portal-svc")
NC_PORTAL_SVC_PASS = os.getenv("NC_PORTAL_SVC_PASS", "")

# SMTP (Proton Bridge)
SMTP_HOST = os.getenv("SMTP_HOST", "172.21.0.1")
SMTP_PORT = int(os.getenv("SMTP_PORT", "1025"))
SMTP_USER = os.getenv("SMTP_USER", "admin@13thlegion.org")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "13th Legion <admin@13thlegion.org>")

# Reapply URL
REAPPLY_URL = os.getenv("REAPPLY_URL", "https://cloud.13thlegion.org/apps/forms/s/Sia3N7Bn7wCW3fLPLZRGP3Tm")
