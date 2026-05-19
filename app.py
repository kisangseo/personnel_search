import csv
import io
import os
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
    "name": ["name", "Name", "Worker", "worker"],
    "rank": ["rank", "Rank", "Worker/Position Job Profile", "Job Profile"],
    "division": ["division", "Division"],
    "email": ["email", "Email"],
    "status": ["status", "Status"],
    "badge_number": ["badge_number", "Badge Number", "badge number"],
}


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
    cursor.execute(
        "IF COL_LENGTH('dbo.agency_members','source_file') IS NULL ALTER TABLE dbo.agency_members ADD source_file NVARCHAR(260) NULL;"
    )
    cursor.execute(
        "IF COL_LENGTH('dbo.agency_members','imported_at') IS NULL ALTER TABLE dbo.agency_members ADD imported_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME();"
    )
    db.commit()


def _get_field(row: dict[str, str], canonical_name: str) -> str:
    for key in CSV_FIELD_ALIASES[canonical_name]:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def ingest_csv_stream(db: Any, csv_stream: io.TextIOBase, source_name: str) -> tuple[int, int]:
    inserted = 0
    updated = 0
    reader = csv.DictReader(csv_stream)
    cursor = db.cursor()

    for row in reader:
        employee_id = _get_field(row, "employee_id")
        if not employee_id:
            continue

        payload = {
            "employee_id": employee_id,
            "name": _get_field(row, "name"),
            "email": _get_field(row, "email"),
            "rank": _get_field(row, "rank"),
            "division": _get_field(row, "division"),
            "status": _get_field(row, "status"),
            "badge_number": _get_field(row, "badge_number"),
            "source_file": source_name,
        }

        cursor.execute("SELECT employee_id FROM dbo.agency_members WHERE employee_id = ?", employee_id)
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                """
                UPDATE dbo.agency_members
                SET name = ?, email = ?, rank = ?, division = ?, status = ?, badge_number = ?,
                    source_file = ?, imported_at = SYSUTCDATETIME()
                WHERE employee_id = ?
                """,
                payload["name"],
                payload["email"],
                payload["rank"],
                payload["division"],
                payload["status"],
                payload["badge_number"],
                payload["source_file"],
                payload["employee_id"],
            )
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO dbo.agency_members
                (employee_id, name, email, rank, division, status, badge_number, source_file, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME())
                """,
                payload["employee_id"],
                payload["name"],
                payload["email"],
                payload["rank"],
                payload["division"],
                payload["status"],
                payload["badge_number"],
                payload["source_file"],
            )
            inserted += 1

    db.commit()
    return inserted, updated


def fetch_members(db: Any):
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT employee_id, name, email, rank, division, status, badge_number, imported_at
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
            "imported_at": r[7],
        }
        for r in rows
    ]


@app.route("/", methods=["GET", "POST"])
def index():
    import_result = None
    db_error = None
    members = []

    try:
        db = get_db()
        initialize_database(db)

        if request.method == "POST":
            uploaded = request.files.get("csv_file")
            if not uploaded or not uploaded.filename:
                import_result = "Please choose a CSV file before clicking Upload CSV."
            else:
                text_stream = io.TextIOWrapper(uploaded.stream, encoding="utf-8-sig", newline="")
                inserted, updated = ingest_csv_stream(db, text_stream, uploaded.filename)
                import_result = f"Import complete to Azure SQL. Inserted: {inserted}, Updated: {updated}."

        members = fetch_members(db)
        db.close()
    except Exception as exc:
        db_error = str(exc)

    return render_template("index.html", members=members, import_result=import_result, db_error=db_error)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
