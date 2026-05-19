# Personnel Search MVP

This app writes to Azure SQL / SQL Server only and ingests personnel CSVs.

## New fields added for second CSV

Mapped from your new CSV into SQL columns:
- Sequence / Sequence Number -> `sequence_num`
- Department Cell -> `department_cell`
- Radio_ID -> `radio_id`
- Race -> `race`
- Sex -> `sex`
- Email -> existing `email`
- Status -> existing `status`

Matching behavior during import:
1. match by `employee_id` if present
2. else match by exact `name`
3. if neither available/matched, row is skipped

## SQL Server statements to add missing fields

Run this in Azure Data Studio:

```sql
IF COL_LENGTH('dbo.agency_members','sequence_num') IS NULL
    ALTER TABLE dbo.agency_members ADD sequence_num NVARCHAR(50) NULL;

IF COL_LENGTH('dbo.agency_members','department_cell') IS NULL
    ALTER TABLE dbo.agency_members ADD department_cell NVARCHAR(100) NULL;

IF COL_LENGTH('dbo.agency_members','radio_id') IS NULL
    ALTER TABLE dbo.agency_members ADD radio_id NVARCHAR(50) NULL;

IF COL_LENGTH('dbo.agency_members','race') IS NULL
    ALTER TABLE dbo.agency_members ADD race NVARCHAR(50) NULL;

IF COL_LENGTH('dbo.agency_members','sex') IS NULL
    ALTER TABLE dbo.agency_members ADD sex NVARCHAR(20) NULL;
```

## Verify columns now exist

```sql
SELECT COLUMN_NAME, DATA_TYPE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'agency_members'
ORDER BY ORDINAL_POSITION;
```


## Name matching normalization

For rows without `employee_id`, importer now matches by normalized name:
- case-insensitive
- ignores punctuation (including commas)
- collapses extra spaces
- ignores one-letter tokens (middle initials)
- tries swapped order (first/last and last/first forms)

The UI now shows per-row ingest logs for matched/unmatched rows.


## Fuzzy matching

When no exact match is found, importer tries 80% fuzzy name similarity and creates an approval suggestion in the UI.
