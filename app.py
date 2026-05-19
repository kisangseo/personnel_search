import csv
import io
import os
import sqlite3
from pathlib import Path

from flask import Flask, g, render_template, request

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "personnel.db"
DEFAULT_CSV_PATH = BASE_DIR / "data" / "agency_members.csv"
SCHEMA_PATH = BASE_DIR / "db_schema.sql"

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


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exception: Exception | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def initialize_database() -> None:
    db = get_db()

    # Run canonical schema file first.
    if SCHEMA_PATH.exists():
        db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    # Backfill any missing columns for older databases.
    expected_columns = {
        "employee_id": "TEXT",
        "name": "TEXT",
        "email": "TEXT",
        "rank": "TEXT",
        "division": "TEXT",
        "status": "TEXT",
        "badge_number": "TEXT",
        "source_file": "TEXT",
        "imported_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
    }

    existing_cols = {row[1] for row in db.execute("PRAGMA table_info(agency_members)").fetchall()}

    # Table may not exist if schema file is missing.
    if not existing_cols:
        db.execute(
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
        existing_cols = {row[1] for row in db.execute("PRAGMA table_info(agency_members)").fetchall()}

    for col, col_type in expected_columns.items():
        if col not in existing_cols:
            db.execute(f"ALTER TABLE agency_members ADD COLUMN {col} {col_type}")

    db.commit()


def _get_field(row: dict[str, str], canonical_name: str) -> str:
    aliases = CSV_FIELD_ALIASES[canonical_name]
    for key in aliases:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def ingest_csv_stream(csv_stream: io.TextIOBase, source_name: str) -> tuple[int, int]:
    inserted = 0
    updated = 0
    reader = csv.DictReader(csv_stream)
    db = get_db()

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

        existing = db.execute(
            "SELECT employee_id FROM agency_members WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()

        if existing:
            db.execute(
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
            db.execute(
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


@app.route("/", methods=["GET", "POST"])
def index():
    initialize_database()

    import_result = None
    if request.method == "POST":
        uploaded = request.files.get("csv_file")
        if uploaded and uploaded.filename:
            text_stream = io.TextIOWrapper(uploaded.stream, encoding="utf-8-sig", newline="")
            inserted, updated = ingest_csv_stream(text_stream, uploaded.filename)
            import_result = f"Import complete. Inserted: {inserted}, Updated: {updated}."
        else:
            if DEFAULT_CSV_PATH.exists():
                inserted, updated = ingest_csv(DEFAULT_CSV_PATH)
                import_result = f"No file uploaded. Imported default CSV. Inserted: {inserted}, Updated: {updated}."
            else:
                import_result = "No file uploaded and default CSV file not found."

    members = get_db().execute(
        """
        SELECT employee_id, name, email, rank, division, status, badge_number, imported_at
        FROM agency_members
        ORDER BY name COLLATE NOCASE, employee_id
        """
    ).fetchall()

    return render_template("index.html", members=members, import_result=import_result)


if __name__ == "__main__":
    os.makedirs(BASE_DIR / "data", exist_ok=True)
    app.run(host="0.0.0.0", port=8000, debug=True)
