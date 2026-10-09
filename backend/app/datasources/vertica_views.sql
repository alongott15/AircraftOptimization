-- Contract for the Vertica canonical views read by the app (DATABASE.md §3 and §7).
-- The data team implements these views over the lake tables; the app account has SELECT on them only.
-- Column names/types must match exactly; the integration contract test verifies them.

CREATE SCHEMA IF NOT EXISTS aircraft_opt;

-- Template: replace <lake_*> with the real lake tables/columns.
CREATE OR REPLACE VIEW aircraft_opt.ao_v_aircraft AS
SELECT CAST(NULL AS INTEGER)       AS aircraft_id,
       CAST(NULL AS VARCHAR(16))   AS tail_number,
       CAST(NULL AS VARCHAR(32))   AS serial_number,
       CAST(NULL AS VARCHAR(16))   AS type_code,
       CAST(NULL AS VARCHAR(8))    AS base_code,
       CAST(NULL AS VARCHAR(16))   AS status,            -- OPERATIONAL | IN_MAINTENANCE | GROUNDED | STORED
       CAST(NULL AS DATE)          AS manufacture_date,
       CAST(NULL AS TIMESTAMPTZ)   AS last_flight_at,
       CAST(NULL AS TIMESTAMPTZ)   AS source_updated_at
WHERE FALSE;

-- Same pattern for the remaining views (columns as in DATABASE.md §3):
--   ao_v_aircraft_type, ao_v_base, ao_v_activity_unit, ao_v_usage_counter, ao_v_flight_log,
--   ao_v_qualification, ao_v_aircraft_qualification, ao_v_zone, ao_v_ata_zone_map,
--   ao_v_inspection_type, ao_v_inspection_limit, ao_v_aircraft_inspection_due, ao_v_inspection_event,
--   ao_v_permit_type, ao_v_aircraft_permit, ao_v_maintenance_action, ao_v_data_load_status
-- Counter semantics: LDG and CYC count full-stop events only; touch-and-goes are reported in TG.

-- Read-only grants (run by the DBA):
-- CREATE USER aircraft_opt_ro IDENTIFIED BY '***';
-- GRANT USAGE ON SCHEMA aircraft_opt TO aircraft_opt_ro;
-- GRANT SELECT ON ALL TABLES IN SCHEMA aircraft_opt TO aircraft_opt_ro;
