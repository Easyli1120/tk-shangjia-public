-- Additive, idempotent migration; existing product/image data are untouched.
CREATE TABLE IF NOT EXISTS ai_models(
      id TEXT PRIMARY KEY, category TEXT NOT NULL, name TEXT NOT NULL, provider TEXT NOT NULL,
      base_url TEXT NOT NULL, model_id TEXT NOT NULL, purpose TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1, is_default INTEGER NOT NULL DEFAULT 0,
      secret TEXT, options TEXT NOT NULL DEFAULT '{}', last_test_at TEXT, last_test_ok INTEGER,
      last_test_error TEXT, vision_verified INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS ai_model_default ON ai_models(category) WHERE is_default=1;
    CREATE TABLE IF NOT EXISTS ai_model_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
