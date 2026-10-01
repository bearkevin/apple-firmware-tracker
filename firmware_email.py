"""Build plain-text firmware notifications without sending mail."""

import argparse
import csv
import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
DEVICE_NAMES = ROOT / "device_names.json"
FIRMWARE_CATALOG = ROOT / "设备名称_固件名称.csv"


def natural_key(value):
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", value)]


def firmware_parts(url):
    """Split from the right because firmware family names contain underscores."""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid firmware URL: {url}")
    filename = unquote(parsed.path.rsplit("/", 1)[-1])
    if not filename.endswith("_Restore.ipsw"):
        raise ValueError(f"Unexpected firmware filename: {filename}")
    family, version, build = filename.removesuffix("_Restore.ipsw").rsplit("_", 2)
    if not family or not re.fullmatch(r"\d+(?:\.\d+)*", version) or not build:
        raise ValueError(f"Invalid firmware metadata: {filename}")
    return family, version, build


def device_family(code):
    for prefix, label in (("iPhone", "iPhone"), ("iPad", "iPad"),
                          ("iPod", "iPod"), ("AudioAccessory", "HomePod")):
        if code.startswith(prefix):
            return label
    return "其他设备"


def simplify_ipad_name(name):
    """Keep model details while removing connectivity, region, and storage variants."""
    variants = {"wifi", "wi-fi", "cellular", "gsm", "cdma", "global", "china"}

    def model_details(match):
        details = [part.strip() for part in match.group(1).split(",")]
        return " ".join(part for part in details
                        if part.casefold() not in variants
                        and not re.fullmatch(r"\d+\s*(?:GB|TB)(?:\s+Model)?", part, re.I))

    return " ".join(re.sub(r"\(([^()]*)\)", model_details, name).split())


def device_name(code, names):
    if code in names:
        name = names[code]
        return simplify_ipad_name(name) if code.startswith("iPad") else name
    logging.warning("Missing device name for %s; update device_names.json", code)
    return f"名称待补充的 {device_family(code)} 设备"


def load_historical_devices(attachment, catalog_path=FIRMWARE_CATALOG):
    """Resolve old URL-only attachments without borrowing today's firmware version."""
    aliases = defaultdict(list)
    with Path(catalog_path).open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            family, version, _ = firmware_parts("https://example.com/" + row["固件名称"])
            aliases[family].append((version, row["设备代码（合并）"].split("；")))

    devices = []
    for url in sorted(set(Path(attachment).read_text(encoding="utf-8").splitlines())):
        url = url.strip()
        if not url:
            continue
        family, version, build = firmware_parts(url)
        codes = re.findall(r"(?:iPhone|iPad|iPod|AudioAccessory)\d+,\d+", family)
        if ",".join(codes) != family:
            candidates = aliases.get(family, [])
            if len(candidates) > 1:
                # Some shared filenames refer to different models across OS generations.
                candidates = [item for item in candidates
                              if item[0].split(".")[0] == version.split(".")[0]]
            codes = candidates[0][1] if len(candidates) == 1 else []
        if not codes:
            logging.warning("Cannot resolve historical firmware family: %s", family)
            codes = [family]
        for code in codes:
            devices.append({"hardware_code": code, "product_version": version,
                            "build_version": build, "firmware_url": url})
    return devices


def render_email(devices, names, attachment_name, mode):
    if not devices:
        raise ValueError("No firmware updates to include in the email")
    groups = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    urls = set()
    for device in devices:
        code = device["hardware_code"]
        key = (device["product_version"] or "未知版本", device["build_version"] or "未知")
        url = device.get("firmware_url")
        # Devices without a URL cannot be assumed to share a firmware file.
        file_key = url or ("missing_url", code)
        groups[key][device_family(code)][file_key].add(device_name(code, names))
        if url:
            urls.add(url)

    versions = sorted({version for version, _ in groups}, key=natural_key, reverse=True)
    version_summary = " / ".join(versions[:4])
    if len(versions) > 4:
        version_summary += f" 等 {len(versions)} 个版本"
    subject = f"[Apple Firmware Tracker] 固件更新：{version_summary}"
    mode_label = "历史更新补发" if mode == "manual" else "本次固件更新"
    summary = f"{len(versions)} 个版本 · {len(urls)} 个固件文件"
    note = ("以下为所选历史附件中的固件与适用设备。"
            if mode == "manual" else
            "以下仅列出本次检测到更新的设备；附件保留当日累计的固件下载链接。")
    note += " 每行对应一个固件文件，共用该文件的设备合并展示。"
    text = ["Apple Firmware Tracker", mode_label, summary, "", note, ""]
    for (version, build), families in sorted(
            groups.items(), key=lambda item: (natural_key(item[0][0]), natural_key(item[0][1])),
            reverse=True):
        text.extend([f"固件 {version}（Build {build}）", "获得更新的设备："])
        for family in ("iPhone", "iPad", "iPod", "HomePod", "其他设备"):
            if family not in families:
                continue
            file_lines = sorted(
                ("、".join(sorted(models, key=natural_key))
                 for models in families[family].values()),
                key=natural_key)
            text.append(f"  {family}")
            text.extend(f"    • {line}" for line in file_lines)
        text.append("")

    text.extend([f"下载链接见附件：{attachment_name}", "由 Apple Firmware Tracker 自动整理。"])
    return subject, "\n".join(text) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attachment", required=True, type=Path)
    parser.add_argument("--mode", choices=("auto", "manual"), default="auto")
    parser.add_argument("--updates", type=Path, default=Path("log/firmware_updates.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("log/email"))
    args = parser.parse_args()
    if not args.attachment.is_file():
        parser.error("Attachment does not exist")
    names = json.loads(DEVICE_NAMES.read_text(encoding="utf-8"))
    devices = (load_historical_devices(args.attachment) if args.mode == "manual"
               else json.loads(args.updates.read_text(encoding="utf-8")))
    subject, text = render_email(devices, names, args.attachment.name, args.mode)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in (("subject.txt", subject), ("body.txt", text)):
        (args.output_dir / filename).write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
