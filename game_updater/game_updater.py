import os
import time
import shutil
import psutil
import configparser
from datetime import datetime
import logging
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def resolve_path(path):
    return path if os.path.isabs(path) else os.path.abspath(os.path.join(BASE_DIR, path))

config = configparser.ConfigParser(interpolation=None) 
ini_path = os.path.join(BASE_DIR, 'game_updater_real.ini')

try:
    config.read(ini_path)

    SOURCE_EXE        = config.get('Paths', 'source_exe') 
    SOURCE_DIR        = config.get('Paths', 'source_dir')  
    DEST_DIR          = config.get('Paths', 'dest_dir')    
    MOD_CACHE_FILE    = resolve_path(config.get('Paths', 'mod_cache_file'))
    STATS_FILE        = config.get('Paths', 'stats_file')  
    BACKUP_DIR        = resolve_path(config.get('Paths', 'backup_dir'))
    USER_FILE         = config.get('Paths', 'user_file')  
    UPDATE_FILE_CHECK = resolve_path(config.get('Paths', 'update_file_check'))

except Exception as e:
    logging.error(f"Error retrieving configuration values: {e}")
    sys.exit(1)

os.makedirs(BACKUP_DIR, exist_ok=True)

def is_process_running(name):
    for proc in psutil.process_iter(['name']):
        if proc.info['name'] and name.lower() in proc.info['name'].lower():
            return True
    return False

def get_file_modified_time(filepath):
    return time.ctime(os.path.getmtime(filepath)) if os.path.exists(filepath) else None

import errno

def copytree_safe(src, dst):
    os.makedirs(dst, exist_ok=True)
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        try:
            if os.path.isdir(s):
                copytree_safe(s, d)
            else:
                shutil.copy2(s, d)
        except FileNotFoundError as e:
            logging.warning(f"File not found during copy: {s} -> {d} | {e}")
        except Exception as e:
            logging.error(f"Error copying {s} -> {d}: {e}")

def main_loop():
    while True:
        logging.info("Checking if remote source folder exists...")
        if not os.path.exists(SOURCE_DIR):
            logging.warning(f"Folder {SOURCE_DIR} not found. Waiting 10 seconds before retrying...")
            time.sleep(10)
            continue

        logging.info("Waiting for armagetronad.exe to close...")
        while is_process_running("armagetronad.exe"):
            time.sleep(1)

        time.sleep(1) 

        if not os.path.exists(SOURCE_EXE):
            logging.error("ERROR: armagetronad.exe is missing! Skipping update cycle.")
            wait_and_continue()
            continue

        modified_time = get_file_modified_time(SOURCE_EXE)

        last_modified = None
        if os.path.exists(MOD_CACHE_FILE):
            try:
                with open(MOD_CACHE_FILE, 'r') as f:
                    last_modified = f.read().strip()
            except Exception as e:
                logging.error(f"Failed to read mod cache file: {e}")

        if modified_time == last_modified:
            logging.info("No changes detected in armagetronad.exe, skipping copy.")
        else:
            logging.info("Change detected in armagetronad.exe...")

            if not os.path.exists(os.path.join(SOURCE_DIR, 'armagetronad.exe')):
                logging.error("ERROR: Source folder became unavailable. Skipping deletion.")
                wait_and_continue()
                continue

            try:
                open(UPDATE_FILE_CHECK, 'w').close()
                logging.info(f"Created update‐lock file: {UPDATE_FILE_CHECK}")
            except Exception as e:
                logging.error(f"Couldn’t create update lock file: {e}")

            if os.path.exists(DEST_DIR):
                logging.info("Deleting old dist folder...")
                shutil.rmtree(DEST_DIR, ignore_errors=True)

            logging.info("Copying new dist folder...")
            try:
                copytree_safe(SOURCE_DIR, DEST_DIR)
                with open(MOD_CACHE_FILE, 'w') as f:
                    f.write(modified_time)
            except Exception as e:
                logging.error(f"ERROR: Copy failed. {e}")
                wait_and_continue()

            else:
                datestamp = datetime.now().strftime('%m-%d-%Y-%H-%M')

                # Backup stats.db
                stats_backup_name = f"stats-{datestamp}.db"
                stats_backup_path = os.path.join(BACKUP_DIR, stats_backup_name)
                logging.info(f"Backing up stats.db to {stats_backup_path}")
                try:
                    shutil.copy2(STATS_FILE, stats_backup_path)
                except Exception as e:
                    logging.error(f"Failed to back up stats.db: {e}")

                user_backup_name = f"usercfg-{datestamp}.cfg"
                user_backup_path = os.path.join(BACKUP_DIR, user_backup_name)
                logging.info(f"Backing up user.cfg to {user_backup_path}")
                try:
                    shutil.copy2(USER_FILE, user_backup_path)
                except Exception as e:
                    logging.error(f"Failed to back up user.cfg: {e}")
                try:
                    os.remove(UPDATE_FILE_CHECK)
                    logging.info(f"Removed update‐lock file: {UPDATE_FILE_CHECK}")
                except Exception:
                    pass

        wait_and_continue()

def wait_and_continue():
    logging.info("Waiting 60 seconds before watching again...")
    time.sleep(60)

main_loop()
