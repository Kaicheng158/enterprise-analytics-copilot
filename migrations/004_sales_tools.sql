CREATE SCHEMA analytics;
REVOKE ALL ON SCHEMA analytics FROM PUBLIC;

CREATE TABLE analytics.sales_orders (
    order_id uuid PRIMARY KEY,
    dataset_version text NOT NULL
        CHECK (dataset_version = 'synthetic-sales-db-v1'),
    period text NOT NULL CHECK (period ~ '^[0-9]{4}-Q[1-4]$'),
    region text NOT NULL CHECK (region IN ('north', 'south')),
    currency text NOT NULL CHECK (currency = 'USD'),
    amount_cents bigint NOT NULL CHECK (amount_cents >= 0),
    occurred_on date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON analytics.sales_orders FROM PUBLIC;
CREATE INDEX sales_orders_summary_filter
    ON analytics.sales_orders (dataset_version, period, region, currency);
