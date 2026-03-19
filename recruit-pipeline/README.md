# S1 Recruit Pipeline

Automated recruit pipeline for the 13th Legion, Texas State Militia.

## Components

1. **Nextcloud Form** — Public application form (replaces JotForm)
   - Form ID: 3
   - Public URL: `https://cloud.13thlegion.org/apps/forms/s/j8De2RnYsi78YZWN`
   - External share: `https://cloud.13thlegion.org/apps/forms/s/Sia3N7Bn7wCW3fLPLZRGP3Tm`

2. **Nextcloud Deck Board** — Kanban pipeline
   - Board ID: 5 ("S1 — Recruit Pipeline")
   - Stacks: New Application (11) → Background Check (12) → Interview (13) → Documents & Payment (14) → Approved — Onboarding (15) → Complete (16)
   - Shared with: levi.kavadas, adam.locy, jessica.eastman

3. **recruit-daemon.py** — Automation glue
   - Polls NC Forms API for new submissions
   - Creates Deck cards with applicant info
   - (Future) Watches for cards moved to "Approved" → auto-onboards

## Pipeline Flow

```
Applicant fills form → Daemon creates Deck card in "New Application"
                     → Recruiter drags to "Background Check"
                     → Recruiter runs check, drags to "Interview"
                     → Interview complete, drags to "Documents & Payment"
                     → NDA signed + $50 paid, drags to "Approved — Onboarding"
                     → Daemon auto-creates NC account, sends welcome email
                     → Card moves to "Complete"
```

## Deployment

```bash
# On 167.172.233.122
mkdir -p /opt/recruit-pipeline
cp recruit-daemon.py /opt/recruit-pipeline/
pip3 install requests

# Test
python3 /opt/recruit-pipeline/recruit-daemon.py --once --dry-run

# Run as service (systemd unit TBD)
python3 /opt/recruit-pipeline/recruit-daemon.py --poll-interval 60
```

## TODO

- [ ] Deploy daemon to NC server
- [ ] Create systemd service
- [ ] Implement full onboarding automation (NC account creation, group assignment, welcome email)
- [ ] File upload support (DL, LTC, DD-214) — NC Forms may need workaround
- [ ] Digital NDA/waiver e-signature
- [ ] Payment tracking integration
- [ ] Geographic team assignment from address
- [ ] Offboarding automation (reverse pipeline)
- [ ] Notification to S1 on new submissions
