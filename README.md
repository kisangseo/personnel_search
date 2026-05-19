# Personnel Search MVP

This app now writes to Azure SQL / SQL Server only.

## Required app setting

`PERSONNEL_SQL_CONNECTION_STRING` must be present or app startup fails.

Example:

`Driver={ODBC Driver 18 for SQL Server};Server=bcso-sql-server-prod.database.windows.net;Database=bcsodb;Uid=bcsoadmin;Pwd=***;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;`

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
gunicorn --bind=0.0.0.0 --timeout 600 app:app
```

## Verify ingestion in Azure Data Studio

```sql
SELECT DB_NAME() AS db_name, SUSER_SNAME() AS login_name;
SELECT COUNT(*) AS row_count FROM dbo.agency_members;
SELECT TOP 20 * FROM dbo.agency_members ORDER BY imported_at DESC;
```
