# Apple Firmware Tracker
A tool for automatically monitoring URLs for Apple Product firmware.

## Feature
* Support iPhone / iPad / iPod / HomePod mini
* Automatic polling with a 15-minute interval
* Uses SQLite to store firmware information, including Device Code / URL / SHA1
* Automatically generates RSS feeds for easy subscription and download via Download Tools.
* Sends plain-text email summaries grouped by firmware version/build and device family, using product names instead of hardware identifiers.

## Email notifications

Automatic notifications list only the devices updated in the current check. The attached
`updates/YYYY-MM-DD_updates.txt` still contains all firmware URLs collected that day.
Each version/build has its own section under iPhone, iPad, iPod, or HomePod.
Each line represents one firmware URL, listing the deduplicated product names that share
that file (for example, iPhone 12 and iPhone 12 Pro). Different firmware files stay on
separate lines. iPad names omit connectivity, regional, and storage variants before being
deduplicated within each file; chip, screen size, and generation remain (for example,
`iPad mini (A17 Pro, Cellular)` and `iPad mini (A17 Pro, WiFi)` become `iPad mini A17 Pro`).
The subject includes the firmware versions. The email body is plain text only.

The workflow requires secrets `MAIL_SMTP_USERNAME`, `MAIL_SMTP_PASSWORD`, `MAIL_TO`,
and the repository variable `MAIL_FROM`. Use the `resend_attachment` workflow input
(e.g. `updates/2026-06-29_updates.txt`) to resend a historical attachment with a matching
summary. Email generation or delivery failures still allow firmware data to be committed,
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
`log/firmware_updates.json`, then the workflow invokes `firmware_email.py --mode auto`.
Generated notification files stay under the ignored `log/` directory.

Run the regression tests:

```bash
python3 -m unittest discover -s tests -v
```


## Structure 
* Script Structure
```
apple-firmware-tracker/
├── firmware_checker.py     # main script
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
