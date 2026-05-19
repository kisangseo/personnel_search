import csv
import io
import os
import sqlite3
from pathlib import Path
from typing import Any

import pyodbc
from flask import Flask, g, render_template, request

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "personnel.db"
DEFAULT_CSV_PATH = BASE_DIR / "data" / "agency_members.csv"
SQL_SERVER_CONN_STR = os.getenv("PERSONNEL_SQL_CONNECTION_STRING", "").strip()

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


def using_sql_server() -> bool:
    return bool(SQL_SERVER_CONN_STR)


def get_db() -> Any:
    if "db" not in g:
        if using_sql_server():
            g.db = pyodbc.connect(SQL_SERVER_CONN_STR)
        else:
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row
            g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exception: Exception | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def initialize_database() -> None:
    db = get_db()
    cursor = db.cursor()

    if using_sql_server():
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
        return

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agency_members (
            employee_id TEXT PRIMARY KEY,
            name TEXT,
            email TEXT,
            rank TEXT,
            division TEXT,
            status TEXT,
            badge_number TEXT,
            source_file TEXT,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.commit()


def _get_field(row: dict[str, str], canonical_name: str) -> str:
    for key in CSV_FIELD_ALIASES[canonical_name]:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _fetch_existing(cursor: Any, employee_id: str):
    if using_sql_server():
        cursor.execute("SELECT employee_id FROM dbo.agency_members WHERE employee_id = ?", employee_id)
    else:
        cursor.execute("SELECT employee_id FROM agency_members WHERE employee_id = ?", (employee_id,))
    return cursor.fetchone()


def ingest_csv_stream(csv_stream: io.TextIOBase, source_name: str) -> tuple[int, int]:
    inserted = 0
    updated = 0
    reader = csv.DictReader(csv_stream)

    db = get_db()
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

        if _fetch_existing(cursor, employee_id):
            if using_sql_server():
                cursor.execute(
                    """
                    UPDATE dbo.agency_members
                    SET name = ?, email = ?, rank = ?, division = ?, status = ?, badge_number = ?,
                        source_file = ?, imported_at = SYSUTCDATETIME()
                    WHERE employee_id = ?
                    """,
                    payload["name"], payload["email"], payload["rank"], payload["division"],
                    payload["status"], payload["badge_number"], payload["source_file"], payload["employee_id"],
                )
            else:
                cursor.execute(
                    """
                    UPDATE agency_members
                    SET name = :name,
                        email = :email,
                        rank = :rank,
                        division = :division,
                        status = :status,
                        badge_number = :badge_number,
                        source_file = :source_file,
                        imported_at = CURRENT_TIMESTAMP
                    WHERE employee_id = :employee_id
                    """,
                    payload,
                )
            updated += 1
        else:
            if using_sql_server():
                cursor.execute(
                    """
                    INSERT INTO dbo.agency_members
                    (employee_id, name, email, rank, division, status, badge_number, source_file, imported_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME())
                    """,
                    payload["employee_id"], payload["name"], payload["email"], payload["rank"], payload["division"],
                    payload["status"], payload["badge_number"], payload["source_file"],
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO agency_members (
                        employee_id, name, email, rank, division, status, badge_number, source_file
                    ) VALUES (
                        :employee_id, :name, :email, :rank, :division, :status, :badge_number, :source_file
                    )
                    """,
                    payload,
                )
            inserted += 1

    db.commit()
    return inserted, updated


def ingest_csv(csv_path: Path) -> tuple[int, int]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return ingest_csv_stream(csv_file, csv_path.name)


def fetch_members():
    db = get_db()
    cursor = db.cursor()
    if using_sql_server():
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

    return db.execute(
        """
        SELECT employee_id, name, email, rank, division, status, badge_number, imported_at
        FROM agency_members
        ORDER BY name COLLATE NOCASE, employee_id
        """
    ).fetchall()


@app.route("/", methods=["GET", "POST"])
def index():
    initialize_database()

    import_result = None
    if request.method == "POST":
        uploaded = request.files.get("csv_file")
        if uploaded and uploaded.filename:
            text_stream = io.TextIOWrapper(uploaded.stream, encoding="utf-8-sig", newline="")
            inserted, updated = ingest_csv_stream(text_stream, uploaded.filename)
            target = "SQL Server" if using_sql_server() else "SQLite"
            import_result = f"Import complete to {target}. Inserted: {inserted}, Updated: {updated}."
        elif DEFAULT_CSV_PATH.exists():
            inserted, updated = ingest_csv(DEFAULT_CSV_PATH)
            import_result = f"No file uploaded. Imported default CSV. Inserted: {inserted}, Updated: {updated}."
        else:
            import_result = "No file uploaded and default CSV file not found."

    members = fetch_members()
    return render_template("index.html", members=members, import_result=import_result, using_sql_server=using_sql_server())


if __name__ == "__main__":
    os.makedirs(BASE_DIR / "data", exist_ok=True)
    app.run(host="0.0.0.0", port=8000, debug=True)
