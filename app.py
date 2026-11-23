import csv
import hmac
import secrets
from datetime import timedelta
import io
import os
import re
from contextlib import closing
from io import BytesIO
from difflib import SequenceMatcher
from typing import Any

import openpyxl
import pyodbc
from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for

import inventory
import tech_import

SQL_SERVER_CONN_STR = os.getenv("PERSONNEL_SQL_CONNECTION_STRING", "").strip()

if not SQL_SERVER_CONN_STR:
    raise RuntimeError(
        "PERSONNEL_SQL_CONNECTION_STRING is required. SQLite fallback has been removed."
    )

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("PERSONNEL_SESSION_SECRET"),
    SESSION_COOKIE_NAME="personnel_session",
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_REFRESH_EACH_REQUEST=False,
)
AUTH_CONNECTION_STRING = os.getenv("PERSONNEL_AUTH_CONNECTION_STRING") or SQL_SERVER_CONN_STR
SEARCH_PORTAL_URL = "https://bcso-cvdb-dwbnfmcjggf2f2a0.centralus-01.azurewebsites.net"


@app.before_request
def require_portal_access():
    if request.endpoint in (None, "static"):
        return
    if not app.secret_key:
        abort(503, "Personnel sign-in is unavailable.")

    if request.method == "POST":
        token = session.get("csrf_token")
        submitted = request.form.get("csrf_token", "")
        if not token or not hmac.compare_digest(token.encode(), submitted.encode()):
            abort(403)

    if request.endpoint == "login":
        return

    if "user_id" in session:
        try:
            with closing(pyodbc.connect(AUTH_CONNECTION_STRING)) as db:
                g.account = db.cursor().execute("""
                    SELECT email, permission, must_change_password
                    FROM search.users WHERE user_id = ? AND is_active = 1
                """, session["user_id"]).fetchone()
        except pyodbc.Error:
            app.logger.exception("Could not verify the Personnel Portal account")
            abort(503, "Personnel sign-in is unavailable.")
        if g.account and not g.account.must_change_password:
            if request.method == "POST" or request.endpoint == "edit_member":
                if request.endpoint != "logout" and not can_edit():
                    abort(403)
            return
        session.clear()

    if request.method != "GET" or request.endpoint == "name_suggestions":
        abort(401)
    return redirect(url_for("login"))


def can_edit():
    account = g.get("account")
    return bool(account and (account.permission or "").strip().lower() in {"admin", "edit"})


@app.context_processor
def portal_permissions():
    return {"can_edit": can_edit(), "account": g.get("account")}


@app.after_request
def protect_personnel_cache(response):
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    status = 200
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        try:
            with closing(pyodbc.connect(AUTH_CONNECTION_STRING)) as db:
                row = db.cursor().execute("""
                    SELECT user_id, password_hash, must_change_password
                    FROM search.users WHERE email = ? AND is_active = 1
                """, email).fetchone()
        except pyodbc.Error:
            app.logger.exception("Could not sign in to the Personnel Portal")
            abort(503, "Personnel sign-in is unavailable.")

        # The existing Search Portal compares the submitted password directly.
        if not password or not row or not hmac.compare_digest(
            password.encode(), (row.password_hash or "").encode()
        ):
            error, status = "Invalid email or password.", 401
        elif row.must_change_password:
            error, status = "Change your password in the BCSO Search Portal, then sign in here.", 403
        else:
            session.clear()
            session.permanent = True
            session["user_id"] = str(row.user_id)
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("index"), code=303)

    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return render_template("login.html", error=error, search_portal_url=SEARCH_PORTAL_URL), status


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"), code=303)


CSV_FIELD_ALIASES = {
    "employee_id": ["employee_id", "Employee ID", "employee id", "EmployeeId"],
    "name": ["name", "Name", "Worker", "worker", "Member"],
    "rank": ["rank", "Rank", "Worker/Position Job Profile", "Job Profile"],
    "division": ["division", "Division", "Assigned Division"],
    "email": ["email", "Email"],
    "status": ["status", "Status"],
    "badge_number": ["badge_number", "Badge Number", "badge number"],
    "sequence_num": ["Sequence", "Sequence Number", "sequence", "sequence number", "sequence_num"],
    "department_cell": ["Department Cell", "department cell", "department_cell"],
    "radio_id": ["Radio_ID", "Radio ID", "radio_id", "radio id"],
    "race": ["Race", "race"],
    "sex": ["Sex", "sex"],
}

PENDING_APPROVALS: dict[str, dict[str, str]] = {}




DIVISION_GROUPS = {
    "Command": ["Command", "Command Staff"],
    "Court Security": ["Court Security", "Court_Security"],
    "Domestic Violence": ["Domestic Violence", "Domestic_Violence"],
    "Special Operations": ["Special Operations", "Special_Operations", "SOD"],
}

DIVISION_OPTIONS = [
    "Command",
    "Communications",
    "Court Security",
    "Domestic Violence",
    "Field Services",
    "Human Resources",
    "Information Technology",
    "Internal Affairs",
    "Neighborhood Services",
    "Special Operations",
    "Training",
    "Others",
]
STATUS_OPTIONS = ["Active", "Inactive"]
RACE_OPTIONS = ["White", "Black", "Asian", "Hispanic", "Other"]
SEX_OPTIONS = ["Male", "Female", "Other"]


def normalize_division_value(value: str) -> str:
    return (value or "").replace("_", " ").strip()


def display_division(value: str) -> str:
    normalized = normalize_division_value(value)
    for label, variants in DIVISION_GROUPS.items():
        normalized_variants = {normalize_division_value(v).lower() for v in variants}
        if normalized.lower() in normalized_variants:
            return label
    return normalized


MEMBER_FIELDS = (
    ("name", "Name", 200),
    ("email", "Email", 320),
    ("division", "Division", 200),
    ("status", "Status", 100),
    ("department_cell", "Department cell", 100),
    ("notes", "Notes", None),
    ("rank", "Rank", 200),
    ("sequence_num", "Sequence", 50),
    ("radio_id", "Radio ID", 50),
    ("race", "Race", 50),
    ("sex", "Sex", 20),
)


def normalize_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (name or "").lower())
    tokens = [token for token in cleaned.split() if len(token) > 1]
    return " ".join(tokens)


def swapped_name_variants(name: str) -> set[str]:
    normalized = normalize_name(name)
    if not normalized:
        return set()

    parts = normalized.split()
    variants = {normalized}
    if len(parts) >= 2:
        variants.add(" ".join([parts[-1], *parts[:-1]]))
    return {v for v in variants if v}


def build_name_index(db: Any) -> dict[str, tuple[str, str]]:
    cursor = db.cursor()
    cursor.execute("SELECT employee_id, name FROM dbo.agency_members WHERE name IS NOT NULL")
    name_index: dict[str, tuple[str, str]] = {}
    for emp_id, name in cursor.fetchall():
        existing_name = str(name or "")
        for v in swapped_name_variants(existing_name):
            if v not in name_index:
                name_index[v] = (str(emp_id), existing_name)
    return name_index


def best_fuzzy_match(name: str, name_index: dict[str, tuple[str, str]]) -> tuple[str, str, str, float]:
    candidates = swapped_name_variants(name)
    best_emp_id = ""
    best_variant = ""
    best_existing_name = ""
    best_score = 0.0

    for candidate in candidates:
        for existing_variant, (emp_id, existing_name) in name_index.items():
            score = SequenceMatcher(None, candidate, existing_variant).ratio()
            if score > best_score:
                best_score = score
                best_variant = existing_variant
                best_emp_id = emp_id
                best_existing_name = existing_name

    return best_emp_id, best_existing_name, best_variant, best_score


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
    for stmt in [
        "IF COL_LENGTH('dbo.agency_members','source_file') IS NULL ALTER TABLE dbo.agency_members ADD source_file NVARCHAR(260) NULL;",
        "IF COL_LENGTH('dbo.agency_members','imported_at') IS NULL ALTER TABLE dbo.agency_members ADD imported_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME();",
        "IF COL_LENGTH('dbo.agency_members','sequence_num') IS NULL ALTER TABLE dbo.agency_members ADD sequence_num NVARCHAR(50) NULL;",
        "IF COL_LENGTH('dbo.agency_members','department_cell') IS NULL ALTER TABLE dbo.agency_members ADD department_cell NVARCHAR(100) NULL;",
        "IF COL_LENGTH('dbo.agency_members','radio_id') IS NULL ALTER TABLE dbo.agency_members ADD radio_id NVARCHAR(50) NULL;",
        "IF COL_LENGTH('dbo.agency_members','race') IS NULL ALTER TABLE dbo.agency_members ADD race NVARCHAR(50) NULL;",
        "IF COL_LENGTH('dbo.agency_members','sex') IS NULL ALTER TABLE dbo.agency_members ADD sex NVARCHAR(20) NULL;",
        "IF COL_LENGTH('dbo.agency_members','notes') IS NULL ALTER TABLE dbo.agency_members ADD notes NVARCHAR(MAX) NULL;",
    ]:
        cursor.execute(stmt)
    db.commit()


def _get_field(row: dict[str, str], canonical_name: str) -> str:
    for key in CSV_FIELD_ALIASES[canonical_name]:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _update_member(cursor: Any, payload: dict[str, str], employee_id: str) -> None:
    cursor.execute(
        """
        UPDATE dbo.agency_members
        SET name = ?, email = ?, rank = ?, division = ?, status = ?, badge_number = ?,
            sequence_num = ?, department_cell = ?, radio_id = ?, race = ?, sex = ?,
            source_file = ?, imported_at = SYSUTCDATETIME()
        WHERE employee_id = ?
        """,
        payload["name"], payload["email"], payload["rank"], payload["division"], payload["status"],
        payload["badge_number"], payload["sequence_num"], payload["department_cell"], payload["radio_id"],
        payload["race"], payload["sex"], payload["source_file"], employee_id,
    )


def ingest_csv_stream(db: Any, csv_stream: io.TextIOBase, source_name: str) -> tuple[int, int, int, list[str]]:
    inserted = 0
    updated = 0
    skipped = 0
    logs: list[str] = []
    reader = csv.DictReader(csv_stream)
    cursor = db.cursor()
    name_index = build_name_index(db)

    for i, row in enumerate(reader, start=1):
        payload = {k: _get_field(row, k) for k in CSV_FIELD_ALIASES.keys()}
        payload["source_file"] = source_name
        target_employee_id = payload["employee_id"]

        if target_employee_id:
            cursor.execute("SELECT employee_id FROM dbo.agency_members WHERE employee_id = ?", target_employee_id)
            existing = cursor.fetchone()
            if existing:
                logs.append(f"MATCH employee_id: {target_employee_id} -> update")
            else:
                logs.append(f"NO employee_id match: {target_employee_id}")
        else:
            existing = None

        if not existing and payload["name"]:
            for variant in swapped_name_variants(payload["name"]):
                if variant in name_index:
                    target_employee_id = name_index[variant][0]
                    existing = (target_employee_id,)
                    logs.append(f"MATCH name: '{payload['name']}' -> employee_id {target_employee_id}")
                    break

        if not existing and payload["name"]:
            fuzzy_emp_id, fuzzy_existing_name, fuzzy_variant, fuzzy_score = best_fuzzy_match(payload["name"], name_index)
            if fuzzy_score >= 0.8 and fuzzy_emp_id:
                approval_id = f"{source_name}:{i}:{payload['name']}"
                PENDING_APPROVALS[approval_id] = {
                    **payload,
                    "employee_id": fuzzy_emp_id,
                    "name": payload["name"],
                    "score": f"{fuzzy_score:.2f}",
                    "variant": fuzzy_variant,
                    "suggested_name": fuzzy_existing_name,
                }
                logs.append(
                    f"NO exact match: '{payload['name']}' | suggestion employee_id={fuzzy_emp_id} name='{fuzzy_existing_name}' score={fuzzy_score:.2f}"
                )
                continue

        if existing:
            _update_member(cursor, payload, target_employee_id)
            updated += 1
            for v in swapped_name_variants(payload["name"]):
                name_index[v] = (target_employee_id, payload["name"])
        else:
            if not payload["employee_id"]:
                skipped += 1
                logs.append(f"SKIP row {i}: no employee_id and no qualified fuzzy match")
                continue
            cursor.execute(
                """
                INSERT INTO dbo.agency_members
                (employee_id, name, email, rank, division, status, badge_number, sequence_num,
                 department_cell, radio_id, race, sex, source_file, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME())
                """,
                payload["employee_id"], payload["name"], payload["email"], payload["rank"], payload["division"],
                payload["status"], payload["badge_number"], payload["sequence_num"], payload["department_cell"],
                payload["radio_id"], payload["race"], payload["sex"], payload["source_file"],
            )
            inserted += 1
            logs.append(f"INSERT employee_id: {payload['employee_id']}")

    db.commit()
    no_match_logs = [line for line in logs if line.startswith("NO ") or line.startswith("SKIP ")]
    matched_logs = [line for line in logs if line not in no_match_logs]
    return inserted, updated, skipped, no_match_logs + matched_logs + [f"SUMMARY inserted={inserted} updated={updated} skipped={skipped}"]


def fetch_members(db: Any, search_name: str = "", search_division: str = "", search_radio_id: str = ""):
    cursor = db.cursor()
    where_clauses = []
    params: list[str] = []

    if search_name:
        where_clauses.append("name LIKE ?")
        params.append(f"%{search_name}%")
    if search_division:
        if search_division in DIVISION_GROUPS:
            variants = DIVISION_GROUPS[search_division]
            placeholders = ",".join(["?"] * len(variants))
            where_clauses.append(f"REPLACE(division, '_', ' ') IN ({placeholders})")
            params.extend([normalize_division_value(v) for v in variants])
        else:
            where_clauses.append("REPLACE(division, '_', ' ') = ?")
            params.append(normalize_division_value(search_division))
    if search_radio_id:
        where_clauses.append("radio_id LIKE ?")
        params.append(f"%{search_radio_id}%")

    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    query = "SELECT employee_id, name, email, rank, division, status, badge_number, sequence_num, department_cell, radio_id, race, sex, notes FROM dbo.agency_members" + where_sql + " ORDER BY name, employee_id"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    return [{"employee_id": r[0], "name": r[1], "email": r[2], "rank": r[3], "division": r[4], "division_display": display_division(r[4]), "status": r[5], "badge_number": r[6], "sequence_num": r[7], "department_cell": r[8], "radio_id": r[9], "race": r[10], "sex": r[11], "notes": r[12]} for r in rows]




def extract_email_rows_from_workbook(file_bytes: bytes) -> list[dict[str, str]]:
    wb = openpyxl.load_workbook(filename=BytesIO(file_bytes), data_only=True)
    rows: list[dict[str, str]] = []

    for ws in wb.worksheets:
        division = (ws.title or "").strip()
        max_row = ws.max_row or 0
        max_col = ws.max_column or 0

        for r in range(1, max_row + 1):
            values = []
            for c in range(1, max_col + 1):
                v = ws.cell(row=r, column=c).value
                values.append(str(v).strip() if v is not None else "")

            emails = [v for v in values if "@" in v and "." in v]
            if not emails:
                continue

            email = emails[0]
            email_idx = values.index(email)
            candidate_name = ""
            for i in range(email_idx - 1, -1, -1):
                txt = values[i]
                if txt and txt.lower() not in {"rank", "deputies", "email", "name"}:
                    candidate_name = txt
                    break

            if not candidate_name:
                continue

            rows.append({"name": candidate_name, "email": email, "division": division})

    return rows


def ingest_email_workbook(db: Any, file_bytes: bytes, source_name: str) -> tuple[int, int, int, list[str]]:
    updates = 0
    approvals = 0
    skipped = 0
    logs: list[str] = []

    cursor = db.cursor()
    name_index = build_name_index(db)
    workbook_rows = extract_email_rows_from_workbook(file_bytes)

    for i, item in enumerate(workbook_rows, start=1):
        name = item["name"]
        email = item["email"]
        division = item["division"]
        target_employee_id = ""
        existing = None

        for variant in swapped_name_variants(name):
            if variant in name_index:
                target_employee_id = name_index[variant][0]
                existing = (target_employee_id,)
                logs.append(f"EMAIL MATCH name: '{name}' -> employee_id {target_employee_id}")
                break

        if not existing:
            fuzzy_emp_id, fuzzy_existing_name, fuzzy_variant, fuzzy_score = best_fuzzy_match(name, name_index)
            if fuzzy_score >= 0.8 and fuzzy_emp_id:
                approval_id = f"emailwb:{source_name}:{i}:{name}"
                PENDING_APPROVALS[approval_id] = {
                    "employee_id": fuzzy_emp_id,
                    "name": name,
                    "email": email,
                    "division": division,
                    "rank": "",
                    "status": "",
                    "sequence_num": "",
                    "department_cell": "",
                    "radio_id": "",
                    "race": "",
                    "sex": "",
                    "badge_number": "",
                    "source_file": source_name,
                    "score": f"{fuzzy_score:.2f}",
                    "variant": fuzzy_variant,
                    "suggested_name": fuzzy_existing_name,
                }
                approvals += 1
                logs.append(f"EMAIL NO exact match: '{name}' | suggestion '{fuzzy_existing_name}' score={fuzzy_score:.2f}")
            else:
                skipped += 1
                logs.append(f"EMAIL SKIP: '{name}' no match")
            continue

        cursor.execute(
            """
            UPDATE dbo.agency_members
            SET email = ?, division = ?, source_file = ?, imported_at = SYSUTCDATETIME()
            WHERE employee_id = ?
            """,
            email,
            division,
            source_name,
            target_employee_id,
        )
        updates += 1

    db.commit()
    logs.append(f"EMAIL SUMMARY updated={updates} approvals={approvals} skipped={skipped}")
    return updates, approvals, skipped, logs


def fetch_name_suggestions(db: Any, q: str, limit: int = 10) -> list[str]:
    q = q.strip()
    if len(q) < 3:
        return []
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT DISTINCT TOP (?) name
        FROM dbo.agency_members
        WHERE name IS NOT NULL AND name LIKE ?
        ORDER BY name
        """,
        limit,
        f"%{q}%",
    )
    return [row[0] for row in cursor.fetchall() if row[0]]



def fetch_rank_options(db: Any) -> list[str]:
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT DISTINCT rank
        FROM dbo.agency_members
        WHERE rank IS NOT NULL AND LTRIM(RTRIM(rank)) <> ''
        ORDER BY rank
        """
    )
    ranks = [str(row[0]).strip() for row in cursor.fetchall() if str(row[0]).strip()]
    return [rank for rank in ranks if rank != "Others"] + ["Others"]

def fetch_divisions(db: Any) -> list[str]:
    cursor = db.cursor()
    cursor.execute("SELECT DISTINCT division FROM dbo.agency_members WHERE division IS NOT NULL AND LTRIM(RTRIM(division)) <> '' ORDER BY division")
    raw = [row[0] for row in cursor.fetchall()]
    mapped = {display_division(v) for v in raw if v}
    return sorted(mapped)






@app.get("/name-suggestions")
def name_suggestions():
    q = request.args.get("q", "")
    try:
        db = get_db()
        names = fetch_name_suggestions(db, q)
        db.close()
        return jsonify({"suggestions": names})
    except Exception as exc:
        return jsonify({"suggestions": [], "error": str(exc)}), 500

@app.route("/personnel/<employee_id>/edit", methods=["GET", "POST"])
def edit_member(employee_id):
    columns = [field for field, _, _ in MEMBER_FIELDS]
    with closing(get_db()) as db:
        cursor = db.cursor()
        record = cursor.execute(
            f"""
            SELECT {', '.join(columns)}, CONVERT(varchar(33), imported_at, 126)
            FROM dbo.agency_members WHERE employee_id = ?
            """,
            employee_id,
        ).fetchone()
        if record is None:
            abort(404)
        values = dict(zip(columns, record))
        revision = record[len(columns)]
        error = None
        status = 200
        if request.method == "POST":
            values = {field: request.form.get(field, "").strip() for field in columns}
            if any(field not in request.form for field in columns):
                abort(400)
            for field, label, limit in MEMBER_FIELDS:
                if limit is not None and len(values[field].encode("utf-16-le")) // 2 > limit:
                    error, status = f"{label} is too long.", 400
                    break
            if not error:
                assignments = ", ".join(f"{field} = ?" for field in columns)
                cursor.execute(
                    f"""
                    UPDATE dbo.agency_members SET {assignments}, imported_at = SYSUTCDATETIME()
                    WHERE employee_id = ? AND CONVERT(varchar(33), imported_at, 126) = ?
                    """,
                    *(values[field] for field in columns), employee_id, request.form.get("revision", ""),
                )
                if cursor.rowcount == 1:
                    db.commit()
                    return "", 204
                error, status = "This record changed while you were editing. Reopen it before saving.", 409
                revision = request.form.get("revision", "")
        options = {
            "division": DIVISION_OPTIONS, "status": STATUS_OPTIONS,
            "rank": fetch_rank_options(db), "race": RACE_OPTIONS, "sex": SEX_OPTIONS,
        }

    return render_template(
        "edit_member.html", employee_id=employee_id, fields=MEMBER_FIELDS,
        values=values, options=options, error=error, revision=revision,
    ), status


@app.post("/approve")
def approve_guess():
    approval_id = request.form.get("approval_id", "")
    if approval_id in PENDING_APPROVALS:
        payload = PENDING_APPROVALS.pop(approval_id)
        db = get_db()
        cursor = db.cursor()
        _update_member(cursor, payload, payload["employee_id"])
        db.commit()
        db.close()
    return redirect(url_for("index"))


@app.post("/add-member")
def add_member():
    employee_id = request.form.get("employee_id", "").strip()
    if not employee_id:
        return redirect(url_for("index"))

    fields = {
        "name": request.form.get("name", "").strip(),
        "email": request.form.get("email", "").strip(),
        "rank": request.form.get("rank", "").strip(),
        "division": request.form.get("division", "").strip(),
        "status": request.form.get("status", "").strip(),
        "sequence_num": request.form.get("sequence_num", "").strip(),
        "department_cell": request.form.get("department_cell", "").strip(),
        "radio_id": request.form.get("radio_id", "").strip(),
        "race": request.form.get("race", "").strip(),
        "sex": request.form.get("sex", "").strip(),
        "notes": request.form.get("notes", "").strip(),
    }

    db = get_db()
    initialize_database(db)
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO dbo.agency_members
        (employee_id, name, email, rank, division, status, sequence_num, department_cell,
         radio_id, race, sex, notes, source_file, imported_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME())
        """,
        employee_id,
        fields["name"],
        fields["email"],
        fields["rank"],
        fields["division"],
        fields["status"],
        fields["sequence_num"],
        fields["department_cell"],
        fields["radio_id"],
        fields["race"],
        fields["sex"],
        fields["notes"],
        "manual",
    )
    db.commit()
    db.close()
    return redirect(url_for("index"))


def selected_inventory_area():
    area = request.args.get("area", inventory.DEFAULT_AREA)
    if area not in inventory.AREAS:
        abort(404)
    return area


@app.get("/inventory")
def inventory_page():
    area = selected_inventory_area()
    try:
        with closing(get_db()) as db:
            initialize_inventory(db)
            devices = db.cursor().execute("""
                SELECT device_id, device_name, serial_number
                FROM dbo.inventory_devices WHERE inventory_area = ?
                ORDER BY device_name, serial_number
            """, area).fetchall()
            roster = []
            if can_edit():
                roster = db.cursor().execute("""
                    SELECT employee_id, name, division FROM dbo.agency_members
                    WHERE NULLIF(LTRIM(RTRIM(name)), '') IS NOT NULL
                      AND COALESCE(status, '') <> 'Inactive'
                    ORDER BY name, employee_id
                """).fetchall()
            quartermaster_roster = []
            if can_edit() and area == "quartermaster":
                quartermaster_roster = db.cursor().execute("""
                    SELECT employee_id, name, division, status
                    FROM dbo.agency_members ORDER BY name, employee_id
                """).fetchall()
            history = inventory.fetch_history(db, "device_id", area)
            imported_details = tech_import.workbook_details(db) if area == "technology-center" else {}
            stock = inventory.quartermaster_stock(db) if area == "quartermaster" else {}
            quartermaster_issued = inventory.quartermaster_history(db, active_only=True) if area == "quartermaster" else []
    except pyodbc.Error:
        app.logger.exception("Could not load inventory")
        return render_template("inventory.html", area=area, areas=inventory.AREAS, error="Inventory is temporarily unavailable."), 503

    catalog_query = request.args.get("q", "").strip()
    catalog_category = request.args.get("category", "")
    catalog = inventory.QUARTERMASTER_CATALOG if area == "quartermaster" else None
    catalog_items = list(inventory.find_quartermaster_items(catalog_query, catalog_category)) if catalog else []
    catalog_categories = sorted({item["category"] for item in catalog["items"]}) if catalog else []
    active_assignments = {
        entry["device_id"]: entry
        for entries in history.values()
        for entry in entries
        if entry["returned_at"] is None
    }
    workbook_issued = {device_id for device_id, details in imported_details.items()
                       if details["workbook_assignees"] and not details["inactive"]}
    available_devices = [
        device for device in devices if device.device_id not in active_assignments
        and device.device_id not in workbook_issued
        and not imported_details.get(device.device_id, {}).get("inactive")
    ]
    stock_count = len(available_devices)
    messages = {"added": "Device added.", "issued": "Device issued.", "returned": "Device returned.", "reassigned": "Assignment changed."}
    return render_template(
        "inventory.html", area=area, areas=inventory.AREAS, devices=devices, roster=roster, history=history,
        active_assignments=active_assignments, available_devices=available_devices,
        issued_count=len(active_assignments.keys() | workbook_issued), stock_count=stock_count, imported_details=imported_details,
        message=messages.get(request.args.get("result")),
        catalog=catalog, catalog_items=catalog_items, catalog_categories=catalog_categories,
        catalog_query=catalog_query, catalog_category=catalog_category,
        stock=stock, quartermaster_issued=quartermaster_issued, quartermaster_roster=quartermaster_roster,
        stock_variant=request.args.get("stock_variant") if can_edit() else None,
        stock_message=request.args.get("stock_message"), stock_error=request.args.get("stock_error"),
    )


def initialize_inventory(db):
    # Supports opening Inventory directly on an empty database.
    initialize_database(db)
    inventory.initialize_schema(db)


@app.post("/inventory/quartermaster/stock")
def change_quartermaster_stock():
    action = request.form.get("action")
    variant_id = request.form.get("variant_id", "")
    quantity = request.form.get("quantity", type=int)
    destination = dict(
        area="quartermaster", q=request.form.get("q") or None,
        category=request.form.get("category") or None, _anchor="quartermaster-catalog",
    )
    if action in {"add", "assign"} and variant_id in inventory.CATALOG_VARIANTS:
        destination.update(stock_variant=variant_id, _anchor=f"stock-{variant_id}")
    try:
        with closing(get_db()) as db:
            initialize_inventory(db)
            if action == "return":
                assignment_id = request.form.get("assignment_id", type=int)
                if not assignment_id or not 0 < assignment_id <= 2147483647:
                    raise ValueError("Select a valid assignment.")
                inventory.return_quartermaster_stock(db, assignment_id)
                message = "Assignment returned to stock."
            else:
                if action not in {"add", "assign"} or variant_id not in inventory.CATALOG_VARIANTS:
                    raise ValueError("Select a valid item and size.")
                if not quantity or not 0 < quantity <= 2147483647:
                    raise ValueError("Enter a positive whole-number quantity.")
                if action == "add":
                    inventory.add_quartermaster_stock(db, variant_id, quantity)
                    message = "Stock added."
                else:
                    employee_id = request.form.get("recipient_id", "").strip()
                    if not employee_id or len(employee_id) > 50:
                        raise ValueError("Select a person from the roster.")
                    inventory.assign_quartermaster_stock(db, variant_id, quantity, employee_id, g.account.email)
                    message = "Items assigned."
            db.commit()
    except ValueError as exc:
        return redirect(url_for("inventory_page", **destination, stock_error=str(exc)), code=303)
    except pyodbc.Error:
        app.logger.exception("Could not update Quartermaster stock")
        return redirect(url_for("inventory_page", **destination, stock_error="The stock change could not be saved."), code=303)
    return redirect(url_for("inventory_page", **destination, stock_message=message), code=303)


@app.post("/inventory/<action>")
def change_inventory(action):
    area = selected_inventory_area()
    if action not in {"add", "issue", "return", "reassign"}:
        abort(404)
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        abort(403)

    try:
        with closing(get_db()) as db:
            initialize_inventory(db)
            if action == "add":
                name = request.form.get("device_name", "").strip()
                serial = request.form.get("serial_number", "").strip()
                if not name or not serial or len(name) > 200 or len(serial) > 150:
                    raise ValueError("Enter a device name and a valid serial number.")
                inventory.add_device(db, area, name, serial)
            else:
                device_id = request.form.get("device_id", type=int)
                if not device_id or not 0 < device_id <= 2147483647:
                    raise ValueError("Select a valid device.")
                if action in {"issue", "reassign"}:
                    recipient_id = request.form.get("recipient_id", "").strip()
                    issuer_id = request.form.get("issuer_id", "").strip()
                    if not recipient_id or not issuer_id or max(len(recipient_id), len(issuer_id)) > 50:
                        raise ValueError("Select a valid recipient and issuer from the personnel roster.")
                    if action == "reassign" and request.form.get("assignment_id"):
                        assignment_id = request.form.get("assignment_id", type=int)
                        if not assignment_id or not 0 < assignment_id <= 2147483647:
                            raise ValueError("Select a valid assignment.")
                        inventory.return_device(db, area, device_id, assignment_id)
                    inventory.issue_device(db, area, device_id, recipient_id, issuer_id)
                else:
                    assignment_id = request.form.get("assignment_id", type=int)
                    if not assignment_id or not 0 < assignment_id <= 2147483647:
                        raise ValueError("Select a valid assignment.")
                    inventory.return_device(db, area, device_id, assignment_id)
            db.commit()
    except ValueError as exc:
        return render_template("inventory.html", area=area, areas=inventory.AREAS, error=str(exc)), 400
    except pyodbc.IntegrityError:
        message = {
            "add": "A device with that serial number already exists in this inventory.",
            "issue": "This device is already issued or the personnel record is unavailable.",
            "return": "The return could not be recorded.",
            "reassign": "The assignment changed or the personnel record is unavailable.",
        }[action]
        return render_template("inventory.html", area=area, areas=inventory.AREAS, error=message), 409
    except pyodbc.Error:
        app.logger.exception("Could not update inventory")
        return render_template("inventory.html", area=area, areas=inventory.AREAS, error="The inventory update could not be completed."), 503

    result = {"add": "added", "issue": "issued", "return": "returned", "reassign": "reassigned"}[action]
    return redirect(url_for("inventory_page", area=area, result=result), code=303)


@app.route("/", methods=["GET", "POST"])
def index():
    import_result = None
    db_error = None
    members = []
    divisions: list[str] = []
    rank_options: list[str] = ["Others"]
    ingest_logs: list[str] = []
    equipment_history = None

    search_name = request.values.get("search_name", "").strip()
    search_division = request.values.get("search_division", "").strip()
    search_radio_id = request.values.get("search_radio_id", "").strip()
    try:
        db = get_db()
        initialize_database(db)
        if request.method == "POST":
            uploaded_email_wb = request.files.get("email_workbook")
            uploaded = request.files.get("csv_file")
            if uploaded_email_wb and uploaded_email_wb.filename:
                wb_bytes = uploaded_email_wb.read()
                updates, approvals, skipped, ingest_logs = ingest_email_workbook(db, wb_bytes, uploaded_email_wb.filename)
                import_result = f"Email workbook import complete. Updated: {updates}, Pending approvals: {approvals}, Skipped: {skipped}."
            elif uploaded and uploaded.filename:
                text_stream = io.TextIOWrapper(uploaded.stream, encoding="utf-8-sig", newline="")
                inserted, updated, skipped, ingest_logs = ingest_csv_stream(db, text_stream, uploaded.filename)
                import_result = f"Import complete to Azure SQL. Inserted: {inserted}, Updated: {updated}, Skipped: {skipped}."
            else:
                import_result = "Please choose a CSV file or Email Workbook before clicking upload."
        members = fetch_members(db, search_name=search_name, search_division=search_division, search_radio_id=search_radio_id)
        divisions = fetch_divisions(db)
        rank_options = fetch_rank_options(db)
        try:
            inventory.initialize_schema(db)
            equipment_history = inventory.fetch_history(db, "employee_id")
            for entry in inventory.quartermaster_history(db):
                equipment_history[entry["employee_id"]].append(entry)
            for entries in equipment_history.values():
                entries.sort(key=lambda entry: (entry["issued_at"] is not None, entry["issued_at"]), reverse=True)
        except pyodbc.Error:
            app.logger.exception("Could not load personnel equipment history")
        finally:
            db.close()
    except Exception as exc:
        db_error = str(exc)

    return render_template("index.html", members=members, equipment_history=equipment_history, import_result=import_result, db_error=db_error, ingest_logs=ingest_logs, pending_approvals=PENDING_APPROVALS, divisions=divisions, division_options=DIVISION_OPTIONS, status_options=STATUS_OPTIONS, rank_options=rank_options, race_options=RACE_OPTIONS, sex_options=SEX_OPTIONS, search_name=search_name, search_division=search_division, search_radio_id=search_radio_id)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
