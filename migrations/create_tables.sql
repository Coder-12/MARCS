-- migrations/create_tables.sql
CREATE TABLE IF NOT EXISTS reviews (
  event_id TEXT PRIMARY KEY,
  repo TEXT,
  review_json TEXT NOT NULL,
  ts INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_ts ON reviews(ts);

CREATE TABLE IF NOT EXISTS metrics_evals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT,
  ok INTEGER,
  tp INTEGER,
  fp INTEGER,
  fn INTEGER,
  precision REAL,
  recall REAL,
  f1 REAL,
  ts INTEGER,
  raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics_evals(ts);