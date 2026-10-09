-- ===========================================================================
-- schema.sql  —  Silver / Gold Layer: Egypt Legal Data Warehouse
--
-- PostgreSQL 15+
--
-- Features:
--   • Arabic full-text search via to_tsvector() with 'simple' config
--     (Arabic stemmer support requires pg_diacritics / custom dict;
--      'simple' works reliably for exact term matching without stemming)
--   • GIN indexes on tsvector columns for fast FTS queries
--   • Composite UNIQUE constraint on (law_number, year) for idempotent inserts
--   • Audit timestamps (created_at, updated_at) on all tables
--   • Enumerated types for category and status to enforce referential integrity
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 0. Extensions
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";       -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;           -- trigram similarity search
CREATE EXTENSION IF NOT EXISTS unaccent;          -- strip Arabic diacritics

-- ---------------------------------------------------------------------------
-- 1. Enumerated Types
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    -- Law status
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'law_status') THEN
        CREATE TYPE law_status AS ENUM ('ساري', 'ملغي', 'معدل', 'موقوف');
    END IF;

    -- Law category
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'law_category') THEN
        CREATE TYPE law_category AS ENUM (
            'قانون',          -- Statute / Law
            'مرسوم',          -- Decree
            'قرار',           -- Decision / Resolution
            'لائحة',          -- Regulation
            'دستور',          -- Constitution
            'اتفاقية',        -- Convention / Treaty
            'أخرى'            -- Other
        );
    END IF;
END;
$$;

-- ---------------------------------------------------------------------------
-- 2. Laws Table  (Silver Layer)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS laws (
    law_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    law_number          TEXT        NOT NULL,
    year                SMALLINT    NOT NULL CHECK (year BETWEEN 1800 AND 2100),
    title               TEXT        NOT NULL,
    category            law_category NOT NULL DEFAULT 'قانون',
    status              law_status  NOT NULL DEFAULT 'ساري',
    issue_date          DATE,
    source_url          TEXT,
    pdf_gcs_uri         TEXT,                  -- gs://egypt-laws-raw-data/raw/pdf/...
    json_gcs_uri        TEXT,                  -- gs://egypt-laws-raw-data/raw/json/...
    raw_text            TEXT,                  -- full raw extracted text
    -- Full-text search vector (auto-updated via trigger below)
    fts_title           TSVECTOR GENERATED ALWAYS AS (
                            to_tsvector('simple', coalesce(title, ''))
                        ) STORED,
    fts_body            TSVECTOR,              -- updated by trigger (too large for GENERATED)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Incremental ingestion guard
    CONSTRAINT uq_law_number_year UNIQUE (law_number, year)
);

COMMENT ON TABLE  laws IS 'Master registry of Egyptian Laws and Decrees (Silver Layer).';
COMMENT ON COLUMN laws.law_id      IS 'Surrogate primary key (UUID v4).';
COMMENT ON COLUMN laws.law_number  IS 'Official law number as published (e.g. "12").';
COMMENT ON COLUMN laws.year        IS 'Gregorian year of publication.';
COMMENT ON COLUMN laws.fts_title   IS 'tsvector for fast Arabic title search (GIN indexed).';
COMMENT ON COLUMN laws.fts_body    IS 'tsvector for full raw-text search (GIN indexed).';

-- ---------------------------------------------------------------------------
-- 3. Articles Table  (Silver Layer)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS articles (
    article_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    law_id              UUID        NOT NULL REFERENCES laws(law_id) ON DELETE CASCADE,
    article_number      TEXT        NOT NULL,   -- "1", "2", "الأولى", …
    content             TEXT        NOT NULL,
    -- Full-text search vector
    fts_content         TSVECTOR GENERATED ALWAYS AS (
                            to_tsvector('simple', coalesce(content, ''))
                        ) STORED,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_article_law_number UNIQUE (law_id, article_number)
);

COMMENT ON TABLE  articles IS 'Individual articles parsed from each law (Silver Layer).';
COMMENT ON COLUMN articles.article_number IS 'Article ordinal as found in the source text.';
COMMENT ON COLUMN articles.fts_content    IS 'tsvector for Arabic article-level FTS (GIN indexed).';

-- ---------------------------------------------------------------------------
-- 4. Scrape Log Table  (Operational)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scrape_log (
    log_id              BIGSERIAL   PRIMARY KEY,
    run_date            DATE        NOT NULL DEFAULT CURRENT_DATE,
    dag_run_id          TEXT,
    laws_checked        INT         NOT NULL DEFAULT 0,
    laws_new            INT         NOT NULL DEFAULT 0,
    laws_failed         INT         NOT NULL DEFAULT 0,
    duration_seconds    NUMERIC(10, 2),
    status              TEXT        NOT NULL DEFAULT 'running',  -- running | success | failed
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE scrape_log IS 'Operational log of each pipeline DAG run.';

-- ---------------------------------------------------------------------------
-- 5. Indexes
-- ---------------------------------------------------------------------------

-- GIN full-text indexes (required for fast @@ queries)
CREATE INDEX IF NOT EXISTS idx_laws_fts_title
    ON laws USING GIN (fts_title);

CREATE INDEX IF NOT EXISTS idx_laws_fts_body
    ON laws USING GIN (fts_body);

CREATE INDEX IF NOT EXISTS idx_articles_fts_content
    ON articles USING GIN (fts_content);

-- Trigram index for fuzzy / LIKE search on titles
CREATE INDEX IF NOT EXISTS idx_laws_title_trgm
    ON laws USING GIN (title gin_trgm_ops);

-- B-tree indexes for common filter patterns
CREATE INDEX IF NOT EXISTS idx_laws_year        ON laws (year);
CREATE INDEX IF NOT EXISTS idx_laws_category    ON laws (category);
CREATE INDEX IF NOT EXISTS idx_laws_status      ON laws (status);
CREATE INDEX IF NOT EXISTS idx_laws_issue_date  ON laws (issue_date);
CREATE INDEX IF NOT EXISTS idx_articles_law_id  ON articles (law_id);

-- ---------------------------------------------------------------------------
-- 6. Triggers — auto-update updated_at & fts_body
-- ---------------------------------------------------------------------------

-- 6a. updated_at trigger function (reusable)
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_laws_updated_at
    BEFORE UPDATE ON laws
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 6b. fts_body trigger — recompute tsvector when raw_text changes
CREATE OR REPLACE FUNCTION update_laws_fts_body()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.fts_body = to_tsvector('simple', coalesce(NEW.raw_text, ''));
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_laws_fts_body
    BEFORE INSERT OR UPDATE OF raw_text ON laws
    FOR EACH ROW EXECUTE FUNCTION update_laws_fts_body();

-- ---------------------------------------------------------------------------
-- 7. Gold Layer Views
-- ---------------------------------------------------------------------------

-- 7a. Active laws only
CREATE OR REPLACE VIEW v_active_laws AS
SELECT
    law_id, law_number, year, title, category,
    issue_date, source_url, pdf_gcs_uri, created_at
FROM laws
WHERE status = 'ساري';

-- 7b. Laws with article count
CREATE OR REPLACE VIEW v_laws_summary AS
SELECT
    l.law_id,
    l.law_number,
    l.year,
    l.title,
    l.category,
    l.status,
    l.issue_date,
    COUNT(a.article_id) AS article_count,
    l.created_at
FROM laws l
LEFT JOIN articles a USING (law_id)
GROUP BY l.law_id;

-- 7c. Full-text search helper view
-- Usage: SELECT * FROM v_law_search WHERE fts_query @@ to_tsquery('simple', 'عمل');
CREATE OR REPLACE VIEW v_law_search AS
SELECT
    l.law_id,
    l.law_number,
    l.year,
    l.title,
    l.category,
    l.status,
    l.fts_title || l.fts_body AS fts_query,
    l.source_url
FROM laws l;

-- ---------------------------------------------------------------------------
-- 8. Helper Stored Procedures
-- ---------------------------------------------------------------------------

-- 8a. Upsert a law record (insert or update on conflict)
CREATE OR REPLACE PROCEDURE upsert_law(
    p_law_number    TEXT,
    p_year          SMALLINT,
    p_title         TEXT,
    p_category      TEXT,
    p_status        TEXT,
    p_issue_date    DATE,
    p_source_url    TEXT,
    p_pdf_gcs_uri   TEXT,
    p_json_gcs_uri  TEXT,
    p_raw_text      TEXT,
    OUT p_law_id    UUID
)
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO laws (
        law_number, year, title, category, status,
        issue_date, source_url, pdf_gcs_uri, json_gcs_uri, raw_text
    )
    VALUES (
        p_law_number, p_year, p_title,
        p_category::law_category, p_status::law_status,
        p_issue_date, p_source_url, p_pdf_gcs_uri, p_json_gcs_uri, p_raw_text
    )
    ON CONFLICT (law_number, year) DO UPDATE
        SET title          = EXCLUDED.title,
            category       = EXCLUDED.category,
            status         = EXCLUDED.status,
            issue_date     = EXCLUDED.issue_date,
            source_url     = EXCLUDED.source_url,
            pdf_gcs_uri    = EXCLUDED.pdf_gcs_uri,
            json_gcs_uri   = EXCLUDED.json_gcs_uri,
            raw_text       = EXCLUDED.raw_text,
            updated_at     = now()
    RETURNING law_id INTO p_law_id;
END;
$$;

-- 8b. Upsert an article record
CREATE OR REPLACE PROCEDURE upsert_article(
    p_law_id        UUID,
    p_article_number TEXT,
    p_content       TEXT
)
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO articles (law_id, article_number, content)
    VALUES (p_law_id, p_article_number, p_content)
    ON CONFLICT (law_id, article_number) DO UPDATE
        SET content = EXCLUDED.content;
END;
$$;

-- ---------------------------------------------------------------------------
-- 9. Sample Full-Text Search Queries (documentation)
-- ---------------------------------------------------------------------------
/*
-- Search laws whose title or body contains the word 'عمل' (labour):
SELECT law_id, law_number, year, title
FROM   laws
WHERE  fts_title || fts_body @@ to_tsquery('simple', 'عمل')
ORDER BY ts_rank(fts_title || fts_body, to_tsquery('simple', 'عمل')) DESC;

-- Search articles containing 'إجازة' (leave/vacation):
SELECT a.article_id, l.law_number, l.year, a.article_number, a.content
FROM   articles a
JOIN   laws l USING (law_id)
WHERE  a.fts_content @@ to_tsquery('simple', 'إجازة')
ORDER BY ts_rank(a.fts_content, to_tsquery('simple', 'إجازة')) DESC
LIMIT 20;

-- Trigram fuzzy search on law titles (handles typos):
SELECT law_number, year, title,
       similarity(title, 'قانون العمل') AS sim
FROM   laws
WHERE  title % 'قانون العمل'
ORDER BY sim DESC;
*/
