from collections import defaultdict
from datetime import datetime, time, timezone
from io import BytesIO
import json
from zipfile import ZipFile

from openpyxl import load_workbook

import inventory


HEADERS = (
    "Item Name", "Serial Number", "Assigned User", "Department",
    "Warranty Expiration Date", "Status", "Notes", "Issued Date", "Extra Notes",
)


def read_devices(content):
    with ZipFile(BytesIO(content)) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 30 * 1024 * 1024:
            raise ValueError("The workbook is too large to process.")
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    grouped = defaultdict(list)
    try:
        for sheet in workbook:
            rows = sheet.values
            headers = tuple(str(value or "").strip() for value in next(rows, ()))
            if not set(HEADERS).issubset(headers):
                raise ValueError(f"{sheet.title} is missing required inventory columns.")
            indexes = [headers.index(name) for name in HEADERS]
            for number, row in enumerate(rows, 2):
                values = [row[index] if index < len(row) else None for index in indexes]
                if not any(value is not None for value in values):
                    continue
                serial = str(values[1] or "").strip()
                name = str(values[0] or "").strip()
                if not name or not serial or len(name.encode("utf-16-le")) > 400 or len(serial.encode("utf-16-le")) > 300:
                    raise ValueError(f"{sheet.title}, row {number}: enter a device name and serial number within the field limits.")
                values[0:2] = name, serial
                grouped[serial.casefold()].append(dict(zip(HEADERS, values), sheet=sheet.title, row=number))
    finally:
        workbook.close()
    if not grouped:
        raise ValueError("The workbook contains no devices.")
    return grouped


def distinct_values(rows, field):
    values = {}
    for row in rows:
        value = str(row[field] or "").strip()
        if value and value.casefold() not in {"none", "n/a", "unassigned"}:
            values.setdefault(value.casefold(), value)
    return list(values.values())


def import_devices(db, grouped):
    cursor = db.cursor()
    roster = defaultdict(list)
    for member in cursor.execute("SELECT employee_id, name, division FROM dbo.agency_members").fetchall():
        if member.name:
            roster[" ".join(member.name.casefold().split())].append(member)

    counts = dict(added=0, linked=0, review=0, skipped=0)
    for _, rows in sorted(grouped.items()):
        first = next((row for row in rows if row["sheet"].casefold() == "master sheet"), rows[0])
        existing = cursor.execute("""
            SELECT device_id FROM dbo.inventory_devices WITH (UPDLOCK, HOLDLOCK)
            WHERE inventory_area = 'technology-center' AND serial_number = ?
        """, first["Serial Number"]).fetchone()
        if existing:
            counts["skipped"] += 1
            continue

        statuses = {value.casefold() for value in distinct_values(rows, "Status")}
        assignees = distinct_values(rows, "Assigned User")
        matched = roster.get(" ".join(assignees[0].casefold().split()), []) if len(assignees) == 1 else []
        inactive = "inactive" in statuses
        member = matched[0] if len(matched) == 1 and not inactive else None
        review = not inactive and not member and bool(assignees or "active" in statuses)
        device = cursor.execute("""
            INSERT INTO dbo.inventory_devices (inventory_area, device_name, serial_number)
            OUTPUT inserted.device_id
            VALUES ('technology-center', ?, ?)
        """, first["Item Name"], first["Serial Number"]).fetchone()
        cursor.execute("""
            INSERT INTO dbo.technology_import_details (device_id, inactive, needs_review, source_json)
            VALUES (?, ?, ?, ?)
        """, device.device_id, inactive, review, json.dumps(rows, default=str))
        if member:
            dates = {row["Issued Date"].date() for row in rows if isinstance(row["Issued Date"], datetime)}
            issued_at = (
                datetime.combine(next(iter(dates)), time(12), inventory.LOCAL_TIME)
                .astimezone(timezone.utc).replace(tzinfo=None) if len(dates) == 1 else None
            )
            cursor.execute("""
                INSERT INTO dbo.inventory_assignments (device_id, employee_id, division, issued_by, issued_at)
                VALUES (?, ?, ?, 'Excel import', ?)
            """, device.device_id, member.employee_id, member.division, issued_at)
            counts["linked"] += 1
        counts["added"] += 1
        counts["review"] += int(review)
    return counts


def display_details(rows):
    fields = ("Assigned User", "Department", "Status", "Warranty Expiration Date", "Issued Date", "Notes")
    details = {}
    for field in fields:
        unique = {}
        keys = ("Notes", "Extra Notes") if field == "Notes" else (field,)
        for row in rows:
            for key in keys:
                raw = row.get(key)
                if raw is None:
                    continue
                value = str(raw).strip()
                if field.endswith("Date"):
                    try:
                        value = datetime.fromisoformat(value).date().isoformat()
                    except ValueError:
                        pass
                if value:
                    unique.setdefault(" ".join(value.casefold().split()), value)
        if unique:
            details[field] = list(unique.values())
    return details


def workbook_details(db):
    return {
        row.device_id: dict(inactive=row.inactive, needs_review=row.needs_review, fields=display_details(json.loads(row.source_json)))
        for row in db.cursor().execute("""
            SELECT device_id, inactive, needs_review, source_json FROM dbo.technology_import_details
        """).fetchall()
    }
