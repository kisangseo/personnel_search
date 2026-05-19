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
