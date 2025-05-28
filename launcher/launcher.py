#!/usr/bin/env python3
import subprocess
import time
import win32gui
import win32con
import sys
import os
import logging
import configparser
import re
import ctypes
from ctypes import wintypes

config = configparser.ConfigParser()
ini_path = os.path.join(os.path.dirname(__file__), 'launcher_real.ini')
try:
    config.read(ini_path)
except Exception as e:
    logging.error(f"Error reading configuration: {e}")
    sys.exit(1)

CONFIG_DIR = config.get('Paths', 'config_dir')
LOG_FILE   = config.get('Paths', 'log_file')

raw_excl = config.get('Settings', 'exclude_keywords').strip()
if raw_excl.startswith('[') and raw_excl.endswith(']'):
    raw_excl = raw_excl[1:-1]
EXCLUDE_KEYWORDS = [kw.strip().lower() for kw in raw_excl.split(',') if kw.strip()]

PYTHON = sys.executable

def get_work_area():
    SPI_GETWORKAREA = 0x0030
    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    return rect.right - rect.left, rect.bottom - rect.top

screen_w, screen_h = get_work_area()
half_w, half_h = screen_w // 2, screen_h // 2

WINDOWS = [
    # top-left
    (["arma_terminal"],
     f'cmd.exe /C start "" cmd /K "title arma_terminal && python \"{os.path.join(CONFIG_DIR, "arma_terminal", "arma_terminal.py")}\""',
     (0, 0),
     False),

    # top-right
    (["game_manager"],
     f'cmd.exe /C start "" cmd /K "title game_manager && python \"{os.path.join(CONFIG_DIR, "game_manager", "game_manager.py")}\""',
     (half_w, 0),
     False),

    # bottom-left
    (["ollama_chat"],
     f'cmd.exe /C start "" cmd /K "title ollama_chat && python \"{os.path.join(CONFIG_DIR, "ollama_chat", "ollama_chat.py")}\""',
     (0, half_h),
     False),
]

# bottom-right
UPDATER = (
    ["game_updater"],
    f'cmd.exe /C start "" cmd /K "title game_updater && '
    f'cd /d \"{os.path.join(CONFIG_DIR, "game_updater")}\" && '
    f'python game_updater.py"',
    (half_w, half_h),
    False
)

def enum_windows():
    wins = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                wins.append((hwnd, title))
        return True
    win32gui.EnumWindows(cb, None)
    return wins

def normalize(s: str) -> str:
    return re.sub(r'\s+', ' ', s.strip().lower())

def find_window(alias_list):
    norm_aliases = [normalize(a) for a in alias_list]
    for hwnd, title in enum_windows():
        n = normalize(title)
        if any(excl in n for excl in EXCLUDE_KEYWORDS):
            continue
        if any(alias in n for alias in norm_aliases):
            print(f"[DEBUG] Matched: {title!r} ← for {alias_list}")
            return hwnd

    if alias_list == ["game_manager"]:
        for hwnd, title in enum_windows():
            n = normalize(title)
            if n.startswith("[c:\\users\\itsne\\desktop\\arma_chatbot_config\\vpn\\ovpn") and "openvpn" in n:
                print(f"[DEBUG] Matched OpenVPN: {title!r}")
                return hwnd

    print(f"[DEBUG] No match for: {alias_list}")
    print("[DEBUG] Open windows:")
    for _, t in enum_windows():
        print(" -", t)
    return None

def launch_and_position(aliases, cmd, pos, minimize):
    x, y = pos or (0, 0)
    hwnd = find_window(aliases)
    if hwnd:
        print(f"[REPOSITION] {aliases[0]}; moving to {x},{y}")
        win32gui.MoveWindow(hwnd, x, y, half_w, half_h, True)
        if minimize:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        return

    print(f"[LAUNCH] {aliases[0]} …")
    subprocess.Popen(cmd, shell=True)

    for _ in range(50):
        time.sleep(0.1)
        hwnd = find_window(aliases)
        if hwnd:
            break

    if not hwnd:
        print(f"[WARN] couldn’t find window “{aliases[0]}” after launch.")
        return

    print(f"[POSITION] {aliases[0]}; moving to {x},{y}")
    win32gui.MoveWindow(hwnd, x, y, half_w, half_h, True)
    if minimize:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)

def main():
    for aliases, cmd, pos, mini in WINDOWS:
        launch_and_position(aliases, cmd, pos, mini)

    aliases, cmd, pos, mini = UPDATER
    launch_and_position(aliases, cmd, pos, mini)

if __name__ == "__main__":
    main()
