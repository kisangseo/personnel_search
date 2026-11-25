from datetime import datetime
import json


def distinct_values(rows, field):
    values = {}
    for row in rows:
        value = str(row[field] or "").strip()
        if value and value.casefold() not in {"none", "n/a", "unassigned"}:
            values.setdefault(value.casefold(), value)
    return list(values.values())


def display_details(rows):
    fields = ("Assigned User", "Department", "Status", "Warranty Expiration Date", "Issued Date", "Notes")
    details = {}
    for field in fields:
        unique = {}
        keys = ("Notes", "Extra Notes") if field == "Notes" else (field,)
        for row in rows:
            for key in keys:
                raw = row.get(key)
                if raw is None:
                    continue
                value = str(raw).strip()
                if field.endswith("Date"):
                    try:
                        value = datetime.fromisoformat(value).date().isoformat()
                    except ValueError:
                        pass
                if value:
                    unique.setdefault(" ".join(value.casefold().split()), value)
        if unique:
            details[field] = list(unique.values())
    return details


def workbook_details(db):
    details = {}
    for row in db.cursor().execute("""
        SELECT device_id, inactive, needs_review, source_json FROM dbo.technology_import_details
    """).fetchall():
        source_rows = json.loads(row.source_json)
        details[row.device_id] = {
            "inactive": row.inactive,
            "location": "Tech Center" if any(
                source.get("sheet", "").strip().casefold() == "tech center" for source in source_rows
            ) else ", ".join(dict.fromkeys(
                source["sheet"] for source in source_rows
                if source.get("sheet", "").strip().casefold() not in {"", "master sheet"}
            )),
            "workbook_assignees": distinct_values(source_rows, "Assigned User") if row.needs_review else [],
            "fields": display_details(source_rows),
        }
    return details
