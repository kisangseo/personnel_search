# Personnel Search MVP

This MVP now supports uploading a CSV from your computer directly in the browser.

## What it does

- Creates `agency_members` table in SQLite.
- Ingests uploaded CSV records keyed by `employee_id`.
- Displays imported personnel in the web UI.

## Minimum supported CSV columns

- `Employee ID` (required)
- `Worker` (mapped to `name`)
- `Worker/Position Job Profile` (mapped to `rank`)

Also supports canonical names such as `employee_id`, `name`, and `rank`.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:8000`, choose a CSV with the file picker, and click **Upload CSV**.


## SQL to create/verify required table + fields

Use these SQL statements in SQLite to ensure the table and fields exist:

```sql
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
);

CREATE INDEX IF NOT EXISTS idx_agency_members_name ON agency_members(name);
CREATE INDEX IF NOT EXISTS idx_agency_members_email ON agency_members(email);
CREATE INDEX IF NOT EXISTS idx_agency_members_badge_number ON agency_members(badge_number);
CREATE INDEX IF NOT EXISTS idx_agency_members_division ON agency_members(division);
```

To check whether all required columns exist:

```sql
PRAGMA table_info(agency_members);
```

If a column is missing, add it (example):

```sql
ALTER TABLE agency_members ADD COLUMN badge_number TEXT;
```


## Azure SQL usage

If you set `PERSONNEL_SQL_CONNECTION_STRING`, the app writes to SQL Server/Azure SQL instead of local SQLite.

Example App Setting:

- `PERSONNEL_SQL_CONNECTION_STRING=Driver={ODBC Driver 18 for SQL Server};Server=...;Database=...;Uid=...;Pwd=...;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;`

Quick verification query in Azure Data Studio:

```sql
SELECT COUNT(*) AS row_count FROM dbo.agency_members;
SELECT TOP 20 * FROM dbo.agency_members ORDER BY imported_at DESC;
```
