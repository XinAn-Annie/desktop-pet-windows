"""Small login-time helper that makes the desktop-pet shortcut a real launcher.

It is intentionally separate from the Tk application.  It remains tiny in the
background, sends a toggle message to a running pet, or starts the pet if it is
not already running.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "DesktopPet"
SETTINGS_FILE = APP_DIR / "settings.json"
CONTROL_PORT = 48761
LOCK_PORT = 48762


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"activation": "hotkey", "hotkey": "win+alt+o"}


def parse_hotkey(value: str) -> tuple[int, int] | None:
    parts = [part.strip().lower() for part in value.split("+") if part.strip()]
    if not parts or len(parts[-1]) != 1:
        return None
    modifiers = 0
    flags = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004, "win": 0x0008, "windows": 0x0008}
    for part in parts[:-1]:
        modifiers |= flags.get(part, 0)
    return (modifiers, ord(parts[-1].upper())) if modifiers else None


def pressed(modifiers: int, key: int) -> bool:
    user32 = ctypes.windll.user32
    if not user32.GetAsyncKeyState(key) & 0x8000:
        return False
    for flag, virtual_keys in ((0x0001, (0x12,)), (0x0002, (0x11,)), (0x0004, (0x10,)), (0x0008, (0x5B, 0x5C))):
        if modifiers & flag and not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in virtual_keys):
            return False
    return True


def toggle_running_pet() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(0.35)
            client.sendto(b"toggle", ("127.0.0.1", CONTROL_PORT))
            return client.recvfrom(16)[0] == b"ok"
    except OSError:
        return False


def start_pet():
    folder = Path(__file__).resolve().parent
    pythonw = folder / ".venv" / "Scripts" / "pythonw.exe"
    app = folder / "app.py"
    if pythonw.exists() and app.exists():
        subprocess.Popen([str(pythonw), str(app), "--show"], cwd=folder, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main():
    # A localhost port makes duplicate login/manual launcher starts harmless.
    try:
        lock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        lock.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        return
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(APP_DIR / 'launcher.log'), level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    registered = None
    failed = None
    launching_until = 0.0
    logging.info('Launcher started, pid=%s', os.getpid())
    try:
        while True:
            settings = load_settings()
            if settings.get("activation") != "hotkey":
                return
            shortcut = parse_hotkey(settings.get("hotkey", "win+alt+o"))
            if shortcut != registered and shortcut != failed:
                if registered:
                    user32.UnregisterHotKey(None, 1)
                    registered = None
                if shortcut and user32.RegisterHotKey(None, 1, shortcut[0] | 0x4000, shortcut[1]):
                    registered = shortcut
                    failed = None
                    logging.info('Hotkey registered: %s', settings.get('hotkey'))
                else:
                    failed = shortcut
                    logging.error('Hotkey registration failed: %s error=%s', shortcut, ctypes.get_last_error())
                    user32.MessageBoxW(None, '桌宠快捷键注册失败，可能被其他软件占用。请打开桌宠设置，更换快捷键后保存。', '桌宠启动器', 0x10)
            message = wintypes.MSG()
            while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                if message.message == 0x0312 and message.wParam == 1:
                    logging.info('Hotkey received')
                    if not toggle_running_pet() and time.monotonic() >= launching_until:
                        start_pet()
                        logging.info('Pet launch requested')
                        launching_until = time.monotonic() + 3
            time.sleep(0.06)
    finally:
        if registered:
            user32.UnregisterHotKey(None, 1)
        lock.close()
        logging.info('Launcher stopped')


if __name__ == "__main__":
    main()
