import curses
import threading
import time
import os
import textwrap
import configparser
import logging
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

config = configparser.ConfigParser()
ini_path = os.path.join(os.path.dirname(__file__), 'arma_terminal_real.ini')
try:
    config.read(ini_path)
except Exception as e:
    logging.error(f"Error reading configuration: {e}")
    sys.exit(1)

try:
    CONSOLE_LOG       = config.get('Paths',    'console_log')
    COMMANDS_FILE     = config.get('Paths',    'commands_file')
    COMMAND_PREFIX    = config.get('Settings', 'command_prefix')
    MAX_LOG_LINES     = config.getint('Settings', 'max_log_lines', fallback=1000)
except Exception as e:
    logging.error(f"Error retrieving config values: {e}")
    sys.exit(1)

def tail_log(lines, lock, stop_event):
    with open(CONSOLE_LOG, 'r', encoding='utf-8', errors='ignore') as f:
        f.seek(0, os.SEEK_END)
        while not stop_event.is_set():
            chunk = f.readline()
            if not chunk:
                time.sleep(0.1)
                continue
            text = chunk.strip()
            if text:
                with lock:
                    lines.append(text)
            with lock:
                if len(lines) > MAX_LOG_LINES:
                    lines[:] = lines[-MAX_LOG_LINES:]

def draw_scrollbar(win, top_line, total_lines, height):
    if total_lines <= height:
        return
    scroll_height = height - 2
    bar_height = max(1, int(scroll_height * (height / total_lines)))
    top_pos = int(scroll_height * (top_line / total_lines))
    for i in range(scroll_height):
        char = '█' if top_pos <= i < top_pos + bar_height else '│'
        try:
            win.addch(i + 1, win.getmaxyx()[1] - 2, char)
        except curses.error:
            pass

def draw_screen(stdscr, lines, lock):
    curses.curs_set(1)
    stdscr.nodelay(True)

    height, width = stdscr.getmaxyx()
    log_h = height - 3
    log_win = curses.newwin(log_h, width, 0, 0)
    input_win = curses.newwin(3, width, log_h, 0)

    input_str = ""
    cursor_x = 0
    scroll_offset = 0
    history = []
    history_index = 0

    dragging = False
    drag_start_y = None
    drag_start_offset = None

    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)

    while True:
        with lock:
            raw_lines = lines[-MAX_LOG_LINES:]
        wrapped = []
        for line in raw_lines:
            wrapped.extend(textwrap.wrap(line, width - 3) or [''])

        visible_lines = log_h - 2
        max_offset = max(len(wrapped) - visible_lines, 0)
        scroll_offset = min(scroll_offset, max_offset)

        start_idx = max(0, len(wrapped) - visible_lines - scroll_offset)
        display = wrapped[start_idx:start_idx + visible_lines]

        log_win.erase()
        log_win.box()
        for idx, disp in enumerate(display):
            log_win.addnstr(idx + 1, 1, disp, width - 3)
        draw_scrollbar(log_win, start_idx, len(wrapped), log_h)
        log_win.refresh()

        input_win.erase()
        input_win.box()
        prompt = "> " + input_str
        input_win.addnstr(1, 1, prompt, width - 2)
        input_win.move(1, 2 + cursor_x)
        input_win.refresh()

        try:
            ch = stdscr.get_wch()
        except curses.error:
            time.sleep(0.05)
            continue

        if ch == curses.KEY_RESIZE:
            height, width = stdscr.getmaxyx()
            log_h = height - 3
            stdscr.erase(); stdscr.refresh()
            log_win.resize(log_h, width); log_win.mvwin(0, 0)
            input_win.resize(3, width); input_win.mvwin(log_h, 0)
            continue

        if ch == curses.KEY_UP:
            if history:
                history_index = max(history_index - 1, 0)
                input_str = history[history_index]
                cursor_x = len(input_str)
            continue
        if ch == curses.KEY_DOWN:
            if history:
                history_index = min(history_index + 1, len(history))
                input_str = history[history_index] if history_index < len(history) else ""
                cursor_x = len(input_str)
            continue

        if ch == curses.KEY_LEFT:
            cursor_x = max(0, cursor_x - 1)
            continue
        if ch == curses.KEY_RIGHT:
            cursor_x = min(len(input_str), cursor_x + 1)
            continue

        if ch == curses.KEY_PPAGE:
            scroll_offset = min(scroll_offset + 3, max_offset)
            continue
        if ch == curses.KEY_NPAGE:
            scroll_offset = max(scroll_offset - 3, 0)
            continue

        if ch == curses.KEY_END:
            scroll_offset = 0
            continue

        if ch == curses.KEY_MOUSE:
            try:
                _, mx, my, _, bstate = curses.getmouse()
                if bstate & curses.BUTTON1_PRESSED:
                    if 1 <= my < log_h - 1 and mx == width - 2:
                        dragging = True
                        drag_start_y = my
                        drag_start_offset = scroll_offset
                elif bstate & curses.BUTTON1_RELEASED:
                    dragging = False
                elif dragging and drag_start_y is not None:
                    dy = my - drag_start_y
                    scroll_offset = min(max(drag_start_offset - dy, 0), max_offset)
                elif bstate & curses.BUTTON4_PRESSED:
                    scroll_offset = min(scroll_offset + 1, max_offset)
                elif bstate & curses.BUTTON5_PRESSED:
                    scroll_offset = max(scroll_offset - 1, 0)
            except Exception:
                pass
            continue

        if isinstance(ch, str) and ch.isprintable():
            input_str = input_str[:cursor_x] + ch + input_str[cursor_x:]
            cursor_x += 1
        elif ch in (curses.KEY_BACKSPACE, '\b', '\x7f'):
            if cursor_x > 0:
                input_str = input_str[:cursor_x - 1] + input_str[cursor_x:]
                cursor_x -= 1
        elif ch == curses.KEY_DC:
            if cursor_x < len(input_str):
                input_str = input_str[:cursor_x] + input_str[cursor_x + 1:]
        elif ch == '\n':
            cmd = input_str.strip()
            input_str = ""
            cursor_x = 0
            if cmd:
                history.append(cmd)
            history_index = len(history)
            if cmd.lower() in ("exit", "quit"):
                return
            full_cmd = f"{COMMAND_PREFIX} {cmd}" if COMMAND_PREFIX else cmd
            try:
                with open(COMMANDS_FILE, 'a', encoding='utf-8') as f:
                    f.write(full_cmd + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            except Exception as e:
                with lock:
                    lines.append(f"→ ERROR writing command: {e}")
            else:
                with lock:
                    lines.append(f"→ SENT: {full_cmd}")
        elif ch in (curses.KEY_EXIT, '\x1b'):
            return

def main(stdscr):
    lines = []
    lock = threading.Lock()
    stop_event = threading.Event()

    t = threading.Thread(target=tail_log, args=(lines, lock, stop_event), daemon=True)
    t.start()

    try:
        draw_screen(stdscr, lines, lock)
    finally:
        stop_event.set()
        t.join(0.1)

if __name__ == "__main__":
    import curses
    curses.wrapper(main)