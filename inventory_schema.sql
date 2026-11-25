SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET QUOTED_IDENTIFIER ON;
SET NUMERIC_ROUNDABORT OFF;

IF OBJECT_ID('dbo.inventory_assignments', 'U') IS NULL
BEGIN
    DECLARE @lock_result INT;
    EXEC @lock_result = sp_getapplock
        @Resource = 'personnel_inventory_schema',
        @LockMode = 'Exclusive',
        @LockOwner = 'Transaction';
    IF @lock_result < 0
        THROW 50001, 'Could not initialize the inventory tables.', 1;

    IF OBJECT_ID('dbo.inventory_devices', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.inventory_devices (
            device_id INT IDENTITY PRIMARY KEY,
            device_name NVARCHAR(200) NOT NULL,
            serial_number NVARCHAR(150) NOT NULL,
            inventory_area VARCHAR(24) NOT NULL,
            CONSTRAINT CK_inventory_area CHECK (inventory_area IN ('quartermaster', 'technology-center')),
            CONSTRAINT UQ_inventory_area_serial UNIQUE (inventory_area, serial_number)
        );
    END;

    IF OBJECT_ID('dbo.inventory_assignments', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.inventory_assignments (
            assignment_id INT IDENTITY PRIMARY KEY,
            device_id INT NOT NULL REFERENCES dbo.inventory_devices(device_id),
            employee_id NVARCHAR(50) NOT NULL REFERENCES dbo.agency_members(employee_id),
            division NVARCHAR(200) NULL,
            issued_by NVARCHAR(200) NOT NULL,
            issued_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
            returned_at DATETIME2 NULL
        );

        CREATE UNIQUE INDEX UX_inventory_open_assignment
            ON dbo.inventory_assignments(device_id)
            WHERE returned_at IS NULL;
    END;
END;

IF OBJECT_ID('dbo.quartermaster_stock', 'U') IS NULL
BEGIN
    DECLARE @stock_lock INT;
    EXEC @stock_lock = sp_getapplock
        @Resource = 'personnel_quartermaster_schema',
        @LockMode = 'Exclusive', @LockOwner = 'Transaction';
    IF @stock_lock < 0
        THROW 50002, 'Could not initialize Quartermaster stock.', 1;

    IF OBJECT_ID('dbo.quartermaster_stock', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.quartermaster_stock (
            variant_id VARCHAR(12) NOT NULL PRIMARY KEY,
            quantity BIGINT NOT NULL CHECK (quantity >= 0)
        );

        CREATE TABLE dbo.quartermaster_assignments (
            assignment_id INT IDENTITY PRIMARY KEY,
            variant_id VARCHAR(12) NOT NULL REFERENCES dbo.quartermaster_stock(variant_id),
            item_name NVARCHAR(300) NOT NULL,
            employee_id NVARCHAR(50) NOT NULL REFERENCES dbo.agency_members(employee_id),
            division NVARCHAR(200) NULL,
            issued_by NVARCHAR(320) NOT NULL,
            quantity INT NOT NULL CHECK (quantity > 0),
            issued_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
            returned_at DATETIME2 NULL
        );
    END;
END;

IF OBJECT_ID('dbo.technology_import_details', 'U') IS NULL
BEGIN
    DECLARE @tech_import_lock INT;
    EXEC @tech_import_lock = sp_getapplock
        @Resource = 'personnel_technology_import_schema',
        @LockMode = 'Exclusive', @LockOwner = 'Transaction';
    IF @tech_import_lock < 0
        THROW 50003, 'Could not initialize Technology Center imports.', 1;

    IF OBJECT_ID('dbo.technology_import_details', 'U') IS NULL
    BEGIN
        ALTER TABLE dbo.inventory_assignments ALTER COLUMN issued_at DATETIME2 NULL;

        CREATE TABLE dbo.technology_import_details (
            device_id INT NOT NULL PRIMARY KEY REFERENCES dbo.inventory_devices(device_id),
            inactive BIT NOT NULL,
            needs_review BIT NOT NULL,
            source_json NVARCHAR(MAX) NOT NULL
        );
    END;
END;

IF COL_LENGTH('dbo.inventory_devices', 'device_status') IS NULL
BEGIN
    DECLARE @device_details_lock INT;
    EXEC @device_details_lock = sp_getapplock
        @Resource = 'personnel_device_details_schema',
        @LockMode = 'Exclusive', @LockOwner = 'Transaction';
    IF @device_details_lock < 0
        THROW 50004, 'Could not initialize device details.', 1;

    IF COL_LENGTH('dbo.inventory_devices', 'device_status') IS NULL
    BEGIN
        ALTER TABLE dbo.inventory_devices ADD
            warranty_expiration DATE NULL,
            device_status VARCHAR(8) NOT NULL
                CONSTRAINT DF_inventory_device_status DEFAULT 'Active'
                CONSTRAINT CK_inventory_device_status CHECK (device_status IN ('Active', 'Inactive')),
            notes NVARCHAR(MAX) NULL;
    END;
END;

IF COL_LENGTH('dbo.inventory_devices', 'device_location') IS NULL
BEGIN
    DECLARE @device_location_lock INT;
    EXEC @device_location_lock = sp_getapplock
        @Resource = 'personnel_device_location_schema',
        @LockMode = 'Exclusive', @LockOwner = 'Transaction';
    IF @device_location_lock < 0
        THROW 50005, 'Could not initialize device locations.', 1;

    IF COL_LENGTH('dbo.inventory_devices', 'device_location') IS NULL
        ALTER TABLE dbo.inventory_devices ADD device_location NVARCHAR(100) NULL;
END;
