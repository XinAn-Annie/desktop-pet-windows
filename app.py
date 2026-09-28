"""Windows desktop pet with a calendar workbench.

Screenshot workflow: screenshot -> Ctrl+C -> click pet -> Ctrl+V.
Images are OCR'd locally and can become one-off or recurring calendar events.
"""

from __future__ import annotations

import calendar
import ctypes
import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse
from feed_monitor import fetch_latest
from image_provider import generate_image

try:
    from PIL import Image, ImageGrab, ImageTk
except ImportError:
    Image = ImageGrab = ImageTk = None

try:
    import pytesseract
except ImportError:
    pytesseract = None
if pytesseract and os.environ.get("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]

try:
    import psutil
except ImportError:
    psutil = None

try:
    import pystray
except ImportError:
    pystray = None

APP_TITLE = "小桌宠工作台"
APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "DesktopPet"
SETTINGS_FILE = APP_DIR / "settings.json"
EVENTS_FILE = APP_DIR / "events.json"
FEEDS_FILE = APP_DIR / "feeds.json"
STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
CONTROL_PORT = 48761
EVENT_COLORS = {"ddl": "#ffd6d6", "normal": "#fff1b8", "regular": "#cfe3ff", "meeting": "#ffd1e3"}
EVENT_LABELS = {"ddl": "DDL", "normal": "普通", "regular": "规律", "meeting": "会议"}


def load_settings() -> dict:
    try:
        value = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if "activation" not in value:
            value["activation"] = "startup" if value.get("autostart") else "hotkey"
        return value
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"first_run": True, "activation": "hotkey", "hotkey": "win+alt+o", "avatar": ""}


def save_settings(settings: dict):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def load_events() -> list[dict]:
    try:
        value = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            return []
        for event in value:
            event.setdefault("start", event.get("time", "全天"))
            if event["start"] == "全天":
                event.setdefault("end", "全天")
            else:
                event.setdefault("end", add_minutes(event["start"], 60))
        return value
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def save_events(events: list[dict]):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    EVENTS_FILE.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")


def load_feeds() -> list[dict]:
    try:
        value = json.loads(FEEDS_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def save_feeds(feeds: list[dict]):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    FEEDS_FILE.write_text(json.dumps(feeds, ensure_ascii=False, indent=2), encoding="utf-8")


def add_minutes(time_text: str, amount: int) -> str:
    try:
        hour, minute = map(int, time_text.split(":"))
        total = min(23 * 60 + 59, hour * 60 + minute + amount)
        return f"{total // 60:02d}:{total % 60:02d}"
    except (ValueError, AttributeError):
        return "全天"


def configure_tesseract() -> str | None:
    if not pytesseract:
        return None
    candidates = [
        os.environ.get("TESSERACT_CMD", ""), shutil.which("tesseract") or "",
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe"),
        str(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            pytesseract.pytesseract.tesseract_cmd = candidate
            try:
                pytesseract.get_tesseract_version()
                return candidate
            except (pytesseract.TesseractNotFoundError, OSError):
                continue
    return None


def visible_app_processes() -> list[tuple[str, str, int, str]]:
    """Return visible top-level Windows windows with their owning process details."""
    if os.name != "nt" or not psutil:
        return []
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    results = []
    def collect(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd): return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0: return True
        title = ctypes.create_unicode_buffer(length + 1); user32.GetWindowTextW(hwnd, title, length + 1)
        pid = wintypes.DWORD(); user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            process = psutil.Process(pid.value); info = process.memory_info()
            results.append((title.value, process.name(), pid.value, f"{info.rss / 1024 / 1024:.1f} MB"))
        except (psutil.NoSuchProcess, psutil.AccessDenied): pass
        return True
    user32.EnumWindows(enum_proc(collect), 0)
    results.sort(key=lambda item: item[0].lower())
    return results


def run_hidden(script_name: str) -> bool:
    """Start one of this folder's VBS launchers without opening a console."""
    script = Path(__file__).with_name(script_name)
    if os.name != "nt" or not script.exists():
        return False
    try:
        subprocess.Popen(["wscript.exe", str(script)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except OSError:
        return False


def set_activation_mode(activation: str) -> bool:
    """Choose exactly one login-time program: pet itself or the hotkey launcher."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE) as key:
            script = "start_desktop_pet.vbs" if activation == "startup" else "start_hotkey_launcher.vbs"
            command = f'"{os.environ.get("WINDIR", r"C:\\Windows")}\\System32\\wscript.exe" "{Path(__file__).with_name(script)}"'
            winreg.SetValueEx(key, "DesktopPet", 0, winreg.REG_SZ, command)
        if activation == "hotkey":
            run_hidden("start_hotkey_launcher.vbs")
        return True
    except OSError:
        return False


def parse_hotkey(value: str) -> tuple[int, int] | None:
    parts = [x.strip().lower() for x in value.split("+") if x.strip()]
    if not parts or len(parts[-1]) != 1:
        return None
    mods = 0
    for part in parts[:-1]:
        mods |= {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004, "win": 0x0008, "windows": 0x0008}.get(part, 0)
    if not mods:
        return None
    return mods, ord(parts[-1].upper())


def parse_event_text(text: str, selected: dt.date, kind: str) -> list[dict]:
    """Parse Chinese dates, time ranges and daily/weekly recurrence expressions."""
    clean = " ".join(x.strip() for x in text.splitlines() if x.strip())
    if not clean:
        return []
    def parse_clock(match):
        hour, minute = int(match.group("hour")), int(match.group("minute") or 0)
        period = match.group("period") or ""
        if period in ("下午", "晚上") and hour < 12: hour += 12
        if period in ("凌晨", "早上", "上午") and hour == 12: hour = 0
        return f"{min(hour, 23):02d}:{min(minute, 59):02d}"

    time_pattern = r"(?P<period>凌晨|早上|上午|中午|下午|晚上)?\s*(?P<hour>\d{1,2})(?::(?P<minute>\d{2})|点(?P<minute_cn>\d{1,2})?分?)"
    time_matches = list(re.finditer(time_pattern, clean))
    def clock_value(match):
        minute = match.group("minute") or match.group("minute_cn") or "0"
        hour = int(match.group("hour")); period = match.group("period") or ""
        if period in ("下午", "晚上") and hour < 12: hour += 12
        if period in ("凌晨", "早上", "上午") and hour == 12: hour = 0
        if period == "中午" and hour < 11: hour += 12
        return f"{min(hour, 23):02d}:{min(int(minute), 59):02d}"
    start_time = clock_value(time_matches[0]) if time_matches else "全天"
    end_time = clock_value(time_matches[1]) if len(time_matches) > 1 else add_minutes(start_time, 60)
    if len(time_matches) > 1 and not time_matches[1].group("period") and time_matches[0].group("period") in ("下午", "晚上") and end_time[:2] != "12":
        end_time = f"{min(int(end_time[:2]) + 12, 23):02d}:{end_time[3:]}"
    if start_time == end_time and start_time != "全天": end_time = add_minutes(start_time, 60)

    range_match = re.search(r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})(?:日)?(?:[-~至](\d{1,2})(?:日)?)?", clean)
    date_match = re.search(r"(今天|明天|后天)", clean)
    dates = []
    if range_match:
        year = int(range_match.group(1) or selected.year); month, start = int(range_match.group(2)), int(range_match.group(3)); end = int(range_match.group(4) or start)
        try: dates = [dt.date(year, month, day) for day in range(start, end + 1)]
        except ValueError: return []
    elif date_match:
        offset = {"今天": 0, "明天": 1, "后天": 2}[date_match.group(1)]; dates = [selected + dt.timedelta(days=offset)]
    else:
        weekdays = re.search(r"每周([一二三四五六日天]+)", clean)
        if weekdays:
            weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
            targets = {weekday_map[x] for x in weekdays.group(1)}
            dates = [selected + dt.timedelta(days=offset) for offset in range(366) if (selected + dt.timedelta(days=offset)).weekday() in targets]
        elif "每天" in clean or "每日" in clean:
            dates = [selected + dt.timedelta(days=offset) for offset in range(30)]
        else:
            dates = [selected]

    title = re.sub(r"(?:(?:20\d{2})年)?\d{1,2}月\d{1,2}(?:日)?(?:[-~至]\d{1,2}(?:日)?)?", "", clean)
    title = re.sub(r"今天|明天|后天|每天|每日|每周[一二三四五六日天]+", "", title)
    title = re.sub(time_pattern, "", title)
    title = re.sub(r"\s+", " ", title).strip(" ：:，,")
    title = re.sub(r"^(?:到|至)\s*|\s+(?:到|至)\s+", "", title) or "未命名事件"
    if kind == "auto":
        if re.search(r"DDL|deadline|截止|交作业|到期", clean, re.I): kind = "ddl"
        elif re.search(r"开会|会议|面试|组会", clean): kind = "meeting"
        elif "每天" in clean or "每周" in clean or "每日" in clean: kind = "regular"
        else: kind = "normal"
    return [{"date": value.isoformat(), "start": start_time, "end": end_time, "time": start_time, "title": title, "kind": kind} for value in dates]


class SettingsDialog(tk.Toplevel):
    def __init__(self, master, settings):
        super().__init__(master); self.title("首次运行设置" if settings.get("first_run", True) else "桌宠设置"); self.resizable(False, False); self.settings = settings
        self.activation = tk.StringVar(value=settings.get("activation", "hotkey")); self.hotkey = tk.StringVar(value=settings.get("hotkey", "win+alt+o"))
        ttk.Label(self, text="桌宠启动方式", font=("Microsoft YaHei UI", 14, "bold")).pack(padx=24, pady=(18, 8))
        ttk.Radiobutton(self, text="登录 Windows 后自动显示桌宠", variable=self.activation, value="startup").pack(anchor="w", padx=24, pady=4)
        ttk.Radiobutton(self, text="使用独立快捷键启动器（桌宠不用常驻）", variable=self.activation, value="hotkey").pack(anchor="w", padx=24, pady=4)
        ttk.Label(self, text="快捷键启动器会在登录后静默运行。按快捷键时：桌宠未运行则启动，已运行则显示/隐藏。", wraplength=390, foreground="#666").pack(anchor="w", padx=24, pady=(4, 10))
        row = ttk.Frame(self); row.pack(fill="x", padx=24); ttk.Label(row, text="唤醒快捷键：").pack(side="left"); ttk.Entry(row, textvariable=self.hotkey, width=18).pack(side="left")
        ttk.Label(self, text="默认：Win + Alt + O", foreground="#666").pack(anchor="w", padx=24, pady=(4, 14)); ttk.Button(self, text="保存", command=self.finish).pack(pady=(0, 18))
        self.grab_set(); self.protocol("WM_DELETE_WINDOW", self.finish)

    def finish(self):
        value = self.hotkey.get().strip().lower()
        if not parse_hotkey(value):
            messagebox.showwarning("快捷键格式", "请输入类似 win+alt+o 或 ctrl+shift+p 的组合。", parent=self); return
        self.settings.update({"first_run": False, "activation": self.activation.get(), "hotkey": value}); save_settings(self.settings)
        if not set_activation_mode(self.activation.get()): messagebox.showwarning("启动方式未设置", "Windows 未允许写入当前用户的登录启动项。请以普通用户重新打开桌宠后再保存。", parent=self)
        self.master.apply_activation_visibility(); self.destroy()


FirstRunDialog = SettingsDialog


class Workbench(tk.Toplevel):
    def __init__(self, master: "DesktopPet"):
        super().__init__(master); self.title(APP_TITLE); self.transient(master)
        area = wintypes.RECT()
        if os.name == "nt" and ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0):
            left, top, screen_w, screen_h = area.left, area.top, area.right - area.left, area.bottom - area.top
        else:
            left, top, screen_w, screen_h = 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = min(1120, screen_w - 24), screen_h - 64
        self.geometry(f"{width}x{height}+{left + (screen_w - width) // 2}+{top + 12}")
        self.minsize(min(850, width), min(560, height))
        self.full_day = tk.BooleanVar(value=True)
        self.events = master.events; self.selected_date = dt.date.today(); self.input_image = None; self._build(); self.refresh_calendar(); self.refresh_processes(); self.after(4000, self.auto_refresh_processes); self.after(3000, self.auto_refresh_feeds)

    def _build(self):
        top = ttk.Frame(self, padding=10); top.pack(fill="x"); self.date_label = ttk.Label(top, font=("Microsoft YaHei UI", 14, "bold")); self.date_label.pack(side="left"); self.status = ttk.Label(top, text="截图 Ctrl+C 后，在输入框按 Ctrl+V"); self.status.pack(side="right"); self._update_date()
        notebook = ttk.Notebook(self); notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        calendar_tab = ttk.Frame(notebook, padding=8); notebook.add(calendar_tab, text="日历与 DDL"); self._build_calendar_tab(calendar_tab)
        process_tab = ttk.Frame(notebook, padding=8); notebook.add(process_tab, text="运行中的程序"); self._build_process_tab(process_tab)
        web_tab = ttk.Frame(notebook, padding=8); notebook.add(web_tab, text="网址提醒"); self._build_feeds_tab(web_tab)

    def _build_feeds_tab(self, parent):
        ttk.Label(parent, text="订阅 RSS/Atom；普通网页会尝试检测公开订阅或检查页面文字变化。摘要在本机生成。", wraplength=820).pack(anchor="w", pady=(0, 8))
        row = ttk.Frame(parent); row.pack(fill="x"); self.feed_url_var = tk.StringVar(); ttk.Entry(row, textvariable=self.feed_url_var).pack(side="left", fill="x", expand=True); ttk.Button(row, text="添加网址", command=self.add_feed).pack(side="left", padx=5)
        self.feed_tree = ttk.Treeview(parent, columns=("source", "latest", "summary", "checked"), show="headings", height=14)
        for col, label, width in (("source", "网址", 230), ("latest", "最新更新", 210), ("summary", "本地摘要", 330), ("checked", "检查状态", 150)):
            self.feed_tree.heading(col, text=label); self.feed_tree.column(col, width=width)
        self.feed_tree.pack(fill="both", expand=True, pady=8)
        actions = ttk.Frame(parent); actions.pack(fill="x"); ttk.Button(actions, text="立即检查选中", command=self.check_selected_feed).pack(side="left"); ttk.Button(actions, text="删除订阅", command=self.delete_selected_feed).pack(side="left", padx=5); self.refresh_feed_view()

    def add_feed(self):
        url = self.feed_url_var.get().strip()
        if self.master.add_feed(url): self.feed_url_var.set(""); self.refresh_feed_view()

    def check_selected_feed(self):
        selection = self.feed_tree.selection()
        if selection: self.master.check_feed(selection[0])

    def delete_selected_feed(self):
        selection = self.feed_tree.selection()
        if selection: self.master.delete_feed(selection[0]); self.refresh_feed_view()

    def refresh_feed_view(self):
        if not hasattr(self, "feed_tree") or not self.feed_tree.winfo_exists(): return
        for row in self.feed_tree.get_children(): self.feed_tree.delete(row)
        for feed in self.master.feeds:
            iid = feed["url"]
            state = feed.get("error") or feed.get("last_checked", "尚未检查")
            self.feed_tree.insert("", "end", iid=iid, values=(feed["url"], feed.get("title", ""), feed.get("summary", ""), state))

    def auto_refresh_feeds(self):
        if not self.winfo_exists(): return
        if self.winfo_viewable(): self.refresh_feed_view()
        self.after(5000, self.auto_refresh_feeds)

    def _build_calendar_tab(self, parent):
        body = ttk.Frame(parent); body.pack(fill="both", expand=True); left = ttk.Frame(body); left.pack(side="left", fill="both", expand=True); nav = ttk.Frame(left); nav.pack(fill="x")
        ttk.Button(nav, text="‹", width=3, command=lambda: self.shift_month(-1)).pack(side="left"); self.month_label = ttk.Label(nav, width=16, anchor="center", font=("Microsoft YaHei UI", 11, "bold")); self.month_label.pack(side="left"); ttk.Button(nav, text="›", width=3, command=lambda: self.shift_month(1)).pack(side="left"); ttk.Button(nav, text="今天", command=lambda: self.select_date(dt.date.today())).pack(side="right")
        self.calendar_frame = ttk.Frame(left); self.calendar_frame.pack(fill="x", pady=8); legend = ttk.Frame(left); legend.pack(fill="x", pady=(0, 8))
        for kind, label in EVENT_LABELS.items(): tk.Label(legend, text=f"  {label}  ", bg=EVENT_COLORS[kind]).pack(side="left", padx=2)
        right = ttk.LabelFrame(body, text="当天时间安排", padding=8); right.pack(side="right", fill="both", expand=True, padx=(14, 0)); self.day_title = ttk.Label(right, font=("Microsoft YaHei UI", 11, "bold")); self.day_title.pack(anchor="w")
        timeline_frame = ttk.Frame(right); timeline_frame.pack(fill="both", expand=True, pady=6); self.timeline = tk.Canvas(timeline_frame, height=300, background="white", highlightthickness=0); timeline_scroll = ttk.Scrollbar(timeline_frame, orient="vertical", command=self.timeline.yview); self.timeline.configure(yscrollcommand=timeline_scroll.set); self.timeline.pack(side="left", fill="both", expand=True); timeline_scroll.pack(side="right", fill="y"); self.timeline.bind("<Configure>", lambda _e: self.refresh_timeline())
        ttk.Checkbutton(right, text="显示全天（取消勾选可放大时间轴）", variable=self.full_day, command=self.refresh_timeline).pack(anchor="w")
        self.timeline.bind("<MouseWheel>", lambda event: self.timeline.yview_scroll(-int(event.delta / 120), "units"))
        ttk.Label(right, text="添加文字或规律事件，例如：9月10-29每天上午2点上课").pack(anchor="w"); self.input_box = tk.Text(right, height=4, wrap="word"); self.input_box.pack(fill="x", pady=4); self.input_box.bind("<Control-v>", self.on_paste)
        controls = ttk.Frame(right); controls.pack(fill="x"); self.kind_var = tk.StringVar(value="自动识别"); self.kind_values = {"自动识别": "auto", "DDL": "ddl", "普通事件": "normal", "规律事件": "regular", "会议": "meeting"}; ttk.Combobox(controls, textvariable=self.kind_var, values=list(self.kind_values), state="readonly", width=10).pack(side="left"); ttk.Button(controls, text="解析并添加", command=self.add_event_text).pack(side="left", padx=5); ttk.Button(controls, text="编辑", command=self.edit_event).pack(side="left"); ttk.Button(controls, text="完成/恢复", command=self.toggle_done).pack(side="left", padx=5); ttk.Button(controls, text="删除选中", command=self.delete_event).pack(side="right")

    def _build_process_tab(self, parent):
        columns = ("title", "name", "pid", "memory"); self.process_tree = ttk.Treeview(parent, columns=columns, show="headings")
        for col, title, width in (("title", "打开的窗口", 350), ("name", "进程", 190), ("pid", "PID", 80), ("memory", "内存", 100)): self.process_tree.heading(col, text=title); self.process_tree.column(col, width=width)
        self.process_tree.pack(fill="both", expand=True); ttk.Button(parent, text="刷新当前程序", command=self.refresh_processes).pack(anchor="e", pady=6)

    def _update_date(self):
        self.date_label.config(text=dt.datetime.now().strftime("%Y年%m月%d日  %A")); self.after(30_000, self._update_date)

    def shift_month(self, delta):
        year, month = self.selected_date.year, self.selected_date.month + delta
        if month == 0: year, month = year - 1, 12
        if month == 13: year, month = year + 1, 1
        self.selected_date = dt.date(year, month, 1); self.refresh_calendar()

    def select_date(self, value): self.selected_date = value; self.refresh_calendar()

    def refresh_calendar(self):
        for child in self.calendar_frame.winfo_children(): child.destroy()
        year, month = self.selected_date.year, self.selected_date.month; self.month_label.config(text=f"{year}年{month}月")
        for col, name in enumerate(("一", "二", "三", "四", "五", "六", "日")):
            ttk.Label(self.calendar_frame, text=name, anchor="center").grid(row=0, column=col, sticky="ew"); self.calendar_frame.columnconfigure(col, weight=1)
        for row, week in enumerate(calendar.monthcalendar(year, month), 1):
            for col, day in enumerate(week):
                if not day: ttk.Label(self.calendar_frame, text="").grid(row=row, column=col, padx=2, pady=2); continue
                value = dt.date(year, month, day); events = [x for x in self.events if x["date"] == value.isoformat()]; priority = {"ddl": 0, "meeting": 1, "regular": 2, "normal": 3}; events.sort(key=lambda x: priority.get(x.get("kind"), 4)); bg = EVENT_COLORS[events[0]["kind"]] if events else ("#e6f4ea" if value == dt.date.today() else "white")
                tk.Button(self.calendar_frame, text=str(day), bg=bg, relief="sunken" if value == self.selected_date else "raised", command=lambda v=value: self.select_date(v)).grid(row=row, column=col, sticky="nsew", padx=2, pady=2)
        self.day_title.config(text=f"{self.selected_date.isoformat()} 的安排"); self.selected_event = None; self.refresh_timeline()

    def refresh_timeline(self):
        if not hasattr(self, "timeline") or not self.timeline.winfo_exists(): return
        canvas = self.timeline; canvas.delete("all"); width = max(canvas.winfo_width(), 340); label_width = 48
        values = sorted([x for x in self.events if x["date"] == self.selected_date.isoformat()], key=lambda x: x.get("start", x.get("time", "全天")))
        all_day = [x for x in values if x.get("start", x.get("time", "全天")) == "全天"]
        header = len(all_day) * 26
        hour_height = max(8, (canvas.winfo_height() - header - 22) / 24) if self.full_day.get() else 46
        canvas.configure(scrollregion=(0, 0, width, header + hour_height * 24 + 22))
        if self.full_day.get(): canvas.yview_moveto(0)
        for hour in range(25):
            y = header + hour * hour_height
            if hour_height >= 18 or hour % 2 == 0: canvas.create_text(4, y + 2, anchor="nw", text=f"{hour:02d}:00", fill="#666", font=("Microsoft YaHei UI", 8))
            canvas.create_line(label_width, y, width, y, fill="#e7e7e7")
        for event in values:
            start, end = event.get("start", event.get("time", "全天")), event.get("end", "全天")
            if start == "全天": y1, y2 = all_day.index(event) * 26, (all_day.index(event) + 1) * 26
            else:
                try:
                    sh, sm = map(int, start.split(":")); eh, em = map(int, end.split(":")); y1 = sh * hour_height + sm * hour_height / 60; y2 = eh * hour_height + em * hour_height / 60
                except (ValueError, AttributeError): continue
                if y2 <= y1: y2 = y1 + hour_height
                y1 += header; y2 += header
            kind = event.get("kind", "normal"); color = EVENT_COLORS.get(kind, EVENT_COLORS["normal"]); selected = event is getattr(self, "selected_event", None); tag = f"event_{id(event)}"
            canvas.create_rectangle(label_width + 3, y1 + 2, width - 4, max(y2 - 2, y1 + 22), fill=color, outline="#555" if selected else color, width=2 if selected else 1, tags=(tag,))
            prefix = "✓ " if event.get("done") else ""; when = "全天" if start == "全天" else f"{start}-{end}"
            canvas.create_text(label_width + 9, y1 + 5, anchor="nw", text=f"{when} [{EVENT_LABELS.get(kind, '普通')}] {prefix}{event['title']}", fill="#333", font=("Microsoft YaHei UI", 9), tags=(tag,))
            canvas.tag_bind(tag, "<Button-1>", lambda _e, item=event: self.select_event(item)); canvas.tag_bind(tag, "<Double-Button-1>", lambda _e, item=event: self.edit_event(item))

    def select_event(self, event): self.selected_event = event; self.refresh_timeline()

    def on_paste(self, _event=None):
        if ImageGrab:
            try: image = ImageGrab.grabclipboard()
            except Exception: image = None
            if isinstance(image, list) and image and Image:
                try: image = Image.open(image[0]).copy()
                except (OSError, ValueError): image = None
            if image is not None and hasattr(image, "size"):
                self.input_image = image; self.status.config(text="已收到截图，正在本地 OCR…"); threading.Thread(target=self.ocr_image, args=(image,), daemon=True).start(); return "break"
        try: text = self.clipboard_get()
        except tk.TclError: text = ""
        if text: self.input_box.insert("insert", text); self.status.config(text="已粘贴文字")
        return "break"

    def ocr_image(self, image):
        if not pytesseract:
            result = "OCR 组件未安装。请在项目目录运行 setup.ps1。"
        elif not configure_tesseract():
            result = "未检测到 Tesseract OCR 引擎。请在 PowerShell 执行：winget install -e --id UB-Mannheim.TesseractOCR；安装时选择 Chinese Simplified (chi_sim) 语言数据，然后重启桌宠。"
        else:
            try:
                languages = set(pytesseract.get_languages(config=""))
                if "chi_sim" in languages:
                    result = pytesseract.image_to_string(image, lang="chi_sim+eng")
                elif "eng" in languages:
                    result = "[提示：未找到 chi_sim 中文语言数据，以下按英文模型识别；请安装中文语言包后重启桌宠。]\n\n" + pytesseract.image_to_string(image, lang="eng")
                else:
                    result = "Tesseract 已安装，但缺少 eng 和 chi_sim 语言数据。请安装英文及简体中文语言包。"
            except Exception as exc:
                result = f"OCR 失败：{exc}"
        self.after(0, lambda: self.finish_ocr(result))

    def finish_ocr(self, text): self.input_box.delete("1.0", "end"); self.input_box.insert("1.0", text.strip()); self.status.config(text="OCR 完成，请确认后解析")

    def add_event_text(self):
        text = self.input_box.get("1.0", "end").strip(); events = parse_event_text(text, self.selected_date, self.kind_values.get(self.kind_var.get(), "auto"))
        if not events: messagebox.showinfo("提示", "请输入事件内容或先粘贴截图。", parent=self); return
        self.events.extend(events); save_events(self.events); self.input_box.delete("1.0", "end"); self.refresh_calendar(); self.status.config(text=f"已添加 {len(events)} 个事件")

    def delete_event(self):
        if not getattr(self, "selected_event", None): return
        self.events.remove(self.selected_event); self.selected_event = None; save_events(self.events); self.refresh_calendar()

    def toggle_done(self):
        if not getattr(self, "selected_event", None): return
        self.selected_event["done"] = not self.selected_event.get("done", False); save_events(self.events); self.refresh_calendar()

    def edit_event(self, event=None):
        event = event or getattr(self, "selected_event", None)
        if not event: return
        dialog = tk.Toplevel(self); dialog.title("编辑日程"); dialog.transient(self); dialog.grab_set(); fields = {}
        for row, (label, key, default) in enumerate((("事项", "title", event["title"]), ("开始时间 HH:MM", "start", event.get("start", "全天")), ("结束时间 HH:MM", "end", event.get("end", "全天")))):
            ttk.Label(dialog, text=label).grid(row=row, column=0, padx=10, pady=6, sticky="w"); fields[key] = tk.StringVar(value=default); ttk.Entry(dialog, textvariable=fields[key], width=28).grid(row=row, column=1, padx=10, pady=6)
        kind_names = {value: key for key, value in EVENT_LABELS.items()}; kind_names["ddl"] = "DDL"; kind_names["normal"] = "普通"; kind_names["regular"] = "规律"; kind_names["meeting"] = "会议"
        kind_var = tk.StringVar(value=kind_names.get(event.get("kind"), "普通")); ttk.Label(dialog, text="类型").grid(row=3, column=0, padx=10, pady=6, sticky="w"); ttk.Combobox(dialog, textvariable=kind_var, values=("DDL", "普通", "规律", "会议"), state="readonly").grid(row=3, column=1, padx=10, pady=6, sticky="ew")
        def save_edit():
            start, end = fields["start"].get().strip(), fields["end"].get().strip()
            if start != "全天" and (not re.fullmatch(r"\d{2}:\d{2}", start) or not re.fullmatch(r"\d{2}:\d{2}", end)):
                messagebox.showwarning("时间格式", "请输入 24 小时制时间，例如 09:30。", parent=dialog); return
            event.update({"title": fields["title"].get().strip(), "start": start, "end": end, "time": start, "kind": {"DDL": "ddl", "普通": "normal", "规律": "regular", "会议": "meeting"}[kind_var.get()]}); save_events(self.events); dialog.destroy(); self.refresh_calendar()
        ttk.Button(dialog, text="保存", command=save_edit).grid(row=4, column=1, pady=10, sticky="e")

    def refresh_processes(self):
        for item in self.process_tree.get_children(): self.process_tree.delete(item)
        if not psutil: self.process_tree.insert("", "end", values=("未安装 psutil", "-", "-", "请运行 setup.ps1")); return
        rows = visible_app_processes()
        if not rows: self.process_tree.insert("", "end", values=("当前没有可见应用窗口", "", "", "")); return
        for title, name, pid, memory in rows: self.process_tree.insert("", "end", values=(title, name, pid, memory))

    def auto_refresh_processes(self):
        if not self.winfo_exists(): return
        if self.winfo_viewable(): self.refresh_processes()
        self.after(4000, self.auto_refresh_processes)


class DesktopPet(tk.Tk):
    def __init__(self, settings):
        super().__init__(); self.settings = settings; self.events = load_events(); self.workbench = None; self.avatar_photo = None; self.drag_start = None; self.click_job = None
        self.feeds = load_feeds(); self.checking_feeds = set(); self.title("Desktop Pet"); self.overrideredirect(True); self.attributes("-topmost", True); self.configure(bg="#ff00ff")
        try: self.attributes("-transparentcolor", "#ff00ff")
        except tk.TclError: pass
        position = settings.get("position", [80, 80]); self.geometry(f"170x190+{int(position[0])}+{int(position[1])}"); self.canvas = tk.Canvas(self, width=170, height=190, bg="#ff00ff", highlightthickness=0); self.canvas.pack(); self.draw_pet()
        self.canvas.bind("<ButtonPress-1>", self.begin_drag); self.canvas.bind("<B1-Motion>", self.drag_pet); self.canvas.bind("<ButtonRelease-1>", self.end_drag); self.canvas.bind("<Button-3>", self.context_menu); self.bind("<Escape>", lambda _e: self.destroy()); self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.tray = None; self.notified = set(); self.start_tray(); self.after(5000, self.check_event_reminders); self.after(15000, self.poll_feeds)
        self.start_control_server()

    def draw_pet(self):
        self.canvas.delete("all")
        if self.settings.get("avatar") and Image and ImageTk:
            try:
                image = Image.open(self.settings["avatar"]).convert("RGBA"); image.thumbnail((150, 150)); self.avatar_photo = ImageTk.PhotoImage(image); self.canvas.create_image(85, 78, image=self.avatar_photo); self.canvas.create_text(85, 178, text="点击打开工作台", fill="#333", font=("Microsoft YaHei UI", 9)); return
            except (OSError, ValueError): pass
        c = self.canvas; c.create_oval(36, 25, 134, 115, fill="#f4b183", outline="#633b27", width=3); c.create_polygon(43, 43, 43, 10, 72, 34, fill="#f4b183", outline="#633b27"); c.create_polygon(127, 43, 127, 10, 98, 34, fill="#f4b183", outline="#633b27"); c.create_oval(61, 58, 69, 68, fill="#333"); c.create_oval(101, 58, 109, 68, fill="#333"); c.create_oval(82, 75, 92, 82, fill="#8b3d3d"); c.create_arc(70, 72, 104, 100, start=200, extent=140, style="arc", width=2); c.create_oval(52, 108, 118, 163, fill="#a9d18e", outline="#45633b", width=3); c.create_text(85, 178, text="点击打开工作台", fill="#333", font=("Microsoft YaHei UI", 9))

    def context_menu(self, event):
        menu = tk.Menu(self, tearoff=0); menu.add_command(label="打开工作台", command=self.open_workbench); menu.add_command(label="上传图片桌宠形象", command=self.upload_avatar); menu.add_command(label="生成宠物形象", command=self.open_avatar_generator); menu.add_command(label="桌宠设置", command=self.open_settings); menu.add_separator(); menu.add_command(label="隐藏桌宠", command=self.withdraw); menu.add_command(label="显示桌宠", command=self.deiconify); menu.add_command(label="退出", command=self.destroy); menu.tk_popup(event.x_root, event.y_root)

    def upload_avatar(self):
        path = filedialog.askopenfilename(filetypes=[("图片", "*.png;*.jpg;*.jpeg;*.webp;*.bmp")])
        if path: self.settings["avatar"] = path; save_settings(self.settings); self.draw_pet()


    def open_avatar_generator(self):
        try:
            import keyring
            saved_key = keyring.get_password("desktop-pet-windows", "image-api-key") or ""
        except Exception:
            keyring = None; saved_key = ""
        dialog = tk.Toplevel(self); dialog.title("生成桌宠形象"); dialog.geometry("500x390"); dialog.transient(self); dialog.grab_set()
        ttk.Label(dialog, text="使用 OpenAI-compatible Images API 生成一张宠物形象。API 密钥仅保存在 Windows 凭据管理器中。", wraplength=460).pack(anchor="w", padx=14, pady=(14, 8))
        form = ttk.Frame(dialog, padding=8); form.pack(fill="both", expand=True); fields = {}
        for row, (label, name, default, secret) in enumerate((
            ("API 地址", "endpoint", self.settings.get("image_api_base", ""), False),
            ("图片模型", "model", self.settings.get("image_model", ""), False),
            ("API 密钥", "key", saved_key, True),
        )):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=6); fields[name] = tk.StringVar(value=default); ttk.Entry(form, textvariable=fields[name], width=48, show="*" if secret else "").grid(row=row, column=1, sticky="ew", pady=6)
        reference_path = tk.StringVar(value="")
        ttk.Label(form, text="参考图片").grid(row=3, column=0, sticky="w", pady=6)
        reference_row = ttk.Frame(form); reference_row.grid(row=3, column=1, sticky="ew", pady=6); ttk.Button(reference_row, text="选择图片（可选）", command=lambda: choose_reference()).pack(side="left"); reference_label = ttk.Label(reference_row, text="未选择"); reference_label.pack(side="left", padx=8)
        def choose_reference():
            path = filedialog.askopenfilename(parent=dialog, filetypes=[("图片", "*.png;*.jpg;*.jpeg;*.webp")])
            if path: reference_path.set(path); reference_label.configure(text=Path(path).name)
        ttk.Label(form, text="形象描述").grid(row=4, column=0, sticky="nw", pady=6); prompt = tk.Text(form, height=5, wrap="word"); prompt.grid(row=4, column=1, sticky="ew", pady=6); prompt.insert("1.0", "参考图片中的对象，生成一只可爱、友善、适合做桌面宠物的原创形象，单体居中，简洁背景")
        ttk.Label(form, text="生成时，参考图会发送到上方填写的图像服务。", wraplength=360, foreground="#666").grid(row=5, column=1, sticky="w")
        form.columnconfigure(1, weight=1); buttons = ttk.Frame(dialog, padding=(14, 4, 14, 14)); buttons.pack(fill="x")
        generate_button = ttk.Button(buttons, text="生成", command=lambda: submit()); generate_button.pack(side="right")
        def submit():
            endpoint, model, api_key, description = fields["endpoint"].get().strip(), fields["model"].get().strip(), fields["key"].get().strip(), prompt.get("1.0", "end").strip()
            if not endpoint.startswith(("https://", "http://")) or not model or not api_key or not description:
                messagebox.showwarning("请填写完整", "需要 API 地址、模型名称、API 密钥和形象描述。", parent=dialog); return
            if keyring is None:
                messagebox.showerror("凭据管理器不可用", "无法使用系统凭据管理器，请安装 requirements.txt 中的依赖后重启。", parent=dialog); return
            try: keyring.set_password("desktop-pet-windows", "image-api-key", api_key)
            except Exception as exc: messagebox.showerror("密钥保存失败", str(exc), parent=dialog); return
            self.settings["image_api_base"] = endpoint; self.settings["image_model"] = model; save_settings(self.settings); generate_button.configure(state="disabled", text="生成中…")
            threading.Thread(target=self.generate_avatar_thread, args=(endpoint, model, api_key, description, reference_path.get(), dialog), daemon=True).start()
        ttk.Button(buttons, text="关闭", command=dialog.destroy).pack(side="right", padx=8)

    def generate_avatar_thread(self, endpoint, model, api_key, prompt, reference_image, dialog):
        try: image_data, error = generate_image(endpoint, model, api_key, prompt, reference_image), None
        except Exception as exc: image_data, error = None, str(exc)
        try: self.after(0, lambda: self.finish_avatar_generation(image_data, error, dialog))
        except tk.TclError: pass

    def finish_avatar_generation(self, image_data, error, dialog):
        if not dialog.winfo_exists(): return
        if error:
            messagebox.showerror("生成失败", error[:1200], parent=dialog)
            for child in dialog.winfo_children():
                for button in child.winfo_children():
                    if isinstance(button, ttk.Button) and button.cget("text") == "生成中…": button.configure(state="normal", text="生成")
            return
        avatar_dir = APP_DIR / "avatars"; avatar_dir.mkdir(parents=True, exist_ok=True); path = avatar_dir / f"generated_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
        try:
            path.write_bytes(image_data)
            if Image:
                with Image.open(path) as image: image.verify()
        except Exception as exc:
            try: path.unlink()
            except OSError: pass
            messagebox.showerror("图片无效", str(exc), parent=dialog); return
        self.settings["avatar"] = str(path); save_settings(self.settings); self.draw_pet(); dialog.destroy(); messagebox.showinfo("完成", "新形象已应用到桌宠。", parent=self)

    def open_workbench(self):
        self.deiconify()
        if self.workbench is None or not self.workbench.winfo_exists(): self.workbench = Workbench(self)
        else: self.workbench.deiconify(); self.workbench.lift()

    def open_settings(self):
        SettingsDialog(self, self.settings)

    def apply_activation_visibility(self):
        if self.settings.get("activation") == "startup": self.deiconify()
        else: self.withdraw()

    def begin_drag(self, event):
        self.drag_start = (event.x_root, event.y_root, self.winfo_x(), self.winfo_y())

    def drag_pet(self, event):
        if not self.drag_start: return
        start_x, start_y, window_x, window_y = self.drag_start
        self.geometry(f"+{window_x + event.x_root - start_x}+{window_y + event.y_root - start_y}")

    def end_drag(self, event):
        if not self.drag_start: return
        start_x, start_y = self.drag_start[:2]
        moved = abs(event.x_root - start_x) + abs(event.y_root - start_y)
        self.drag_start = None
        if moved < 8:
            self.open_workbench()
        else:
            self.settings["position"] = [self.winfo_x(), self.winfo_y()]; save_settings(self.settings)

    def start_control_server(self):
        """Receive toggle commands from the independent hotkey launcher."""
        def listen():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
                    server.bind(("127.0.0.1", CONTROL_PORT))
                    while self.winfo_exists():
                        message, address = server.recvfrom(64)
                        if message == b"toggle":
                            self.after(0, self.toggle_pet)
                            server.sendto(b"ok", address)
            except OSError:
                # Another pet instance already owns the local control port.
                pass
        threading.Thread(target=listen, name="desktop-pet-control", daemon=True).start()

    def start_tray(self):
        if not pystray or not Image:
            return
        icon_image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        from PIL import ImageDraw
        draw = ImageDraw.Draw(icon_image); draw.ellipse((7, 9, 57, 59), fill="#f4b183", outline="#633b27", width=3); draw.ellipse((20, 27, 24, 32), fill="#333333"); draw.ellipse((40, 27, 44, 32), fill="#333333")
        menu = pystray.Menu(
            pystray.MenuItem("显示/隐藏桌宠", lambda _icon, _item: self.after(0, self.toggle_pet)),
            pystray.MenuItem("打开工作台", lambda _icon, _item: self.after(0, self.open_workbench)),
            pystray.MenuItem("桌宠设置", lambda _icon, _item: self.after(0, self.open_settings)),
            pystray.MenuItem("退出", lambda icon, _item: (icon.stop(), self.after(0, self.destroy))),
        )
        self.tray = pystray.Icon("desktop_pet", icon_image, "桌宠工作台", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def toggle_pet(self):
        if self.state() == "withdrawn": self.deiconify()
        else: self.withdraw()

    def add_feed(self, url):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            messagebox.showwarning("网址格式", "请输入完整的 http:// 或 https:// 网址。", parent=self); return False
        if any(item["url"] == url for item in self.feeds):
            messagebox.showinfo("已订阅", "这个网址已经在监控列表中。", parent=self); return False
        self.feeds.append({"url": url, "title": "", "summary": "", "last_id": "", "last_checked": "尚未检查"}); save_feeds(self.feeds); self.check_feed(url); return True

    def delete_feed(self, url):
        self.feeds[:] = [feed for feed in self.feeds if feed["url"] != url]; save_feeds(self.feeds)

    def check_feed(self, url):
        if url in self.checking_feeds: return
        feed = next((item for item in self.feeds if item["url"] == url), None)
        if not feed: return
        self.checking_feeds.add(url); feed.pop("error", None)
        threading.Thread(target=self.fetch_feed_thread, args=(url,), daemon=True).start()

    def fetch_feed_thread(self, url):
        try: result, error = fetch_latest(url), None
        except Exception as exc: result, error = None, str(exc)
        try: self.after(0, lambda: self.finish_feed_check(url, result, error))
        except tk.TclError: pass

    def finish_feed_check(self, url, result, error):
        self.checking_feeds.discard(url); feed = next((item for item in self.feeds if item["url"] == url), None)
        if not feed: return
        feed["last_checked"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        if error:
            feed["error"] = error[:160]
        else:
            old_id = feed.get("last_id", ""); changed = bool(old_id and old_id != result["latest_id"])
            feed.update(result); feed["last_id"] = result["latest_id"]; feed.pop("error", None)
            if changed and self.tray:
                prefix = "网页内容更新" if result.get("page_mode") else "订阅有新内容"
                try: self.tray.notify(f"{result['title']}\n{result['summary']}", title=prefix)
                except Exception: pass
        save_feeds(self.feeds)
        if self.workbench and self.workbench.winfo_exists(): self.workbench.refresh_feed_view()

    def poll_feeds(self):
        if not self.winfo_exists(): return
        for feed in list(self.feeds): self.check_feed(feed["url"])
        self.after(15 * 60 * 1000, self.poll_feeds)

    def check_event_reminders(self):
        if not self.winfo_exists(): return
        now = dt.datetime.now().replace(second=0, microsecond=0)
        for event in self.events:
            if event.get("done"): continue
            clock = event.get("start", event.get("time", "全天")); clock = "09:00" if clock == "全天" and event.get("kind") == "ddl" else clock
            if clock == "全天": continue
            try: due = dt.datetime.combine(dt.date.fromisoformat(event["date"]), dt.time.fromisoformat(clock))
            except (ValueError, KeyError): continue
            offsets = (60, 0) if event.get("kind") == "ddl" else (0,)
            for offset in offsets:
                reminder_at = due - dt.timedelta(minutes=offset); key = (event["date"], clock, event["title"], offset)
                if key not in self.notified and now == reminder_at:
                    self.notified.add(key)
                    if self.tray:
                        label = "DDL 即将截止" if offset else ("DDL 到期" if event.get("kind") == "ddl" else "日程提醒")
                        try: self.tray.notify(f"{event['title']} · {due.strftime('%H:%M')}", title=label)
                        except Exception: pass
        self.after(30_000, self.check_event_reminders)


def main():
    settings = load_settings(); root = DesktopPet(settings); root.withdraw()
    if settings.get("first_run", True): root.after(100, lambda: FirstRunDialog(root, settings))
    elif settings.get("activation") == "startup" or "--show" in sys.argv: root.deiconify()
    root.mainloop()


if __name__ == "__main__": main()
