-- Schema for the SQLite pipeline database (not used to build pasture_reference.db)

CREATE TABLE IF NOT EXISTS geographic_boundaries (
  boundary_id TEXT PRIMARY KEY,
  name TEXT,
  geojson TEXT NOT NULL,
  crs TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nrcs_soil_data (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  boundary_id TEXT NOT NULL,
  soil_awc_mean REAL,
  soil_ph_mean REAL,
  soil_om_mean REAL,
  source_version TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (boundary_id) REFERENCES geographic_boundaries(boundary_id)
);

CREATE TABLE IF NOT EXISTS rap_biomass (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  boundary_id TEXT NOT NULL,
  composite_date TEXT NOT NULL,
  biomass_kg_per_ha REAL,
  source_version TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (boundary_id) REFERENCES geographic_boundaries(boundary_id)
);

CREATE TABLE IF NOT EXISTS weather_forecasts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  boundary_id TEXT NOT NULL,
  forecast_date TEXT NOT NULL,
  precipitation_mm REAL,
  temp_max_c REAL,
  temp_min_c REAL,
  wind_speed_kmh REAL,
  source_version TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (boundary_id) REFERENCES geographic_boundaries(boundary_id)
);

CREATE TABLE IF NOT EXISTS herd_configurations (
  id TEXT PRIMARY KEY,
  name TEXT,
  animal_count INTEGER NOT NULL,
  daily_intake_kg_per_head REAL NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS boundary_daily_features (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  boundary_id TEXT NOT NULL,
  feature_date TEXT NOT NULL,
  rap_biomass_kg_per_ha REAL,
  soil_awc_mean REAL,
  weather_precipitation_mm REAL,
  weather_temp_max_c REAL,
  weather_temp_min_c REAL,
  weather_wind_speed_kmh REAL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (boundary_id) REFERENCES geographic_boundaries(boundary_id)
);

CREATE TABLE IF NOT EXISTS grazing_recommendations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  boundary_id TEXT NOT NULL,
  herd_config_id TEXT NOT NULL,
  calculation_date TEXT NOT NULL,
  available_forage_kg REAL NOT NULL,
  daily_consumption_kg REAL NOT NULL,
  days_of_grazing_remaining INTEGER NOT NULL,
  recommended_move_date TEXT NOT NULL,
  model_version TEXT NOT NULL,
  config_version TEXT NOT NULL,
  input_data_versions_json TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (boundary_id) REFERENCES geographic_boundaries(boundary_id),
  FOREIGN KEY (herd_config_id) REFERENCES herd_configurations(id)
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
  run_id TEXT PRIMARY KEY,
  boundary_id TEXT NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  source_versions_json TEXT,
  records_ingested INTEGER,
  error_message TEXT,
  FOREIGN KEY (boundary_id) REFERENCES geographic_boundaries(boundary_id)
);

CREATE TABLE IF NOT EXISTS data_quality_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  check_name TEXT NOT NULL,
  passed INTEGER NOT NULL,
  check_type TEXT NOT NULL,
  details_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES ingestion_runs(run_id)
);

-- --- Task 3: Monitoring tables ---

CREATE TABLE IF NOT EXISTS monitoring_runs (
  run_id TEXT PRIMARY KEY,
  boundary_id TEXT NOT NULL,
  herd_config_id TEXT,
  model_version TEXT,
  window_start TEXT NOT NULL,
  window_end TEXT NOT NULL,
  lookback_days INTEGER NOT NULL,
  status TEXT NOT NULL,
  metrics_json TEXT NOT NULL,
  thresholds_json TEXT NOT NULL,
  manifest_path TEXT,
  created_at TEXT NOT NULL,
  error_message TEXT
);

CREATE TABLE IF NOT EXISTS monitoring_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  boundary_id TEXT NOT NULL,
  severity TEXT NOT NULL,
  alert_name TEXT NOT NULL,
  message TEXT NOT NULL,
  details_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES monitoring_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_rap_boundary_date ON rap_biomass(boundary_id, composite_date);
CREATE INDEX IF NOT EXISTS idx_weather_boundary_date ON weather_forecasts(boundary_id, forecast_date);
CREATE INDEX IF NOT EXISTS idx_features_boundary_date ON boundary_daily_features(boundary_id, feature_date);

CREATE UNIQUE INDEX IF NOT EXISTS uq_reco_boundary_herd_date
  ON grazing_recommendations(boundary_id, herd_config_id, calculation_date);

CREATE INDEX IF NOT EXISTS idx_monitor_runs_boundary_end
  ON monitoring_runs(boundary_id, window_end);

CREATE INDEX IF NOT EXISTS idx_monitor_alerts_run
  ON monitoring_alerts(run_id);
