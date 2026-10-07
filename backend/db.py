import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_scans (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS batch_id integer;
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS batch_no text;

CREATE TABLE IF NOT EXISTS batches (
    id serial PRIMARY KEY,
    batch_no text NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'pending',
    note text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    inspected_by text,
    inspected_at timestamptz,
    voided_by text,
    voided_at timestamptz,
    CONSTRAINT batches_status_chk CHECK (status IN ('pending', 'inspected', 'voided'))
);

CREATE TABLE IF NOT EXISTS batch_bindings (
    id serial PRIMARY KEY,
    batch_id integer NOT NULL UNIQUE REFERENCES batches(id),
    string_code text NOT NULL,
    bound_by text NOT NULL,
    bound_at timestamptz NOT NULL
);

-- 单据入队闸门：批次必须存在、已通过抽检（不能是待抽检/已作废），
-- 且绑定的组串与单据组串一致；批次号在写入瞬间冻进单据行。
CREATE OR REPLACE FUNCTION gate_iv_scan_insert() RETURNS trigger AS $$
DECLARE
    b_status text;
    b_no text;
    b_string text;
BEGIN
    IF NEW.batch_id IS NULL THEN
        RAISE EXCEPTION '组串没有完成到货抽检就不能入队';
    END IF;
    SELECT status, batch_no INTO b_status, b_no FROM batches WHERE id = NEW.batch_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION '批次不存在';
    END IF;
    IF b_status = 'pending' THEN
        RAISE EXCEPTION '批次 % 尚未完成到货抽检，不能入队', b_no;
    END IF;
    IF b_status = 'voided' THEN
        RAISE EXCEPTION '批次 % 已作废，不能入队', b_no;
    END IF;
    SELECT string_code INTO b_string FROM batch_bindings WHERE batch_id = NEW.batch_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION '批次 % 还没有绑定组串，不能入队', b_no;
    END IF;
    IF b_string <> NEW.string_code THEN
        RAISE EXCEPTION '批次 % 绑定的是 %，不能开在 % 的单据上', b_no, b_string, NEW.string_code;
    END IF;
    NEW.batch_no := b_no;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_gate ON iv_scans;
CREATE TRIGGER trg_iv_scan_gate
BEFORE INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION gate_iv_scan_insert();

CREATE OR REPLACE FUNCTION notify_iv_scan() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('iv_scan_new', NEW.id::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_notify ON iv_scans;
CREATE TRIGGER trg_iv_scan_notify
AFTER INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION notify_iv_scan();
"""
