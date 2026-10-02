import json
import logging
import os
import sys
from contextlib import closing
from dataclasses import asdict
from email.utils import format_datetime, parsedate_to_datetime
from typing import Optional
import plistlib
import requests
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from device import AppleDevice

# --- Constants ---
PLIST_URL = "https://s.mzstatic.com/version"
DB_FILE = "firmware.db"
RSS_FILE = "firmware_rss.xml"
LOG_DIR = "log"
UPDATES_DIR = "updates"
SKIPPED_LOG = os.path.join(LOG_DIR, "skipped_devices.log")
EMAIL_UPDATES_FILE = os.path.join(LOG_DIR, "firmware_updates.json")
# Path of this run's daily attachment; the workflow reads it instead of guessing from git status.
UPDATE_ATTACHMENT_FILE = os.path.join(LOG_DIR, "update_attachment.txt")
# Daily attachments are named by Beijing date, matching the schedule in the workflow.
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
# Number of recent update runs kept in the RSS feed, so slow-polling readers don't miss one.
RSS_KEEP_BATCHES = 3


def fetch_and_parse_plist(url: str) -> Optional[dict]:
    retry = Retry(total=3, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",))
    try:
        with requests.Session() as session:
            session.mount("https://", HTTPAdapter(max_retries=retry))
            response = session.get(url, timeout=15)
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
        except (KeyError, TypeError):
            logging.debug("Skipped %s: unexpected plist structure", code)
            _append_skipped_log(code)
            continue
        if not restore_info.get("FirmwareURL") or not restore_info.get("FirmwareSHA1"):
            # Legacy entries (e.g. iPhone1,1) carry no downloadable firmware.
            logging.debug("Skipped %s: missing firmware URL or SHA1", code)
            continue
        devices.append(AppleDevice(
            hardware_code=code,
            build_version=restore_info.get("BuildVersion"),
            firmware_sha1=restore_info.get("FirmwareSHA1"),
            firmware_url=restore_info.get("FirmwareURL"),
            product_version=restore_info.get("ProductVersion"),
        ))
    return devices


def _append_skipped_log(code: str):
    """Append a skipped-device entry to log/skipped_devices.log."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(SKIPPED_LOG, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} - Skipped {code}: unexpected plist structure\n")


# --- Database & RSS Functions ---

def init_db(db_path: str):
    """Initializes the database and creates the firmware tables if they don't exist."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS firmware (
                hardware_code TEXT PRIMARY KEY,
                product_version TEXT,
                build_version TEXT,
                firmware_sha1 TEXT,
                firmware_url TEXT,
                last_checked TIMESTAMP
            )
        ''')
        conn.execute('''
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
        conn.execute("DELETE FROM firmware WHERE hardware_code LIKE 'AppleTV%'")

def get_existing_firmware(db_path: str) -> dict[str, str]:
    """Gets a dictionary of existing firmware SHA1s from the database."""
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            return dict(conn.execute("SELECT hardware_code, firmware_sha1 FROM firmware"))
    except sqlite3.OperationalError:
        return {}

def save_firmware(db_path: str, devices: list[AppleDevice], updated_devices: list[AppleDevice]):
    """Stores the latest firmware for all devices and history for changed ones in one transaction."""
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.executemany('''
            INSERT OR REPLACE INTO firmware (
                hardware_code, product_version, build_version,
                firmware_sha1, firmware_url, last_checked
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', [(d.hardware_code, d.product_version, d.build_version,
               d.firmware_sha1, d.firmware_url, checked_at) for d in devices])
        conn.executemany('''
            INSERT INTO firmware_history
                (hardware_code, product_version, build_version, firmware_sha1, firmware_url)
            VALUES (?, ?, ?, ?, ?)
        ''', [(d.hardware_code, d.product_version, d.build_version,
               d.firmware_sha1, d.firmware_url) for d in updated_devices])
    logging.info("Database updated; recorded %d firmware history entries.", len(updated_devices))


def update_daily_urls(updates_dir: str, updated_devices: list[AppleDevice]) -> str:
    """Merges this run's firmware URLs into today's cumulative attachment and returns its path."""
    os.makedirs(updates_dir, exist_ok=True)
    path = os.path.join(updates_dir, f"{datetime.now(LOCAL_TZ):%Y-%m-%d}_updates.txt")
    logging.info(f"Saving updated firmware URLs to {path}...")
    try:
        with open(path, encoding="utf-8") as f:
            urls = {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        urls = set()
    urls.update(d.firmware_url for d in updated_devices if d.firmware_url)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(url + "\n" for url in sorted(urls))
    return path


def _item_time(item: ET.Element) -> datetime:
    try:
        return parsedate_to_datetime(item.findtext("pubDate"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def update_rss_feed(rss_path: str, updated_devices: list[AppleDevice]):
    """Adds this run's firmware files to the RSS feed, keeping the last RSS_KEEP_BATCHES runs."""
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

    # Existing items are kept verbatim (guid/link/pubDate) so download tools don't see them as new.
    existing = channel.findall('item')
    for item in existing:
        channel.remove(item)
    known_urls = {item.findtext('guid') for item in existing}

    # 按固件 URL 分组：同一个 .ipsw 文件只生成一个条目，避免下载工具重复下载。
    # 跳过没有 URL 的设备（否则会写出空 link/guid）和已在 feed 中的文件。
    groups: dict[str, list[AppleDevice]] = {}
    for device in updated_devices:
        if device.firmware_url and device.firmware_url not in known_urls:
            groups.setdefault(device.firmware_url, []).append(device)

    pub_date = format_datetime(datetime.now(timezone.utc).replace(microsecond=0), usegmt=True)
    new_items = []
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
        new_items.append(item)

    # Each run shares one pubDate, so a batch is the set of items with the same pubDate.
    items = sorted(new_items + existing, key=_item_time, reverse=True)
    kept_batches = sorted({_item_time(item) for item in items}, reverse=True)[:RSS_KEEP_BATCHES]
    kept = [item for item in items if _item_time(item) in kept_batches]

    build_date = channel.find('lastBuildDate')
    if build_date is None:
        build_date = ET.SubElement(channel, 'lastBuildDate')
    build_date.text = pub_date
    channel.extend(kept)

    ET.indent(tree, space="  ", level=0)
    tree.write(rss_path, encoding='utf-8', xml_declaration=True)
    logging.info("RSS feed: %d new firmware file entries, %d entries kept from %d runs.",
                 len(new_items), len(kept), len(kept_batches))

def main() -> int:
    """Runs a single firmware check, updates local files, and returns the process exit code."""
    os.makedirs(LOG_DIR, exist_ok=True)
    for stale in (EMAIL_UPDATES_FILE, UPDATE_ATTACHMENT_FILE):
        if os.path.exists(stale):
            os.remove(stale)

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
        return 1

    remote_devices = extract_firmware_info(plist_data)
    if not remote_devices:
        logging.error("Could not extract remote device info. Exiting.")
        return 1

    updated_devices = [d for d in remote_devices if d.firmware_sha1 != local_firmware.get(d.hardware_code)]

    if updated_devices:
        logging.info("--- !!! FIRMWARE UPDATES DETECTED !!! ---")
        logging.info(f"Found {len(updated_devices)} new or updated firmwares:")
        for device in updated_devices:
            logging.info("%s %s (%s) %s", device.hardware_code, device.product_version,
                         device.build_version, device.firmware_url)

        save_firmware(DB_FILE, remote_devices, updated_devices)
        attachment = update_daily_urls(UPDATES_DIR, updated_devices)
        update_rss_feed(RSS_FILE, updated_devices)
        # Keep this run's devices separate from the cumulative daily attachment.
        with open(EMAIL_UPDATES_FILE, 'w', encoding='utf-8') as f:
            json.dump([asdict(device) for device in updated_devices], f, ensure_ascii=False)
        with open(UPDATE_ATTACHMENT_FILE, 'w', encoding='utf-8') as f:
            f.write(attachment)
    else:
        logging.info("No updates found.")

    logging.info("Check complete.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
