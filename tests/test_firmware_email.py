import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import firmware_checker as checker
from device import AppleDevice
from firmware_email import (
    DEVICE_NAMES, FIRMWARE_CATALOG, device_name, load_historical_devices,
    main as email_main, render_email,
)


class FirmwareEmailTests(unittest.TestCase):
    def setUp(self):
        self.names = {
            "iPhone9,1": "iPhone 7", "iPhone9,3": "iPhone 7",
            "iPad7,5": "iPad 6 (WiFi)",
        }

    def device(self, code="iPhone9,1", version="15.8.8", build="19H422", url="https://example.com/a.ipsw"):
        return {"hardware_code": code, "product_version": version,
                "build_version": build, "firmware_url": url}

    def render(self, devices, mode="auto"):
        return render_email(devices, self.names, "2026-06-29_updates.txt", mode)

    def test_groups_versions_builds_and_device_families(self):
        subject, text = self.render([
            self.device(), self.device("iPad7,5", "17.7.11", "21H461"),
            self.device("iPhone9,1", "15.8.8", "19H423"),
        ])
        self.assertIn("17.7.11 / 15.8.8", subject)
        self.assertIn("固件 15.8.8（Build 19H422）", text)
        self.assertIn("固件 15.8.8（Build 19H423）", text)
        self.assertIn("• iPad 6\n", text)
        self.assertNotIn("iPad7,5", text)
        self.assertIn("2 个版本", text)

    def test_shared_models_and_urls_are_counted_once(self):
        _, text = self.render([self.device(), self.device("iPhone9,3")])
        self.assertEqual(text.count("• iPhone 7"), 1)
        self.assertIn("1 个固件文件", text)
        self.assertNotIn("iPhone9,", text)

    def test_devices_sharing_a_file_are_on_one_line_in_both_modes(self):
        self.names.update({"iPhone13,2": "iPhone 12", "iPhone13,3": "iPhone 12 Pro",
                           "iPad11,1": "iPad mini 5", "iPad11,3": "iPad Air 3"})
        devices = [
            self.device("iPhone13,2", url="https://example.com/iphone.ipsw"),
            self.device("iPhone13,3", url="https://example.com/iphone.ipsw"),
            self.device("iPad11,1", url="https://example.com/ipad.ipsw"),
            self.device("iPad11,3", url="https://example.com/ipad.ipsw"),
        ]
        for mode in ("auto", "manual"):
            with self.subTest(mode=mode):
                _, text = self.render(devices, mode=mode)
                self.assertIn("• iPhone 12、iPhone 12 Pro\n", text)
                self.assertIn("• iPad Air 3、iPad mini 5\n", text)
                self.assertEqual(text.count("    • "), 2)
                self.assertIn("2 个固件文件", text)

    def test_same_model_with_different_files_stays_on_separate_lines(self):
        _, text = self.render([
            self.device(url="https://example.com/a.ipsw"),
            self.device("iPhone9,3", url="https://example.com/b.ipsw"),
        ])
        self.assertEqual(text.count("• iPhone 7\n"), 2)
        self.assertIn("2 个固件文件", text)

    def test_missing_urls_do_not_merge_unrelated_devices(self):
        _, text = self.render([
            self.device(url=None), self.device("iPhone9,3", url=None),
        ])
        self.assertEqual(text.count("• iPhone 7\n"), 2)

    def test_ipad_variants_are_simplified_and_deduplicated_per_file(self):
        self.names.update({"iPad16,1": "iPad mini (A17 Pro, WiFi)",
                           "iPad16,2": "iPad mini (A17 Pro, Cellular)"})
        for mode in ("auto", "manual"):
            with self.subTest(mode=mode):
                _, text = self.render([self.device("iPad16,1"), self.device("iPad16,2")], mode)
                self.assertEqual(text.count("iPad mini A17 Pro"), 1)
                self.assertIn("• iPad mini A17 Pro\n", text)
                self.assertNotIn("WiFi", text)
                self.assertNotIn("Cellular", text)
                _, separate = self.render([
                    self.device("iPad16,1", url="https://example.com/a.ipsw"),
                    self.device("iPad16,2", url="https://example.com/b.ipsw"),
                ], mode)
                self.assertEqual(separate.count("• iPad mini A17 Pro\n"), 2)

    def test_ipad_model_details_are_preserved(self):
        cases = {
            "iPad Pro (M4, 11-inch, Cellular)": "iPad Pro M4 11-inch",
            "iPad Pro (M4, 13-inch, WiFi)": "iPad Pro M4 13-inch",
            "iPad Pro (11-inch, WiFi) (3rd generation)": "iPad Pro 11-inch 3rd generation",
            "iPad Pro (11-inch, Cellular) (4th generation)": "iPad Pro 11-inch 4th generation",
            "iPad Pro 3 (11-inch, WiFi, 1TB Model)": "iPad Pro 3 11-inch",
            "iPad Pro 3 (11-inch, Cellular)": "iPad Pro 3 11-inch",
            "iPad (Cellular, 9th generation)": "iPad 9th generation",
            "iPad Air (China)": "iPad Air",
            "iPad mini (GSM)": "iPad mini",
            "iPad 2 (Mid 2012)": "iPad 2 Mid 2012",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(device_name("iPadTest,1", {"iPadTest,1": name}), expected)
        self.assertEqual(device_name("iPhone12,8", {"iPhone12,8": "iPhone SE (2020)"}),
                         "iPhone SE (2020)")

    def test_non_ipad_carrier_and_region_variants_are_dropped(self):
        cases = {
            "iPhone 7 (GSM)": "iPhone 7", "iPhone 7 (Global)": "iPhone 7",
            "iPhone XS Max (China)": "iPhone XS Max", "iPhone 4 (CDMA)": "iPhone 4",
            "iPhone 18 Pro Max (U.S.)": "iPhone 18 Pro Max",
            "iPhone 4 (GSM / 2012)": "iPhone 4 (GSM / 2012)",
            "iPhone SE (3rd generation)": "iPhone SE (3rd generation)",
            "HomePod (2nd generation)": "HomePod (2nd generation)",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(device_name("iPhoneTest,1", {"iPhoneTest,1": name}), expected)
        self.names.update({"iPhone9,1": "iPhone 7 (Global)", "iPhone9,3": "iPhone 7 (GSM)"})
        _, text = self.render([self.device(), self.device("iPhone9,3")])
        self.assertIn("• iPhone 7\n", text)
        self.assertNotIn("GSM", text)

    def test_cli_generates_only_plain_text_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            attachment = root / "sample_updates.txt"
            attachment.write_text("https://example.com/a.ipsw\n", encoding="utf-8")
            snapshot = root / "updates.json"
            snapshot.write_text(json.dumps([self.device()]), encoding="utf-8")
            output = root / "email"
            with patch("sys.argv", ["firmware_email.py", "--attachment", str(attachment),
                                    "--updates", str(snapshot), "--output-dir", str(output)]):
                email_main()
            self.assertEqual({path.name for path in output.iterdir()}, {"subject.txt", "body.txt"})
            text = (output / "body.txt").read_text(encoding="utf-8")
            self.assertIn("固件 15.8.8", text)
            self.assertNotIn("<html", text)

    def test_unknown_names_are_explicit_without_exposing_codes(self):
        with self.assertLogs(level="WARNING"):
            _, text = self.render([self.device("iPhone99,1")])
        self.assertIn("名称待补充", text)
        self.assertNotIn("iPhone99,1", text)

    def test_empty_updates_do_not_produce_notification(self):
        with self.assertRaises(ValueError):
            self.render([])

    def test_manual_email_is_labeled_as_historical(self):
        _, text = self.render([self.device()], mode="manual")
        self.assertIn("历史更新补发", text)
        self.assertNotIn("本次检测", text)

    def test_historical_versions_and_ambiguous_shared_filenames(self):
        with tempfile.TemporaryDirectory() as temp:
            attachment = Path(temp) / "old_updates.txt"
            attachment.write_text(
                "https://example.com/iPhone9,1_15.8.4_19H390_Restore.ipsw\n"
                "https://example.com/iPad_64bit_TouchID_ASTC_17.7.10_21H450_Restore.ipsw\n"
                "https://example.com/iPad_64bit_TouchID_ASTC_16.7.12_20H364_Restore.ipsw\n",
                encoding="utf-8")
            devices = load_historical_devices(attachment)
        self.assertEqual({d["hardware_code"] for d in devices if d["product_version"] == "17.7.10"},
                         {"iPad7,5", "iPad7,6"})
        self.assertEqual({d["hardware_code"] for d in devices if d["product_version"] == "16.7.12"},
                         {"iPad6,11", "iPad6,12"})
        self.assertEqual(next(d["product_version"] for d in devices if d["hardware_code"] == "iPhone9,1"),
                         "15.8.4")

    def test_all_repository_attachments_have_real_device_names(self):
        names = json.loads(DEVICE_NAMES.read_text(encoding="utf-8"))
        for attachment in sorted(FIRMWARE_CATALOG.parent.glob("updates/*_updates.txt")):
            with self.subTest(attachment=attachment.name):
                devices = load_historical_devices(attachment)
                self.assertTrue(devices)
                self.assertFalse({d["hardware_code"] for d in devices} - names.keys())

    def test_checker_snapshot_contains_only_current_run_and_clears_on_no_updates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = root / "firmware_updates.json"
            attachment_file = root / "log" / "update_attachment.txt"
            first = AppleDevice(hardware_code="iPhone9,1", build_version="19H421", firmware_sha1="old",
                                firmware_url="https://example.com/old.ipsw", product_version="15.8.7")
            second = AppleDevice(hardware_code="iPad7,5", build_version="21H461", firmware_sha1="new",
                                 firmware_url="https://example.com/new.ipsw", product_version="17.7.11")
            settings = {"DB_FILE": str(root / "firmware.db"), "LOG_DIR": str(root / "log"),
                        "UPDATES_DIR": str(root / "updates"), "RSS_FILE": str(root / "rss.xml"),
                        "EMAIL_UPDATES_FILE": str(snapshot),
                        "UPDATE_ATTACHMENT_FILE": str(attachment_file)}
            with patch.multiple(checker, **settings), \
                    patch.object(checker, "fetch_and_parse_plist", return_value={"valid": True}), \
                    patch.object(checker, "extract_firmware_info") as extract, \
                    patch.object(checker.logging, "FileHandler"), \
                    patch.object(checker.logging, "basicConfig"), \
                    contextlib.redirect_stdout(io.StringIO()):
                extract.return_value = [first]
                checker.main()
                extract.return_value = [first, second]
                checker.main()
                devices = json.loads(snapshot.read_text(encoding="utf-8"))
                self.assertEqual([d["hardware_code"] for d in devices], ["iPad7,5"])
                attachment = next((root / "updates").glob("*_updates.txt"))
                self.assertEqual(attachment_file.read_text(encoding="utf-8"), str(attachment))
                self.assertEqual(len(attachment.read_text().splitlines()), 2)
                self.assertEqual(checker.main(), 0)
                self.assertFalse(snapshot.exists())
                self.assertFalse(attachment_file.exists())


if __name__ == "__main__":
    unittest.main()
