-- Additive, idempotent migration; existing product/image data are untouched.
CREATE TABLE IF NOT EXISTS visual_analysis_jobs(
      id TEXT PRIMARY KEY, market TEXT NOT NULL, product_id TEXT NOT NULL, product_name TEXT NOT NULL,
      request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'analysis',
      model_config_id TEXT NOT NULL, model TEXT NOT NULL, model_signature TEXT NOT NULL, mode TEXT NOT NULL,
      prompt_version TEXT NOT NULL, prompt_digest TEXT NOT NULL, target_model TEXT NOT NULL,
      image_count INTEGER NOT NULL, source_images TEXT NOT NULL, batch_size INTEGER NOT NULL,
      stream INTEGER NOT NULL, state TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
      result TEXT, usage TEXT, error TEXT, cached_from TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS visual_jobs_product ON visual_analysis_jobs(market,product_id,created_at DESC);
    CREATE INDEX IF NOT EXISTS visual_jobs_cache ON visual_analysis_jobs(fingerprint,state);
    CREATE TABLE IF NOT EXISTS visual_analysis_batches(
      id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES visual_analysis_jobs(id), position INTEGER NOT NULL,
      image_ids TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', result TEXT, usage TEXT, error TEXT,
      UNIQUE(job_id,position));
    CREATE TABLE IF NOT EXISTS visual_api_calls(
      id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES visual_analysis_jobs(id), batch_id INTEGER,
      attempt INTEGER NOT NULL, model TEXT NOT NULL, endpoint TEXT NOT NULL DEFAULT '/chat/completions',
      status INTEGER, request_id TEXT, outcome TEXT NOT NULL, usage TEXT, error TEXT, duration_ms INTEGER,
      created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS visual_prompt_versions(
      id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES visual_analysis_jobs(id),
      task_type TEXT NOT NULL, prompt_en TEXT NOT NULL, prompt_zh TEXT NOT NULL, negative_prompt TEXT NOT NULL,
      created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS visual_generation_links(
      id INTEGER PRIMARY KEY, analysis_id TEXT NOT NULL REFERENCES visual_analysis_jobs(id),
      prompt_version_id INTEGER NOT NULL REFERENCES visual_prompt_versions(id),
      generation_id TEXT NOT NULL REFERENCES innovation_jobs(id), created_at TEXT NOT NULL);
