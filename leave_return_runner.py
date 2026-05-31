#!/usr/bin/env python3
"""Daily leave-of-absence auto-return runner.
Clears on_leave for members whose leave_end has passed, removes them from the
NC on-leave group, and emails them a return notice. Run via host cron daily.
Executed inside the praetorium-app container (has app + DB env)."""
import asyncio
import sys


async def main():
    from app.database import async_session
    from app.routes.s1_admin import _process_leave_returns

    async with async_session() as db:
        processed = await _process_leave_returns(db)

    if not processed:
        print("No leave returns due.")
        return
    for m, emailed in processed:
        print(f"Returned from leave: {m.first_name} {m.last_name} "
              f"(leave_end={m.leave_end}) email_sent={emailed}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
