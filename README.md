# Apple Firmware Checker
A tool for automatically monitoring URLs for Apple Product firmware.

## Feature
* Support iPhone / iPad / iPod / HomePod mini
* Automatic polling with a 15-minute interval
* Uses SQLite to store firmware information, including Device Code / URL / SHA1
* Automatically generates RSS feeds for easy subscription and download via Download Tools.
* Query tool for searching and exporting firmware data from the database

## Structure 
* Script Structure
```
apple_firmware_checker/
├── firmware_checker.py     # main script
├── query_firmware.py       # query tool for database
├── device.py               # custom class
├── requirements.txt        
├── firmware.db            # SQLite database
├── firmware_rss.xml       # RSS feed
├── *.txt                  # URL history
└── .github/workflows/     # GitHub Actions
    ├── firmware_check.yml
    └── purge_jsdelivr_cache.yml
```

* Database Structure
```
CREATE TABLE firmware (
    hardware_code TEXT PRIMARY KEY,
    product_version TEXT,
    build_version TEXT,
    firmware_sha1 TEXT,
    firmware_url TEXT,
    last_checked TIMESTAMP
);
```

## Query Database

Use `query_firmware.py` to search and export firmware data:

### Basic Usage

```bash
# Show database statistics
python query_firmware.py --stats

# List all devices
python query_firmware.py --all

# List first 10 devices
python query_firmware.py --all --limit 10

# Search by device code (supports partial match)
python query_firmware.py --device iPhone14
python query_firmware.py --device iPad

# Search by product version
python query_firmware.py --version 26.2.1

# Search by build version
python query_firmware.py --build 23C71
```

### Export Options

```bash
# Export to JSON
python query_firmware.py --all --format json

# Export to CSV
python query_firmware.py --device iPad --format csv > ipads.csv

# Export specific version to JSON
python query_firmware.py --version 26.2.1 --format json > firmware_26.2.1.json
```

### Query Options

- `--all`: List all devices
- `--device <code>`: Search by device code (supports partial match)
- `--version <version>`: Search by product version
- `--build <build>`: Search by build version
- `--stats`: Show database statistics
- `--format <table|json|csv>`: Output format (default: table)
- `--limit <n>`: Limit number of results (only for --all)
- `--db <path>`: Database file path (default: firmware.db)
