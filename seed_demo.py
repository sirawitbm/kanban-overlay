"""
Seed KanbanOverlay.json with a realistic month-by-month demo board.

    python seed_demo.py            # refuses to clobber an existing board
    python seed_demo.py --force    # overwrites, keeping a .bak

Three months of everyday-life tasks, laid out to exercise every branch of
the bucketing rule:

  this month   regular - exactly MAX_PER_CARD open, so it sits as one card
                         right on the boundary, plus two completed ones to
                         show that finished work does not force a split. Two
                         of them fall today, so the bar's counter is not 0
  next month   busy    - thirteen open tasks, so the month splits into weeks,
                         and one of those weeks holds six, so that week
                         splits again into days
  month after  chill   - three open tasks, one quiet card

Plus two overdue items for the pinned red card, and three undated ones for
Someday.

Dates are computed from today, and the busy week is anchored to a real
Monday, so the demo keeps its shape whenever you run it.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORE = HERE / "KanbanOverlay.json"


def month_end(y, m):
    return date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)


def add_months(d, n):
    m = d.month + n
    y, m = d.year + (m - 1) // 12, (m - 1) % 12 + 1
    return date(y, m, min(d.day, month_end(y, m).day))


def mondays_of(y, m):
    first = date(y, m, 1)
    first_mon = first + timedelta(days=(7 - first.weekday()) % 7)
    out, cur = [], first_mon
    while cur.month == m:
        out.append(cur)
        cur += timedelta(days=7)
    return out


def build(today=None):
    today = today or date.today()
    tasks = []

    def add(text, due, status="todo"):
        tasks.append({"id": len(tasks) + 1, "text": text,
                      "due": due.isoformat() if due else None,
                      "status": status})

    # -- overdue, for the pinned card --------------------------------------
    add("Reply to the dentist about rescheduling", today - timedelta(days=4))
    add("Take the recycling out", today - timedelta(days=2))

    # -- this month: regular, exactly at the cap ---------------------------
    end = month_end(today.year, today.month)

    def soon(n):
        return min(today + timedelta(days=n), end)

    add("Grocery run", today)
    add("Water the plants", today)
    add("Call the landlord about the boiler", soon(2))
    add("Swap the winter clothes in", soon(6))
    add("Sunday meal prep", soon(6))
    add("Pick up the dry cleaning", today - timedelta(days=3), "done")
    add("Renew the car insurance", today - timedelta(days=6), "done")

    # -- next month: busy, splits into weeks, one week splits into days ----
    nxt = add_months(date(today.year, today.month, 1), 1)
    mons = mondays_of(nxt.year, nxt.month)
    w0 = mons[0]
    heavy = mons[1] if len(mons) > 1 else mons[0]

    add("Book the car service", w0 + timedelta(days=1))
    add("Pay the electricity bill", w0 + timedelta(days=2))
    add("Mum's birthday dinner", w0 + timedelta(days=5))

    # six in one week -> this week splits down to individual days
    add("Dentist checkup", heavy + timedelta(days=1))
    add("Return the Amazon parcel", heavy + timedelta(days=1))
    add("Renew the gym membership", heavy + timedelta(days=2))
    add("Flu shot at the pharmacy", heavy + timedelta(days=2))
    add("Haircut", heavy + timedelta(days=3))
    add("Passport photos", heavy + timedelta(days=4))

    if len(mons) > 2:
        add("Deep clean the kitchen", mons[2] + timedelta(days=1))
        add("Book December flights", mons[2] + timedelta(days=3))

    nxt_end = month_end(nxt.year, nxt.month)
    add("Sort out a Halloween costume", nxt_end - timedelta(days=1))
    add("Pay the rent", nxt_end)

    # -- month after: chill ------------------------------------------------
    after = add_months(date(today.year, today.month, 1), 2)
    a_end = month_end(after.year, after.month)

    def day_in_after(n):
        return date(after.year, after.month, min(n, a_end.day))

    add("Swap to winter tyres", day_in_after(7))
    add("Friend's housewarming", day_in_after(14))
    add("Book the Christmas train tickets", day_in_after(28))

    # -- someday -----------------------------------------------------------
    add("Learn to make proper ramen", None)
    add("Sort out the loft", None)
    add("Find a new podcast", None)

    return {
        "tasks": tasks,
        "next_id": len(tasks) + 1,
        "pos": None,
        "mode": "frosted",
        "alpha": 0.85,
        "width": 300,
        "show_done": True,
        "active": None,
        "filters": [],
        "panel_open": True,
        # a demo board opens all three modules, docked, so the bar's buttons
        # have something to show straight away
        "modules": {key: {"open": True, "pos": None}
                    for key in ("board", "today", "stats")},
        "auto_hide": False,
    }


def main():
    force = "--force" in sys.argv
    if STORE.exists() and not force:
        print("%s already exists. Re-run with --force to overwrite "
              "(a .bak is kept)." % STORE.name)
        return 1
    if STORE.exists():
        shutil.copy2(STORE, STORE.with_suffix(".json.bak"))
        print("backed up to", STORE.with_suffix(".json.bak").name)

    db = build()
    STORE.write_text(json.dumps(db, indent=2), encoding="utf-8")

    opens = sum(1 for t in db["tasks"] if t["status"] != "done")
    print("wrote %s: %d tasks (%d open, %d done)"
          % (STORE.name, len(db["tasks"]), opens, len(db["tasks"]) - opens))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
