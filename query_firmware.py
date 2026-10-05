#!/usr/bin/env python3
"""
Query tool for firmware.db
Allows users to query and export firmware data from the SQLite database.
"""
import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional

DB_FILE = "firmware.db"


def connect_db(db_path: str = DB_FILE) -> sqlite3.Connection:
    """Open the firmware database read-only, so a mistyped path doesn't create an empty file."""
    if not Path(db_path).is_file():
        print(f"Database file not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    try:
        conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"Database connection error: {e}", file=sys.stderr)
        sys.exit(1)


def search_devices(conn: sqlite3.Connection, device_code: Optional[str] = None,
                   version: Optional[str] = None, build: Optional[str] = None,
                   limit: Optional[int] = None) -> List[Dict]:
    """Return devices matching every given filter; device_code is a partial match."""
    clauses, params = [], []
    if device_code:
        clauses.append("hardware_code LIKE ?")
        params.append(f"%{device_code}%")
    if version:
        clauses.append("product_version = ?")
        params.append(version)
    if build:
        clauses.append("build_version = ?")
        params.append(build)
    query = "SELECT * FROM firmware"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY hardware_code"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    return [dict(row) for row in conn.execute(query, params)]


def get_statistics(conn: sqlite3.Connection) -> Dict:
    """Get database statistics."""
    cursor = conn.cursor()
    
    # Total count
    cursor.execute("SELECT COUNT(*) as count FROM firmware")
    total = cursor.fetchone()["count"]
    
    # Count by product version
    cursor.execute("""
        SELECT product_version, COUNT(*) as count 
        FROM firmware 
        GROUP BY product_version 
        ORDER BY product_version DESC
    """)
    by_version = {row["product_version"]: row["count"] for row in cursor.fetchall()}
    
    # Device types (iPhone, iPad, iPod, etc.)
    cursor.execute("""
        SELECT 
            CASE 
                WHEN hardware_code LIKE 'iPhone%' THEN 'iPhone'
                WHEN hardware_code LIKE 'iPad%' THEN 'iPad'
                WHEN hardware_code LIKE 'iPod%' THEN 'iPod'
                WHEN hardware_code LIKE 'AudioAccessory%' THEN 'HomePod'
                ELSE 'Other'
            END as device_type,
            COUNT(*) as count
        FROM firmware
        GROUP BY device_type
        ORDER BY count DESC
    """)
    by_type = {row["device_type"]: row["count"] for row in cursor.fetchall()}
    
    return {
        "total_devices": total,
        "by_product_version": by_version,
        "by_device_type": by_type
    }


def print_devices(devices: List[Dict], output_format: str = "table"):
    """Print devices in the specified format."""
    if not devices:
        print("No devices found.")
        return
    
    if output_format == "json":
        print(json.dumps(devices, indent=2, ensure_ascii=False))
    elif output_format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=devices[0].keys())
        writer.writeheader()
        writer.writerows(devices)
    else:  # table format
        print(f"\nFound {len(devices)} device(s):\n")
        print("-" * 80)
        print(f"{'Hardware Code':<25} {'Version':<12} {'Build':<15} {'Last Checked':<26}")
        print("-" * 80)
        for device in devices:
            print(
                f"{device['hardware_code']:<25} "
                f"{device['product_version'] or '-':<12} "
                f"{device['build_version'] or '-':<15} "
                f"{device['last_checked'] or '-':<26}"
            )
        print("-" * 80)
        print(f"Total: {len(devices)} device(s)\n")


def print_statistics(stats: Dict):
    """Print database statistics."""
    print("\n=== Database Statistics ===\n")
    print(f"Total devices: {stats['total_devices']}\n")
    
    print("By device type:")
    for device_type, count in stats['by_device_type'].items():
        print(f"  {device_type:<12}: {count:>4} devices")
    
    print("\nBy product version (top 10):")
    # Compare versions numerically so 27.0.1 ranks above 9.3.6.
    for version, count in sorted(
        stats['by_product_version'].items(),
        key=lambda x: [int(p) if p.isdigit() else 0 for p in (x[0] or "").split(".")],
        reverse=True
    )[:10]:
        print(f"  {version or 'unknown':<12}: {count:>4} devices")
    
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Query firmware data from firmware.db",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all devices
  python query_firmware.py --all
  
  # Search by device code (partial match)
  python query_firmware.py --device iPhone14
  
  # Search by product version
  python query_firmware.py --version 18.2
  
  # Search by build version
  python query_firmware.py --build 22C150

  # Combine filters (all must match)
  python query_firmware.py --device iPad --version 18.2
  
  # Show database statistics
  python query_firmware.py --stats
  
  # Export to JSON
  python query_firmware.py --all --format json
  
  # Export to CSV
  python query_firmware.py --device iPad --format csv > ipads.csv
        """
    )
    
    parser.add_argument(
        "--all", 
        action="store_true", 
        help="List all devices"
    )
    parser.add_argument(
        "--device", 
        type=str, 
        help="Search by device code (supports partial match, e.g., 'iPhone14')"
    )
    parser.add_argument(
        "--version", 
        type=str, 
        help="Search by product version (e.g., '18.2')"
    )
    parser.add_argument(
        "--build", 
        type=str, 
        help="Search by build version (e.g., '22C150')"
    )
    parser.add_argument(
        "--stats", 
        action="store_true", 
        help="Show database statistics"
    )
    parser.add_argument(
        "--format", 
        choices=["table", "json", "csv"], 
        default="table",
        help="Output format (default: table)"
    )
    parser.add_argument(
        "--limit", 
        type=int, 
        help="Limit number of results"
    )
    parser.add_argument(
        "--db", 
        type=str, 
        default=DB_FILE,
        help=f"Database file path (default: {DB_FILE})"
    )
    
    args = parser.parse_args()
    
    # Check if at least one action is specified
    if not (args.all or args.device or args.version or args.build or args.stats):
        parser.print_help()
        sys.exit(1)
    
    conn = connect_db(args.db)
    
    try:
        if args.stats:
            print_statistics(get_statistics(conn))
        else:
            devices = search_devices(conn, args.device, args.version, args.build, args.limit)
            print_devices(devices, args.format)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
