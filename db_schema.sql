-- Core personnel table (source of truth for employee identities)
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
