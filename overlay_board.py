"""
Overlay Board
=============
A see-through, always-on-top planning board for Windows. It sits over
whatever you are actually working in (IDE, browser, game) and shows what is
left and what is done, without taking a taskbar slot or a chunk of screen.

Run:    pythonw overlay_board.py   (no console window)
        python  overlay_board.py   (console window, useful for debugging)

Deps:   none - standard library tkinter only.

The app is two pieces:

  the bar    - a slim strip, sized to sit over the Windows taskbar (it docks
               there on first run) but draggable anywhere. It is the
               dashboard: open count, overdue count, a progress meter, and
               the next thing due. Always solid, always clickable.
  the panel  - the post-it stack, which unfolds above or below the bar when
               you click the chevron, and tucks away again.

Only the bar is permanent. The panel, the search field and the menus are
transient: they appear while the app has focus and get out of the way the
moment you click back into whatever you were doing. Clicking the bar brings
them back. Turn that off with "Hide panel when unfocused" in the bar menu.

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
BAR_MIN_W = 400
BAR_PAD = 9
BAR_GAP = 6                         # bar to panel
ICON_W = 26
METER_W = 52
METER_H = 5
CHIP_H = 20
SEP_W = 13                          # gap that carries a divider line
SPOT_W = 620                        # the centred search field
SPOT_PAD = 18
SPOT_ROW = 26
SPOT_MAX = 5                        # live matches listed under the input
FOCUS_POLL_MS = 250                 # how often the pin and focus are re-checked

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
         "more": "", "close": ""}
ICONS_ASCII = {"search": "⌕", "up": "▴", "down": "▾",
               "more": "⋮", "close": "×"}

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
    "panel_open": True,
    "auto_hide": True,              # panel only while the app has focus
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

def clip(text, px, fnt):
    """Trim text to a pixel budget, with an ellipsis if anything was lost."""
    if fnt.measure(text) <= px:
        return text
    while text and fnt.measure(text + "…") > px:
        text = text[:-1]
    return text + "…"


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
        self.attributes("-topmost", True)
        self.configure(bg=MENU_EDGE)          # the 1px border is this showing

        self.scrim = Scrim(app, self.close)
        self.lift()

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
# the bar
# ---------------------------------------------------------------------------

class Bar(tk.Tk):
    """The always-visible strip: dashboard, search, and filter blocks.

    It owns the panel, the search overlay and any open menu, and it is the
    only window that never goes transparent - whatever else is hidden, there
    is always this to click on.
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
        self.bar_w = BAR_MIN_W

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=BAR_BG)
        # the bar is chrome, not content: it stays near-solid whatever the
        # panel's opacity is set to, so it never dissolves into the taskbar
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

        self.panel = Panel(self)
        self.bind_all("<F2>", lambda e: self.toggle_mode())
        self.bind_all("<Control-f>", lambda e: self.open_search())
        self.bind_all("<Control-n>", lambda e: self.add_task())
        self.bind_all("<Control-z>", lambda e: self.undo_last())
        self.bind_all("<Control-q>", lambda e: self.quit_app())

        self.refresh()
        self.after(60000, self._tick)
        self.after(FOCUS_POLL_MS, self._watch_focus)

    # -- focus and pinning --------------------------------------------------

    def panel_should_show(self):
        """The panel is transient; the bar is the only permanent window.

        Ghost mode is exempt on purpose. Its whole point is reading your
        tasks while you work in something else, so auto-hiding it there
        would leave the mode with nothing to do.
        """
        if not self.db["panel_open"]:
            return False
        if self.db["auto_hide"] and not self.active and self.db["mode"] != "ghost":
            return False
        return True

    def _watch_focus(self):
        active = foreground_is_ours()
        if active != self.active:
            self.active = active
            if not active:
                # a menu or the search field left open behind us is clutter
                if self.pop is not None and self.pop.winfo_exists():
                    self.pop.close()
                if self.spot is not None and self.spot.winfo_exists():
                    self.spot.close()
            self.place_windows()
        pin_topmost(self)
        if self.panel.winfo_ismapped():
            pin_topmost(self.panel)
        self.after(FOCUS_POLL_MS, self._watch_focus)

    def activate(self, *_):
        """Claim focus so the panel comes back when the bar is clicked."""
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
        self.panel.render()
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

    def _icon(self, key, x, cmd, fg=TEXT_DIM, size=10):
        lbl = tk.Label(self, text=self.icons[key], font=(self.icon_family, size),
                       fg=fg, bg=BAR_BG, cursor="hand2")
        lbl.place(x=x, y=0, width=ICON_W, height=BAR_H)
        lbl.bind("<Button-1>", lambda e, w=lbl: (self.activate(), cmd(w)))
        lbl.bind("<Button-3>", self.menu_event)
        lbl.bind("<Enter>", lambda e: lbl.configure(bg=HOVER_BG, fg=TEXT))
        lbl.bind("<Leave>", lambda e: lbl.configure(bg=BAR_BG, fg=fg))
        self.widgets.append(lbl)
        return lbl

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

    def _meter(self, x, frac, accent):
        y = (BAR_H - METER_H) // 2
        back = tk.Frame(self, bg=METER_BG)
        back.place(x=x, y=y, width=METER_W, height=METER_H)
        fill = int(round(METER_W * max(0.0, min(1.0, frac))))
        if fill:
            tk.Frame(back, bg=accent).place(x=0, y=0, width=fill, height=METER_H)
        self._draggable(back)
        self.widgets.append(back)

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

    # -- render -------------------------------------------------------------

    def render_bar(self):
        for w in self.widgets:
            w.destroy()
        self.widgets = []

        tasks = self.view()
        s = summarise(tasks, self.today)
        filters = self.db["filters"]

        open_txt = "%d open" % s["open"] if s["open"] else "all clear"
        late_txt = "%d late" % s["late"] if s["late"] else ""
        next_fg = TEXT_DIM
        if s["next"]:
            d, t = s["next"]
            when = "today" if d == self.today else fmt_short(d)
            next_txt = "%s   %s" % (clip(t["text"], 140, self.m_bar), when)
            # the soonest open task can already be in the past; say so
            if d < self.today:
                next_fg = ACCENT_OVERDUE
            elif d == self.today:
                next_fg = TEXT
        elif filters:
            next_txt = "no match" if not s["total"] else "nothing scheduled"
        else:
            next_txt = "nothing scheduled" if s["total"] else "nothing planned"

        open_w = self.m_barb.measure(open_txt) + 12
        late_w = (self.m_barb.measure(late_txt) + 12) if late_txt else 0
        next_w = self.m_bar.measure(next_txt) + 8
        chip_ws = [self.m_chip.measure(f) + 32 for f in filters]

        left_w = (BAR_PAD + ICON_W + 4 + open_w + late_w + SEP_W + METER_W
                  + SEP_W + next_w)
        right_w = (ICON_W + sum(w + 5 for w in chip_ws) + ICON_W + BAR_PAD)
        self.bar_w = max(BAR_MIN_W, left_w + 22 + right_w)

        above = self._panel_above()
        if self.db["panel_open"]:
            caret = "down" if above else "up"      # points the way it folds
        else:
            caret = "up" if above else "down"
        x = BAR_PAD
        self._icon(caret, x, self.toggle_panel,
                   fg=TEXT if self.db["panel_open"] else TEXT_DIM)
        x += ICON_W + 4

        self._text(open_txt, x, open_w, FONT_BAR_B,
                   TEXT if s["open"] else ACCENTS[1])
        x += open_w
        if late_txt:
            self._text(late_txt, x, late_w, FONT_BAR_B, ACCENT_OVERDUE)
            x += late_w

        self._sep(x)
        x += SEP_W
        self._meter(x, s["frac"], ACCENTS[1] if s["frac"] >= 1.0 else ACCENTS[2])
        x += METER_W
        self._sep(x)
        x += SEP_W
        self._text(next_txt, x, next_w, FONT_BAR, next_fg)

        # right edge, laid out backwards so the chips grow the bar leftwards
        rx = self.bar_w - BAR_PAD - ICON_W
        self._icon("more", rx, self.open_menu)
        for f, w in zip(reversed(filters), reversed(chip_ws)):
            rx -= w + 5
            self._chip(f, rx, w)
        rx -= ICON_W
        self._icon("search", rx, self.open_search,
                   fg=CHIP_FG if filters else TEXT_DIM)

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
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if not self.db["pos"]:
            self.db["pos"] = list(self._default_pos())
        x, y = int(self.db["pos"][0]), int(self.db["pos"][1])
        x = max(-self.bar_w + 90, min(x, sw - 90))
        y = max(0, min(y, sh - BAR_H))
        self.geometry("%dx%d+%d+%d" % (self.bar_w, BAR_H, x, y))
        self.panel.place_near(x, y, self._panel_above())

    # -- dragging -----------------------------------------------------------

    def _press(self, e):
        self.activate()                   # clicking the bar brings the panel back
        self.drag = [e.x_root, e.y_root, self.winfo_x(), self.winfo_y(), False]

    def _move(self, e):
        if not self.drag:
            return
        x0, y0, wx, wy, moved = self.drag
        dx, dy = e.x_root - x0, e.y_root - y0
        if not moved and abs(dx) + abs(dy) < 4:
            return                        # absorb the jitter of a plain click
        self.drag[4] = True
        nx, ny = wx + dx, wy + dy
        self.geometry("+%d+%d" % (nx, ny))
        self.panel.place_near(nx, ny, ny > self.winfo_screenheight() // 2)

    def _release(self, e):
        if self.drag and self.drag[4]:
            self.db["pos"] = [self.winfo_x(), self.winfo_y()]
            self.save()
            self.render_bar()             # the fold arrow may have flipped
        self.drag = None

    # -- actions ------------------------------------------------------------

    def toggle_panel(self, *_):
        self.db["panel_open"] = not self.db["panel_open"]
        self.save()
        self.refresh()

    def toggle_mode(self, *_):
        if not self.panel.ghost_ok:
            return
        self.db["mode"] = "frosted" if self.db["mode"] == "ghost" else "ghost"
        self.save()
        self.refresh()

    def add_task(self, *_):
        self.db["panel_open"] = True
        self.refresh()
        self.panel.focus_entry()

    def open_search(self, *_):
        if self.spot is not None and self.spot.winfo_exists():
            self.spot.raise_it()
        else:
            self.spot = Spotlight(self)

    def jump_to(self, tid):
        """Open the card a task lives in - the search result's payoff."""
        self.db["panel_open"] = True
        self.refresh()
        self.panel.reveal(tid)

    def add_filter(self, q):
        if q and q not in self.db["filters"]:
            self.db["filters"].append(q)
            self.db["panel_open"] = True
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

    def toggle_startup(self, *_):
        set_startup(not startup_enabled())

    def collapse_all(self, *_):
        self.db["active"] = None
        self.save()
        self.refresh()

    def reset_position(self, *_):
        self.db["pos"] = None
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

    def quit_app(self, *_):
        self.save()
        self.destroy()

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
            {"label": "Reset position", "cmd": self.reset_position},
            {"kind": "sep"},
        ]
        if self.panel.ghost_ok:
            items.append({"label": "Ghost mode", "accel": "F2",
                          "checked": self.db["mode"] == "ghost",
                          "cmd": self.toggle_mode})
        items += [
            {"label": "Show completed", "checked": bool(self.db["show_done"]),
             "cmd": self.toggle_done},
            {"label": "Hide panel when unfocused",
             "checked": bool(self.db["auto_hide"]), "cmd": self.toggle_auto_hide},
            {"label": "Run at login", "checked": startup_enabled(),
             "cmd": self.toggle_startup},
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
            {"label": "Undo delete", "accel": "Ctrl+Z",
             "enabled": bool(self.undo), "cmd": self.undo_last},
            {"label": "Clear completed", "cmd": self.clear_completed},
            {"label": "Quit", "accel": "Ctrl+Q", "danger": True,
             "cmd": self.quit_app},
        ]
        return items

    def open_task_menu(self, x, y, tid):
        p = self.panel
        t = p.find(tid)
        if not t:
            return
        items = [{"label": "%s   %s" % (GLYPH[s], s),
                  "checked": t["status"] == s,
                  "cmd": (lambda s=s: p.set_status(tid, s))}
                 for s in ("todo", "doing", "done")]
        items += [
            {"kind": "sep"},
            {"label": "Edit text and date", "cmd": lambda: p.edit(tid)},
            {"kind": "choice", "label": "Due",
             "options": [("today", "today"), ("tmr", "tomorrow"),
                         ("+1w", "+1w"), ("none", None)],
             "value": _UNSET,             # nothing preselected on this row
             "cmd": lambda v: p.set_due(tid, v)},
            {"kind": "sep"},
            {"label": "Delete", "danger": True, "cmd": lambda: p.delete(tid)},
        ]
        self.popup(x, y, items)


_UNSET = object()


# ---------------------------------------------------------------------------
# the panel
# ---------------------------------------------------------------------------

class Panel(tk.Toplevel):
    """The post-it stack. Unfolds from the bar; goes transparent in ghost."""

    def __init__(self, bar):
        super().__init__(bar)
        self.bar = bar
        self.db = bar.db
        self.cards = []
        self.buckets = []
        self.editing = None
        self.entry_y = None
        self.scroll_y = 0
        self.content_h = 60
        self.bucket_ranges = {}
        self.win_w, self.win_h = int(self.db["width"]), 60

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

        self.canvas = tk.Canvas(self, bg=KEY, borderwidth=0,
                                highlightthickness=0)
        self.content = tk.Frame(self.canvas, bg=KEY)
        self.content_window = self.canvas.create_window(
            0, 0, window=self.content, anchor="nw")
        self.bind("<MouseWheel>", self._on_mousewheel)

        # sits on the Toplevel rather than inside the canvas, so it stays put
        # while the content slides under it
        self.scrollbar = tk.Frame(self, bg=CARD_EDGE)

        self.font_row = tkfont.Font(font=FONT_ROW)
        self.font_done = tkfont.Font(font=FONT_ROW)
        self.font_done.configure(overstrike=True)

        self.entry = tk.Entry(self.content, font=FONT_ROW, fg=TEXT, bg=ENTRY_BG,
                              insertbackground=TEXT, relief="flat",
                              highlightthickness=1,
                              highlightbackground=CARD_EDGE,
                              highlightcolor=ACCENTS[2])
        self.entry.bind("<Return>", self.commit_entry)
        self.entry.bind("<Escape>", self.cancel_entry)

        # a real Label rather than placeholder text in the Entry, so an empty
        # field is never mistaken for a typed one by commit_entry
        self.hint = tk.Label(self.content, text="  add a task…   @fri  @+2w  @10/3",
                             font=FONT_DIM, fg=TEXT_DONE, bg=ENTRY_BG,
                             anchor="w", cursor="xterm")
        self.hint.bind("<Button-1>", lambda e: self.focus_entry())
        for ev in ("<KeyRelease>", "<FocusIn>", "<FocusOut>"):
            self.entry.bind(ev, self.sync_hint, add="+")
        self.withdraw()

    @property
    def mode(self):
        return self.db["mode"] if self.ghost_ok else "frosted"

    def find(self, tid):
        return next((t for t in self.db["tasks"] if t["id"] == tid), None)

    def refresh(self):
        self.bar.refresh()

    def sync_hint(self, e=None):
        """Show the hint only while the field is on screen and empty."""
        if self.entry_y is not None and not self.entry.get():
            self.hint.place(x=PAD + 2, y=self.entry_y + 1,
                            width=int(self.db["width"]) - 2 * PAD - 4,
                            height=ENTRY_H - 2)
            self.hint.lift()
        else:
            self.hint.place_forget()

    # -- render -------------------------------------------------------------

    def render(self):
        for w in self.cards:
            w.destroy()
        self.cards = []

        ghost = self.mode == "ghost"
        bg = KEY if ghost else CARD_BG
        width = int(self.db["width"])
        inner = width - 2 * PAD

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
            self.cards.append(card)
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
            empty = self._label(self.content, msg, PAD + 2, y, inner, 20,
                                FONT_DIM, TEXT_DIM, bg, ghost)
            self.cards.append(empty)
            bottom = y + 20

        if ghost:
            self.entry.place_forget()
            self.entry_y = None
            total_h = bottom + PAD
        else:
            self.entry_y = bottom + GAP
            self.entry.place(x=PAD, y=self.entry_y, width=inner, height=ENTRY_H)
            total_h = bottom + GAP + ENTRY_H + PAD
        self.sync_hint()

        self.attributes("-alpha", 1.0 if ghost else self.db["alpha"])
        self.content_h = total_h
        self.win_w = width
        self.content.configure(width=width, height=total_h)
        self.canvas.itemconfigure(self.content_window, width=width, height=total_h)
        self.canvas.configure(scrollregion=(0, 0, width, total_h))
        self._resize_viewport(min(total_h, self.winfo_screenheight() - BAR_H - 2 * BAR_GAP))

    def place_near(self, bar_x, bar_y, above):
        if not self.bar.panel_should_show():
            self.withdraw()
            return
        self.deiconify()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        available = bar_y - BAR_GAP if above else sh - bar_y - BAR_H - BAR_GAP
        self._resize_viewport(min(self.content_h, max(80, available)))
        y = (bar_y - BAR_GAP - self.win_h) if above else (bar_y + BAR_H + BAR_GAP)
        x = max(0, min(bar_x, sw - self.win_w))
        y = max(0, min(y, sh - self.win_h))
        self.geometry("%dx%d+%d+%d" % (self.win_w, self.win_h, x, y))

    def _resize_viewport(self, height):
        self.win_h = max(1, int(height))
        self.canvas.place(x=0, y=0, width=self.win_w, height=self.win_h)
        self._set_scroll(self.scroll_y)

    def _set_scroll(self, offset):
        """Offset the content by moving the canvas item, not the canvas view.

        yview_moveto clamps against the canvas's *realized* height, which is
        still the old one until the geometry manager catches up - so the first
        scroll after a re-render silently did nothing, which is precisely when
        select() and reveal() ask to bring a card into view. Repositioning the
        window item is exact and needs no idle pass.
        """
        limit = max(0, self.content_h - self.win_h)
        self.scroll_y = max(0, min(int(offset), limit))
        self.canvas.coords(self.content_window, 0, -self.scroll_y)
        self._sync_scrollbar(limit)

    def _sync_scrollbar(self, limit):
        """Show a thumb only while there is somewhere to scroll to.

        Hidden in ghost mode: a floating bar with no panel behind it would be
        the one opaque thing left on screen.
        """
        if limit <= 0 or self.mode == "ghost":
            self.scrollbar.place_forget()
            return
        track = max(1, self.win_h - 2 * PAD)
        thumb = max(20, int(track * self.win_h / self.content_h))
        y = PAD + int((track - thumb) * (self.scroll_y / limit))
        self.scrollbar.place(x=self.win_w - 5, y=y, width=3, height=thumb)
        self.scrollbar.lift()

    def _on_mousewheel(self, event):
        if self.content_h <= self.win_h:
            return None
        direction = -1 if event.delta > 0 else 1
        self._set_scroll(self.scroll_y + direction * ROW_H * 3)
        return "break"

    def scroll_to_bucket(self, key):
        span = self.bucket_ranges.get(key)
        if not span:
            return
        top, bottom = span
        if top < self.scroll_y:
            self._set_scroll(top)
        elif bottom > self.scroll_y + self.win_h:
            self._set_scroll(bottom - self.win_h)

    def _label(self, parent, text, x, y, w, h, font, fg, bg, ghost,
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

    def _header(self, card, b, inner, h, expanded, bg, ghost):
        hdr = tk.Frame(card, bg=bg, cursor="hand2")
        hdr.place(x=0, y=0, width=inner, height=HDR_H)
        title = self._label(hdr, "%s  %s" % (CARET[expanded], b.title),
                            12, 0, inner - 84, HDR_H,
                            FONT_HDR, b.accent, bg, ghost)
        badge = self._label(hdr, b.badge(), inner - 70, 0, 58, HDR_H,
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
            self._label(card, "empty", 32, HDR_H + BODY_TOP, inner - 44, ROW_H,
                        FONT_DIM, TEXT_DIM, bg, ghost)
            return
        y = HDR_H + BODY_TOP
        for d, t in rows:
            self._row(card, b, d, t, y, inner, bg, ghost)
            y += ROW_H

    def _row(self, card, b, d, t, y, inner, bg, ghost):
        status = t["status"]
        done = status == "done"
        glyph_fg = {"todo": TEXT_DIM, "doing": b.accent, "done": TEXT_DONE}[status]

        g = self._label(card, GLYPH[status], 11, y, 18, ROW_H,
                        FONT_ROW, glyph_fg, bg, ghost, anchor="center")
        g.configure(cursor="hand2")

        date_w = 46 if (b.show_dates and d) else 0
        lbl = self._label(card, t["text"], 32, y, inner - 44 - date_w, ROW_H,
                          self.font_done if done else self.font_row,
                          TEXT_DONE if done else TEXT, bg, ghost)

        g.bind("<Button-1>", lambda e, i=t["id"]: self.cycle(i))
        lbl.bind("<Double-Button-1>", lambda e, i=t["id"]: self.edit(i))
        for w in (g, lbl):
            w.bind("<Button-3>",
                   lambda e, i=t["id"]: self.bar.open_task_menu(e.x_root,
                                                                e.y_root, i))

        if date_w:
            self._label(card, fmt_short(d), inner - 12 - date_w, y, date_w,
                        ROW_H, FONT_DIM, TEXT_DONE if done else TEXT_DIM,
                        bg, ghost, anchor="e")

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
        if self.mode == "ghost":
            self.bar.toggle_mode()
        self._set_scroll(self.content_h - self.win_h)
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
