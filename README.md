# Project Praetorium — 13th Legion Unit Portal

Internal unit management portal for the 13th Legion (Texas State Militia).

## Stack

- **Backend:** FastAPI (Python 3.12)
- **Frontend:** Jinja2 templates + HTMX
- **Database:** PostgreSQL 16
- **Auth:** Nextcloud OAuth2 SSO
- **Deployment:** Docker Compose + nginx reverse proxy

## Quick Start (Development)

```bash
# Copy env file and fill in values
cp .env.example .env

# Start services
docker compose up -d

# Run initial migration
docker compose exec app alembic upgrade head

# Access at http://localhost:8100
```

## Project Structure

```
portal/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── auth.py              # NC OAuth2 + RBAC helpers
│   ├── database.py          # SQLAlchemy engine/session
│   ├── models/              # ORM models
│   ├── routes/              # Route handlers
│   ├── templates/           # Jinja2 + HTMX templates
│   └── static/              # CSS, JS, images
├── migrations/              # Alembic migrations
├── config.py                # Pydantic settings
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Architecture Decisions

- **FastAPI over Django/Flask:** Async-native, lightweight, great for internal tools. No unnecessary batteries.
- **HTMX over React/Vue:** Server-rendered HTML with targeted interactivity. No JS build step, no SPA complexity. Perfect for an internal portal with ~50-500 users.
- **PostgreSQL over SQLite:** Multi-company future (TSM-wide), concurrent access from daemon + web app.
- **NC OAuth2 SSO:** Single identity across Nextcloud + Portal. NC groups drive RBAC automatically.

## RBAC Model

Portal roles are derived from Nextcloud group memberships, synced on every login:

| NC Group | Portal Role | Access Level |
|----------|------------|--------------|
| admin | admin | Full access |
| Command | command | Full access |
| Rank - Officer | officer | All profiles, all data |
| Rank - NCO | nco | Subordinates' full data |
| Rank - Patched | patched | Own team + limited roster |
| Rank - Recruit | recruit | Own profile only |
| [S-1] through [S-6] | s1-s6 | Shop-specific views |
| Team - {name} | team_{name} | Team-specific views |
