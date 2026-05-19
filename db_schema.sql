-- SQL Server / Azure SQL schema updates for agency_members
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
