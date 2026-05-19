# Personnel Search MVP

Initial MVP for the personnel admin portal:

- Import a CSV file into an SQL table (`agency_members`) using `employee_id` as primary key.
- Display imported personnel records on a web page.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:8000` and click **Import CSV** to load `data/agency_members.csv`.

## CSV format

Expected columns:

- `employee_id`
- `name`
- `email`
- `rank`
- `division`
- `status`
- `badge_number`
