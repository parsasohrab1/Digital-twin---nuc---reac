-- Nuclear Digital Twin - PostgreSQL Schema
-- Alerts, DSS logs, configuration, audit trail

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- System configuration (UI-RQ-08, UI-RQ-09)
CREATE TABLE IF NOT EXISTS system_config (
    id          SERIAL PRIMARY KEY,
    key         VARCHAR(128) UNIQUE NOT NULL,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_by  VARCHAR(64) DEFAULT 'system'
);

INSERT INTO system_config (key, value) VALUES
    ('safety_thresholds', '{"max_clad_temp_c": 620, "min_dnbr": 1.3, "max_roughness_rate": 0.001}'),
    ('pinn_lambda', '{"lambda_ns": 0.1, "lambda_energy": 0.1, "lambda_neutronics": 0.01}'),
    ('ekf_noise', '{"process_noise": 0.01, "measurement_noise": 0.05}')
ON CONFLICT (key) DO NOTHING;

-- Alerts (AL-01 to AL-05)
CREATE TABLE IF NOT EXISTS alerts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    level           VARCHAR(16) NOT NULL CHECK (level IN ('critical', 'high', 'medium', 'info')),
    message         TEXT NOT NULL,
    tte_sec         DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged    BOOLEAN DEFAULT FALSE,
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by VARCHAR(64),
    snoozed_until   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(acknowledged) WHERE acknowledged = FALSE;

-- DSS recommendations log (SAF-05 - WORM in production)
CREATE TABLE IF NOT EXISTS dss_recommendations (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    action              VARCHAR(128) NOT NULL,
    description         TEXT,
    safety_score        SMALLINT CHECK (safety_score BETWEEN 1 AND 5),
    success_probability DOUBLE PRECISION,
    operator_approved   BOOLEAN DEFAULT FALSE,
    approved_at         TIMESTAMPTZ,
    approved_by         VARCHAR(64),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    scenario_context    JSONB
);

CREATE INDEX IF NOT EXISTS idx_dss_created ON dss_recommendations(created_at DESC);

-- EKF parameter history (AD-06)
CREATE TABLE IF NOT EXISTS ekf_history (
    id              BIGSERIAL PRIMARY KEY,
    epsilon_fuel    DOUBLE PRECISION NOT NULL,
    r_f             DOUBLE PRECISION NOT NULL,
    k_spacer        DOUBLE PRECISION NOT NULL,
    t_coolant_out   DOUBLE PRECISION NOT NULL,
    innovation_norm DOUBLE PRECISION,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ekf_recorded ON ekf_history(recorded_at DESC);

-- Security audit log (SEC-04)
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    event_type  VARCHAR(64) NOT NULL,
    user_id     VARCHAR(64),
    details     JSONB,
    ip_address  INET,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);

-- Shutdown snapshot (NDT-SD-01, NDT-SD-03)
CREATE TABLE IF NOT EXISTS shutdown_snapshots (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ekf_params      JSONB NOT NULL,
    sensor_buffer   JSONB,
    summary_24h     JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
