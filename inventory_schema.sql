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
