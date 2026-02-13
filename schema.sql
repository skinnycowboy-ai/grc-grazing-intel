CREATE TABLE IF NOT EXISTS geographic_boundaries (
    boundary_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    ranch_id TEXT,
    pasture_id TEXT,
    geometry_geojson TEXT NOT NULL,
    area_ha REAL,
    crs TEXT DEFAULT 'EPSG:4326',
    created_at TEXT NOT NULL,
    source_file TEXT
);

CREATE TABLE IF NOT EXISTS nrcs_soil_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boundary_id TEXT NOT NULL,
    mukey TEXT,
    component_name TEXT,
    productivity_index REAL,
    land_capability_class TEXT,
    hydrologic_group TEXT,
    available_water_capacity REAL,
    source_version TEXT,
    ingested_at TEXT NOT NULL,
    FOREIGN KEY (boundary_id) REFERENCES geographic_boundaries(boundary_id)
);


CREATE TABLE IF NOT EXISTS rap_biomass (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boundary_id TEXT NOT NULL,
    composite_date TEXT NOT NULL,
    biomass_kg_per_ha REAL,
    annual_herbaceous_cover_pct REAL,
    ndvi REAL,
    source_version TEXT,
    ingested_at TEXT NOT NULL,
    UNIQUE(boundary_id, composite_date),
    FOREIGN KEY (boundary_id) REFERENCES geographic_boundaries(boundary_id)
);


CREATE TABLE IF NOT EXISTS weather_forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boundary_id TEXT NOT NULL,
    forecast_date TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    precipitation_mm REAL,
    temp_max_c REAL,
    temp_min_c REAL,
    wind_speed_kmh REAL,
    source_version TEXT,
    ingested_at TEXT NOT NULL,
    FOREIGN KEY (boundary_id) REFERENCES geographic_boundaries(boundary_id)
);


CREATE TABLE IF NOT EXISTS herd_configurations (
    id TEXT PRIMARY KEY,
    ranch_id TEXT NOT NULL,
    pasture_id TEXT,
    boundary_id TEXT,
    animal_count INTEGER NOT NULL,
    animal_type TEXT,
    daily_intake_kg_per_head REAL NOT NULL,
    avg_daily_gain_kg REAL,
    config_snapshot_json TEXT,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    created_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id TEXT PRIMARY KEY,
    boundary_id TEXT,
    timeframe_start TEXT,
    timeframe_end TEXT,
    sources_included TEXT,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    records_ingested INTEGER,
    error_message TEXT
);


CREATE TABLE IF NOT EXISTS grazing_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boundary_id TEXT NOT NULL,
    herd_config_id TEXT NOT NULL,
    calculation_date TEXT NOT NULL,
    available_forage_kg REAL,
    daily_consumption_kg REAL,
    days_of_grazing_remaining REAL,
    recommended_move_date TEXT,
    model_version TEXT NOT NULL,
    config_version TEXT,
    input_data_versions_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (boundary_id) REFERENCES geographic_boundaries(boundary_id),
    FOREIGN KEY (herd_config_id) REFERENCES herd_configurations(id)
);

CREATE TABLE IF NOT EXISTS data_quality_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    check_name TEXT NOT NULL,
    check_type TEXT,
    passed INTEGER NOT NULL,
    details_json TEXT,
    checked_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES ingestion_runs(run_id)
);


CREATE TABLE IF NOT EXISTS model_versions (
    version_id TEXT PRIMARY KEY,
    description TEXT,
    parameters_json TEXT,
    deployed_at TEXT NOT NULL,
    deprecated_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rap_boundary_date ON rap_biomass(boundary_id, composite_date);
CREATE INDEX IF NOT EXISTS idx_weather_boundary_date ON weather_forecasts(boundary_id, forecast_date);
CREATE INDEX IF NOT EXISTS idx_recommendations_lookup ON grazing_recommendations(boundary_id, calculation_date);
CREATE INDEX IF NOT EXISTS idx_herd_ranch_pasture ON herd_configurations(ranch_id, pasture_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_timeframe ON ingestion_runs(timeframe_start, timeframe_end);
