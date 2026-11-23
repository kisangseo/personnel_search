import json
from collections import defaultdict
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA = Path(__file__).with_name("inventory_schema.sql").read_text(encoding="utf-8")
QUARTERMASTER_CATALOG = json.loads(
    Path(__file__).with_name("data").joinpath("quartermaster_catalog.json").read_text(encoding="utf-8")
)
AREAS = {
    "quartermaster": "Quartermaster",
    "technology-center": "Technology Center",
}
DEFAULT_AREA = "technology-center"
LOCAL_TIME = ZoneInfo("America/New_York")


def find_quartermaster_items(query, category):
    terms = query.casefold().split()
    for item in QUARTERMASTER_CATALOG["items"]:
        if category and item["category"] != category:
            continue
        description = f'{item["category"]} {item["name"]}'.casefold()
        matches = [
            variant for variant in item["variants"]
            if all(term in f'{description} {variant["label"].casefold()}' for term in terms)
        ]
        if matches:
            yield {**item, "variants": matches}


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


ISSUER_NAMES = ("Kisang Kevin Seo", "Brandyn Bard", "Sewell Mykell")


def inventory_issuers(roster):
    # A unique name match also supports the roster's "Last, First Middle" format.
    by_name = {}
    for member in roster:
        name = member.name.casefold()
        if "," in name:
            last, first = name.split(",", 1)
            name = f"{first} {last}"
        by_name.setdefault(" ".join(name.split()), []).append(member)
    issuers = {}
    for label in ISSUER_NAMES:
        names = label.casefold().split()
        matches = by_name.get(" ".join(names), [])
        if not matches:
            matches = [
                member for name, members in by_name.items()
                if len(name.split()) >= 2 and (name.split()[0], name.split()[-1]) == (names[0], names[-1])
                for member in members
            ]
        if len(matches) == 1:
            issuers[matches[0].employee_id] = label
    return issuers


def group_stock(devices):
    groups = {}
    for device in devices:
        key = " ".join(device.device_name.casefold().split())
        groups.setdefault(key, []).append(device)
    return list(groups.values())


def issue_device(db, area, device_id, recipient_id, issuer_id):
    cursor = db.cursor()
    recipient = cursor.execute("""
        SELECT division FROM dbo.agency_members
        WHERE employee_id = ? AND NULLIF(LTRIM(RTRIM(name)), '') IS NOT NULL
          AND COALESCE(status, '') <> 'Inactive'
    """, recipient_id).fetchone()
    issuers = inventory_issuers(cursor.execute("""
        SELECT employee_id, name FROM dbo.agency_members
        WHERE NULLIF(LTRIM(RTRIM(name)), '') IS NOT NULL
          AND COALESCE(status, '') <> 'Inactive'
    """).fetchall())
    issuer = issuers.get(issuer_id)
    if recipient is None or issuer is None:
        raise ValueError("Select an eligible recipient and one of the authorized issuers.")

    cursor.execute("""
        INSERT INTO dbo.inventory_assignments (device_id, employee_id, division, issued_by)
        SELECT d.device_id, ?, ?, ?
        FROM dbo.inventory_devices d
        WHERE d.device_id = ? AND d.inventory_area = ?
          AND NOT EXISTS (
            SELECT 1 FROM dbo.technology_import_details t WHERE t.device_id = d.device_id AND t.inactive = 1
          )
    """, recipient_id, recipient.division, issuer, device_id, area)
    if cursor.rowcount != 1:
        raise ValueError("This device is inactive or could not be found.")
    if area == "technology-center":
        cursor.execute("""
            UPDATE dbo.technology_import_details SET needs_review = 0 WHERE device_id = ?
        """, device_id)


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


def catalog_variant_name(item, variant):
    label = variant["label"]
    return item["name"] if label == "Unspecified" else f"{item['name']} · {label}"


CATALOG_VARIANTS = {
    variant["source"][0]: catalog_variant_name(item, variant)
    for item in QUARTERMASTER_CATALOG["items"]
    for variant in item["variants"]
}


def quartermaster_stock(db):
    return dict(db.cursor().execute(
        "SELECT variant_id, quantity FROM dbo.quartermaster_stock"
    ).fetchall())


def quartermaster_history(db, active_only=False):
    cursor = db.cursor()
    active_filter = "WHERE a.returned_at IS NULL" if active_only else ""
    rows = cursor.execute(f"""
        SELECT a.assignment_id, a.employee_id, m.name AS recipient, a.item_name AS device_name,
               a.division, a.issued_by, a.quantity, a.issued_at, a.returned_at
        FROM dbo.quartermaster_assignments a
        JOIN dbo.agency_members m ON a.employee_id = m.employee_id
        {active_filter}
        ORDER BY a.issued_at DESC, a.assignment_id DESC
    """).fetchall()
    columns = [column[0] for column in cursor.description]
    return [
        dict(zip(columns, row), inventory_label="Quartermaster",
             issue_date=inventory_date(row.issued_at), return_date=inventory_date(row.returned_at),
             serial_number="—")
        for row in rows
    ]


def add_quartermaster_stock(db, variant_id, quantity):
    cursor = db.cursor()
    cursor.execute("""
        UPDATE dbo.quartermaster_stock WITH (UPDLOCK, HOLDLOCK)
        SET quantity = quantity + ? WHERE variant_id = ?
    """, quantity, variant_id)
    if cursor.rowcount == 0:
        cursor.execute("""
            INSERT INTO dbo.quartermaster_stock (variant_id, quantity) VALUES (?, ?)
        """, variant_id, quantity)


def assign_quartermaster_stock(db, variant_id, quantity, employee_id, issued_by):
    cursor = db.cursor()
    member = cursor.execute("""
        SELECT division FROM dbo.agency_members
        WHERE employee_id = ?
    """, employee_id).fetchone()
    if member is None:
        raise ValueError("Select a person from the roster.")

    cursor.execute("""
        UPDATE dbo.quartermaster_stock SET quantity = quantity - ?
        WHERE variant_id = ? AND quantity >= ?
    """, quantity, variant_id, quantity)
    if cursor.rowcount != 1:
        raise ValueError("There is insufficient stock for that item and size.")

    cursor.execute("""
        INSERT INTO dbo.quartermaster_assignments
            (variant_id, item_name, employee_id, division, issued_by, quantity)
        VALUES (?, ?, ?, ?, ?, ?)
    """, variant_id, CATALOG_VARIANTS[variant_id], employee_id, member.division, issued_by, quantity)


def return_quartermaster_stock(db, assignment_id):
    cursor = db.cursor()
    returned = cursor.execute("""
        UPDATE dbo.quartermaster_assignments SET returned_at = SYSUTCDATETIME()
        OUTPUT inserted.variant_id, inserted.quantity
        WHERE assignment_id = ? AND returned_at IS NULL
    """, assignment_id).fetchall()
    if not returned:
        raise ValueError("This assignment is already closed or could not be found.")
    row = returned[0]
    add_quartermaster_stock(db, row.variant_id, row.quantity)
