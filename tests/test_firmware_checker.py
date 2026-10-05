import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import firmware_checker as checker
from device import AppleDevice


def device(code, url, sha1="sha", version="27.0.1", build="24A446"):
    return AppleDevice(hardware_code=code, build_version=build, firmware_sha1=sha1,
                       firmware_url=url, product_version=version)


def restore(url="https://example.com/a.ipsw", sha1="sha"):
    return {"Unknown": {"Universal": {"Restore": {
        "BuildVersion": "24A446", "ProductVersion": "27.0.1",
        "FirmwareURL": url, "FirmwareSHA1": sha1}}}}


class FakeClock:
    """Patches checker.datetime.now so each RSS run gets a distinct pubDate."""

    def __init__(self, *moments):
        self.moments = list(moments)

    def now(self, tz=None):
        moment = self.moments[0]
        return moment.astimezone(tz) if tz else moment


class ExtractTests(unittest.TestCase):
    def test_uses_latest_numeric_version_node_and_skips_unusable_entries(self):
        plist = {"MobileDeviceSoftwareVersionsByVersion": {
            "9": {"MobileDeviceSoftwareVersions": {"iPhone9,1": restore("https://example.com/old.ipsw")}},
            "10": {"MobileDeviceSoftwareVersions": {
                "iPhone9,1": restore(),
                "AppleTV5,3": restore(),
                "iPhone1,1": restore(url=None, sha1=None),
                "iPad7,5": {"Unknown": {}},
                "iPod1,1": "not a dict",
            }},
            "note": {},
        }}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(checker, "LOG_DIR", temp), \
                patch.object(checker, "SKIPPED_LOG", str(Path(temp) / "skipped.log")):
            devices = checker.extract_firmware_info(plist)
            skipped = (Path(temp) / "skipped.log").read_text()
        self.assertEqual([(d.hardware_code, d.firmware_url) for d in devices],
                         [("iPhone9,1", "https://example.com/a.ipsw")])
        self.assertIn("iPad7,5", skipped)
        self.assertIn("iPod1,1", skipped)
        self.assertNotIn("iPhone1,1", skipped)

    def test_non_dict_restore_node_is_skipped(self):
        plist = {"MobileDeviceSoftwareVersionsByVersion": {"1": {"MobileDeviceSoftwareVersions": {
            "iPhone9,1": {"Unknown": {"Universal": {"Restore": "unexpected"}}}}}}}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(checker, "LOG_DIR", temp), \
                patch.object(checker, "SKIPPED_LOG", str(Path(temp) / "skipped.log")):
            self.assertEqual(checker.extract_firmware_info(plist), [])
            self.assertIn("iPhone9,1", (Path(temp) / "skipped.log").read_text())
        self.assertEqual(checker.extract_firmware_info(["not", "a", "dict"]), [])

    def test_missing_version_nodes_return_nothing(self):
        self.assertEqual(checker.extract_firmware_info({}), [])
        self.assertEqual(checker.extract_firmware_info(
            {"MobileDeviceSoftwareVersionsByVersion": {"x": {}}}), [])


class RssTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.rss = str(Path(temp.name) / "rss.xml")

    def run_batch(self, hour, devices):
        clock = FakeClock(datetime(2026, 9, 1, hour, tzinfo=timezone.utc))
        with patch.object(checker, "datetime", clock):
            checker.update_rss_feed(self.rss, devices)

    def items(self):
        return ET.parse(self.rss).getroot().find("channel").findall("item")

    def test_keeps_the_three_most_recent_runs_without_item_cap(self):
        for hour in range(1, 5):
            self.run_batch(hour, [device(f"iPhone{hour},{n}", f"https://example.com/{hour}-{n}.ipsw")
                                  for n in range(60)])
        items = self.items()
        self.assertEqual(len(items), 180)
        self.assertEqual({item.findtext("guid").split("/")[-1].split("-")[0] for item in items},
                         {"2", "3", "4"})
        # Newest run first.
        self.assertTrue(items[0].findtext("guid").endswith("/4-0.ipsw"))
        self.assertTrue(items[-1].findtext("guid").startswith("https://example.com/2-"))

    def test_existing_items_are_kept_verbatim_and_known_urls_not_duplicated(self):
        self.run_batch(1, [device("iPad16,1", "https://example.com/shared.ipsw"),
                           device("iPad16,2", "https://example.com/shared.ipsw")])
        before = ET.tostring(self.items()[0])
        self.run_batch(2, [device("iPad16,3", "https://example.com/shared.ipsw", sha1="new"),
                           device("iPhone9,1", "https://example.com/new.ipsw"),
                           device("iPhone1,1", None)])
        items = self.items()
        self.assertEqual([item.findtext("guid") for item in items],
                         ["https://example.com/new.ipsw", "https://example.com/shared.ipsw"])
        self.assertEqual(ET.tostring(items[1]), before)
        self.assertEqual(items[1].findtext("title"), "iPad16,1, iPad16,2 - 27.0.1 (24A446)")
        channel = ET.parse(self.rss).getroot().find("channel")
        self.assertEqual(channel.findtext("lastBuildDate"), items[0].findtext("pubDate"))

    def test_run_with_only_known_urls_does_not_count_as_a_batch(self):
        for hour in (1, 2, 3):
            self.run_batch(hour, [device("iPhone9,1", f"https://example.com/{hour}.ipsw")])
        self.run_batch(4, [device("iPhone9,2", "https://example.com/3.ipsw")])
        self.assertEqual(len(self.items()), 3)


class StorageTests(unittest.TestCase):
    def test_daily_urls_use_beijing_date_and_ignore_blank_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            # 2026-09-28 22:26 UTC is already 2026-09-29 in Beijing.
            clock = FakeClock(datetime(2026, 9, 28, 22, 26, tzinfo=timezone.utc))
            existing = Path(temp) / "2026-09-29_updates.txt"
            existing.write_text("https://example.com/b.ipsw\n\n", encoding="utf-8")
            with patch.object(checker, "datetime", clock):
                path = checker.update_daily_urls(temp, [device("iPhone9,1", "https://example.com/a.ipsw"),
                                                        device("iPhone1,1", None)])
            self.assertEqual(Path(path), existing)
            self.assertEqual(existing.read_text(encoding="utf-8"),
                             "https://example.com/a.ipsw\nhttps://example.com/b.ipsw\n")

    def test_save_firmware_writes_latest_and_history_together(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / "firmware.db")
            checker.init_db(db)
            old = device("iPhone9,1", "https://example.com/old.ipsw", sha1="old")
            new = device("iPhone9,1", "https://example.com/new.ipsw", sha1="new")
            other = device("iPad7,5", "https://example.com/ipad.ipsw")
            checker.save_firmware(db, [old, other], [old, other])
            checker.save_firmware(db, [new, other], [new])
            self.assertEqual(checker.get_existing_firmware(db), {"iPhone9,1": "new", "iPad7,5": "sha"})
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM firmware_history").fetchone()[0], 3)
                checked = conn.execute("SELECT last_checked FROM firmware LIMIT 1").fetchone()[0]
            self.assertTrue(checked.endswith("+00:00"))


class FetchTests(unittest.TestCase):
    def test_malformed_plist_returns_none(self):
        for content in (b"garbage", b"<?xml version='1.0'?><plist><dict><key>a</key>"):
            response = type("Response", (), {"content": content, "raise_for_status": lambda self: None})()
            with patch.object(checker.requests.Session, "get", return_value=response), \
                    self.assertLogs(level="ERROR"):
                self.assertIsNone(checker.fetch_and_parse_plist("https://example.com/version"))


class MainTests(unittest.TestCase):
    def test_fetch_or_parse_failure_returns_error_exit_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = {"DB_FILE": str(root / "firmware.db"), "LOG_DIR": str(root / "log"),
                        "EMAIL_UPDATES_FILE": str(root / "log" / "updates.json"),
                        "UPDATE_ATTACHMENT_FILE": str(root / "log" / "attachment.txt")}
            with patch.multiple(checker, **settings), \
                    patch.object(checker.logging, "FileHandler"), \
                    patch.object(checker.logging, "basicConfig"), \
                    self.assertLogs(level="ERROR"):
                with patch.object(checker, "fetch_and_parse_plist", return_value=None):
                    self.assertEqual(checker.main(), 1)
                with patch.object(checker, "fetch_and_parse_plist", return_value={"x": 1}):
                    self.assertEqual(checker.main(), 1)


if __name__ == "__main__":
    unittest.main()
