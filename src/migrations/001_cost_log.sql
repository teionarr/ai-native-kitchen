-- Cost log — one row per authenticated request, joinable by skill_id for per-skill
-- daily-cap enforcement and operational analytics ("which skill burned how much").
--
-- Indexed on (skill_id, ts) because the hot read pattern is "sum cost for this skill
-- since midnight UTC" (the daily-cap check on every request).
--
-- Idempotent — safe to run on every container start. Future migrations follow the
-- 00N_<short-name>.sql convention.

CREATE TABLE IF NOT EXISTS cost_log (
    id                   BIGSERIAL PRIMARY KEY,
    skill_id             TEXT        NOT NULL,
    endpoint             TEXT        NOT NULL,
    status_code          INTEGER     NOT NULL,
    duration_ms          INTEGER     NOT NULL,
    cache_hit            BOOLEAN     NOT NULL DEFAULT FALSE,
    upstream_cost_usd    NUMERIC(10, 4) NOT NULL DEFAULT 0,
    request_id           TEXT,
    ts                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Hot path: SELECT SUM(upstream_cost_usd) FROM cost_log WHERE skill_id=$1 AND ts > $2
CREATE INDEX IF NOT EXISTS cost_log_skill_ts ON cost_log (skill_id, ts);

-- For operator queries / 90-day retention pruning
CREATE INDEX IF NOT EXISTS cost_log_ts ON cost_log (ts);
