WITH inserted AS (
    INSERT INTO analytics.sales_orders (
        order_id, dataset_version, period, region, currency, amount_cents, occurred_on
    ) VALUES
        ('00000000-0000-4000-8000-000000000101', 'synthetic-sales-db-v1', '2026-Q1', 'north', 'USD', 12000, '2026-01-15'),
        ('00000000-0000-4000-8000-000000000102', 'synthetic-sales-db-v1', '2026-Q1', 'north', 'USD',  8000, '2026-02-10'),
        ('00000000-0000-4000-8000-000000000103', 'synthetic-sales-db-v1', '2026-Q1', 'south', 'USD', 15000, '2026-02-20'),
        ('00000000-0000-4000-8000-000000000104', 'synthetic-sales-db-v1', '2026-Q1', 'south', 'USD',  5000, '2026-03-05'),
        ('00000000-0000-4000-8000-000000000201', 'synthetic-sales-db-v1', '2026-Q2', 'north', 'USD', 18000, '2026-04-15'),
        ('00000000-0000-4000-8000-000000000202', 'synthetic-sales-db-v1', '2026-Q2', 'north', 'USD', 12000, '2026-05-10'),
        ('00000000-0000-4000-8000-000000000203', 'synthetic-sales-db-v1', '2026-Q2', 'south', 'USD', 16000, '2026-05-20'),
        ('00000000-0000-4000-8000-000000000204', 'synthetic-sales-db-v1', '2026-Q2', 'south', 'USD', 14000, '2026-06-05')
    ON CONFLICT (order_id) DO NOTHING
    RETURNING 1
)
SELECT count(*)::integer AS inserted_rows FROM inserted;
