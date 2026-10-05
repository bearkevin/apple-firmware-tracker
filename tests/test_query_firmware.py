import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import firmware_checker as checker
import query_firmware as query


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "firmware.db")
        checker.init_db(self.db)
        with sqlite3.connect(self.db) as conn:
            conn.executemany("INSERT INTO firmware VALUES (?, ?, ?, ?, ?, ?)", [
                ("iPad7,5", "18.2", "22C150", "a", "https://example.com/a.ipsw", None),
                ("iPad8,1", "27.0.1", "24A446", "b", "https://example.com/b.ipsw", None),
                ("iPhone10,3", "18.2", "22C150", "c", "https://example.com/c.ipsw", None),
                ("iPhone11,2", None, None, "d", "https://example.com/d.ipsw", None),
            ])
        conn.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_filters_combine_and_limit_applies(self):
        conn = query.connect_db(self.db)
        try:
            codes = lambda **kw: [d["hardware_code"] for d in query.search_devices(conn, **kw)]
            self.assertEqual(codes(device_code="iPad", version="18.2"), ["iPad7,5"])
            self.assertEqual(codes(version="18.2", build="22C150"), ["iPad7,5", "iPhone10,3"])
            self.assertEqual(codes(limit=2), ["iPad7,5", "iPad8,1"])
        finally:
            conn.close()

    def test_missing_values_print_without_errors(self):
        conn = query.connect_db(self.db)
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                query.print_devices(query.search_devices(conn), "table")
                query.print_statistics(query.get_statistics(conn))
        finally:
            conn.close()
        self.assertIn("iPhone11,2", out.getvalue())
        self.assertIn("unknown", out.getvalue())

    def test_missing_database_is_not_created(self):
        missing = Path(self.temp.name) / "missing.db"
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            query.connect_db(str(missing))
        self.assertFalse(missing.exists())

    def test_connection_is_read_only(self):
        conn = query.connect_db(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM firmware")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
