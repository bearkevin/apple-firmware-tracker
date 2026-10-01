import json
import logging
import os
from dataclasses import asdict
from typing import Optional
import plistlib
import requests
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from device import AppleDevice

# --- Constants ---
PLIST_URL = "https://s.mzstatic.com/version"
DB_FILE = "firmware.db"
RSS_FILE = "firmware_rss.xml"
LOG_DIR = "log"
UPDATES_DIR = "updates"
SKIPPED_LOG = os.path.join(LOG_DIR, "skipped_devices.log")
EMAIL_UPDATES_FILE = os.path.join(LOG_DIR, "firmware_updates.json")


def fetch_and_parse_plist(url: str) -> Optional[dict]:
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return plistlib.loads(response.content)
    except requests.RequestException as e:
        logging.error(f"Error fetching data: {e}")
        return None
    except plistlib.InvalidFileException as e:
        logging.error(f"Error parsing plist data: {e}")
        return None

def find_latest_version_node(data: dict) -> Optional[dict]:
    numeric_keys = [int(k) for k in data.keys() if k.isdigit()]
    if not numeric_keys:
        return None
    return data.get(str(max(numeric_keys)))

def extract_firmware_info(full_data: dict) -> list[AppleDevice]:
    devices = []
    by_version_node = full_data.get("MobileDeviceSoftwareVersionsByVersion")
    if not by_version_node:
        return []
    latest_version_node = find_latest_version_node(by_version_node)
    if not latest_version_node:
        return []
    versions = latest_version_node.get("MobileDeviceSoftwareVersions")
    if not versions:
        return []

    for code, info in versions.items():
        if code.startswith("AppleTV"):
            continue
        try:
            restore_info = info["Unknown"]["Universal"]["Restore"]
            devices.append(AppleDevice(
                hardware_code=code,
                build_version=restore_info.get("BuildVersion"),
                firmware_sha1=restore_info.get("FirmwareSHA1"),
                firmware_url=restore_info.get("FirmwareURL"),
                product_version=restore_info.get("ProductVersion"),
            ))
        except KeyError:
            logging.debug("Skipped %s: unexpected plist structure", code)
            _append_skipped_log(code)
    return devices


def _append_skipped_log(code: str):
    """Append a skipped-device entry to log/skipped_devices.log."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(SKIPPED_LOG, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} - Skipped {code}: unexpected plist structure\n")


# --- Database & RSS Functions ---

def init_db(db_path: str):
    """Initializes the database and creates the firmware table if it doesn't exist."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS firmware (
                hardware_code TEXT PRIMARY KEY,
                product_version TEXT,
                build_version TEXT,
                firmware_sha1 TEXT,
                firmware_url TEXT,
                last_checked TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS firmware_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hardware_code TEXT NOT NULL,
                product_version TEXT,
                build_version TEXT,
                firmware_sha1 TEXT,
                firmware_url TEXT,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Clean up any old AppleTV entries on initialization
        cursor.execute("DELETE FROM firmware WHERE hardware_code LIKE 'AppleTV%'")
        conn.commit()

def get_existing_firmware(db_path: str) -> dict[str, str]:
    """Gets a dictionary of existing firmware SHA1s from the database."""
    existing = {}
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT hardware_code, firmware_sha1 FROM firmware")
            for row in cursor.fetchall():
                existing[row["hardware_code"]] = row["firmware_sha1"]
    except sqlite3.OperationalError:
        pass
    return existing

def update_database(db_path: str, devices: list[AppleDevice]):
    """Inserts or replaces device firmware information in the database."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        for device in devices:
            cursor.execute('''
                INSERT OR REPLACE INTO firmware (
                    hardware_code, product_version, build_version, 
                    firmware_sha1, firmware_url, last_checked
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                device.hardware_code, device.product_version, device.build_version,
                device.firmware_sha1, device.firmware_url, datetime.now()
            ))
        conn.commit()

def record_firmware_history(db_path: str, updated_devices: list[AppleDevice]):
    """Inserts a history record for each device whose firmware just changed."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        for device in updated_devices:
            cursor.execute('''
                INSERT INTO firmware_history
                    (hardware_code, product_version, build_version, firmware_sha1, firmware_url)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                device.hardware_code, device.product_version, device.build_version,
                device.firmware_sha1, device.firmware_url
            ))
        conn.commit()
    logging.info("Recorded %d firmware history entries.", len(updated_devices))


def update_rss_feed(rss_path: str, updated_devices: list[AppleDevice]):
    """Creates or updates a local RSS feed file with the latest firmware."""
    logging.info(f"Updating RSS feed at {rss_path}...")
    try:
        tree = ET.parse(rss_path)
        channel = tree.find('channel')
    except (FileNotFoundError, ET.ParseError):
        root = ET.Element('rss', version='2.0')
        tree = ET.ElementTree(root)
        channel = ET.SubElement(root, 'channel')
        ET.SubElement(channel, 'title').text = 'Apple Firmware Updates'
        ET.SubElement(channel, 'link').text = 'https://www.apple.com'
        ET.SubElement(channel, 'description').text = 'Latest Apple firmware updates found by checker script.'

    # 清空现有的所有条目
    for item in channel.findall('item'):
        channel.remove(item)

    # 按固件 URL 分组：同一个 .ipsw 文件只生成一个条目，避免下载工具重复下载。
    # 跳过没有 URL 的设备（否则会写出空 link/guid）。
    groups: dict[str, list[AppleDevice]] = {}
    for device in updated_devices:
        if not device.firmware_url:
            continue
        groups.setdefault(device.firmware_url, []).append(device)

    pub_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    for url, devices in groups.items():
        first = devices[0]
        codes = ", ".join(d.hardware_code for d in devices)
        item = ET.Element('item')
        ET.SubElement(item, 'title').text = f'{codes} - {first.product_version} ({first.build_version})'
        ET.SubElement(item, 'link').text = url
        ET.SubElement(item, 'guid').text = url  # 每个文件一条，天然唯一
        ET.SubElement(item, 'pubDate').text = pub_date
        ET.SubElement(item, 'description').text = f"Build: {first.build_version}, SHA1: {first.firmware_sha1}"
        enclosure = ET.SubElement(item, 'enclosure')
        enclosure.set('url', url)
        enclosure.set('type', 'application/x-ipsw')
        enclosure.set('length', '0')
        channel.append(item)

    ET.indent(tree, space="  ", level=0)
    tree.write(rss_path, encoding='utf-8', xml_declaration=True)
    logging.info(f"RSS feed updated with {len(groups)} firmware file entries ({len(updated_devices)} devices).")

def main():
    """Main function to run a single firmware check and update local files."""
    os.makedirs(LOG_DIR, exist_ok=True)
    if os.path.exists(EMAIL_UPDATES_FILE):
        os.remove(EMAIL_UPDATES_FILE)

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(os.path.join(LOG_DIR, "firmware_checker.log")),
            logging.StreamHandler()
        ]
    )

    logging.info("Initializing firmware checker...")
    init_db(DB_FILE)
    
    logging.info("Running check...")
    local_firmware = get_existing_firmware(DB_FILE)
    plist_data = fetch_and_parse_plist(PLIST_URL)
    if not plist_data:
        logging.error("Fetch failed. Exiting.")
        return
    
    remote_devices = extract_firmware_info(plist_data)
    if not remote_devices:
        logging.error("Could not extract remote device info. Exiting.")
        return

    updated_devices = [d for d in remote_devices if d.firmware_sha1 != local_firmware.get(d.hardware_code)]
    
    if updated_devices:
        logging.info("--- !!! FIRMWARE UPDATES DETECTED !!! ---")
        logging.info(f"Found {len(updated_devices)} new or updated firmwares:")
        print("-"*50)
        for device in updated_devices:
            print(device)
            print()
        
        update_database(DB_FILE, remote_devices)
        logging.info("Database has been updated.")

        record_firmware_history(DB_FILE, updated_devices)

        os.makedirs(UPDATES_DIR, exist_ok=True)
        url_filename = os.path.join(UPDATES_DIR, f"{datetime.now().strftime('%Y-%m-%d')}_updates.txt")
        logging.info(f"Saving updated firmware URLs to {url_filename}...")
        new_urls = sorted({d.firmware_url for d in updated_devices if d.firmware_url})
        try:
            with open(url_filename, 'r') as f:
                existing_urls = [line.strip() for line in f.readlines()]
        except FileNotFoundError:
            existing_urls = []
        all_urls = sorted(list(set(existing_urls + new_urls)))
        with open(url_filename, 'w') as f:
            for url in all_urls:
                f.write(url + '\n')
        logging.info("URLs saved.")

        update_rss_feed(RSS_FILE, updated_devices)
        # Keep this run's devices separate from the cumulative daily attachment.
        with open(EMAIL_UPDATES_FILE, 'w', encoding='utf-8') as f:
            json.dump([asdict(device) for device in updated_devices], f, ensure_ascii=False)
    else:
        logging.info("No updates found.")

    logging.info("Check complete.")

if __name__ == "__main__":
    main()
