import csv
import io
import os
import re
from typing import Any

import pyodbc
from flask import Flask, render_template, request

SQL_SERVER_CONN_STR = os.getenv("PERSONNEL_SQL_CONNECTION_STRING", "").strip()

if not SQL_SERVER_CONN_STR:
    raise RuntimeError(
        "PERSONNEL_SQL_CONNECTION_STRING is required. SQLite fallback has been removed."
    )

app = Flask(__name__)

CSV_FIELD_ALIASES = {
    "employee_id": ["employee_id", "Employee ID", "employee id", "EmployeeId"],
    "name": ["name", "Name", "Worker", "worker", "Member"],
    "rank": ["rank", "Rank", "Worker/Position Job Profile", "Job Profile"],
    "division": ["division", "Division", "Assigned Division"],
    "email": ["email", "Email"],
    "status": ["status", "Status"],
    "badge_number": ["badge_number", "Badge Number", "badge number"],
    "sequence_num": ["Sequence", "sequence", "sequence_num"],
    "department_cell": ["Department Cell", "department cell", "department_cell"],
    "radio_id": ["Radio_ID", "Radio ID", "radio_id", "radio id"],
    "race": ["Race", "race"],
    "sex": ["Sex", "sex"],
}


def normalize_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (name or "").lower())
    cleaned = " ".join(cleaned.split())
    return cleaned


def swapped_name_variants(name: str) -> set[str]:
    normalized = normalize_name(name)
    if not normalized:
        return set()

    parts = normalized.split()
    variants = {normalized}

    if len(parts) >= 2:
        variants.add(" ".join([parts[-1], *parts[:-1]]))

    if "," in (name or ""):
        raw_parts = [p.strip() for p in name.split(",") if p.strip()]
        if len(raw_parts) >= 2:
            combined = normalize_name(" ".join(raw_parts[1:] + [raw_parts[0]]))
            if combined:
                variants.add(combined)

    return {v for v in variants if v}


def build_name_index(db: Any) -> dict[str, str]:
    cursor = db.cursor()
    cursor.execute("SELECT employee_id, name FROM dbo.agency_members WHERE name IS NOT NULL")
    name_index: dict[str, str] = {}
    for emp_id, name in cursor.fetchall():
        for v in swapped_name_variants(str(name or "")):
            if v not in name_index:
                name_index[v] = str(emp_id)
    return name_index


def get_db() -> Any:
    return pyodbc.connect(SQL_SERVER_CONN_STR)


def initialize_database(db: Any) -> None:
    cursor = db.cursor()
    cursor.execute(
        """
        IF OBJECT_ID('dbo.agency_members', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.agency_members (
                employee_id NVARCHAR(50) NOT NULL PRIMARY KEY,
                name NVARCHAR(200) NULL,
                email NVARCHAR(320) NULL,
                rank NVARCHAR(200) NULL,
                division NVARCHAR(200) NULL,
                status NVARCHAR(100) NULL,
                badge_number NVARCHAR(100) NULL,
                source_file NVARCHAR(260) NULL,
                imported_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
            );
        END;
        """
    )

    alter_statements = [
        "IF COL_LENGTH('dbo.agency_members','source_file') IS NULL ALTER TABLE dbo.agency_members ADD source_file NVARCHAR(260) NULL;",
        "IF COL_LENGTH('dbo.agency_members','imported_at') IS NULL ALTER TABLE dbo.agency_members ADD imported_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME();",
        "IF COL_LENGTH('dbo.agency_members','sequence_num') IS NULL ALTER TABLE dbo.agency_members ADD sequence_num NVARCHAR(50) NULL;",
        "IF COL_LENGTH('dbo.agency_members','department_cell') IS NULL ALTER TABLE dbo.agency_members ADD department_cell NVARCHAR(100) NULL;",
        "IF COL_LENGTH('dbo.agency_members','radio_id') IS NULL ALTER TABLE dbo.agency_members ADD radio_id NVARCHAR(50) NULL;",
        "IF COL_LENGTH('dbo.agency_members','race') IS NULL ALTER TABLE dbo.agency_members ADD race NVARCHAR(50) NULL;",
        "IF COL_LENGTH('dbo.agency_members','sex') IS NULL ALTER TABLE dbo.agency_members ADD sex NVARCHAR(20) NULL;",
    ]
    for stmt in alter_statements:
        cursor.execute(stmt)
    db.commit()


def _get_field(row: dict[str, str], canonical_name: str) -> str:
    for key in CSV_FIELD_ALIASES[canonical_name]:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def ingest_csv_stream(db: Any, csv_stream: io.TextIOBase, source_name: str) -> tuple[int, int, int, list[str]]:
    inserted = 0
    updated = 0
    skipped = 0
    logs: list[str] = []
    reader = csv.DictReader(csv_stream)
    cursor = db.cursor()
    name_index = build_name_index(db)

    for row in reader:
        payload = {
            "employee_id": _get_field(row, "employee_id"),
            "name": _get_field(row, "name"),
            "email": _get_field(row, "email"),
            "rank": _get_field(row, "rank"),
            "division": _get_field(row, "division"),
            "status": _get_field(row, "status"),
            "badge_number": _get_field(row, "badge_number"),
            "sequence_num": _get_field(row, "sequence_num"),
            "department_cell": _get_field(row, "department_cell"),
            "radio_id": _get_field(row, "radio_id"),
            "race": _get_field(row, "race"),
            "sex": _get_field(row, "sex"),
            "source_file": source_name,
        }

        target_employee_id = payload["employee_id"]

        if target_employee_id:
            cursor.execute("SELECT employee_id FROM dbo.agency_members WHERE employee_id = ?", target_employee_id)
            existing = cursor.fetchone()
            if existing:
                logs.append(f"MATCH employee_id: {payload['employee_id']} -> update")
            else:
                logs.append(f"NO employee_id match: {payload['employee_id']}")
        elif payload["name"]:
            existing = None
            matched_variant = ""
            for variant in swapped_name_variants(payload["name"]):
                if variant in name_index:
                    target_employee_id = name_index[variant]
                    existing = (target_employee_id,)
                    matched_variant = variant
                    break
            if existing:
                logs.append(f"MATCH name: '{payload['name']}' (normalized='{matched_variant}') -> employee_id {target_employee_id}")
            else:
                logs.append(f"NO name match: '{payload['name']}'")
        else:
            existing = None
            logs.append("SKIP row missing employee_id and name")

        if existing:
            cursor.execute(
                """
                UPDATE dbo.agency_members
                SET name = ?, email = ?, rank = ?, division = ?, status = ?, badge_number = ?,
                    sequence_num = ?, department_cell = ?, radio_id = ?, race = ?, sex = ?,
                    source_file = ?, imported_at = SYSUTCDATETIME()
                WHERE employee_id = ?
                """,
                payload["name"],
                payload["email"],
                payload["rank"],
                payload["division"],
                payload["status"],
                payload["badge_number"],
                payload["sequence_num"],
                payload["department_cell"],
                payload["radio_id"],
                payload["race"],
                payload["sex"],
                payload["source_file"],
                target_employee_id,
            )
            updated += 1
            for v in swapped_name_variants(payload["name"]):
                name_index[v] = target_employee_id
        else:
            if not payload["employee_id"]:
                skipped += 1
                continue

            cursor.execute(
                """
                INSERT INTO dbo.agency_members
                (employee_id, name, email, rank, division, status, badge_number, sequence_num,
                 department_cell, radio_id, race, sex, source_file, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME())
                """,
                payload["employee_id"],
                payload["name"],
                payload["email"],
                payload["rank"],
                payload["division"],
                payload["status"],
                payload["badge_number"],
                payload["sequence_num"],
                payload["department_cell"],
                payload["radio_id"],
                payload["race"],
                payload["sex"],
                payload["source_file"],
            )
            inserted += 1
            for v in swapped_name_variants(payload["name"]):
                name_index[v] = payload["employee_id"]

    db.commit()
    logs.append(f"SUMMARY inserted={inserted} updated={updated} skipped={skipped}")
    return inserted, updated, skipped, logs


def fetch_members(db: Any):
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT employee_id, name, email, rank, division, status, badge_number,
               sequence_num, department_cell, radio_id, race, sex, imported_at
        FROM dbo.agency_members
        ORDER BY name, employee_id
        """
    )
    rows = cursor.fetchall()
    return [
        {
            "employee_id": r[0],
            "name": r[1],
            "email": r[2],
            "rank": r[3],
            "division": r[4],
            "status": r[5],
            "badge_number": r[6],
            "sequence_num": r[7],
            "department_cell": r[8],
            "radio_id": r[9],
            "race": r[10],
            "sex": r[11],
            "imported_at": r[12],
        }
        for r in rows
    ]


@app.route("/", methods=["GET", "POST"])
def index():
    import_result = None
    db_error = None
    members = []
    ingest_logs: list[str] = []

    try:
        db = get_db()
        initialize_database(db)

        if request.method == "POST":
            uploaded = request.files.get("csv_file")
            if not uploaded or not uploaded.filename:
                import_result = "Please choose a CSV file before clicking Upload CSV."
            else:
                text_stream = io.TextIOWrapper(uploaded.stream, encoding="utf-8-sig", newline="")
                inserted, updated, skipped, ingest_logs = ingest_csv_stream(db, text_stream, uploaded.filename)
                import_result = (
                    f"Import complete to Azure SQL. Inserted: {inserted}, Updated: {updated}, "
                    f"Skipped (no employee_id and no name match): {skipped}."
                )

        members = fetch_members(db)
        db.close()
    except Exception as exc:
        db_error = str(exc)

    return render_template("index.html", members=members, import_result=import_result, db_error=db_error, ingest_logs=ingest_logs)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
