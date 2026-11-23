from collections import defaultdict
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA = Path(__file__).with_name("inventory_schema.sql").read_text(encoding="utf-8")
AREAS = {
    "quartermaster": "Quartermaster",
    "technology-center": "Technology Center",
}
DEFAULT_AREA = "technology-center"
LOCAL_TIME = ZoneInfo("America/New_York")


def initialize_schema(db):
    cursor = db.cursor()
    # A table read starts the ODBC transaction before acquiring the schema lock.
    cursor.execute("SELECT TOP (1) employee_id FROM dbo.agency_members").fetchall()
    cursor.execute(SCHEMA)
    db.commit()


def inventory_date(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone(LOCAL_TIME).date().isoformat()


def fetch_history(db, group_by, area=None):
    cursor = db.cursor()
    area_filter = "WHERE d.inventory_area = ?" if area is not None else ""
    params = (area,) if area is not None else ()
    rows = cursor.execute(f"""
        SELECT a.assignment_id, a.device_id, a.employee_id, m.name AS recipient,
               d.device_name, d.serial_number, d.inventory_area, a.division, a.issued_by,
               a.issued_at, a.returned_at
        FROM dbo.inventory_assignments a
        JOIN dbo.inventory_devices d ON a.device_id = d.device_id
        JOIN dbo.agency_members m ON a.employee_id = m.employee_id
        {area_filter}
        ORDER BY a.issued_at DESC, a.assignment_id DESC
    """, *params).fetchall()
    history = defaultdict(list)
    fields = [column[0] for column in cursor.description]
    for row in rows:
        entry = dict(zip(fields, row))
        entry["inventory_label"] = AREAS[entry["inventory_area"]]
        entry["issue_date"] = inventory_date(row.issued_at)
        entry["return_date"] = inventory_date(row.returned_at)
        history[entry[group_by]].append(entry)
    return history


def add_device(db, area, name, serial):
    db.cursor().execute("""
        INSERT INTO dbo.inventory_devices (inventory_area, device_name, serial_number)
        VALUES (?, ?, ?)
    """, area, name, serial)


def issue_device(db, area, device_id, recipient_id, issuer_id):
    cursor = db.cursor()
    recipient = cursor.execute("""
        SELECT division FROM dbo.agency_members
        WHERE employee_id = ? AND NULLIF(LTRIM(RTRIM(name)), '') IS NOT NULL
          AND COALESCE(status, '') <> 'Inactive'
    """, recipient_id).fetchone()
    issuer = cursor.execute("""
        SELECT name FROM dbo.agency_members
        WHERE employee_id = ? AND NULLIF(LTRIM(RTRIM(name)), '') IS NOT NULL
          AND COALESCE(status, '') <> 'Inactive'
    """, issuer_id).fetchone()
    if recipient is None or issuer is None:
        raise ValueError("Select a valid recipient and issuer from the personnel roster.")

    cursor.execute("""
        INSERT INTO dbo.inventory_assignments (device_id, employee_id, division, issued_by)
        SELECT d.device_id, ?, ?, ?
        FROM dbo.inventory_devices d WHERE d.device_id = ? AND d.inventory_area = ?
    """, recipient_id, recipient.division, issuer.name, device_id, area)
    if cursor.rowcount != 1:
        raise ValueError("This device could not be found.")


def return_device(db, area, device_id, assignment_id):
    cursor = db.cursor()
    cursor.execute("""
        UPDATE a SET returned_at = SYSUTCDATETIME()
        FROM dbo.inventory_assignments a
        JOIN dbo.inventory_devices d ON d.device_id = a.device_id
        WHERE a.device_id = ? AND a.assignment_id = ? AND a.returned_at IS NULL
          AND d.inventory_area = ?
    """, device_id, assignment_id, area)
    if cursor.rowcount != 1:
        raise ValueError("This assignment is already closed or could not be found.")
