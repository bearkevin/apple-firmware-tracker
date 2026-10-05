# Apple Firmware Tracker
A tool for automatically monitoring URLs for Apple Product firmware.

## Feature
* Support iPhone / iPad / iPod / HomePod mini
* Automatic polling every 15 minutes, Tuesday–Saturday 00:37–07:52 Beijing time
  (UTC Monday–Friday 16:37–23:52), when Apple usually publishes firmware. Minutes are
  offset from :00/:15/:30/:45, where GitHub most often delays or drops scheduled runs
* Uses SQLite to store firmware information, including Device Code / URL / SHA1
* Automatically generates RSS feeds for easy subscription and download via Download Tools.
  The feed keeps the firmware files from the three most recent update runs (no item cap),
  one item per firmware file. Existing items keep their original `guid`/`link`/`pubDate`,
  so readers that poll less often (e.g. Synology Download Station, daily by default)
  don't miss a run or re-download old files.
* Sends plain-text email summaries grouped by firmware version/build and device family, using product names instead of hardware identifiers.
* Query tool for searching and exporting firmware data from the database

## Email notifications

Automatic notifications list only the devices updated in the current check. The attached
`updates/YYYY-MM-DD_updates.txt` (named by Beijing date) still contains all firmware URLs
collected that day.
Each version/build has its own section under iPhone, iPad, iPod, or HomePod.
Each line represents one firmware URL, listing the deduplicated product names that share
that file (for example, iPhone 12 and iPhone 12 Pro). Different firmware files stay on
separate lines. iPad names omit connectivity, regional, and storage variants before being
deduplicated within each file; chip, screen size, and generation remain (for example,
`iPad mini (A17 Pro, Cellular)` and `iPad mini (A17 Pro, WiFi)` become `iPad mini A17 Pro`).
Other devices drop carrier/region-only parentheses (`iPhone 7 (GSM)` and `iPhone 7 (Global)`
become `iPhone 7`), while years and generations such as `iPhone SE (2020)` are kept.
The subject includes the firmware versions. The email body is plain text only.

The workflow requires secrets `MAIL_SMTP_USERNAME`, `MAIL_SMTP_PASSWORD`, `MAIL_TO`,
and the repository variable `MAIL_FROM`. `MAIL_TO` is the primary (visible) recipient.
Put any other recipients in the optional secret `MAIL_BCC` as a comma-separated list
(e.g. `a@example.com,b@example.com`); they receive the email as blind copies and cannot
see each other's addresses. Leave `MAIL_BCC` unset to send to `MAIL_TO` only.
Use the `resend_attachment` workflow input (e.g. `updates/2026-06-29_updates.txt`) to resend
a historical attachment with a matching summary. Email generation or delivery failures still allow firmware data to be committed,
then mark the workflow as failed.

`device_names.json` contains an offline identifier-to-product-name catalog sourced from
<https://api.ipsw.me/v4/devices>. Update it when adding new devices. Unknown names are
shown as “名称待补充” and logged, rather than presented as device codes.
`设备名称_固件名称.csv` supplies shared firmware filename aliases for historical attachments;
keep both mapping files in the repository. Historical versions/builds come from the selected
attachment, not the current database. Ambiguous or unknown historical aliases are logged
and displayed as unresolved names.

Generate a local preview without sending email:

```bash
python3 firmware_email.py --mode manual --attachment updates/2026-06-29_updates.txt
# Open log/email/body.txt; the subject is in log/email/subject.txt.
```

For automatic notifications, `firmware_checker.py` writes this run's changed devices to
`log/firmware_updates.json` and the attachment path to `log/update_attachment.txt`; the
workflow treats their presence as "updates found", commits the data, then invokes
`firmware_email.py --mode auto` and sends the email. Generated notification files stay under
the ignored `log/` directory. If the firmware list cannot be fetched or parsed, the checker
exits non-zero so the workflow run shows as failed.

Run the regression tests:

```bash
python3 -m unittest discover -s tests -v
```


## Structure 
* Script Structure
```
apple-firmware-tracker/
├── firmware_checker.py     # main script: fetch, compare, store, RSS
├── firmware_email.py       # builds the plain-text notification email
├── query_firmware.py       # query tool for database
├── device.py               # custom class
├── device_names.json       # identifier -> product name catalog
├── 设备名称_固件名称.csv     # shared firmware filename aliases (historical resend)
├── requirements.txt
├── firmware.db             # SQLite database
├── firmware_rss.xml        # RSS feed
├── updates/                # daily URL history (YYYY-MM-DD_updates.txt)
├── tests/                  # unit tests
└── .github/workflows/      # GitHub Actions
    └── firmware_check.yml
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

CREATE TABLE firmware_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hardware_code TEXT NOT NULL,
    product_version TEXT,
    build_version TEXT,
    firmware_sha1 TEXT,
    firmware_url TEXT,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
