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
import sys
import threading
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

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
STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
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
        return value if isinstance(value, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def save_events(events: list[dict]):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    EVENTS_FILE.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")


def set_autostart(enabled: bool):
    if os.name != "nt":
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                exe = sys.executable[:-10] + "pythonw.exe" if sys.executable.lower().endswith("python.exe") else sys.executable
                command = f'"{exe}" "{Path(__file__).resolve()}" --startup'
                winreg.SetValueEx(key, "DesktopPet", 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, "DesktopPet")
                except FileNotFoundError:
                    pass
    except OSError:
        pass


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
    """Parse expressions such as 9月10-29每天上午2点上课."""
    clean = " ".join(x.strip() for x in text.splitlines() if x.strip())
    if not clean:
        return []
    time_match = re.search(r"(上午|下午|晚上|早上)?\s*(\d{1,2})(?::|点)(\d{0,2})", clean)
    time_text = ""
    if time_match:
        hour = int(time_match.group(2)); minute = int(time_match.group(3) or 0)
        if time_match.group(1) in ("下午", "晚上") and hour < 12: hour += 12
        time_text = f"{hour:02d}:{minute:02d}"
    title = re.sub(r"\d{1,2}月\d{1,2}(?:[-~至]\d{1,2})?日?", "", clean)
    title = re.sub(r"(?:每天|每周[一二三四五六日天]?|上午|下午|晚上|早上)\s*\d{0,2}(?::|点)?\d{0,2}", "", title)
    title = re.sub(r"\s+", " ", title).strip(" ：:，,") or "未命名事件"
    range_match = re.search(r"(\d{1,2})月(\d{1,2})(?:[-~至](\d{1,2}))?", clean)
    dates = [selected]
    if range_match:
        month, start = int(range_match.group(1)), int(range_match.group(2)); end = int(range_match.group(3) or start)
        year = selected.year
        dates = [dt.date(year, month, day) for day in range(start, end + 1) if day <= calendar.monthrange(year, month)[1]]
    return [{"date": value.isoformat(), "time": time_text or "全天", "title": title, "kind": kind} for value in dates]


class SettingsDialog(tk.Toplevel):
    def __init__(self, master, settings):
        super().__init__(master); self.title("首次运行设置" if settings.get("first_run", True) else "桌宠设置"); self.resizable(False, False); self.settings = settings
        self.activation = tk.StringVar(value=settings.get("activation", "hotkey")); self.hotkey = tk.StringVar(value=settings.get("hotkey", "win+alt+o"))
        ttk.Label(self, text="桌宠启动方式", font=("Microsoft YaHei UI", 14, "bold")).pack(padx=24, pady=(18, 8))
        ttk.Radiobutton(self, text="登录 Windows 后自动显示桌宠", variable=self.activation, value="startup").pack(anchor="w", padx=24, pady=4)
        ttk.Radiobutton(self, text="登录后在后台运行，按快捷键显示/隐藏", variable=self.activation, value="hotkey").pack(anchor="w", padx=24, pady=4)
        ttk.Label(self, text="快捷键需要后台程序保持运行，因此此模式会在登录后启动到系统托盘，桌宠本体保持隐藏。", wraplength=360, foreground="#666").pack(anchor="w", padx=24, pady=(4, 10))
        row = ttk.Frame(self); row.pack(fill="x", padx=24); ttk.Label(row, text="唤醒快捷键：").pack(side="left"); ttk.Entry(row, textvariable=self.hotkey, width=18).pack(side="left")
        ttk.Label(self, text="默认：Win + Alt + O", foreground="#666").pack(anchor="w", padx=24, pady=(4, 14)); ttk.Button(self, text="保存", command=self.finish).pack(pady=(0, 18))
        self.grab_set(); self.protocol("WM_DELETE_WINDOW", self.finish)

    def finish(self):
        value = self.hotkey.get().strip().lower()
        if not parse_hotkey(value):
            messagebox.showwarning("快捷键格式", "请输入类似 win+alt+o 或 ctrl+shift+p 的组合。", parent=self); return
        self.settings.update({"first_run": False, "activation": self.activation.get(), "hotkey": value}); save_settings(self.settings); set_autostart(True)
        self.master.apply_activation_visibility(); self.destroy()


FirstRunDialog = SettingsDialog


class Workbench(tk.Toplevel):
    def __init__(self, master: "DesktopPet"):
        super().__init__(master); self.title(APP_TITLE); self.geometry("980x680"); self.minsize(850, 560); self.transient(master)
        self.events = master.events; self.selected_date = dt.date.today(); self.input_image = None; self._build(); self.bind_all("<Control-v>", self.on_paste, add="+"); self.refresh_calendar(); self.refresh_processes()

    def _build(self):
        top = ttk.Frame(self, padding=10); top.pack(fill="x"); self.date_label = ttk.Label(top, font=("Microsoft YaHei UI", 14, "bold")); self.date_label.pack(side="left"); self.status = ttk.Label(top, text="截图 Ctrl+C 后，在输入框按 Ctrl+V"); self.status.pack(side="right"); self._update_date()
        notebook = ttk.Notebook(self); notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        calendar_tab = ttk.Frame(notebook, padding=8); notebook.add(calendar_tab, text="日历与 DDL"); self._build_calendar_tab(calendar_tab)
        process_tab = ttk.Frame(notebook, padding=8); notebook.add(process_tab, text="运行中的程序"); self._build_process_tab(process_tab)
        web_tab = ttk.Frame(notebook, padding=8); notebook.add(web_tab, text="网址提醒"); ttk.Label(web_tab, text="网址监控模块预留：下一步接入 RSS/Atom 和网页更新摘要。", foreground="#555").pack(anchor="w")

    def _build_calendar_tab(self, parent):
        body = ttk.Frame(parent); body.pack(fill="both", expand=True); left = ttk.Frame(body); left.pack(side="left", fill="both", expand=True); nav = ttk.Frame(left); nav.pack(fill="x")
        ttk.Button(nav, text="‹", width=3, command=lambda: self.shift_month(-1)).pack(side="left"); self.month_label = ttk.Label(nav, width=16, anchor="center", font=("Microsoft YaHei UI", 11, "bold")); self.month_label.pack(side="left"); ttk.Button(nav, text="›", width=3, command=lambda: self.shift_month(1)).pack(side="left"); ttk.Button(nav, text="今天", command=lambda: self.select_date(dt.date.today())).pack(side="right")
        self.calendar_frame = ttk.Frame(left); self.calendar_frame.pack(fill="x", pady=8); legend = ttk.Frame(left); legend.pack(fill="x", pady=(0, 8))
        for kind, label in EVENT_LABELS.items(): tk.Label(legend, text=f"  {label}  ", bg=EVENT_COLORS[kind]).pack(side="left", padx=2)
        right = ttk.LabelFrame(body, text="当天安排", padding=8); right.pack(side="right", fill="both", expand=True, padx=(14, 0)); self.day_title = ttk.Label(right, font=("Microsoft YaHei UI", 11, "bold")); self.day_title.pack(anchor="w"); self.schedule = tk.Listbox(right, height=15, font=("Microsoft YaHei UI", 10)); self.schedule.pack(fill="both", expand=True, pady=6)
        ttk.Label(right, text="添加文字或规律事件，例如：9月10-29每天上午2点上课").pack(anchor="w"); self.input_box = tk.Text(right, height=4, wrap="word"); self.input_box.pack(fill="x", pady=4); self.input_box.bind("<Control-v>", self.on_paste)
        controls = ttk.Frame(right); controls.pack(fill="x"); self.kind_var = tk.StringVar(value="normal"); ttk.Combobox(controls, textvariable=self.kind_var, values=list(EVENT_LABELS), state="readonly", width=9).pack(side="left"); ttk.Button(controls, text="解析并添加", command=self.add_event_text).pack(side="left", padx=6); ttk.Button(controls, text="上传课表图片", command=self.choose_schedule_image).pack(side="left"); ttk.Button(controls, text="删除选中", command=self.delete_event).pack(side="right")

    def _build_process_tab(self, parent):
        columns = ("name", "pid", "memory"); self.process_tree = ttk.Treeview(parent, columns=columns, show="headings")
        for col, title, width in (("name", "程序", 320), ("pid", "PID", 100), ("memory", "内存", 130)): self.process_tree.heading(col, text=title); self.process_tree.column(col, width=width)
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
                value = dt.date(year, month, day); events = [x for x in self.events if x["date"] == value.isoformat()]; bg = EVENT_COLORS[events[0]["kind"]] if events else ("#e6f4ea" if value == dt.date.today() else "white")
                tk.Button(self.calendar_frame, text=str(day), bg=bg, relief="sunken" if value == self.selected_date else "raised", command=lambda v=value: self.select_date(v)).grid(row=row, column=col, sticky="nsew", padx=2, pady=2)
        self.day_title.config(text=f"{self.selected_date.isoformat()} 的安排"); self.schedule.delete(0, "end")
        for event in sorted([x for x in self.events if x["date"] == self.selected_date.isoformat()], key=lambda x: x["time"]): self.schedule.insert("end", f"{event['time']}  [{EVENT_LABELS[event['kind']]}] {event['title']}")

    def on_paste(self, _event=None):
        if ImageGrab:
            try: image = ImageGrab.grabclipboard()
            except Exception: image = None
            if image is not None and hasattr(image, "size"):
                self.input_image = image; self.status.config(text="已收到截图，正在本地 OCR…"); threading.Thread(target=self.ocr_image, args=(image,), daemon=True).start(); return "break"
        try: text = self.clipboard_get()
        except tk.TclError: text = ""
        if text: self.input_box.insert("insert", text); self.status.config(text="已粘贴文字")
        return "break"

    def ocr_image(self, image):
        if not pytesseract: result = "请先安装 pytesseract 和 Tesseract OCR。"
        else:
            try: result = pytesseract.image_to_string(image, lang="chi_sim+eng")
            except Exception as exc: result = f"OCR 失败：{exc}"
        self.after(0, lambda: self.finish_ocr(result))

    def finish_ocr(self, text): self.input_box.delete("1.0", "end"); self.input_box.insert("1.0", text.strip()); self.status.config(text="OCR 完成，请确认后解析")

    def choose_schedule_image(self):
        if not Image: messagebox.showerror("缺少 Pillow", "请先运行 setup.ps1。", parent=self); return
        path = filedialog.askopenfilename(filetypes=[("图片", "*.png;*.jpg;*.jpeg;*.bmp")])
        if path: self.status.config(text="正在识别课表图片…"); threading.Thread(target=self.ocr_image, args=(Image.open(path),), daemon=True).start()

    def add_event_text(self):
        text = self.input_box.get("1.0", "end").strip(); events = parse_event_text(text, self.selected_date, self.kind_var.get())
        if not events: messagebox.showinfo("提示", "请输入事件内容或先粘贴截图。", parent=self); return
        self.events.extend(events); save_events(self.events); self.input_box.delete("1.0", "end"); self.refresh_calendar(); self.status.config(text=f"已添加 {len(events)} 个事件")

    def delete_event(self):
        index = self.schedule.curselection()
        if not index: return
        visible = sorted([x for x in self.events if x["date"] == self.selected_date.isoformat()], key=lambda x: x["time"]); self.events.remove(visible[index[0]]); save_events(self.events); self.refresh_calendar()

    def refresh_processes(self):
        for item in self.process_tree.get_children(): self.process_tree.delete(item)
        if not psutil: self.process_tree.insert("", "end", values=("未安装 psutil", "-", "请运行 setup.ps1")); return
        rows = []
        for proc in psutil.process_iter(["name", "pid", "memory_info"]):
            try:
                info = proc.info; memory = info.get("memory_info"); rss = memory.rss / 1024 / 1024 if memory else 0; rows.append((rss, info.get("name") or "未知", info.get("pid"), f"{rss:.1f} MB"))
            except (psutil.NoSuchProcess, psutil.AccessDenied): pass
        for _, name, pid, memory in sorted(rows, reverse=True)[:80]: self.process_tree.insert("", "end", values=(name, pid, memory))


class DesktopPet(tk.Tk):
    def __init__(self, settings):
        super().__init__(); self.settings = settings; self.events = load_events(); self.workbench = None; self.avatar_photo = None; self.drag_start = None; self.click_job = None; self.hotkey_was_down = False
        self.title("Desktop Pet"); self.overrideredirect(True); self.attributes("-topmost", True); self.configure(bg="#ff00ff")
        try: self.attributes("-transparentcolor", "#ff00ff")
        except tk.TclError: pass
        position = settings.get("position", [80, 80]); self.geometry(f"170x190+{int(position[0])}+{int(position[1])}"); self.canvas = tk.Canvas(self, width=170, height=190, bg="#ff00ff", highlightthickness=0); self.canvas.pack(); self.draw_pet()
        self.canvas.bind("<ButtonPress-1>", self.begin_drag); self.canvas.bind("<B1-Motion>", self.drag_pet); self.canvas.bind("<ButtonRelease-1>", self.end_drag); self.canvas.bind("<Button-3>", self.context_menu); self.bind("<Escape>", lambda _e: self.destroy()); self.protocol("WM_DELETE_WINDOW", self.destroy); self.after(100, self.poll_hotkey)
        self.tray = None; self.start_tray()

    def draw_pet(self):
        self.canvas.delete("all")
        if self.settings.get("avatar") and Image and ImageTk:
            try:
                image = Image.open(self.settings["avatar"]).convert("RGBA"); image.thumbnail((150, 150)); self.avatar_photo = ImageTk.PhotoImage(image); self.canvas.create_image(85, 78, image=self.avatar_photo); self.canvas.create_text(85, 178, text="点击打开工作台", fill="#333", font=("Microsoft YaHei UI", 9)); return
            except (OSError, ValueError): pass
        c = self.canvas; c.create_oval(36, 25, 134, 115, fill="#f4b183", outline="#633b27", width=3); c.create_polygon(43, 43, 43, 10, 72, 34, fill="#f4b183", outline="#633b27"); c.create_polygon(127, 43, 127, 10, 98, 34, fill="#f4b183", outline="#633b27"); c.create_oval(61, 58, 69, 68, fill="#333"); c.create_oval(101, 58, 109, 68, fill="#333"); c.create_oval(82, 75, 92, 82, fill="#8b3d3d"); c.create_arc(70, 72, 104, 100, start=200, extent=140, style="arc", width=2); c.create_oval(52, 108, 118, 163, fill="#a9d18e", outline="#45633b", width=3); c.create_text(85, 178, text="点击打开工作台", fill="#333", font=("Microsoft YaHei UI", 9))

    def context_menu(self, event):
        menu = tk.Menu(self, tearoff=0); menu.add_command(label="打开工作台", command=self.open_workbench); menu.add_command(label="上传图片桌宠形象", command=self.upload_avatar); menu.add_command(label="桌宠设置", command=self.open_settings); menu.add_separator(); menu.add_command(label="隐藏桌宠", command=self.withdraw); menu.add_command(label="显示桌宠", command=self.deiconify); menu.add_command(label="退出", command=self.destroy); menu.tk_popup(event.x_root, event.y_root)

    def upload_avatar(self):
        path = filedialog.askopenfilename(filetypes=[("图片", "*.png;*.jpg;*.jpeg;*.webp;*.bmp")])
        if path: self.settings["avatar"] = path; save_settings(self.settings); self.draw_pet()

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

    def poll_hotkey(self):
        parsed = parse_hotkey(self.settings.get("hotkey", "win+alt+o")); down = False
        if self.settings.get("activation") == "hotkey" and parsed and os.name == "nt":
            modifiers, key = parsed; user32 = ctypes.windll.user32
            down = bool(user32.GetAsyncKeyState(key) & 0x8000)
            for flag, vks in ((0x0001, (0x12,)), (0x0002, (0x11,)), (0x0004, (0x10,)), (0x0008, (0x5B, 0x5C))):
                if modifiers & flag: down = down and any(bool(user32.GetAsyncKeyState(vk) & 0x8000) for vk in vks)
        if down and not self.hotkey_was_down: self.deiconify() if self.state() == "withdrawn" else self.withdraw()
        self.hotkey_was_down = down
        self.after(100, self.poll_hotkey)

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


def main():
    settings = load_settings(); root = DesktopPet(settings); root.withdraw()
    if settings.get("first_run", True): root.after(100, lambda: FirstRunDialog(root, settings))
    elif settings.get("activation") == "startup": root.deiconify()
    root.mainloop()


if __name__ == "__main__": main()
