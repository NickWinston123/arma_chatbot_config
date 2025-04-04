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

# configuration from config.ini
config = configparser.ConfigParser()
config.read('manage_chatbot_py_config.ini')

OPENVPN_PATH  = config.get('Paths', 'openvpn_path')
VPN_LOG       = config.get('Paths', 'vpn_log')
OVPN_DIR      = config.get('Paths', 'ovpn_dir')
BANNED_FILE   = config.get('Paths', 'banned_file')
COMMANDS_FILE = config.get('Paths', 'commands_file')
BANNED_LOG    = config.get('Paths', 'banned_log')
REAL_IP       = config.get('Settings', 'real_ip')

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S"
)

def log_stats():
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

def run_as_admin():
    try:
        admin = os.getuid() == 0
    except AttributeError:
        admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    if not admin:
        logging.warning("Elevating privileges...")
        params = " ".join(sys.argv)
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        sys.exit()

def kill_existing_vpn():
    logging.info("Killing any existing OpenVPN processes...")
    for proc in psutil.process_iter(['name']):
        try:
            if proc.info['name'] and "openvpn.exe" in proc.info['name'].lower():
                logging.info(f"Killing process {proc.pid} ({proc.info['name']})")
                proc.kill()
        except Exception as e:
            logging.error(f"Error killing process {proc.pid}: {e}")

def parse_ban_duration(reason):
    m = re.search(r'at least (\d+)', reason)
    if m:
        return int(m.group(1)) * 60
    return 3600

def add_to_banned_log(ovpn_file, banned_ip, ban_reason):
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

def perform_ban_actions():
    global current_mode, last_applied_mode, force_rebuild_active, rebuild_start_time, last_rebuild_ban_trigger, alt_mode

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

def load_banned_log():
    banned = {}
    if os.path.exists(BANNED_LOG):
        with open(BANNED_LOG, 'r') as f:
            for line in f:
                parts = line.strip().split(" | ")
                if len(parts) == 5:
                    ip, ovpn_file, ts, duration, reason = parts
                    banned[ip] = (ovpn_file, float(ts), int(duration), reason)
    return banned

def is_ip_banned(ip):
    banned = load_banned_log()
    if ip in banned:
        ovpn_file, ts, duration, reason = banned[ip]
        if time.time() < ts + duration:
            logging.warning(f"IP {ip} is banned until {time.strftime('%H:%M:%S', time.localtime(ts+duration))}.")
            return True
    return False

def update_commands(commands_file, command_str):
    global command_write_count
    try:
        with open(commands_file, 'a') as f:
            f.write(command_str + "\n")
        command_write_count += 1
        logging.info(f"→ {command_str} (Total Written: {command_write_count})")
    except Exception as e:
        logging.error(f"Error updating commands file: {e}")

def start_game():
    logging.info("Launching Armagetronad...")
    subprocess.Popen([r"C:\Users\itsne\Desktop\dist\armagetronad.exe"])

def connect_vpn_filtered(vpn_path, ovpn_dir, log_path):
    """Connect using an ovpn file that is not currently banned."""
    kill_existing_vpn()
    open(log_path, 'w').close()
    all_ovpn_files = [os.path.join(ovpn_dir, f) for f in os.listdir(ovpn_dir)
                      if f.lower().endswith('.ovpn')]
    if not all_ovpn_files:
        logging.error("No .ovpn files found!")
        return None, None

    banned = load_banned_log()
    banned_ovpn_files = set(entry[0] for entry in banned.values() if time.time() < entry[1] + entry[2])
    available_ovpn_files = [f for f in all_ovpn_files if f not in banned_ovpn_files]
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
        proc, used_ovpn = connect_vpn_filtered(OPENVPN_PATH, OVPN_DIR, VPN_LOG)
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

def check_banned(banned_file):
    if os.path.exists(banned_file):
        try:
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

    run_as_admin()
    
    for cmd in [
        "PLAYER_NAME_RANDOM_SMART 0",
        "PLAYER_NAME_RANDOM_STEAL 0",
        "PLAYER_NAME_PLAYERIDS 0",
        "FORCE_PLAYER_ZREBUILD 0"
    ]:
        update_commands(COMMANDS_FILE, cmd)

    counter = 0

    while True:
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
                with open(BANNED_FILE, 'r') as f:
                    ban_reason = f.read().strip()
                logging.warning(f"[Ban {ban_count}] Triggered at {time.strftime('%H:%M:%S')}")
                log_stats()
                add_to_banned_log(used_ovpn, new_ip, ban_reason)
                
                perform_ban_actions()                

                kill_existing_vpn()
                time.sleep(3)
                new_ip, used_ovpn = connect_until_new_ip(REAL_IP, banned_ip=new_ip)
            
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

if __name__ == '__main__':
    main()
