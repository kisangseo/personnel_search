import csv
import os
import sqlite3
from pathlib import Path

from flask import Flask, g, render_template, request

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "personnel.db"
DEFAULT_CSV_PATH = BASE_DIR / "data" / "agency_members.csv"

app = Flask(__name__)


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
    db.commit()


def ingest_csv(csv_path: Path) -> tuple[int, int]:
    """Ingest CSV rows into agency_members.

    Returns a tuple of (inserted_count, updated_count).
    """
    inserted = 0
    updated = 0

    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        db = get_db()

        for row in reader:
            employee_id = (row.get("employee_id") or "").strip()
            if not employee_id:
                continue

            payload = {
                "employee_id": employee_id,
                "name": (row.get("name") or "").strip(),
                "email": (row.get("email") or "").strip(),
                "rank": (row.get("rank") or "").strip(),
                "division": (row.get("division") or "").strip(),
                "status": (row.get("status") or "").strip(),
                "badge_number": (row.get("badge_number") or "").strip(),
                "source_file": str(csv_path.name),
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


@app.route("/", methods=["GET", "POST"])
def index():
    initialize_database()

    import_result = None
    if request.method == "POST":
        csv_input = request.form.get("csv_path", "").strip()
        csv_path = Path(csv_input) if csv_input else DEFAULT_CSV_PATH

        if not csv_path.is_absolute():
            csv_path = BASE_DIR / csv_path

        if csv_path.exists() and csv_path.is_file():
            inserted, updated = ingest_csv(csv_path)
            import_result = f"Import complete. Inserted: {inserted}, Updated: {updated}."
        else:
            import_result = f"File not found: {csv_path}"

    query = """
        SELECT employee_id, name, email, rank, division, status, badge_number, imported_at
        FROM agency_members
        ORDER BY name COLLATE NOCASE, employee_id
    """
    members = get_db().execute(query).fetchall()

    return render_template(
        "index.html",
        members=members,
        import_result=import_result,
        default_csv=str(DEFAULT_CSV_PATH.relative_to(BASE_DIR)),
    )


if __name__ == "__main__":
    os.makedirs(BASE_DIR / "data", exist_ok=True)
    app.run(host="0.0.0.0", port=8000, debug=True)
