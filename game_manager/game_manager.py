import os
import sys
import time
import random
import subprocess
import psutil
import requests
import ctypes
import logging
import configparser
import re

config = configparser.ConfigParser()
ini_path = os.path.join(os.path.dirname(__file__), 'game_manager_real.ini')
try:
    config.read(ini_path)
except Exception as e:
    logging.error(f"Error reading configuration: {e}")
    sys.exit(1)

try:
    OPENVPN_PATH       = config.get('Paths', 'openvpn_path')
    VPN_LOG            = config.get('Paths', 'vpn_log')
    OVPN_DIR           = config.get('Paths', 'ovpn_dir')
    BANNED_FILE        = config.get('Paths', 'banned_file')
    COMMANDS_FILE      = config.get('Paths', 'commands_file')
    BANNED_LOG         = config.get('Paths', 'banned_log')
    OUTPUT_LOG         = config.get('Paths', 'output_log')
    EXE_PATH           = config.get('Paths', 'exe_path')
    UPDATE_FILE_CHECK  = config.get('Paths', 'update_file_check')
    REAL_IP            = config.get('Settings', 'real_ip')

except Exception as e:
    logging.error(f"Error retrieving configuration values: {e}")
    sys.exit(1)


log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S")

file_handler = logging.FileHandler(OUTPUT_LOG)
file_handler.setFormatter(log_formatter)

import io

class StreamToUTF8(io.TextIOWrapper):
    def write(self, b):
        try:
            super().write(b)
        except UnicodeEncodeError:
            super().write(b.encode('utf-8', errors='replace').decode('utf-8'))

console_handler = logging.StreamHandler(StreamToUTF8(sys.stdout.buffer, encoding='utf-8', errors='replace'))
console_handler.setFormatter(log_formatter)


logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)


# global variables
alt_mode                 = False
force_rebuild_active     = False
rebuild_start_time       = 0
last_rebuild_ban_trigger = -1
script_start_time        = time.time()
last_ban_time            = None
reset_after_ban_applied  = False
current_mode             = "Default"
last_applied_mode        = None
command_write_count      = 0
initial_launch           = True  
ban_count                = 0  
active_ban_count         = 0  

def print_log(message):
    print(message)
    logging.info(message)


def log_stats():
    try:
        elapsed = int(time.time() - script_start_time)
        hrs, rem = divmod(elapsed, 3600)
        mins, secs = divmod(rem, 60)
        uptime_str = f"{hrs}h {mins}m {secs}s"
        logging.info("-------- STATS --------")
        logging.info(f"Total Bans: {ban_count}")
        logging.info(f"Active Bans: {active_ban_count}")
        logging.info(f"Script Uptime: {uptime_str}")
        if last_ban_time:
            since_last = int(time.time() - last_ban_time)
            m, s = divmod(since_last, 60)
            last_ban_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_ban_time))
            logging.info(f"Last Ban Time: {last_ban_str}")
            logging.info(f"Time Since Last Ban: {m}m {s}s")
        logging.info(f"Current Mode: {current_mode}")
        logging.info(f"Force Rebuild Active: {force_rebuild_active}")
        if force_rebuild_active:
            remaining = int(300 - (time.time() - rebuild_start_time))
            m, s = divmod(remaining, 60)
            logging.info(f"Rebuild Cooldown: {m}m {s}s remaining")
        logging.info(f"Alt Mode: {'ON' if alt_mode else 'OFF'}")
        logging.info("-----------------------")
    except Exception as e:
        logging.error(f"Error logging stats: {e}")

def run_as_admin():
    try:
        admin = os.getuid() == 0
    except AttributeError:
        try:
            admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception as e:
            logging.error(f"Error checking admin privileges: {e}")
            admin = False
    if not admin:
        logging.warning("Elevating privileges...")
        try:
            params = " ".join(sys.argv)
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        except Exception as e:
            logging.error(f"Error during privilege elevation: {e}")
        sys.exit()

def kill_existing_vpn():
    try:
        logging.info("Killing any existing OpenVPN processes...")
        for proc in psutil.process_iter(['name']):
            try:
                if proc.info['name'] and "openvpn.exe" in proc.info['name'].lower():
                    logging.info(f"Killing process {proc.pid} ({proc.info['name']})")
                    proc.kill()
            except Exception as e:
                logging.error(f"Error killing process {proc.pid}: {e}")
    except Exception as e:
        logging.error(f"Error iterating processes for VPN kill: {e}")

def parse_ban_duration(reason):
    try:
        m = re.search(r'at least (\d+)', reason)
        if m:
            return int(m.group(1)) * 60
    except Exception as e:
        logging.error(f"Error parsing ban duration from reason '{reason}': {e}")
    return 3600

def add_to_banned_log(ovpn_file, banned_ip, ban_reason):
    try:
        if "kicked" in ban_reason.lower():
            logging.info("Kick detected; entry not logged: " + ban_reason)
            return

        ban_duration = parse_ban_duration(ban_reason)
        ts = time.time()
        entry = f"{banned_ip} | {ovpn_file} | {ts} | {ban_duration} | {ban_reason}"
        
        file_exists = os.path.exists(BANNED_LOG)
        file_empty = not file_exists or os.path.getsize(BANNED_LOG) == 0

        with open(BANNED_LOG, 'a') as f:
            if file_empty:
                header = "Banned IP | OVPN File | Timestamp | Duration (sec) | Ban Reason"
                f.write(header + "\n")
            f.write(entry + "\n")
        
        logging.info(f"Added banned log entry: {entry}")
    except Exception as e:
        logging.error(f"Error adding to banned log: {e}")

def perform_ban_actions():
    global current_mode, last_applied_mode, force_rebuild_active, rebuild_start_time, last_rebuild_ban_trigger, alt_mode
    try:
        if active_ban_count == 3 and last_applied_mode != "RANDOM_SMART":
            update_commands(COMMANDS_FILE, "PLAYER_NAME_RANDOM_SMART 1")
            current_mode = last_applied_mode = "RANDOM_SMART"
        elif active_ban_count == 5 and last_applied_mode != "RANDOM_STEAL":
            update_commands(COMMANDS_FILE, "PLAYER_NAME_RANDOM_SMART 0")
            update_commands(COMMANDS_FILE, "PLAYER_NAME_RANDOM_STEAL 1")
            current_mode = last_applied_mode = "RANDOM_STEAL"
        elif active_ban_count == 7 and last_applied_mode != "PLAYERIDS":
            update_commands(COMMANDS_FILE, "PLAYER_NAME_RANDOM_SMART 0")
            update_commands(COMMANDS_FILE, "PLAYER_NAME_RANDOM_STEAL 0")
            update_commands(COMMANDS_FILE, "PLAYER_NAME_PLAYERIDS 1")
            current_mode = last_applied_mode = "PLAYERIDS"
        elif active_ban_count >= 8:
            if active_ban_count % 8 == 0 and active_ban_count != last_rebuild_ban_trigger:
                update_commands(COMMANDS_FILE, "FORCE_PLAYER_ZREBUILD 1")
                rebuild_start_time = time.time()
                force_rebuild_active = True
                last_rebuild_ban_trigger = active_ban_count
                logging.info("FORCE_PLAYER_ZREBUILD activated.")
            if alt_mode:
                update_commands(COMMANDS_FILE, "PLAYER_NAME_PLAYERIDS 1")
                update_commands(COMMANDS_FILE, "PLAYER_NAME_RANDOM_STEAL 0")
                current_mode = "RANDOM_STEAL"
            else:
                update_commands(COMMANDS_FILE, "PLAYER_NAME_PLAYERIDS 0")
                update_commands(COMMANDS_FILE, "PLAYER_NAME_RANDOM_STEAL 1")
                current_mode = "RANDOM_STEAL"
            alt_mode = not alt_mode
    except Exception as e:
        logging.error(f"Error performing ban actions: {e}")

def load_banned_log():
    banned = {}
    try:
        if os.path.exists(BANNED_LOG):
            with open(BANNED_LOG, 'r') as f:
                header_skipped = False
                for line in f:
                    if not header_skipped:
                        if line.strip().startswith("Banned IP"):
                            header_skipped = True
                            continue
                        header_skipped = True  
                    parts = line.strip().split(" | ")
                    if len(parts) == 5:
                        ip, ovpn_file, ts, duration, reason = parts
                        banned[ip] = (ovpn_file, float(ts), int(duration), reason)
    except Exception as e:
        logging.error(f"Error loading banned log: {e}")
    return banned

def is_ip_banned(ip):
    try:
        banned = load_banned_log()
        if ip in banned:
            ovpn_file, ts, duration, reason = banned[ip]
            if time.time() < ts + duration:
                logging.warning(f"IP {ip} is banned until {time.strftime('%H:%M:%S', time.localtime(ts+duration))}.")
                return True
    except Exception as e:
        logging.error(f"Error checking if IP {ip} is banned: {e}")
    return False

def update_commands(commands_file, command_str):
    global command_write_count
    try:
        with open(commands_file, 'a') as f:
            f.write(command_str + "\n")
        command_write_count += 1
        logging.info(f"-> {command_str} (Total Written: {command_write_count})")
    except Exception as e:
        logging.error(f"Error updating commands file: {e}")

def start_game():
    while os.path.exists(UPDATE_FILE_CHECK):
        logging.info("Update in progress—waiting for it to finish…")
        time.sleep(1)

    logging.info("Waiting for Armagetronad executable to be available…")
    max_wait = 300
    waited  = 0
    interval = 2

    while not os.path.isfile(EXE_PATH):
        if waited >= max_wait:
            logging.error("Timed out waiting for Armagetronad.exe.")
            return
        time.sleep(interval)
        waited += interval

    logging.info("Armagetronad executable found. Launching…")
    subprocess.Popen([EXE_PATH])

def connect_vpn_filtered(vpn_path, ovpn_dir, log_path):
    try:
        kill_existing_vpn()
        try:
            open(log_path, 'w').close()
        except Exception as e:
            logging.error(f"Error clearing VPN log: {e}")
        
        all_ovpn_files = []
        try:
            all_ovpn_files = [os.path.join(ovpn_dir, f) for f in os.listdir(ovpn_dir)
                              if f.lower().endswith('.ovpn')]
        except Exception as e:
            logging.error(f"Error listing ovpn files: {e}")
        
        total_count = len(all_ovpn_files)
        if not all_ovpn_files:
            logging.error("No .ovpn files found!")
            return None, None

        banned = load_banned_log()
        banned_ovpn_files = set(entry[0] for entry in banned.values() if time.time() < entry[1] + entry[2])
        banned_count = sum(1 for f in all_ovpn_files if f in banned_ovpn_files)
        
        available_ovpn_files = [f for f in all_ovpn_files if f not in banned_ovpn_files]
        available_count = len(available_ovpn_files)

        logging.info(f"OVPN Files - Total: {total_count}, Available: {available_count}, Banned: {banned_count}")

        if not available_ovpn_files:
            logging.warning("All ovpn files are banned; using one anyway.")
            available_ovpn_files = all_ovpn_files

        rand_file = random.choice(available_ovpn_files)
        logging.info(f"Using VPN configuration file: {rand_file}")
        
        proc = subprocess.Popen(
            [vpn_path, '--config', rand_file, '--dev', 'tun', '--ifconfig', '10.8.0.2', '10.8.0.1'],
            stdout=open(log_path, 'w'), stderr=subprocess.STDOUT
        )
        return proc, rand_file
    except Exception as e:
        logging.error(f"Error in connect_vpn_filtered: {e}")
        return None, None

def wait_for_vpn_initialization(log_path, timeout=30, interval=1):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with open(log_path, 'r') as f:
                content = f.read()
                if "Initialization Sequence Completed" in content:
                    logging.info("VPN initialization complete.")
                    return True
                if "AUTH: Received control message: AUTH_FAILED" in content:
                    logging.error("VPN authentication failed.")
                    return False
        except Exception as e:
            logging.error(f"Error reading VPN log: {e}")
        time.sleep(interval)
    logging.warning("Timeout reached waiting for VPN initialization.")
    return False

def get_current_ip(real_ip, retry_interval=1, max_retries=30):
    retries = 0
    while retries < max_retries:
        try:
            resp = requests.get('https://httpbin.org/ip', timeout=5)
            current_ip = resp.json()['origin'].strip()
            logging.info(f"Fetched IP: {current_ip}")
            if current_ip != real_ip:
                return current_ip
            logging.warning("Current IP still matches REAL_IP. Waiting for new IP...")
        except Exception as e:
            logging.error(f"Error fetching IP (retrying): {e}")
        time.sleep(retry_interval)
        retries += 1
    return None

def connect_until_new_ip(real_ip, banned_ip=None):
    while True:
        try:
            proc, used_ovpn = connect_vpn_filtered(OPENVPN_PATH, OVPN_DIR, VPN_LOG)
            if not proc:
                logging.error("VPN process not started; retrying...")
                time.sleep(3)
                continue
            if not wait_for_vpn_initialization(VPN_LOG):
                if proc and proc.poll() is None:
                    proc.kill()
                    time.sleep(3)
                continue
            candidate_ip = get_current_ip(real_ip)
            if candidate_ip is None:
                continue
            if banned_ip and candidate_ip == banned_ip:
                logging.warning("Candidate IP matches banned IP. Killing VPN and retrying...")
                kill_existing_vpn()
                time.sleep(3)
                continue
            if is_ip_banned(candidate_ip):
                logging.warning(f"Candidate IP {candidate_ip} is in banned log. Retrying...")
                kill_existing_vpn()
                time.sleep(3)
                continue
            logging.info(f"VPN IP acquired: {candidate_ip}")
            return candidate_ip, used_ovpn
        except Exception as e:
            logging.error(f"Error in connect_until_new_ip: {e}")
            time.sleep(3)

def check_banned(banned_file):
    try:
        if os.path.exists(banned_file):
            with open(banned_file, 'r') as f:
                content = f.read().strip()
            if content:
                logging.warning("Ban detected!")
                logging.info(f"Ban Reason: {content}")
                return True
    except Exception as e:
        logging.error(f"Error reading ban file: {e}")
    return False

def main():
    global alt_mode, force_rebuild_active, rebuild_start_time, last_rebuild_ban_trigger
    global last_ban_time, reset_after_ban_applied, current_mode, last_applied_mode, initial_launch
    global ban_count, active_ban_count

    try:
        run_as_admin()
    except Exception as e:
        logging.error(f"Error in run_as_admin: {e}")
        sys.exit(1)
    
    for cmd in [
        "PLAYER_NAME_RANDOM_SMART 0",
        "PLAYER_NAME_RANDOM_STEAL 0",
        "PLAYER_NAME_PLAYERIDS 0",
        "FORCE_PLAYER_ZREBUILD 0"
    ]:
        update_commands(COMMANDS_FILE, cmd)

    counter = 0

    while True:
        try:
            if any("armagetronad.exe" in p.name() for p in psutil.process_iter()):
                logging.info("Armagetronad is running.")
            else:
                logging.info("Armagetronad is not running. Checking VPN...")
                try:
                    resp = requests.get('https://httpbin.org/ip', timeout=5)
                    current_ip = resp.json()['origin'].strip()
                except Exception as e:
                    logging.error(f"IP fetch error: {e}")
                    current_ip = REAL_IP

                new_ip, used_ovpn = connect_until_new_ip(REAL_IP)

                if not initial_launch and check_banned(BANNED_FILE):
                    ban_count += 1
                    active_ban_count += 1
                    last_ban_time = time.time()
                    reset_after_ban_applied = False
                    try:
                        with open(BANNED_FILE, 'r') as f:
                            ban_reason = f.read().strip()
                    except Exception as e:
                        logging.error(f"Error reading banned file: {e}")
                        ban_reason = "Unknown"
                    logging.warning(f"[Ban {ban_count}] Triggered at {time.strftime('%H:%M:%S')}")
                    log_stats()
                    add_to_banned_log(used_ovpn, new_ip, ban_reason)
                    
                    perform_ban_actions()                

                    kill_existing_vpn()
                    time.sleep(3)
                    new_ip, used_ovpn = connect_until_new_ip(REAL_IP, banned_ip=new_ip)
                
                if force_rebuild_active:
                    update_commands(COMMANDS_FILE, "FORCE_PLAYER_ZREBUILD 1")
                    
                start_game()
                
                if initial_launch:
                    initial_launch = False

            if last_ban_time and (time.time() - last_ban_time > 1800) and not reset_after_ban_applied:
                logging.info("30 minutes since last ban. Resetting command modes to default and active ban count.")
                for cmd in [
                    "PLAYER_NAME_RANDOM_SMART 0",
                    "PLAYER_NAME_RANDOM_STEAL 0",
                    "PLAYER_NAME_PLAYERIDS 0",
                    "FORCE_PLAYER_ZREBUILD 0"
                ]:
                    update_commands(COMMANDS_FILE, cmd)
                reset_after_ban_applied = True
                current_mode = last_applied_mode = "Default"
                active_ban_count = 0  

            counter += 1
            if counter >= 10:
                log_stats()
                counter = 0

            if force_rebuild_active and time.time() - rebuild_start_time > 300:
                update_commands(COMMANDS_FILE, "FORCE_PLAYER_ZREBUILD 0")
                logging.info("5-minute punishment ended.")
                force_rebuild_active = False

            time.sleep(5)
        except Exception as e:
            logging.error(f"Error in main loop: {e}")
            time.sleep(5)

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        logging.critical(f"Fatal error in main: {e}")
        sys.exit(1)
