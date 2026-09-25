"""
Overlay Board
=============
A see-through, always-on-top planning board for Windows. It sits over
whatever you are actually working in (IDE, browser, game) and shows what is
left and what is done, without taking a taskbar slot or a chunk of screen.

Run:    pythonw overlay_board.py   (no console window)
        python  overlay_board.py   (console window, useful for debugging)

Deps:   none - standard library tkinter only.

The app is a bar plus a set of modules:

  the bar    - a slim strip, sized to sit over the Windows taskbar (it docks
               there on first run) but draggable anywhere. It carries the
               module buttons and two numbers - due today, and overdue -
               and nothing else, because every field costs length on a strip
               that has to share the taskbar. Always solid, always clickable.
               Hovering a button says what it opens.
  board      - the post-it stack of time buckets, plus the task input
  today      - overdue, today and tomorrow, and nothing else
  stats      - open/late/done at a glance, and the next seven days

Each module is its own window. It starts docked - laid out in a row beside
the bar and following it around - and the moment you drag its header it
detaches, floats wherever you dropped it, and stays there. Double-click the
header, or use "Dock all modules", to send it back. A dot beside a module's
name means it is floating.

There is a tray icon too. Left-click it to bring the app back, right-click
for a short menu, and quit from there. "Hide to tray" puts everything away
and leaves only the icon. It is drawn with Shell_NotifyIcon directly rather
than pystray, to keep the dependency count at zero.

Only the bar is permanent. The modules, the search field and the menus are
transient: they appear while the app has focus and get out of the way the
moment you click back into whatever you were doing. Clicking the bar brings
them back. Turn that off with "Hide when unfocused" in the bar menu.

Ghost mode is exempt from the auto-hide, since reading your tasks while you
work in another window is the entire point of it.

Always-on-top is re-asserted on a timer rather than set once. Tk applies
WS_EX_TOPMOST when a window is created and never again, so anything that
goes topmost later simply ends up above you and stays there.

The panel has two display modes, toggled from the bar menu or with F2:

  frosted - translucent dark cards, fully interactive. Use this to plan.
  ghost   - the card backgrounds are keyed out entirely: only the text
            floats on screen and mouse clicks fall straight through to the
            app behind. Use this while you work. The bar never goes ghost,
            so there is always something solid to click back on.

Search is Spotlight-style: click the magnifier on the bar (or Ctrl+F) and a
large input opens in the middle of the screen, listing live matches as you
type. Enter attaches the term to the bar as a filter block with its own
discard button; clicking that button drops the filter and detaches the
block. Several filters stack, and they narrow with AND.

A filter narrows the whole board, not just the view - the dashboard counts,
the card splitting and the meter all reflect the filtered set, so a filtered
month that drops under the cap collapses back into one card.

Sections are TIME buckets, not stages, and they split themselves:

  - tasks group by month
  - a month holding more than MAX_PER_CARD open tasks splits into weeks
  - a week holding more than MAX_PER_CARD open tasks splits into days
  - anything open and past due is pulled into a pinned Overdue card
  - anything with no date lands in Someday at the bottom

Completed tasks never count toward the split threshold, so finishing work
collapses the board back down instead of fragmenting it further.

Cards render as a stack of post-its: each shows only its top strip (title +
open/total) until clicked, which brings that card to the front and expands
its task list. Inside a card, click the glyph to cycle a task through
todo -> doing -> done, double-click the text to edit it.

Form factor owes a debt to TBH: Task Bar Hero - a thing docked to the
taskbar that you glance at rather than attend to.

Typing a task accepts a trailing @date shorthand:

    Fix login bug @fri          Call vendor @tomorrow     Ship v1 @2026-10-03
    Renew domain @+2w           Standup @mon              Invoice @10/3

State lives in OverlayBoard.json next to this script - tasks, window
position, mode, opacity. Plain JSON, safe to hand-edit while closed.

Known limitations:
  - Windows only. -transparentcolor (the ghost mode key) and the taskbar
    docking both lean on Win32; elsewhere the bar centres itself at the
    bottom of the screen and only frosted mode is offered.
  - Ghost mode is read-only by design: clicks pass through, so a task cannot
    be ticked off without toggling back to frosted first.
  - Hotkeys fire only while a window of the app has focus. The bar is always
    clickable, which is the deliberate escape hatch.
  - Filters are plain case-insensitive substring matches on task text. There
    is no field syntax (no due:, no status:).
  - The Opacity setting governs the panel only. The bar deliberately ignores
    it and stays near-solid, since it is the one thing always clickable.
  - Weeks are Monday-start and clipped to the month they split out of, so a
    week straddling a month boundary can show up as two short cards.
  - The panel does not scroll; a very tall stack can run off screen. Collapse
    cards, filter, or clear completed if that happens.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import sys
import tkinter as tk
from ctypes import wintypes
from datetime import date, timedelta
from pathlib import Path
from tkinter import font as tkfont

__version__ = "0.1.0"

HERE = Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else HERE


def data_dir():
    """Return a writable state directory for source, installed, or portable use."""
    if not getattr(sys, "frozen", False):
        return HERE
    if (APP_DIR / "portable.flag").exists():
        return APP_DIR
    local_appdata = os.environ.get("LOCALAPPDATA")
    return Path(local_appdata) / "OverlayBoard" if local_appdata else APP_DIR


STORE = data_dir() / "OverlayBoard.json"
BACKUP_STORE = STORE.with_suffix(".json.bak")

MAX_PER_CARD = 5                    # more open tasks than this -> split finer
WIDTHS = (260, 300, 360, 440)
ALPHAS = (0.55, 0.70, 0.85, 0.95, 1.0)
BAR_ALPHA = 0.97                    # the bar ignores the panel opacity setting

# -- geometry, px -----------------------------------------------------------
BAR_H = 34                          # tuned to sit inside a default taskbar
BAR_MIN_W = 300
BAR_PAD = 9
BAR_GAP = 6                         # bar to panel
ICON_W = 26
METER_H = 5
CHIP_H = 20
SEP_W = 13                          # gap that carries a divider line
SPOT_W = 620                        # the centred search field
SPOT_PAD = 18
SPOT_ROW = 26
SPOT_MAX = 5                        # live matches listed under the input
FOCUS_POLL_MS = 250                 # how often focus is re-checked
PIN_EVERY = 8                       # re-assert topmost every Nth focus poll
TIP_DELAY_MS = 450                  # hover dwell before a tooltip appears

PAD = 10                            # window margin around the card stack
TOP_H = 8                           # panel margin above the stack
HDR_H = 30                          # visible strip of a collapsed card
OVERLAP = 7                         # how far a card tucks under the one above
ROW_H = 23                          # one task row
BODY_TOP = 3
BODY_BOT = 9
GAP = 5                             # breathing room under the expanded card
ENTRY_H = 28

# -- colors -----------------------------------------------------------------
KEY = "#0b0c0d"                     # keyed out; never used as a text color
CARD_BG = "#17181d"
CARD_EDGE = "#2f323b"
GHOST_HALO = "#000000"              # 1px outline behind ghost-mode text
TEXT = "#e8e8ea"
TEXT_DIM = "#8a8d96"
TEXT_DONE = "#5f636d"

BAR_BG = "#101216"
BAR_EDGE = "#33373f"
CHIP_BG = "#24303c"
CHIP_FG = "#8ecdf5"
METER_BG = "#22252c"
SPOT_BG = "#14161b"
HOVER_BG = "#22252c"
ENTRY_BG = "#1e2027"                # lighter than a card, so it reads as input

MENU_BG = "#191b21"
MENU_EDGE = "#3a3f49"
MENU_HOVER = "#272c35"
MENU_SEP = "#2b2f38"
MENU_W = 258
MENU_ROW = 28
MENU_CHOICE_H = 32
MENU_SEP_H = 9
MENU_PAD_X = 13
MENU_PAD_Y = 6
CHECK = "✓"

ACCENT_OVERDUE = "#ff7a6b"
ACCENT_SOMEDAY = "#9aa0ab"
ACCENTS = ("#ffd166", "#7ee787", "#79c0ff", "#d2a8ff", "#ffa657", "#5eead4")

GLYPH = {"todo": "○", "doing": "◐", "done": "●"}
NEXT_STATUS = {"todo": "doing", "doing": "done", "done": "todo"}
CARET = {True: "▾", False: "▸"}
DASH = "–"

FONT_HDR = ("Segoe UI Semibold", 9)
FONT_ROW = ("Segoe UI", 9)
FONT_DIM = ("Segoe UI", 8)
FONT_BAR = ("Segoe UI", 9)
FONT_BAR_B = ("Segoe UI Semibold", 9)
FONT_CHIP = ("Segoe UI", 8)
FONT_SPOT = ("Segoe UI Light", 19)
FONT_SPOT_ROW = ("Segoe UI", 10)

# Segoe MDL2 Assets ships with Windows 10/11; the fallbacks are plain Segoe
# UI characters so the bar still reads on a box that somehow lacks it.
ICON_FONT = "Segoe MDL2 Assets"
ICONS = {"search": "", "up": "", "down": "",
         "more": "", "close": "", "grip": "",
         "board": "", "today": "", "stats": ""}
ICONS_ASCII = {"search": "⌕", "up": "▴", "down": "▾",
               "more": "⋮", "close": "×", "grip": "≡",
               "board": "≡", "today": "▤", "stats": "▐"}

# every module is a separate always-on-top window the bar can summon
MODULE_KEYS = ("board", "today", "stats")
MOD_HDR_H = 22                      # the strip you grab to move a module
MOD_GAP = 8                         # between modules docked in a row
STAT_BAR_H = 26                     # the seven-day histogram

DEFAULTS = {
    "tasks": [],
    "next_id": 1,
    "pos": None,                    # bar position; the panel hangs off it
    "mode": "frosted",
    "alpha": 0.85,
    "width": 300,                   # panel width, not bar width
    "show_done": True,
    "active": None,
    "filters": [],
    "panel_open": True,             # legacy; migrated into modules["board"]
    "auto_hide": True,              # modules only while the app has focus
    "modules": {},                  # per module: {"open": bool, "pos": [x, y]}
}


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------

def _read_state(path):
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return saved if isinstance(saved, dict) else None


def _valid_pos(pos):
    return (isinstance(pos, (list, tuple)) and len(pos) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in pos))


def _clean_modules(db):
    """Normalise per-module state, migrating the old single-panel flag.

    A module with pos None is docked and gets laid out beside the bar; any
    other pos means the user dragged it somewhere and that is where it goes.
    """
    saved = db.get("modules") if isinstance(db.get("modules"), dict) else {}
    out = {}
    for key in MODULE_KEYS:
        entry = saved.get(key) if isinstance(saved.get(key), dict) else {}
        pos = entry.get("pos")
        out[key] = {
            "open": bool(entry.get("open", key == "board")),
            "pos": [int(pos[0]), int(pos[1])] if _valid_pos(pos) else None,
        }
    if "board" not in saved and isinstance(db.get("panel_open"), bool):
        out["board"]["open"] = db["panel_open"]
    return out


def load_state():
    db = dict(DEFAULTS)
    saved = _read_state(STORE)
    if saved is None:
        saved = _read_state(BACKUP_STORE)
    if saved:
        db.update({k: v for k, v in saved.items() if k in DEFAULTS})

    clean = []
    used_ids = set()
    raw_tasks = db["tasks"] if isinstance(db["tasks"], list) else []
    for t in raw_tasks:
        if isinstance(t, dict) and t.get("text"):
            try:
                task_id = int(t.get("id") or 0)
            except (TypeError, ValueError):
                task_id = 0
            if task_id <= 0 or task_id in used_ids:
                task_id = 0
            else:
                used_ids.add(task_id)
            clean.append({
                "id": task_id,
                "text": str(t["text"]),
                "due": t.get("due") or None,
                "status": t["status"] if t.get("status") in GLYPH else "todo",
            })
    next_id = 1
    for t in clean:
        if not t["id"]:
            while next_id in used_ids:
                next_id += 1
            t["id"] = next_id
            used_ids.add(next_id)
    db["tasks"] = clean
    db["next_id"] = max([t["id"] for t in clean] + [0]) + 1
    raw_filters = db["filters"] if isinstance(db["filters"], list) else []
    db["filters"] = [str(f) for f in raw_filters if str(f).strip()]
    db["show_done"] = db["show_done"] if isinstance(db["show_done"], bool) else True
    db["panel_open"] = db["panel_open"] if isinstance(db["panel_open"], bool) else True
    db["auto_hide"] = db["auto_hide"] if isinstance(db["auto_hide"], bool) else True
    if db["active"] is not None and not isinstance(db["active"], str):
        db["active"] = None

    db["modules"] = _clean_modules(db)

    # a hand-edited or truncated store should not crash the placement maths
    pos = db.get("pos")
    if not (isinstance(pos, (list, tuple)) and len(pos) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in pos)):
        db["pos"] = None
    if db["mode"] not in ("frosted", "ghost"):
        db["mode"] = "frosted"
    try:
        db["alpha"] = min(1.0, max(0.3, float(db["alpha"])))
        db["width"] = max(200, min(900, int(db["width"])))
    except (TypeError, ValueError):
        db["alpha"], db["width"] = DEFAULTS["alpha"], DEFAULTS["width"]
    return db


# ---------------------------------------------------------------------------
# run at login
# ---------------------------------------------------------------------------

def startup_shortcut():
    """Path of the launcher in the user's Startup folder.

    A .cmd rather than a .lnk: shortcuts need COM, and this has no
    dependencies to spend.
    """
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu" /
            "Programs" / "Startup" / "OverlayBoard.cmd")


def startup_enabled():
    f = startup_shortcut()
    return bool(f and f.exists())


def set_startup(on):
    f = startup_shortcut()
    if not f:
        return False
    try:
        if on:
            if getattr(sys, "frozen", False):
                command = 'start "" "%s"' % Path(sys.executable).resolve()
            else:
                pyw = Path(sys.executable).with_name("pythonw.exe")
                if not pyw.exists():
                    pyw = Path(sys.executable)
                command = 'start "" "%s" "%s"' % (pyw, Path(__file__).resolve())
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("@echo off\r\n%s\r\n" % command, encoding="utf-8")
        elif f.exists():
            f.unlink()
        return True
    except OSError as exc:
        print("could not change startup:", exc, file=sys.stderr)
        return False


def save_state(db):
    """Write the store atomically.

    Every edit saves, so a crash or a kill mid-write is not far-fetched, and
    a half-written file would take every task with it. Write beside the real
    file and rename over it: os.replace is atomic on Windows and POSIX both,
    so the store is either the old one or the new one, never a fragment.
    """
    tmp = STORE.with_name(STORE.name + ".tmp")
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(db, indent=2), encoding="utf-8")
        if STORE.exists():
            try:
                shutil.copy2(STORE, BACKUP_STORE)
            except OSError:
                pass
        os.replace(tmp, STORE)
    except OSError as exc:
        print("could not save:", exc, file=sys.stderr)
        try:
            tmp.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def to_date(s):
    try:
        return date.fromisoformat(s) if s else None
    except (ValueError, TypeError):
        return None


def month_end(y, m):
    return date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)


def week_start(d):
    return d - timedelta(days=d.weekday())


def parse_due(s, today=None):
    """Turn @date shorthand into a date. None if it does not mean anything."""
    today = today or date.today()
    s = (s or "").strip().lower().lstrip("@")
    if not s:
        return None
    if s in ("today", "tod", "now"):
        return today
    if s in ("tomorrow", "tmr", "tom", "tmrw"):
        return today + timedelta(days=1)
    if s in ("eow", "weekend"):
        return week_start(today) + timedelta(days=6)
    if s == "eom":
        return month_end(today.year, today.month)
    if len(s) <= 9 and s[:3] in WEEKDAYS:
        delta = (WEEKDAYS[s[:3]] - today.weekday()) % 7
        return today + timedelta(days=delta or 7)

    m = re.fullmatch(r"\+(\d+)([dwm])", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit == "d":
            return today + timedelta(days=n)
        if unit == "w":
            return today + timedelta(weeks=n)
        mo = today.month + n
        y, mo = today.year + (mo - 1) // 12, (mo - 1) % 12 + 1
        return date(y, mo, min(today.day, month_end(y, mo).day))

    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})", s)
    if m:
        mo, day = int(m.group(1)), int(m.group(2))
        for y in (today.year, today.year + 1):
            try:
                cand = date(y, mo, day)
            except ValueError:
                return None
            if cand >= today:
                return cand
        return None

    if re.fullmatch(r"\d{1,2}", s):
        day, y, mo = int(s), today.year, today.month
        for _ in range(2):
            if day <= month_end(y, mo).day:
                cand = date(y, mo, day)
                if cand >= today:
                    return cand
            y, mo = y + (mo == 12), (mo % 12) + 1
        return None
    return None


def split_due(raw, today=None):
    """'Fix login bug @fri' -> ('Fix login bug', date).

    An unparseable @token is left in the text rather than silently swallowed,
    so a typo shows up on the card instead of vanishing into Someday.
    """
    m = re.search(r"\s+@(\S+)\s*$", raw)
    if not m:
        return raw.strip(), None
    due = parse_due(m.group(1), today)
    if due is None:
        return raw.strip(), None
    return raw[:m.start()].strip(), due


def fmt_month(y, m):
    return date(y, m, 1).strftime("%B %Y")


def fmt_day(d):
    return "%s %s %d" % (d.strftime("%a"), d.strftime("%b"), d.day)


def fmt_short(d):
    return "%s %d" % (d.strftime("%b"), d.day)


def fmt_range(a, b):
    if a == b:
        return fmt_day(a)
    if a.month == b.month:
        return "%s %d %s %d" % (a.strftime("%b"), a.day, DASH, b.day)
    return "%s %s %s" % (fmt_short(a), DASH, fmt_short(b))


# ---------------------------------------------------------------------------
# bucketing
# ---------------------------------------------------------------------------

class Bucket:
    """One post-it: a span of time plus the (date, task) pairs inside it."""

    def __init__(self, key, title, pairs, accent, show_dates):
        self.key = key
        self.title = title
        self.accent = accent
        self.show_dates = show_dates
        # open first, then completed; each group by date then insertion order
        self.pairs = sorted(
            pairs,
            key=lambda p: (p[1]["status"] == "done", p[0] or date.max, p[1]["id"]),
        )

    @property
    def total(self):
        return len(self.pairs)

    @property
    def open_n(self):
        return sum(1 for _, t in self.pairs if t["status"] != "done")

    def badge(self):
        return "done" if self.open_n == 0 else "%d/%d" % (self.open_n, self.total)

    def rows(self, show_done):
        return [p for p in self.pairs if show_done or p[1]["status"] != "done"]


def _open_n(pairs):
    return sum(1 for _, t in pairs if t["status"] != "done")


def build_buckets(tasks, today, show_done):
    """Month -> week -> day, splitting only where open tasks exceed the cap."""
    dated, undated = [], []
    for t in tasks:
        d = to_date(t.get("due"))
        (dated if d else undated).append((d, t))

    overdue = [(d, t) for d, t in dated if d < today and t["status"] != "done"]
    rest = [(d, t) for d, t in dated if not (d < today and t["status"] != "done")]

    out = []
    if overdue:
        out.append(Bucket("overdue", "Overdue", overdue, ACCENT_OVERDUE, True))

    months = {}
    for d, t in rest:
        months.setdefault((d.year, d.month), []).append((d, t))

    ai = 0
    for y, m in sorted(months):
        items = months[(y, m)]
        if not _open_n(items) and not show_done:
            continue
        accent = ACCENTS[ai % len(ACCENTS)]
        ai += 1

        if _open_n(items) <= MAX_PER_CARD:
            out.append(Bucket("m%04d-%02d" % (y, m), fmt_month(y, m),
                              items, accent, True))
            continue

        weeks = {}
        for d, t in items:
            weeks.setdefault(week_start(d), []).append((d, t))

        for ws in sorted(weeks):
            witems = weeks[ws]
            if not _open_n(witems) and not show_done:
                continue
            lo = max(ws, date(y, m, 1))
            hi = min(ws + timedelta(days=6), month_end(y, m))
            # month in the key: the same Monday can start a week in two months
            wkey = "w%04d-%02d-%s" % (y, m, ws.isoformat())

            if _open_n(witems) <= MAX_PER_CARD:
                out.append(Bucket(wkey, fmt_range(lo, hi), witems, accent, True))
                continue

            days = {}
            for d, t in witems:
                days.setdefault(d, []).append((d, t))
            for d in sorted(days):
                out.append(Bucket("d" + d.isoformat(), fmt_day(d),
                                  days[d], accent, False))

    if undated and (show_done or _open_n(undated)):
        out.append(Bucket("someday", "Someday", undated, ACCENT_SOMEDAY, False))
    return out




def apply_filters(tasks, filters):
    """Case-insensitive substring match on task text, ANDed across filters."""
    for f in filters:
        needle = f.strip().lower()
        if needle:
            tasks = [t for t in tasks if needle in t["text"].lower()]
    return tasks


def summarise(tasks, today):
    """The numbers the bar puts on show."""
    total = len(tasks)
    done = sum(1 for t in tasks if t["status"] == "done")
    open_n = total - done
    late = sum(1 for t in tasks
               if t["status"] != "done" and (to_date(t.get("due")) or date.max) < today)
    upcoming = sorted(
        ((to_date(t.get("due")), t) for t in tasks
         if t["status"] != "done" and to_date(t.get("due"))),
        key=lambda p: p[0])
    return {
        "total": total,
        "done": done,
        "open": open_n,
        "late": late,
        "today": sum(1 for t in tasks if t["status"] != "done"
                     and to_date(t.get("due")) == today),
        "next": upcoming[0] if upcoming else None,
        "frac": (done / total) if total else 0.0,
    }


# ---------------------------------------------------------------------------
# win32 odds and ends
# ---------------------------------------------------------------------------

def work_area():
    """Screen rect minus the taskbar, or None off Windows.

    SPI_GETWORKAREA is what tells us where the taskbar actually is, including
    when it is docked to a side or the top, so the bar can park on it instead
    of guessing at the bottom of the screen.
    """
    try:
        r = wintypes.RECT()
        ok = ctypes.windll.user32.SystemParametersInfoW(0x0030, 0,
                                                        ctypes.byref(r), 0)
        return (r.left, r.top, r.right, r.bottom) if ok else None
    except (AttributeError, OSError):
        return None


_INSTANCE_HANDLE = None


GA_ROOT = 2
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010


def _hwnd(widget):
    """The real top-level window handle behind a Tk widget."""
    handle = widget.winfo_id()
    if os.name != "nt":
        return handle
    try:
        return ctypes.windll.user32.GetAncestor(handle, GA_ROOT) or handle
    except (AttributeError, OSError):
        return handle


def pin_topmost(widget):
    """Re-assert always-on-top without stealing focus.

    Tk's -topmost only sets WS_EX_TOPMOST when the window is created. Any
    window that goes topmost afterwards - another overlay, a notification,
    the shell rearranging itself - simply ends up above it and stays there.
    Nothing tells us when that happens, so the pin is re-applied on a timer.
    SWP_NOACTIVATE keeps it from pulling focus off whatever you are typing in.
    """
    if os.name != "nt":
        return
    try:
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = (
            wintypes.HWND, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT)
        user32.SetWindowPos(_hwnd(widget), ctypes.c_void_p(HWND_TOPMOST),
                            0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except (AttributeError, OSError, tk.TclError):
        pass


def foreground_is_ours():
    """True when the focused window belongs to this process.

    Asked of Windows rather than of Tk: these windows are overrideredirect,
    so Tk's own focus events are unreliable, and this one question covers
    every window we own - bar, panel, search, menus - without tracking each.
    """
    if os.name != "nt":
        return True
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()
    except (AttributeError, OSError):
        return True


ERROR_ALREADY_EXISTS = 183


def acquire_single_instance(name="Local\\OverlayBoard"):
    """Hold a Windows named mutex for the life of the process.

    The use_last_error handle matters: reading the code back through
    kernel32.GetLastError() is itself a foreign call, and ctypes gives no
    guarantee the thread's last error survives it. Getting that wrong fails
    open - two copies of the app running, both writing the same JSON, last
    save wins - so the error comes from ctypes' own saved copy instead.
    """
    global _INSTANCE_HANDLE
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL,
                                      wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if not handle:
        return True                     # fail open rather than block launch
    if err == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _INSTANCE_HANDLE = handle
    return True


def release_single_instance():
    global _INSTANCE_HANDLE
    if _INSTANCE_HANDLE and os.name == "nt":
        ctypes.windll.kernel32.CloseHandle(_INSTANCE_HANDLE)
    _INSTANCE_HANDLE = None




# ---------------------------------------------------------------------------
# shared widget helpers
# ---------------------------------------------------------------------------

class Tooltip(tk.Toplevel):
    """A small label that says what a bar icon will do.

    The bar is nothing but icons, so without this the only way to learn what
    a button opens is to press it.
    """

    def __init__(self, bar, text, anchor):
        super().__init__(bar)
        self.overrideredirect(True)
        self.withdraw()                  # placed before shown, as with menus
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.96)
        self.configure(bg=MENU_EDGE)
        lbl = tk.Label(self, text=text, font=FONT_DIM, fg=TEXT, bg=MENU_BG,
                       padx=8, pady=3)
        lbl.pack(padx=1, pady=1)
        self.update_idletasks()

        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = anchor.winfo_rootx() + (anchor.winfo_width() - w) // 2
        y = anchor.winfo_rooty() - h - 6
        if y < 0:                        # bar docked at the top of the screen
            y = anchor.winfo_rooty() + anchor.winfo_height() + 6
        self.geometry("%dx%d+%d+%d" % (w, h, max(2, min(x, sw - w - 2)),
                                       max(2, min(y, sh - h - 2))))
        self.deiconify()
        pin_topmost(self)


class Scrim(tk.Toplevel):
    """A full-screen invisible catcher, so a click anywhere shuts the menu."""

    def __init__(self, app, on_click):
        super().__init__(app)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.01)
        self.configure(bg="#000000")
        self.geometry("%dx%d+0+0" % (self.winfo_screenwidth(),
                                     self.winfo_screenheight()))
        for ev in ("<Button-1>", "<Button-2>", "<Button-3>"):
            self.bind(ev, lambda e: on_click())


class PopMenu(tk.Toplevel):
    """A menu drawn out of ordinary frames and labels.

    tk.Menu on Windows hands rendering to the OS: it keeps a raised white
    border and ignores most of the colour options, which is why the first
    version looked nothing like the bar it hung off. Everything here is drawn
    by us, so it matches, and it can carry inline choice rows instead of
    flyout submenus.

    Items are dicts:
      {"label", "accel"?, "cmd", "checked"?, "enabled"?, "danger"?}
      {"kind": "sep"}
      {"kind": "choice", "label", "options": [(text, value)], "value", "cmd"}
    """

    def __init__(self, app, x, y, items):
        super().__init__(app)
        self.app = app
        self.overrideredirect(True)
        # stay unmapped until placed: an overrideredirect window that maps
        # before its geometry is set comes up at 0,0, and pin_topmost then
        # nails it there, because SWP_NOMOVE means "keep where you are"
        self.withdraw()
        self.attributes("-topmost", True)
        self.configure(bg=MENU_EDGE)          # the 1px border is this showing

        self.scrim = Scrim(app, self.close)

        body = tk.Frame(self, bg=MENU_BG)
        w = MENU_W
        yy = MENU_PAD_Y
        for it in items:
            kind = it.get("kind", "cmd")
            if kind == "sep":
                tk.Frame(body, bg=MENU_SEP).place(
                    x=MENU_PAD_X, y=yy + MENU_SEP_H // 2,
                    width=w - 2 * MENU_PAD_X, height=1)
                yy += MENU_SEP_H
            elif kind == "choice":
                yy = self._choice(body, it, yy, w)
            else:
                yy = self._command(body, it, yy, w)
        yy += MENU_PAD_Y

        body.place(x=1, y=1, width=w, height=yy)
        self._open(x, y, w + 2, yy + 2)
        self.deiconify()                 # only now does it appear, in place
        self.lift()
        pin_topmost(self.scrim)          # order matters: scrim first, then
        pin_topmost(self)                # the menu, so clicks reach the menu
        self.bind("<Escape>", lambda e: self.close())
        self.focus_force()

    # -- rows ---------------------------------------------------------------

    def _hover(self, parts, cmd):
        def paint(bg):
            for p in parts:
                p.configure(bg=bg)
        for p in parts:
            p.bind("<Enter>", lambda e: paint(MENU_HOVER))
            p.bind("<Leave>", lambda e: paint(MENU_BG))
            p.bind("<Button-1>", lambda e: self.fire(cmd))

    def _command(self, body, it, y, w):
        enabled = it.get("enabled", True)
        checked = it.get("checked")
        fg = TEXT
        if it.get("danger"):
            fg = ACCENT_OVERDUE
        if not enabled:
            fg = TEXT_DONE

        row = tk.Frame(body, bg=MENU_BG, cursor="hand2" if enabled else "arrow")
        row.place(x=0, y=y, width=w, height=MENU_ROW)
        lab = tk.Label(row, text=it["label"], font=FONT_ROW, fg=fg, bg=MENU_BG,
                       anchor="w")
        lab.place(x=MENU_PAD_X, y=0, width=w - MENU_PAD_X - 64, height=MENU_ROW)
        parts = [row, lab]

        if checked is not None:
            mark = tk.Label(row, text=CHECK if checked else "", font=FONT_ROW,
                            fg=ACCENTS[2], bg=MENU_BG, anchor="e")
            mark.place(x=w - MENU_PAD_X - 18, y=0, width=18, height=MENU_ROW)
            parts.append(mark)
        elif it.get("accel"):
            acc = tk.Label(row, text=it["accel"], font=FONT_DIM, fg=TEXT_DONE,
                           bg=MENU_BG, anchor="e")
            acc.place(x=w - MENU_PAD_X - 60, y=0, width=60, height=MENU_ROW)
            parts.append(acc)

        if enabled:
            self._hover(parts, it.get("cmd"))
        return y + MENU_ROW

    def _choice(self, body, it, y, w):
        """A row of inline buttons, standing in for a flyout submenu."""
        row = tk.Frame(body, bg=MENU_BG)
        row.place(x=0, y=y, width=w, height=MENU_CHOICE_H)
        tk.Label(row, text=it["label"], font=FONT_DIM, fg=TEXT_DIM, bg=MENU_BG,
                 anchor="w").place(x=MENU_PAD_X, y=0, width=60,
                                   height=MENU_CHOICE_H)
        opts = it["options"]
        bw = (w - MENU_PAD_X * 2 - 62) // len(opts)
        bx = MENU_PAD_X + 62
        for text, val in opts:
            sel = val == it["value"]
            b = tk.Label(row, text=text, font=FONT_DIM, cursor="hand2",
                         fg=KEY if sel else TEXT_DIM,
                         bg=ACCENTS[2] if sel else MENU_BG)
            b.place(x=bx, y=6, width=bw - 3, height=MENU_CHOICE_H - 12)
            if not sel:
                b.bind("<Enter>", lambda e, b=b: b.configure(bg=MENU_HOVER,
                                                             fg=TEXT))
                b.bind("<Leave>", lambda e, b=b: b.configure(bg=MENU_BG,
                                                             fg=TEXT_DIM))
            b.bind("<Button-1>",
                   lambda e, v=val, c=it["cmd"]: self.fire(lambda: c(v)))
            bx += bw
        return y + MENU_CHOICE_H

    # -- lifecycle ----------------------------------------------------------

    def _open(self, x, y, w, h):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if y + h > sh - 4:              # the bar usually lives at the bottom
            y = max(4, y - h)
        self.geometry("%dx%d+%d+%d" % (w, h, max(4, min(x, sw - w - 4)), y))

    def fire(self, cmd):
        self.close()
        if cmd:
            cmd()

    def close(self):
        if getattr(self.app, "pop", None) is self:
            self.app.pop = None
        for win in (self.scrim, self):
            try:
                win.destroy()
            except tk.TclError:
                pass


# ---------------------------------------------------------------------------
# modules
# ---------------------------------------------------------------------------

class Module(tk.Toplevel):
    """A panel the bar can summon, which the user can then move anywhere.

    Every module is its own always-on-top window. It starts *docked* - laid
    out in a row beside the bar, following it around - and the moment you
    drag its header it becomes *floating* and stays exactly where you put it
    until you send it back. That is the part Task Bar Hero does not do: its
    modules are welded to the main widget.

    Subclasses provide build() for persistent widgets and fill_content() to
    draw the scrollable body, returning its height in pixels.
    """

    key = "module"
    label = "Module"
    icon = "board"
    blurb = ""
    default_w = 260

    def __init__(self, bar):
        super().__init__(bar)
        self.bar = bar
        self.db = bar.db
        self.parts = []
        self.scroll_y = 0
        self.content_h = 40
        self.viewport_h = 40
        self.win_w = self.default_w
        self.win_h = 40
        self.hdr_h = MOD_HDR_H
        self.drag = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=KEY)

        # -transparentcolor is Win32-only; without it ghost mode cannot work
        self.ghost_ok = True
        try:
            self.attributes("-transparentcolor", KEY)
        except tk.TclError:
            self.ghost_ok = False
            self.db["mode"] = "frosted"

        self.font_row = tkfont.Font(font=FONT_ROW)
        self.font_done = tkfont.Font(font=FONT_ROW)
        self.font_done.configure(overstrike=True)

        self._build_header()
        self.canvas = tk.Canvas(self, bg=KEY, borderwidth=0,
                                highlightthickness=0)
        self.content = tk.Frame(self.canvas, bg=KEY)
        self.content_window = self.canvas.create_window(
            0, 0, window=self.content, anchor="nw")
        # sits on the Toplevel rather than inside the canvas, so it stays put
        # while the content slides under it
        self.scrollbar = tk.Frame(self, bg=CARD_EDGE)
        self.bind("<MouseWheel>", self._on_mousewheel)

        self.build()
        self.withdraw()

    # -- state --------------------------------------------------------------

    @property
    def state(self):
        return self.db["modules"][self.key]

    @property
    def is_open(self):
        return bool(self.state["open"])

    @property
    def floating(self):
        return self.state["pos"] is not None

    @property
    def ghost(self):
        return self.db["mode"] == "ghost" and self.ghost_ok

    def width(self):
        return self.default_w

    # -- header, which is also the drag handle ------------------------------

    def _build_header(self):
        self.header = tk.Frame(self, bg=CARD_BG, cursor="fleur")
        self.h_grip = tk.Label(self.header, text=self.bar.icons["grip"],
                               font=(self.bar.icon_family, 8), fg=TEXT_DONE,
                               bg=CARD_BG, cursor="fleur")
        self.h_title = tk.Label(self.header, text=self.label, font=FONT_DIM,
                                fg=TEXT_DIM, bg=CARD_BG, anchor="w",
                                cursor="fleur")
        self.h_close = tk.Label(self.header, text=self.bar.icons["close"],
                                font=(self.bar.icon_family, 8), fg=TEXT_DONE,
                                bg=CARD_BG, cursor="hand2")
        self.h_close.bind("<Button-1>", lambda e: self.bar.close_module(self.key))
        self.h_close.bind("<Enter>",
                          lambda e: self.h_close.configure(fg=ACCENT_OVERDUE))
        self.h_close.bind("<Leave>",
                          lambda e: self.h_close.configure(fg=TEXT_DONE))

        for w in (self.header, self.h_grip, self.h_title):
            w.bind("<ButtonPress-1>", self._press)
            w.bind("<B1-Motion>", self._move)
            w.bind("<ButtonRelease-1>", self._release)
            w.bind("<Double-Button-1>", lambda e: self.bar.dock_module(self.key))
            w.bind("<Button-3>", self._menu_event)

    def _layout_header(self):
        if self.ghost:
            self.hdr_h = 0
            self.header.place_forget()
            return
        self.hdr_h = MOD_HDR_H
        self.header.place(x=0, y=0, width=self.win_w, height=self.hdr_h)
        self.h_grip.place(x=6, y=0, width=14, height=self.hdr_h)
        self.h_title.place(x=22, y=0, width=self.win_w - 60, height=self.hdr_h)
        self.h_close.place(x=self.win_w - 20, y=0, width=15, height=self.hdr_h)
        self.h_title.configure(
            text=self.label if not self.floating else self.label + "  •")

    def _press(self, e):
        self.bar.activate()
        self.drag = {"rx": e.x_root, "ry": e.y_root,
                     "wx": self.winfo_x(), "wy": self.winfo_y(),
                     "at": None}

    def _move(self, e):
        if not self.drag:
            return
        dx, dy = e.x_root - self.drag["rx"], e.y_root - self.drag["ry"]
        if self.drag["at"] is None and abs(dx) + abs(dy) < 4:
            return                        # absorb the jitter of a plain click
        # remember where we put it rather than asking afterwards: winfo_x is
        # only as fresh as the last processed event
        self.drag["at"] = (self.drag["wx"] + dx, self.drag["wy"] + dy)
        self.geometry("+%d+%d" % self.drag["at"])

    def _release(self, e):
        if self.drag and self.drag["at"]:
            # dragging is what detaches a module; there is no other gesture
            self.state["pos"] = list(self.drag["at"])
            self.bar.save()
            self._layout_header()
            self.bar.render_bar()
        self.drag = None

    def _menu_event(self, e):
        items = []
        if self.floating:
            items.append({"label": "Dock to bar",
                          "cmd": lambda: self.bar.dock_module(self.key)})
        else:
            items.append({"label": "Drag the header to detach",
                          "enabled": False, "cmd": None})
        items += [
            {"kind": "sep"},
            {"label": "Close " + self.label, "danger": True,
             "cmd": lambda: self.bar.close_module(self.key)},
        ]
        self.bar.popup(e.x_root, e.y_root, items)

    # -- geometry -----------------------------------------------------------

    def _resize_viewport(self, height):
        self.viewport_h = max(1, int(height))
        self.win_h = self.hdr_h + self.viewport_h
        self._layout_header()
        self.canvas.place(x=0, y=self.hdr_h, width=self.win_w,
                          height=self.viewport_h)
        self._set_scroll(self.scroll_y)

    def _set_scroll(self, offset):
        """Offset the content by moving the canvas item, not the canvas view.

        yview_moveto clamps against the canvas's *realized* height, which is
        still the old one until the geometry manager catches up - so the first
        scroll after a re-render silently did nothing, which is precisely when
        select() and reveal() ask to bring a card into view. Repositioning the
        window item is exact and needs no idle pass.
        """
        limit = max(0, self.content_h - self.viewport_h)
        self.scroll_y = max(0, min(int(offset), limit))
        self.canvas.coords(self.content_window, 0, -self.scroll_y)
        self._sync_scrollbar(limit)

    def _sync_scrollbar(self, limit):
        """Show a thumb only while there is somewhere to scroll to.

        Hidden in ghost mode: a floating bar with no panel behind it would be
        the one opaque thing left on screen.
        """
        if limit <= 0 or self.ghost:
            self.scrollbar.place_forget()
            return
        track = max(1, self.viewport_h - 2 * PAD)
        thumb = max(20, int(track * self.viewport_h / self.content_h))
        y = self.hdr_h + PAD + int((track - thumb) * (self.scroll_y / limit))
        self.scrollbar.place(x=self.win_w - 5, y=y, width=3, height=thumb)
        self.scrollbar.lift()

    def _on_mousewheel(self, event):
        if self.content_h <= self.viewport_h:
            return None
        direction = -1 if event.delta > 0 else 1
        self._set_scroll(self.scroll_y + direction * ROW_H * 3)
        return "break"

    def render(self):
        for w in self.parts:
            w.destroy()
        self.parts = []
        self.win_w = self.width()
        self.content_h = max(20, self.fill_content(KEY if self.ghost else CARD_BG,
                                                   self.ghost))
        self.content.configure(width=self.win_w, height=self.content_h)
        self.canvas.itemconfigure(self.content_window, width=self.win_w,
                                  height=self.content_h)
        self.canvas.configure(scrollregion=(0, 0, self.win_w, self.content_h))
        self.attributes("-alpha", 1.0 if self.ghost else self.db["alpha"])

    def place_docked(self, x, bar_y, above):
        sh = self.winfo_screenheight()
        room = (bar_y - BAR_GAP) if above else (sh - bar_y - BAR_H - BAR_GAP)
        self._fit(room)
        y = (bar_y - BAR_GAP - self.win_h) if above else (bar_y + BAR_H + BAR_GAP)
        self._show_at(x, y)

    def place_floating(self):
        x, y = self.state["pos"]
        self._fit(self.winfo_screenheight() - 2 * BAR_GAP)
        self._show_at(x, y)

    def _fit(self, room):
        self._resize_viewport(min(self.content_h, max(60, room - self.hdr_h)))

    def _show_at(self, x, y):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, min(int(x), sw - self.win_w))
        y = max(0, min(int(y), sh - self.win_h))
        # geometry before deiconify: a window being mapped this same pass can
        # swallow a position set afterwards and come up at 0,0 instead
        self.geometry("%dx%d+%d+%d" % (self.win_w, self.win_h, x, y))
        self.deiconify()
        pin_topmost(self)

    # -- drawing helpers shared by every module -----------------------------

    def label_at(self, parent, text, x, y, w, h, font, fg, bg, ghost,
                 anchor="w"):
        """Place a string, haloed in ghost mode.

        Ghost mode has no panel behind the text, so light text landing on
        light pixels of the app underneath becomes unreadable. Four offset
        copies in black give every glyph a 1px dark outline, which separates
        it from whatever it happens to be sitting on.
        """
        if ghost:
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                tk.Label(parent, text=text, font=font, fg=GHOST_HALO, bg=bg,
                         anchor=anchor).place(x=x + dx, y=y + dy,
                                              width=w, height=h)
        lbl = tk.Label(parent, text=text, font=font, fg=fg, bg=bg, anchor=anchor)
        lbl.place(x=x, y=y, width=w, height=h)
        return lbl

    def task_row(self, parent, t, due, y, inner, bg, ghost, accent,
                 show_date=True):
        """One task line: click the glyph to cycle, right-click for the menu."""
        status = t["status"]
        done = status == "done"
        glyph_fg = {"todo": TEXT_DIM, "doing": accent, "done": TEXT_DONE}[status]

        g = self.label_at(parent, GLYPH[status], 11, y, 18, ROW_H,
                          FONT_ROW, glyph_fg, bg, ghost, anchor="center")
        g.configure(cursor="hand2")

        date_w = 46 if (show_date and due) else 0
        lbl = self.label_at(parent, t["text"], 32, y, inner - 44 - date_w, ROW_H,
                            self.font_done if done else self.font_row,
                            TEXT_DONE if done else TEXT, bg, ghost)

        board = self.bar.board
        g.bind("<Button-1>", lambda e, i=t["id"]: board.cycle(i))
        lbl.bind("<Double-Button-1>", lambda e, i=t["id"]: board.edit(i))
        for w in (g, lbl):
            w.bind("<Button-3>",
                   lambda e, i=t["id"]: self.bar.open_task_menu(e.x_root,
                                                                e.y_root, i))
        if date_w:
            self.label_at(parent, fmt_short(due), inner - 12 - date_w, y, date_w,
                          ROW_H, FONT_DIM, TEXT_DONE if done else TEXT_DIM,
                          bg, ghost, anchor="e")

    # -- subclass hooks -----------------------------------------------------

    def build(self):
        """Create widgets that outlive a render (entries and the like)."""

    def fill_content(self, bg, ghost):
        raise NotImplementedError


class BoardModule(Module):
    """The post-it stack: time buckets that split themselves, plus the input."""

    key = "board"
    label = "Board"
    icon = "board"
    blurb = "Board — every task, grouped by when"

    def width(self):
        return int(self.db["width"])

    def build(self):
        self.buckets = []
        self.bucket_ranges = {}
        self.editing = None
        self.entry_y = None

        self.entry = tk.Entry(self.content, font=FONT_ROW, fg=TEXT, bg=ENTRY_BG,
                              insertbackground=TEXT, relief="flat",
                              highlightthickness=1,
                              highlightbackground=CARD_EDGE,
                              highlightcolor=ACCENTS[2])
        self.entry.bind("<Return>", self.commit_entry)
        self.entry.bind("<Escape>", self.cancel_entry)

        # a real Label rather than placeholder text in the Entry, so an empty
        # field is never mistaken for a typed one by commit_entry
        self.hint = tk.Label(self.content,
                             text="  add a task…   @fri  @+2w  @10/3",
                             font=FONT_DIM, fg=TEXT_DONE, bg=ENTRY_BG,
                             anchor="w", cursor="xterm")
        self.hint.bind("<Button-1>", lambda e: self.focus_entry())
        for ev in ("<KeyRelease>", "<FocusIn>", "<FocusOut>"):
            self.entry.bind(ev, self.sync_hint, add="+")

    def find(self, tid):
        return next((t for t in self.db["tasks"] if t["id"] == tid), None)

    def refresh(self):
        self.bar.refresh()

    def sync_hint(self, e=None):
        """Show the hint only while the field is on screen and empty."""
        if self.entry_y is not None and not self.entry.get():
            self.hint.place(x=PAD + 2, y=self.entry_y + 1,
                            width=self.win_w - 2 * PAD - 4, height=ENTRY_H - 2)
            self.hint.lift()
        else:
            self.hint.place_forget()

    # -- render -------------------------------------------------------------

    def fill_content(self, bg, ghost):
        inner = self.win_w - 2 * PAD
        tasks = apply_filters(self.db["tasks"], self.db["filters"])
        self.buckets = build_buckets(tasks, self.bar.today, self.db["show_done"])
        self.bucket_ranges = {}
        keys = [b.key for b in self.buckets]
        if self.db["active"] is not None and self.db["active"] not in keys:
            self.db["active"] = keys[0] if keys else None

        y = bottom = TOP_H
        for b in self.buckets:
            expanded = b.key == self.db["active"]
            rows = b.rows(self.db["show_done"]) if expanded else []
            body = BODY_TOP + max(1, len(rows)) * ROW_H + BODY_BOT if expanded else 0
            h = HDR_H + body

            card = tk.Frame(self.content, bg=bg)
            card.place(x=PAD, y=y, width=inner, height=h)
            self.parts.append(card)
            self.bucket_ranges[b.key] = (y, y + h)
            self._header(card, b, inner, h, expanded, bg, ghost)
            if expanded:
                self._body(card, b, rows, inner, bg, ghost)

            bottom = y + h
            # collapsed cards tuck under the next one; expanded ones stand clear
            y += (h + GAP) if expanded else (HDR_H - OVERLAP)

        if not self.buckets:
            msg = "no task matches the filter" if self.db["filters"] else \
                  "nothing planned yet"
            self.parts.append(self.label_at(self.content, msg, PAD + 2, y,
                                            inner, 20, FONT_DIM, TEXT_DIM,
                                            bg, ghost))
            bottom = y + 20

        if ghost:
            self.entry.place_forget()
            self.entry_y = None
            total = bottom + PAD
        else:
            self.entry_y = bottom + GAP
            self.entry.place(x=PAD, y=self.entry_y, width=inner, height=ENTRY_H)
            total = bottom + GAP + ENTRY_H + PAD
        self.sync_hint()
        return total

    def _header(self, card, b, inner, h, expanded, bg, ghost):
        hdr = tk.Frame(card, bg=bg, cursor="hand2")
        hdr.place(x=0, y=0, width=inner, height=HDR_H)
        title = self.label_at(hdr, "%s  %s" % (CARET[expanded], b.title),
                              12, 0, inner - 84, HDR_H,
                              FONT_HDR, b.accent, bg, ghost)
        badge = self.label_at(hdr, b.badge(), inner - 70, 0, 58, HDR_H,
                              FONT_DIM, b.accent if ghost else TEXT_DIM, bg,
                              ghost, anchor="e")

        if not ghost:
            # drawn after the header so they sit on top of it: the edge is what
            # makes the stack read as separate sheets rather than one dark slab
            tk.Frame(card, bg=CARD_EDGE).place(x=0, y=0, width=inner, height=1)
            tk.Frame(card, bg=b.accent).place(x=0, y=0, width=3, height=h)

        for w in (hdr, title, badge):
            w.bind("<Button-1>", lambda e, k=b.key: self.select(k))
            w.bind("<Button-3>", self.bar.menu_event)

        if expanded and not ghost:
            tk.Frame(card, bg=CARD_EDGE).place(x=12, y=HDR_H - 1,
                                               width=inner - 24, height=1)

    def _body(self, card, b, rows, inner, bg, ghost):
        if not rows:
            self.label_at(card, "empty", 32, HDR_H + BODY_TOP, inner - 44,
                          ROW_H, FONT_DIM, TEXT_DIM, bg, ghost)
            return
        y = HDR_H + BODY_TOP
        for d, t in rows:
            self.task_row(card, t, d, y, inner, bg, ghost, b.accent,
                          show_date=b.show_dates)
            y += ROW_H

    def scroll_to_bucket(self, key):
        span = self.bucket_ranges.get(key)
        if not span:
            return
        top, bottom = span
        if top < self.scroll_y:
            self._set_scroll(top)
        elif bottom > self.scroll_y + self.viewport_h:
            self._set_scroll(bottom - self.viewport_h)

    # -- actions ------------------------------------------------------------

    def select(self, key):
        # clicking the open card again closes it, so the stack can sit flat
        self.db["active"] = None if self.db["active"] == key else key
        self.bar.save()
        self.refresh()
        if self.db["active"] == key:
            self.scroll_to_bucket(key)

    def cycle(self, tid):
        t = self.find(tid)
        if t:
            t["status"] = NEXT_STATUS[t["status"]]
            self.bar.save()
            self.refresh()

    def set_status(self, tid, status):
        t = self.find(tid)
        if t:
            t["status"] = status
            self.bar.save()
            self.refresh()

    def set_due(self, tid, shorthand):
        t = self.find(tid)
        if not t:
            return
        d = parse_due(shorthand, self.bar.today) if shorthand else None
        t["due"] = d.isoformat() if d else None
        self.bar.save()
        self.refresh()
        self.reveal(tid)

    def delete(self, tid):
        gone = [t for t in self.db["tasks"] if t["id"] == tid]
        self.bar.push_undo(gone)
        self.db["tasks"] = [t for t in self.db["tasks"] if t["id"] != tid]
        if self.editing == tid:
            self.cancel_entry()
        self.bar.save()
        self.refresh()

    def edit(self, tid):
        t = self.find(tid)
        if not t:
            return
        self.editing = tid
        raw = t["text"] + (" @" + t["due"] if t["due"] else "")
        self.entry.configure(highlightbackground=ACCENTS[3],
                             highlightcolor=ACCENTS[3])
        self.entry.delete(0, "end")
        self.entry.insert(0, raw)
        self.sync_hint()
        self.focus_entry()
        self.entry.select_range(0, "end")
        self.entry.icursor("end")

    def focus_entry(self):
        if self.ghost:
            self.bar.toggle_mode()
        self._set_scroll(self.content_h - self.viewport_h)
        self.focus_force()               # overrideredirect windows need a shove
        self.entry.focus_set()

    def cancel_entry(self, e=None):
        self.editing = None
        self.entry.delete(0, "end")
        self.entry.configure(highlightbackground=CARD_EDGE,
                             highlightcolor=ACCENTS[2])
        self.sync_hint()

    def commit_entry(self, e=None):
        raw = self.entry.get().strip()
        if not raw:
            return self.cancel_entry()
        text, due = split_due(raw, self.bar.today)
        if not text:
            return self.cancel_entry()
        iso = due.isoformat() if due else None

        if self.editing is not None:
            t = self.find(self.editing)
            if t:
                t["text"], t["due"] = text, iso
            target = self.editing
        else:
            target = self.db["next_id"]
            self.db["next_id"] += 1
            self.db["tasks"].append(
                {"id": target, "text": text, "due": iso, "status": "todo"})

        self.cancel_entry()
        self.bar.save()
        self.refresh()
        self.reveal(target)
        self.entry.focus_set()

    def reveal(self, tid):
        """Open whichever card the task just landed in.

        A task can be invisible here: if an active filter excludes it, it is
        in no bucket at all, and the stack is left as it was.
        """
        for b in self.buckets:
            if any(t["id"] == tid for _, t in b.pairs):
                if b.key != self.db["active"]:
                    self.db["active"] = b.key
                    self.bar.save()
                    self.refresh()
                self.scroll_to_bucket(b.key)
                return


class TodayModule(Module):
    """The short answer to "what now" - overdue, today, tomorrow. Nothing else."""

    key = "today"
    label = "Today"
    icon = "today"
    blurb = "Today — overdue, today and tomorrow"
    default_w = 250

    def fill_content(self, bg, ghost):
        inner = self.win_w - 2 * PAD
        today = self.bar.today
        tasks = [t for t in apply_filters(self.db["tasks"], self.db["filters"])
                 if t["status"] != "done"]

        groups = [
            ("Overdue", ACCENT_OVERDUE,
             sorted(((to_date(t.get("due")), t) for t in tasks
                     if to_date(t.get("due")) and to_date(t.get("due")) < today),
                    key=lambda p: p[0])),
            ("Today", ACCENTS[0],
             [(today, t) for t in tasks if to_date(t.get("due")) == today]),
            ("Tomorrow", ACCENTS[2],
             [(today + timedelta(days=1), t) for t in tasks
              if to_date(t.get("due")) == today + timedelta(days=1)]),
        ]
        groups = [g for g in groups if g[2]]

        y = TOP_H
        if not groups:
            self.parts.append(self.label_at(
                self.content, "nothing due – enjoy it", PAD + 2, y, inner,
                ROW_H, FONT_DIM, TEXT_DIM, bg, ghost))
            return y + ROW_H + PAD

        for name, accent, rows in groups:
            block = tk.Frame(self.content, bg=bg)
            h = HDR_H + BODY_TOP + len(rows) * ROW_H + BODY_BOT
            block.place(x=PAD, y=y, width=inner, height=h)
            self.parts.append(block)

            if not ghost:
                tk.Frame(block, bg=CARD_EDGE).place(x=0, y=0, width=inner,
                                                    height=1)
                tk.Frame(block, bg=accent).place(x=0, y=0, width=3, height=h)
            self.label_at(block, name, 12, 0, inner - 60, HDR_H,
                          FONT_HDR, accent, bg, ghost)
            self.label_at(block, str(len(rows)), inner - 50, 0, 38, HDR_H,
                          FONT_DIM, accent if ghost else TEXT_DIM, bg, ghost,
                          anchor="e")

            ry = HDR_H + BODY_TOP
            for due, t in rows:
                self.task_row(block, t, due, ry, inner, bg, ghost, accent,
                              show_date=(name == "Overdue"))
                ry += ROW_H
            y += h + GAP
        return y - GAP + PAD


class StatsModule(Module):
    """A glance at the shape of the week: counts, progress, what is coming."""

    key = "stats"
    label = "Stats"
    icon = "stats"
    blurb = "Stats — progress and the next seven days"
    default_w = 232

    def fill_content(self, bg, ghost):
        inner = self.win_w - 2 * PAD
        today = self.bar.today
        tasks = apply_filters(self.db["tasks"], self.db["filters"])
        s = summarise(tasks, today)

        panel = tk.Frame(self.content, bg=bg)
        self.parts.append(panel)

        y = 8
        self.label_at(panel, str(s["open"]), 12, y, 70, 34,
                      ("Segoe UI Light", 24),
                      ACCENTS[1] if not s["open"] else TEXT, bg, ghost)
        self.label_at(panel, "open", 12, y + 34, 70, 14, FONT_DIM, TEXT_DIM,
                      bg, ghost)
        self.label_at(panel, str(s["late"]), 86, y, 60, 34,
                      ("Segoe UI Light", 24),
                      ACCENT_OVERDUE if s["late"] else TEXT_DONE, bg, ghost)
        self.label_at(panel, "late", 86, y + 34, 60, 14, FONT_DIM, TEXT_DIM,
                      bg, ghost)
        self.label_at(panel, "%d%%" % round(s["frac"] * 100), inner - 76, y,
                      64, 34, ("Segoe UI Light", 24), ACCENTS[2], bg, ghost,
                      anchor="e")
        self.label_at(panel, "done", inner - 76, y + 34, 64, 14, FONT_DIM,
                      TEXT_DIM, bg, ghost, anchor="e")
        y += 56

        track = tk.Frame(panel, bg=METER_BG)
        track.place(x=12, y=y, width=inner - 24, height=METER_H)
        fill = int(round((inner - 24) * max(0.0, min(1.0, s["frac"]))))
        if fill:
            tk.Frame(track, bg=ACCENTS[1] if s["frac"] >= 1.0 else ACCENTS[2]
                     ).place(x=0, y=0, width=fill, height=METER_H)
        y += METER_H + 14

        self.label_at(panel, "next 7 days", 12, y, inner - 24, 14, FONT_DIM,
                      TEXT_DIM, bg, ghost)
        y += 18

        counts = []
        for i in range(7):
            day = today + timedelta(days=i)
            counts.append(sum(1 for t in tasks if t["status"] != "done"
                              and to_date(t.get("due")) == day))
        peak = max(counts + [1])
        slot = (inner - 24) // 7
        for i, n in enumerate(counts):
            day = today + timedelta(days=i)
            bx = 12 + i * slot
            bh = max(2, int(STAT_BAR_H * n / peak)) if n else 2
            colour = ACCENTS[0] if i == 0 else ACCENTS[2]
            tk.Frame(panel, bg=colour if n else METER_BG).place(
                x=bx + 2, y=y + (STAT_BAR_H - bh), width=slot - 5, height=bh)
            self.label_at(panel, day.strftime("%a")[0], bx, y + STAT_BAR_H + 2,
                          slot - 1, 14, FONT_DIM,
                          TEXT if i == 0 else TEXT_DIM, bg, ghost,
                          anchor="center")
            if n:
                self.label_at(panel, str(n), bx, y + STAT_BAR_H + 15, slot - 1,
                              12, FONT_DIM, TEXT_DIM, bg, ghost,
                              anchor="center")
        y += STAT_BAR_H + 32

        if not ghost:
            tk.Frame(panel, bg=CARD_EDGE).place(x=0, y=0, width=inner, height=1)
        panel.place(x=PAD, y=TOP_H, width=inner, height=y)
        return TOP_H + y + PAD


# ---------------------------------------------------------------------------
# notification area
# ---------------------------------------------------------------------------

WM_APP = 0x8000
TRAY_CALLBACK = WM_APP + 1
NIM_ADD, NIM_DELETE = 0, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x0010, 0x0040
WS_EX_TOOLWINDOW = 0x00000080
PM_REMOVE = 1
TRAY_PUMP_MS = 120

if os.name == "nt":
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                 wintypes.UINT, wintypes.WPARAM,
                                 wintypes.LPARAM)

    # Without argtypes ctypes guesses a 32-bit int for the handed-back LPARAM
    # and raises on anything larger, which on a 64-bit build is most messages.
    _DEF_WNDPROC = ctypes.windll.user32.DefWindowProcW
    _DEF_WNDPROC.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                             wintypes.LPARAM)
    _DEF_WNDPROC.restype = ctypes.c_ssize_t

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("hWnd", wintypes.HWND),
                    ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT),
                    ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128),
                    ("dwState", wintypes.DWORD),
                    ("dwStateMask", wintypes.DWORD),
                    ("szInfo", wintypes.WCHAR * 256),
                    ("uVersion", wintypes.UINT),
                    ("szInfoTitle", wintypes.WCHAR * 64),
                    ("dwInfoFlags", wintypes.DWORD),
                    ("guidItem", ctypes.c_byte * 16),
                    ("hBalloonIcon", wintypes.HICON)]


class TrayIcon:
    """A notification-area icon, from Shell_NotifyIcon and a hidden window.

    Not pystray: installing nothing is the point of this app, and the tray is
    a page of ctypes against two dependencies and a heavier build. Tk owns
    the main loop, so rather than a blocking GetMessage pump, the hidden
    window's queue is drained from a Tk timer, and the callbacks hand work
    back to Tk with after_idle instead of touching widgets inside a WndProc.

    Every failure here is non-fatal. A missing tray is a missing convenience;
    it must never stop the app from starting.
    """

    CLASS_NAME = "OverlayBoardTrayWindow"

    MAX_TRIES = 5

    def __init__(self, bar):
        self.bar = bar
        self.hwnd = None
        self.nid = None
        self.ok = False
        self._tries = 0
        self._taskbar_created = 0
        if os.name != "nt":
            return
        try:
            self._create()
        except Exception as exc:                  # noqa: BLE001 - never fatal
            print("tray window unavailable:", exc, file=sys.stderr)
            self.remove()
            return
        self._add_icon()
        self.bar.after(TRAY_PUMP_MS, self._pump)

    def _add_icon(self):
        """Ask the shell for a slot, retrying: it can refuse while busy."""
        try:
            if self._shell_add():
                self.ok = True
                self._tries = 0
                return
        except Exception as exc:                  # noqa: BLE001
            print("tray icon error:", exc, file=sys.stderr)
        self._tries += 1
        if self._tries < self.MAX_TRIES:
            self.bar.after(1500, self._add_icon)
        else:
            print("gave up on the tray icon after %d tries" % self._tries,
                  file=sys.stderr)

    # -- setup --------------------------------------------------------------

    def _load_icon(self):
        user32 = ctypes.windll.user32
        user32.LoadIconW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR)
        user32.LoadIconW.restype = wintypes.HICON
        # a packaged build carries the icon as resource 1 of the exe itself
        inst = ctypes.windll.kernel32.GetModuleHandleW(None)
        icon = user32.LoadIconW(inst, ctypes.cast(ctypes.c_void_p(1),
                                                  wintypes.LPCWSTR))
        if icon:
            return icon
        for path in (APP_DIR / "OverlayBoard.ico",
                     HERE / "assets" / "OverlayBoard.ico"):
            if path.exists():
                user32.LoadImageW.restype = wintypes.HANDLE
                icon = user32.LoadImageW(None, str(path), IMAGE_ICON, 0, 0,
                                         LR_LOADFROMFILE | LR_DEFAULTSIZE)
                if icon:
                    return icon
        return None

    def _create(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # the callback must outlive the window, or Python frees it and the
        # first tray click jumps into reclaimed memory
        self._proc = WNDPROC(self._wndproc)
        self._cls = WNDCLASSW()
        self._cls.lpfnWndProc = self._proc
        self._cls.hInstance = kernel32.GetModuleHandleW(None)
        self._cls.lpszClassName = self.CLASS_NAME
        user32.RegisterClassW(ctypes.byref(self._cls))

        user32.CreateWindowExW.restype = wintypes.HWND
        self.hwnd = user32.CreateWindowExW(
            WS_EX_TOOLWINDOW, self.CLASS_NAME, "Overlay Board", 0,
            0, 0, 0, 0, None, None, self._cls.hInstance, None)
        if not self.hwnd:
            raise OSError("could not create the tray window")

        # Explorer broadcasts this when it restarts, having dropped every
        # tray icon on the way down; re-adding is the only way back
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")

    def _shell_add(self):
        icon = self._load_icon()
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_TIP | (NIF_ICON if icon else 0)
        nid.uCallbackMessage = TRAY_CALLBACK
        nid.hIcon = icon or 0
        nid.szTip = "Overlay Board"
        if not ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD,
                                                       ctypes.byref(nid)):
            return False
        self.nid = nid
        return True

    # -- message handling ---------------------------------------------------

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if self._taskbar_created and msg == self._taskbar_created:
            self.nid = None
            self._tries = 0
            self.bar.after_idle(self._add_icon)
            return 0
        if msg == TRAY_CALLBACK:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.bar.after_idle(self.bar.tray_click)
            elif event == WM_RBUTTONUP:
                self.bar.after_idle(self.bar.tray_menu)
            return 0
        return _DEF_WNDPROC(hwnd, msg, wparam, lparam)

    def _pump(self):
        """Drain the hidden window's queue; Tk's loop will not do it for us."""
        if not self.hwnd:
            return
        try:
            user32 = ctypes.windll.user32
            msg = wintypes.MSG()
            while user32.PeekMessageW(ctypes.byref(msg), self.hwnd,
                                      0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:                          # noqa: BLE001
            pass
        self.bar.after(TRAY_PUMP_MS, self._pump)

    def remove(self):
        try:
            if self.nid is not None:
                ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE,
                                                        ctypes.byref(self.nid))
                self.nid = None
            if self.hwnd:
                ctypes.windll.user32.DestroyWindow(self.hwnd)
                self.hwnd = None
        except Exception:                          # noqa: BLE001
            pass
        self.ok = False


# ---------------------------------------------------------------------------
# the bar
# ---------------------------------------------------------------------------

MODULE_CLASSES = (BoardModule, TodayModule, StatsModule)


class Bar(tk.Tk):
    """The always-visible strip: dashboard, module buttons, search, filters.

    It owns every module, the search overlay and any open menu, and it is the
    only window that never goes transparent and never hides - whatever else
    is tucked away, there is always this to click on.
    """

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.widgets = []
        self.drag = None
        self.today = date.today()
        self.spot = None
        self.pop = None
        self.undo = []
        self.active = True
        self._pin_tick = 0
        self.hidden = False
        self.tip = None
        self._tip_job = None
        self.bar_w = BAR_MIN_W

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=BAR_BG)
        # the bar is chrome, not content: it stays near-solid whatever the
        # modules' opacity is set to, so it never dissolves into the taskbar
        self.attributes("-alpha", BAR_ALPHA)

        fams = set(tkfont.families())
        has_mdl2 = ICON_FONT in fams
        self.icons = ICONS if has_mdl2 else ICONS_ASCII
        self.icon_family = ICON_FONT if has_mdl2 else "Segoe UI"

        self.m_bar = tkfont.Font(font=FONT_BAR)
        self.m_barb = tkfont.Font(font=FONT_BAR_B)
        self.m_chip = tkfont.Font(font=FONT_CHIP)

        self._draggable(self)
        self.bind("<Button-3>", self.menu_event)

        self.modules = [cls(self) for cls in MODULE_CLASSES]
        self.by_key = {m.key: m for m in self.modules}
        self.board = self.by_key["board"]

        self.bind_all("<F2>", lambda e: self.toggle_mode())
        self.bind_all("<Control-f>", lambda e: self.open_search())
        self.bind_all("<Control-n>", lambda e: self.add_task())
        self.bind_all("<Control-z>", lambda e: self.undo_last())
        self.bind_all("<Control-q>", lambda e: self.quit_app())

        self.tray = TrayIcon(self)

        self.refresh()
        self.after(60000, self._tick)
        self.after(FOCUS_POLL_MS, self._watch_focus)

    # -- focus and pinning --------------------------------------------------

    def module_visible(self, m):
        """Modules are transient; the bar is the only permanent window.

        Ghost mode is exempt on purpose. Its whole point is reading your
        tasks while you work in something else, so auto-hiding it there
        would leave the mode with nothing to do.
        """
        if not m.is_open:
            return False
        if self.db["auto_hide"] and not self.active and self.db["mode"] != "ghost":
            return False
        return True

    def overlay_open(self):
        return ((self.pop is not None and self.pop.winfo_exists())
                or (self.spot is not None and self.spot.winfo_exists()))

    def close_overlays(self):
        # a menu or the search field left open behind us is clutter
        if self.pop is not None and self.pop.winfo_exists():
            self.pop.close()
        if self.spot is not None and self.spot.winfo_exists():
            self.spot.close()

    def _watch_focus(self):
        active = foreground_is_ours()
        if active != self.active:
            self.active = active
            if not active:
                self.close_overlays()
            self.place_windows()

        # Re-pinning re-inserts a window at the top of the topmost band, so
        # doing it while a menu is up buries the menu behind the modules and
        # eats the click meant for it. Menus are short-lived: leave the order
        # alone until one closes. Every couple of seconds is also plenty -
        # at the focus-poll rate the constant reordering visibly flickered.
        self._pin_tick += 1
        if not self.overlay_open() and self._pin_tick % PIN_EVERY == 0:
            pin_topmost(self)
            for m in self.modules:
                if m.winfo_ismapped():
                    pin_topmost(m)
        self.after(FOCUS_POLL_MS, self._watch_focus)

    def activate(self, *_):
        """Claim focus so the modules come back when the bar is clicked."""
        self.active = True
        try:
            self.focus_force()
        except tk.TclError:
            pass
        self.place_windows()

    # -- state --------------------------------------------------------------

    def view(self):
        return apply_filters(self.db["tasks"], self.db["filters"])

    def save(self):
        save_state(self.db)

    def refresh(self):
        self.render_bar()
        for m in self.modules:
            if self.module_visible(m):
                m.render()
        self.place_windows()

    def _tick(self):
        now = date.today()
        if now != self.today:
            self.today = now
            self.refresh()
        self.after(60000, self._tick)

    # -- small widget factories --------------------------------------------

    def _draggable(self, w):
        w.bind("<ButtonPress-1>", self._press)
        w.bind("<B1-Motion>", self._move)
        w.bind("<ButtonRelease-1>", self._release)

    def _icon(self, key, x, cmd, fg=TEXT_DIM, size=10, tip=None):
        lbl = tk.Label(self, text=self.icons[key], font=(self.icon_family, size),
                       fg=fg, bg=BAR_BG, cursor="hand2")
        lbl.place(x=x, y=0, width=ICON_W, height=BAR_H)
        lbl.bind("<Button-1>",
                 lambda e, w=lbl: (self.hide_tip(), self.activate(), cmd(w)))
        lbl.bind("<Button-3>", lambda e: (self.hide_tip(), self.menu_event(e)))
        lbl.bind("<Enter>", lambda e, w=lbl: (w.configure(bg=HOVER_BG, fg=TEXT),
                                              self.arm_tip(w, tip)))
        lbl.bind("<Leave>", lambda e, w=lbl: (w.configure(bg=BAR_BG, fg=fg),
                                              self.hide_tip()))
        self.widgets.append(lbl)
        return lbl

    # -- tooltips -----------------------------------------------------------

    def arm_tip(self, widget, text):
        """Wait a beat before showing, so sweeping across the bar is quiet."""
        self.hide_tip()
        if text:
            self._tip_job = self.after(
                TIP_DELAY_MS, lambda: self._show_tip(widget, text))

    def _show_tip(self, widget, text):
        self._tip_job = None
        if not widget.winfo_exists():
            return
        self.tip = Tooltip(self, text, widget)

    def hide_tip(self, *_):
        if self._tip_job is not None:
            self.after_cancel(self._tip_job)
            self._tip_job = None
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None

    def _text(self, text, x, w, font, fg):
        lbl = tk.Label(self, text=text, font=font, fg=fg, bg=BAR_BG, anchor="w")
        lbl.place(x=x, y=0, width=w, height=BAR_H)
        self._draggable(lbl)
        lbl.bind("<Button-3>", self.menu_event)
        self.widgets.append(lbl)
        return lbl

    def _sep(self, x):
        f = tk.Frame(self, bg=BAR_EDGE)
        f.place(x=x + SEP_W // 2, y=10, width=1, height=BAR_H - 20)
        self.widgets.append(f)

    def _chip(self, text, x, w):
        """A filter block hanging off the bar, with its own discard button."""
        y = (BAR_H - CHIP_H) // 2
        box = tk.Frame(self, bg=CHIP_BG)
        box.place(x=x, y=y, width=w, height=CHIP_H)
        tk.Label(box, text=text, font=FONT_CHIP, fg=CHIP_FG, bg=CHIP_BG,
                 anchor="w").place(x=8, y=0, width=w - 28, height=CHIP_H)
        kill = tk.Label(box, text=self.icons["close"],
                        font=(self.icon_family, 7), fg=CHIP_FG, bg=CHIP_BG,
                        cursor="hand2")
        kill.place(x=w - 19, y=0, width=15, height=CHIP_H)
        kill.bind("<Button-1>", lambda e: self.drop_filter(text))
        kill.bind("<Enter>", lambda e: kill.configure(fg=ACCENT_OVERDUE))
        kill.bind("<Leave>", lambda e: kill.configure(fg=CHIP_FG))
        self.widgets.append(box)

    def _module_button(self, m, x):
        """Lit when the module is on screen, with a dot when it floats free."""
        on = m.is_open
        fg = ACCENTS[2] if on else TEXT_DIM
        verb = "Hide" if on else "Show"
        self._icon(m.icon, x, lambda w, k=m.key: self.toggle_module(k),
                   fg=fg, tip="%s %s" % (verb, m.blurb))
        if on:
            dot_w = 10 if m.floating else ICON_W - 10
            marker = tk.Frame(self, bg=ACCENTS[2] if not m.floating
                              else ACCENTS[3])
            marker.place(x=x + (ICON_W - dot_w) // 2, y=BAR_H - 3,
                         width=dot_w, height=2)
            self.widgets.append(marker)

    # -- render -------------------------------------------------------------

    def render_bar(self):
        for w in self.widgets:
            w.destroy()
        self.widgets = []

        tasks = self.view()
        s = summarise(tasks, self.today)
        filters = self.db["filters"]

        # deliberately just two numbers: what is due now and what is overdue.
        # The bar sits on the taskbar, so every extra field costs length that
        # has to come out of somewhere else on screen.
        today_txt = "%d today" % s["today"]
        late_txt = "%d late" % s["late"] if s["late"] else ""

        today_w = self.m_barb.measure(today_txt) + 14
        late_w = (self.m_barb.measure(late_txt) + 14) if late_txt else 0
        chip_ws = [self.m_chip.measure(f) + 32 for f in filters]
        buttons_w = ICON_W * len(self.modules)

        left_w = BAR_PAD + buttons_w + SEP_W + today_w + late_w
        right_w = (ICON_W + sum(w + 5 for w in chip_ws) + ICON_W + BAR_PAD)
        self.bar_w = max(BAR_MIN_W, left_w + 18 + right_w)

        x = BAR_PAD
        for m in self.modules:
            self._module_button(m, x)
            x += ICON_W
        self._sep(x)
        x += SEP_W

        self._text(today_txt, x, today_w, FONT_BAR_B,
                   TEXT if s["today"] else TEXT_DIM)
        x += today_w
        if late_txt:
            self._text(late_txt, x, late_w, FONT_BAR_B, ACCENT_OVERDUE)

        # right edge, laid out backwards so the chips grow the bar leftwards
        rx = self.bar_w - BAR_PAD - ICON_W
        self._icon("more", rx, self.open_menu, tip="Menu")
        for f, w in zip(reversed(filters), reversed(chip_ws)):
            rx -= w + 5
            self._chip(f, rx, w)
        rx -= ICON_W
        self._icon("search", rx, self.open_search,
                   fg=CHIP_FG if filters else TEXT_DIM,
                   tip="Search tasks  (Ctrl+F)")

    # -- placement ----------------------------------------------------------

    def _default_pos(self):
        """Park on the taskbar if there is one, else at the bottom edge."""
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        wa = work_area()
        if wa and wa[3] < sh:
            tb_h = sh - wa[3]
            return (sw - self.bar_w) // 2, wa[3] + max(0, (tb_h - BAR_H) // 2)
        return (sw - self.bar_w) // 2, sh - BAR_H - 8

    def _panel_above(self):
        pos = self.db["pos"] or self._default_pos()
        return int(pos[1]) > self.winfo_screenheight() // 2

    def place_windows(self):
        if self.hidden:
            self.withdraw()
            for m in self.modules:
                m.withdraw()
            return
        self.deiconify()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if not self.db["pos"]:
            self.db["pos"] = list(self._default_pos())
        x, y = int(self.db["pos"][0]), int(self.db["pos"][1])
        x = max(-self.bar_w + 90, min(x, sw - 90))
        y = max(0, min(y, sh - BAR_H))
        self.geometry("%dx%d+%d+%d" % (self.bar_w, BAR_H, x, y))

        above = self._panel_above()
        slot = x                          # docked modules queue up beside the bar
        for m in self.modules:
            if not self.module_visible(m):
                m.withdraw()
                continue
            if m.floating:
                m.place_floating()
            else:
                m.place_docked(slot, y, above)
                slot += m.win_w + MOD_GAP

    # -- dragging the bar ---------------------------------------------------

    def _press(self, e):
        self.activate()                   # clicking the bar brings modules back
        self.drag = {"rx": e.x_root, "ry": e.y_root,
                     "wx": self.winfo_x(), "wy": self.winfo_y(),
                     "at": None}

    def _move(self, e):
        if not self.drag:
            return
        dx, dy = e.x_root - self.drag["rx"], e.y_root - self.drag["ry"]
        if self.drag["at"] is None and abs(dx) + abs(dy) < 4:
            return                        # absorb the jitter of a plain click
        nx, ny = self.drag["wx"] + dx, self.drag["wy"] + dy
        self.drag["at"] = (nx, ny)
        self.geometry("+%d+%d" % (nx, ny))
        above = ny > self.winfo_screenheight() // 2
        slot = nx
        for m in self.modules:            # docked modules follow the bar
            if self.module_visible(m) and not m.floating:
                m.place_docked(slot, ny, above)
                slot += m.win_w + MOD_GAP

    def _release(self, e):
        if self.drag and self.drag["at"]:
            self.db["pos"] = list(self.drag["at"])
            self.save()
            self.render_bar()
        self.drag = None

    # -- modules ------------------------------------------------------------

    def toggle_module(self, key):
        m = self.by_key[key]
        m.state["open"] = not m.state["open"]
        self.save()
        self.refresh()

    def close_module(self, key):
        self.by_key[key].state["open"] = False
        self.save()
        self.refresh()

    def dock_module(self, key):
        self.by_key[key].state["pos"] = None
        self.save()
        self.refresh()

    def dock_all(self, *_):
        for m in self.modules:
            m.state["pos"] = None
        self.save()
        self.refresh()

    # -- actions ------------------------------------------------------------

    def toggle_mode(self, *_):
        if not self.board.ghost_ok:
            return
        self.db["mode"] = "frosted" if self.db["mode"] == "ghost" else "ghost"
        self.save()
        self.refresh()

    def add_task(self, *_):
        self.board.state["open"] = True
        self.activate()
        self.refresh()
        self.board.focus_entry()

    def open_search(self, *_):
        if self.spot is not None and self.spot.winfo_exists():
            self.spot.raise_it()
        else:
            self.spot = Spotlight(self)

    def jump_to(self, tid):
        """Open the card a task lives in - the search result's payoff."""
        self.board.state["open"] = True
        self.activate()
        self.refresh()
        self.board.reveal(tid)

    def add_filter(self, q):
        if q and q not in self.db["filters"]:
            self.db["filters"].append(q)
            self.save()
        self.refresh()

    def drop_filter(self, q):
        self.db["filters"] = [f for f in self.db["filters"] if f != q]
        self.save()
        self.refresh()

    def clear_filters(self, *_):
        self.db["filters"] = []
        self.save()
        self.refresh()

    def set_alpha(self, value):
        self.db["alpha"] = round(float(value), 2)
        self.save()
        self.refresh()

    def set_width(self, value):
        self.db["width"] = int(value)
        self.save()
        self.refresh()

    def toggle_done(self, *_):
        self.db["show_done"] = not self.db["show_done"]
        self.save()
        self.refresh()

    def toggle_auto_hide(self, *_):
        self.db["auto_hide"] = not self.db["auto_hide"]
        self.save()
        self.refresh()

    def collapse_all(self, *_):
        self.db["active"] = None
        self.save()
        self.refresh()

    def reset_position(self, *_):
        self.db["pos"] = None
        for m in self.modules:
            m.state["pos"] = None
        self.save()
        self.refresh()

    # -- undo ---------------------------------------------------------------

    def push_undo(self, tasks):
        """Remember removed tasks. Deleting is one click and has no prompt."""
        if tasks:
            self.undo.append([dict(t) for t in tasks])
            del self.undo[:-20]

    def undo_last(self, *_):
        if not self.undo:
            return
        have = {t["id"] for t in self.db["tasks"]}
        self.db["tasks"].extend(t for t in self.undo.pop()
                                if t["id"] not in have)
        self.db["next_id"] = max([t["id"] for t in self.db["tasks"]] + [0]) + 1
        self.save()
        self.refresh()

    def clear_completed(self, *_):
        done = [t for t in self.db["tasks"] if t["status"] == "done"]
        self.push_undo(done)
        self.db["tasks"] = [t for t in self.db["tasks"] if t["status"] != "done"]
        self.save()
        self.refresh()

    def set_hidden(self, hidden):
        self.hidden = bool(hidden)
        if not self.hidden:
            self.active = True
        self.refresh()

    def tray_click(self):
        """Left click: bring everything back, or just re-focus it."""
        if self.hidden:
            self.set_hidden(False)
        else:
            self.activate()

    def tray_menu(self):
        self.active = True
        try:
            self.focus_force()
        except tk.TclError:
            pass
        x, y = self.winfo_pointerx(), self.winfo_pointery()
        items = [
            {"label": "Show Overlay Board" if self.hidden else "Hide from screen",
             "cmd": lambda: self.set_hidden(not self.hidden)},
            {"kind": "sep"},
        ]
        if not self.hidden:
            for m in self.modules:
                items.append({"label": m.label, "checked": m.is_open,
                              "cmd": (lambda k=m.key: self.toggle_module(k))})
            items.append({"label": "Reset positions", "cmd": self.reset_position})
            items.append({"kind": "sep"})
        items.append({"label": "Quit", "accel": "Ctrl+Q", "danger": True,
                      "cmd": self.quit_app})
        self.popup(x, y, items)

    def quit_app(self, *_):
        self.save()
        if getattr(self, "tray", None) is not None:
            self.tray.remove()          # otherwise a dead icon lingers in the
        self.destroy()                  # tray until something hovers over it

    # -- menus --------------------------------------------------------------

    def popup(self, x, y, items):
        if self.pop is not None and self.pop.winfo_exists():
            self.pop.close()
        self.pop = PopMenu(self, x, y, items)

    def menu_event(self, e):
        self.open_menu(x=e.x_root, y=e.y_root)

    def open_menu(self, widget=None, x=None, y=None):
        if x is None:
            anchor = widget if widget is not None else self
            x = anchor.winfo_rootx()
            y = anchor.winfo_rooty() + BAR_H + 2
        self.popup(x, y, self._main_items())

    def _main_items(self):
        items = [
            {"label": "Add task", "accel": "Ctrl+N", "cmd": self.add_task},
            {"label": "Search", "accel": "Ctrl+F", "cmd": self.open_search},
            {"label": "Collapse all", "cmd": self.collapse_all},
            {"kind": "sep"},
        ]
        for m in self.modules:
            items.append({"label": m.label, "checked": m.is_open,
                          "cmd": (lambda k=m.key: self.toggle_module(k))})
        if any(m.floating for m in self.modules):
            items.append({"label": "Dock all modules", "cmd": self.dock_all})
        items.append({"kind": "sep"})

        if self.board.ghost_ok:
            items.append({"label": "Ghost mode", "accel": "F2",
                          "checked": self.db["mode"] == "ghost",
                          "cmd": self.toggle_mode})
        items += [
            {"label": "Show completed", "checked": bool(self.db["show_done"]),
             "cmd": self.toggle_done},
            {"label": "Hide when unfocused", "checked": bool(self.db["auto_hide"]),
             "cmd": self.toggle_auto_hide},
            {"kind": "sep"},
            {"kind": "choice", "label": "Opacity",
             "options": [("%d" % round(a * 100), a) for a in ALPHAS],
             "value": self.db["alpha"], "cmd": self.set_alpha},
            {"kind": "choice", "label": "Width",
             "options": [(str(w), w) for w in WIDTHS],
             "value": self.db["width"], "cmd": self.set_width},
            {"kind": "sep"},
        ]
        if self.db["filters"]:
            items.append({"label": "Clear all filters",
                          "cmd": self.clear_filters})
        items += [
            {"label": "Reset positions", "cmd": self.reset_position},
            {"label": "Hide to tray", "enabled": bool(getattr(self, "tray", None)
                                                      and self.tray.ok),
             "cmd": lambda: self.set_hidden(True)},
            {"label": "Undo delete", "accel": "Ctrl+Z",
             "enabled": bool(self.undo), "cmd": self.undo_last},
            {"label": "Clear completed", "cmd": self.clear_completed},
            {"label": "Quit", "accel": "Ctrl+Q", "danger": True,
             "cmd": self.quit_app},
        ]
        return items

    def open_task_menu(self, x, y, tid):
        board = self.board
        t = board.find(tid)
        if not t:
            return
        items = [{"label": "%s   %s" % (GLYPH[s], s),
                  "checked": t["status"] == s,
                  "cmd": (lambda s=s: board.set_status(tid, s))}
                 for s in ("todo", "doing", "done")]
        items += [
            {"kind": "sep"},
            {"label": "Edit text and date", "cmd": lambda: board.edit(tid)},
            {"kind": "choice", "label": "Due",
             "options": [("today", "today"), ("tmr", "tomorrow"),
                         ("+1w", "+1w"), ("none", None)],
             "value": _UNSET,             # nothing preselected on this row
             "cmd": lambda v: board.set_due(tid, v)},
            {"kind": "sep"},
            {"label": "Delete", "danger": True, "cmd": lambda: board.delete(tid)},
        ]
        self.popup(x, y, items)


_UNSET = object()


# ---------------------------------------------------------------------------
# the search overlay
# ---------------------------------------------------------------------------

class Spotlight(tk.Toplevel):
    """A big centred input that lists matches live. Enter hands the term to
    the bar as a filter block; clicking a result jumps to its card instead."""

    def __init__(self, bar):
        super().__init__(bar)
        self.bar = bar
        self.rows = []

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.97)
        self.configure(bg=SPOT_BG, highlightthickness=1,
                       highlightbackground=MENU_EDGE)

        tk.Label(self, text=bar.icons["search"], font=(bar.icon_family, 14),
                 fg=TEXT_DIM, bg=SPOT_BG).place(x=SPOT_PAD, y=SPOT_PAD,
                                                width=26, height=32)
        self.var = tk.StringVar()
        self.entry = tk.Entry(self, textvariable=self.var, font=FONT_SPOT,
                              fg=TEXT, bg=SPOT_BG, insertbackground=ACCENTS[2],
                              relief="flat", highlightthickness=0)
        self.entry.place(x=SPOT_PAD + 32, y=SPOT_PAD,
                         width=SPOT_W - 2 * SPOT_PAD - 150, height=32)
        self.count = tk.Label(self, text="", font=FONT_BAR, fg=TEXT_DIM,
                              bg=SPOT_BG, anchor="e")
        self.count.place(x=SPOT_W - SPOT_PAD - 112, y=SPOT_PAD,
                         width=112, height=32)

        self.var.trace_add("write", lambda *a: self.update_hits())
        self.entry.bind("<Return>", self.apply)
        self.entry.bind("<Escape>", lambda e: self.close())
        self.bind("<Escape>", lambda e: self.close())

        self.update_hits()
        self.raise_it()

    def raise_it(self):
        self.deiconify()
        self.lift()
        pin_topmost(self)
        self.focus_force()
        self.entry.focus_set()

    def update_hits(self):
        for w in self.rows:
            w.destroy()
        self.rows = []

        q = self.var.get().strip()
        base = apply_filters(self.bar.db["tasks"], self.bar.db["filters"])
        hits = apply_filters(base, [q]) if q else []
        if q:
            self.count.configure(
                text="%d match%s" % (len(hits), "" if len(hits) == 1 else "es"),
                fg=TEXT_DIM if hits else ACCENT_OVERDUE)
        else:
            self.count.configure(text="", fg=TEXT_DIM)

        shown = hits[:SPOT_MAX]
        iw = SPOT_W - 2 * SPOT_PAD
        y = SPOT_PAD + 40
        if shown:
            sep = tk.Frame(self, bg=MENU_SEP)
            sep.place(x=SPOT_PAD, y=y - 6, width=iw, height=1)
            self.rows.append(sep)

        for t in shown:
            d = to_date(t.get("due"))
            done = t["status"] == "done"
            row = tk.Frame(self, bg=SPOT_BG, cursor="hand2")
            row.place(x=SPOT_PAD, y=y, width=iw, height=SPOT_ROW)
            g = tk.Label(row, text=GLYPH[t["status"]], font=FONT_SPOT_ROW,
                         fg=TEXT_DONE if done else TEXT_DIM, bg=SPOT_BG)
            g.place(x=4, y=0, width=20, height=SPOT_ROW)
            lbl = tk.Label(row, text=t["text"], font=FONT_SPOT_ROW, bg=SPOT_BG,
                           anchor="w", fg=TEXT_DONE if done else TEXT)
            lbl.place(x=30, y=0, width=iw - 140, height=SPOT_ROW)
            when = tk.Label(row, text=fmt_short(d) if d else "someday",
                            font=FONT_DIM, fg=TEXT_DONE if done else TEXT_DIM,
                            bg=SPOT_BG, anchor="e")
            when.place(x=iw - 104, y=0, width=100, height=SPOT_ROW)

            parts = (row, g, lbl, when)

            def paint(bg, parts=parts):
                for p in parts:
                    p.configure(bg=bg)
            for p in parts:
                p.bind("<Enter>", lambda e, f=paint: f(MENU_HOVER))
                p.bind("<Leave>", lambda e, f=paint: f(SPOT_BG))
                p.bind("<Button-1>", lambda e, i=t["id"]: self.jump(i))
            self.rows.append(row)
            y += SPOT_ROW

        if len(hits) > SPOT_MAX:
            more = tk.Label(self, text="+%d more" % (len(hits) - SPOT_MAX),
                            font=FONT_DIM, fg=TEXT_DIM, bg=SPOT_BG, anchor="w")
            more.place(x=SPOT_PAD + 30, y=y, width=200, height=SPOT_ROW)
            self.rows.append(more)
            y += SPOT_ROW

        self._place(y + (SPOT_PAD if shown else 8))

    def _place(self, h):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry("%dx%d+%d+%d" % (SPOT_W, h, (sw - SPOT_W) // 2,
                                       int(sh * 0.26)))

    def jump(self, tid):
        self.close()
        self.bar.jump_to(tid)

    def apply(self, e=None):
        q = self.var.get().strip()
        self.close()
        if q:
            self.bar.add_filter(q)

    def close(self):
        self.bar.spot = None
        self.destroy()


def main():
    if not acquire_single_instance():
        ctypes.windll.user32.MessageBoxW(
            None, "Overlay Board is already running.", "Overlay Board", 0x40)
        return
    try:
        Bar(load_state()).mainloop()
    finally:
        release_single_instance()


if __name__ == "__main__":
    main()
