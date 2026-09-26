-- =============================================================================
-- queries.sql
-- Pauseiro & Vance Legal Group - demonstration queries against firm_finance.db
--
-- Grouped to show, in order: joins, aggregations, window functions, CTEs
-- (including a recursive one), and a full intercompany-elimination
-- consolidation query. Every query below has been run against the generated
-- database and returns results with no errors.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. JOIN + AGGREGATION
--    Trial balance by entity: net movement per account, current open month.
-- -----------------------------------------------------------------------------
SELECT
    e.entity_code,
    e.entity_name,
    a.account_code,
    a.account_name,
    a.account_type,
    ROUND(SUM(f.debit - f.credit), 2) AS net_movement
FROM fact_gl_transactions f
JOIN dim_entity  e ON e.entity_id  = f.entity_id
JOIN dim_account a ON a.account_id = f.account_id
JOIN dim_date    d ON d.date_id    = f.date_id
WHERE d.year = 2025 AND d.month = 6
GROUP BY e.entity_code, e.entity_name, a.account_code, a.account_name, a.account_type
ORDER BY e.entity_code, a.account_code;


-- -----------------------------------------------------------------------------
-- 2. AGGREGATION
--    Monthly revenue and operating expense by entity, full 18-month history.
-- -----------------------------------------------------------------------------
SELECT
    e.entity_code,
    d.year,
    d.month,
    ROUND(SUM(CASE WHEN a.account_type = 'Revenue' AND a.is_intercompany = 0
                    THEN f.credit - f.debit ELSE 0 END), 2) AS revenue,
    ROUND(SUM(CASE WHEN a.account_type IN ('COGS','Opex')
                    THEN f.debit - f.credit ELSE 0 END), 2) AS total_expense
FROM fact_gl_transactions f
JOIN dim_entity  e ON e.entity_id  = f.entity_id
JOIN dim_account a ON a.account_id = f.account_id
JOIN dim_date    d ON d.date_id    = f.date_id
GROUP BY e.entity_code, d.year, d.month
ORDER BY e.entity_code, d.year, d.month;


-- -----------------------------------------------------------------------------
-- 3. WINDOW FUNCTION - running total
--    Cumulative cash movement per entity across the full history (a quick
--    proxy for the operating cash trend feeding the bank reconciliation).
-- -----------------------------------------------------------------------------
SELECT
    entity_code,
    year, month,
    monthly_net_cash,
    ROUND(SUM(monthly_net_cash) OVER (
        PARTITION BY entity_code ORDER BY year, month
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ), 2) AS running_cash_balance
FROM (
    SELECT e.entity_code, d.year, d.month,
           ROUND(SUM(f.debit - f.credit), 2) AS monthly_net_cash
    FROM fact_gl_transactions f
    JOIN dim_entity e ON e.entity_id = f.entity_id
    JOIN dim_date   d ON d.date_id   = f.date_id
    WHERE f.account_id = 1  -- Cash - Operating
    GROUP BY e.entity_code, d.year, d.month
) monthly
ORDER BY entity_code, year, month;


-- -----------------------------------------------------------------------------
-- 4. WINDOW FUNCTION - LAG()
--    Month-over-month revenue variance by entity (FP&A flux analysis).
-- -----------------------------------------------------------------------------
WITH monthly_revenue AS (
    SELECT e.entity_code, d.year, d.month,
           ROUND(SUM(f.credit - f.debit), 2) AS revenue
    FROM fact_gl_transactions f
    JOIN dim_entity  e ON e.entity_id  = f.entity_id
    JOIN dim_account a ON a.account_id = f.account_id
    JOIN dim_date    d ON d.date_id    = f.date_id
    WHERE a.account_type = 'Revenue' AND a.is_intercompany = 0
    GROUP BY e.entity_code, d.year, d.month
)
SELECT
    entity_code, year, month, revenue,
    LAG(revenue) OVER (PARTITION BY entity_code ORDER BY year, month) AS prior_month_revenue,
    ROUND(revenue - LAG(revenue) OVER (PARTITION BY entity_code ORDER BY year, month), 2) AS mom_change,
    ROUND(100.0 * (revenue - LAG(revenue) OVER (PARTITION BY entity_code ORDER BY year, month))
          / NULLIF(LAG(revenue) OVER (PARTITION BY entity_code ORDER BY year, month), 0), 1) AS mom_pct_change
FROM monthly_revenue
ORDER BY entity_code, year, month;


-- -----------------------------------------------------------------------------
-- 5. WINDOW FUNCTION - RANK()
--    Largest expense accounts by entity in the open close month.
-- -----------------------------------------------------------------------------
SELECT *
FROM (
    SELECT
        e.entity_code,
        a.account_name,
        ROUND(SUM(f.debit - f.credit), 2) AS expense_amount,
        RANK() OVER (PARTITION BY e.entity_code ORDER BY SUM(f.debit - f.credit) DESC) AS expense_rank
    FROM fact_gl_transactions f
    JOIN dim_entity  e ON e.entity_id  = f.entity_id
    JOIN dim_account a ON a.account_id = f.account_id
    JOIN dim_date    d ON d.date_id    = f.date_id
    WHERE a.account_type IN ('COGS', 'Opex') AND d.year = 2025 AND d.month = 6
    GROUP BY e.entity_code, a.account_name
)
WHERE expense_rank <= 5
ORDER BY entity_code, expense_rank;


-- -----------------------------------------------------------------------------
-- 6. WINDOW FUNCTION - moving average
--    3-month rolling average Group revenue (smooths month-to-month noise).
-- -----------------------------------------------------------------------------
WITH group_monthly_revenue AS (
    SELECT d.year, d.month,
           ROUND(SUM(f.credit - f.debit), 2) AS revenue
    FROM fact_gl_transactions f
    JOIN dim_account a ON a.account_id = f.account_id
    JOIN dim_date    d ON d.date_id    = f.date_id
    WHERE a.account_type = 'Revenue' AND a.is_intercompany = 0
    GROUP BY d.year, d.month
)
SELECT
    year, month, revenue,
    ROUND(AVG(revenue) OVER (
        ORDER BY year, month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
    ), 2) AS revenue_3mo_moving_avg
FROM group_monthly_revenue
ORDER BY year, month;


-- -----------------------------------------------------------------------------
-- 7. CTE (multi-step) - bank reconciliation working paper
--    Book (GL) cash balance vs. bank statement balance for the open month,
--    with the reconciling items broken out, per entity.
-- -----------------------------------------------------------------------------
WITH gl_cash AS (
    SELECT e.entity_code, e.entity_id,
           ROUND(SUM(f.debit - f.credit), 2) AS book_cash_movement
    FROM fact_gl_transactions f
    JOIN dim_entity e ON e.entity_id = f.entity_id
    JOIN dim_date   d ON d.date_id   = f.date_id
    WHERE f.account_id = 1 AND d.year = 2025 AND d.month = 6
    GROUP BY e.entity_code, e.entity_id
),
bank_cash AS (
    SELECT e.entity_code, e.entity_id,
           ROUND(SUM(b.amount), 2) AS bank_statement_movement
    FROM fact_bank_transactions b
    JOIN dim_entity e ON e.entity_id = b.entity_id
    JOIN dim_date   d ON d.date_id   = b.date_id
    WHERE d.year = 2025 AND d.month = 6
    GROUP BY e.entity_code, e.entity_id
),
outstanding_items AS (
    SELECT f.entity_id,
           ROUND(SUM(f.debit - f.credit), 2) AS gl_items_not_on_bank
    FROM fact_gl_transactions f
    LEFT JOIN fact_bank_transactions b ON b.matched_gl_txn_id = f.txn_id
    JOIN dim_date d ON d.date_id = f.date_id
    WHERE f.account_id = 1 AND d.year = 2025 AND d.month = 6 AND b.bank_txn_id IS NULL
    GROUP BY f.entity_id
),
unmatched_bank_items AS (
    SELECT b.entity_id,
           ROUND(SUM(b.amount), 2) AS bank_items_not_in_gl
    FROM fact_bank_transactions b
    WHERE b.matched_gl_txn_id IS NULL
    GROUP BY b.entity_id
)
SELECT
    g.entity_code,
    g.book_cash_movement,
    COALESCE(u.bank_items_not_in_gl, 0) AS bank_items_not_yet_booked,
    ROUND(g.book_cash_movement + COALESCE(u.bank_items_not_in_gl, 0), 2) AS adjusted_book_balance,
    bk.bank_statement_movement,
    COALESCE(o.gl_items_not_on_bank, 0) AS book_items_not_yet_on_bank,
    ROUND(bk.bank_statement_movement + COALESCE(o.gl_items_not_on_bank, 0), 2) AS adjusted_bank_balance
FROM gl_cash g
JOIN bank_cash bk ON bk.entity_id = g.entity_id
LEFT JOIN outstanding_items o ON o.entity_id = g.entity_id
LEFT JOIN unmatched_bank_items u ON u.entity_id = g.entity_id
ORDER BY g.entity_code;


-- -----------------------------------------------------------------------------
-- 8. RECURSIVE CTE - chart of accounts rollup
--    Rolls every leaf account up to its top-level P&L/BS category, however
--    many levels of hierarchy dim_account has. Demonstrates a controlling-
--    style "account family" rollup driven entirely by the self-referencing
--    parent_account_id rather than a hardcoded mapping table.
-- -----------------------------------------------------------------------------
WITH RECURSIVE account_rollup AS (
    SELECT account_id, account_code, account_name, parent_account_id,
           account_id AS top_level_account_id, account_name AS top_level_account_name
    FROM dim_account
    WHERE parent_account_id IS NULL

    UNION ALL

    SELECT a.account_id, a.account_code, a.account_name, a.parent_account_id,
           r.top_level_account_id, r.top_level_account_name
    FROM dim_account a
    JOIN account_rollup r ON a.parent_account_id = r.account_id
)
SELECT
    r.top_level_account_name,
    e.entity_code,
    ROUND(SUM(f.debit - f.credit), 2) AS net_movement
FROM fact_gl_transactions f
JOIN account_rollup r ON r.account_id = f.account_id
JOIN dim_entity e ON e.entity_id = f.entity_id
JOIN dim_date   d ON d.date_id   = f.date_id
WHERE d.year = 2025 AND d.month = 6
GROUP BY r.top_level_account_name, e.entity_code
ORDER BY r.top_level_account_name, e.entity_code;


-- -----------------------------------------------------------------------------
-- 9. CONSOLIDATION - Group P&L with intercompany elimination
--    Rolls all three entities up to Group level, then backs out the
--    intercompany shared-services revenue/expense so the Group P&L doesn't
--    overstate revenue and expense by the same intercompany amount.
-- -----------------------------------------------------------------------------
WITH entity_pl AS (
    SELECT
        a.account_type,
        a.is_intercompany,
        ROUND(SUM(CASE WHEN a.account_type = 'Revenue' THEN f.credit - f.debit
                        ELSE f.debit - f.credit END), 2) AS amount
    FROM fact_gl_transactions f
    JOIN dim_account a ON a.account_id = f.account_id
    JOIN dim_date    d ON d.date_id    = f.date_id
    WHERE a.account_type IN ('Revenue','COGS','Opex') AND d.year = 2025 AND d.month = 6
    GROUP BY a.account_type, a.is_intercompany
),
group_pl_before_elim AS (
    SELECT
        SUM(CASE WHEN account_type = 'Revenue' THEN amount ELSE 0 END) AS total_revenue,
        SUM(CASE WHEN account_type = 'COGS' THEN amount ELSE 0 END) AS total_cogs,
        SUM(CASE WHEN account_type = 'Opex' THEN amount ELSE 0 END) AS total_opex,
        SUM(CASE WHEN is_intercompany = 1 AND account_type = 'Revenue' THEN amount ELSE 0 END) AS ic_revenue,
        SUM(CASE WHEN is_intercompany = 1 AND account_type = 'Opex' THEN amount ELSE 0 END) AS ic_expense
    FROM entity_pl
)
SELECT
    ROUND(total_revenue, 2)                              AS group_revenue_before_elim,
    ROUND(ic_revenue, 2)                                  AS intercompany_elimination,
    ROUND(total_revenue - ic_revenue, 2)                  AS group_revenue_after_elim,
    ROUND(total_cogs, 2)                                  AS group_cogs,
    ROUND(total_opex, 2)                                  AS total_opex_before_elim,
    ROUND(ic_expense, 2)                                  AS opex_elimination,
    ROUND(total_opex - ic_expense, 2)                     AS group_opex_after_elim,
    ROUND((total_revenue - ic_revenue) - total_cogs - (total_opex - ic_expense), 2) AS group_operating_income
FROM group_pl_before_elim;


-- -----------------------------------------------------------------------------
-- 10. BUDGET VS ACTUAL - largest unfavorable variances, open month
-- -----------------------------------------------------------------------------
WITH actuals AS (
    SELECT f.entity_id, f.account_id, ROUND(SUM(f.debit - f.credit), 2) AS actual_amount
    FROM fact_gl_transactions f
    JOIN dim_date d ON d.date_id = f.date_id
    JOIN dim_account a ON a.account_id = f.account_id
    WHERE d.year = 2025 AND d.month = 6 AND a.account_type IN ('COGS','Opex')
    GROUP BY f.entity_id, f.account_id
)
SELECT
    e.entity_code,
    a.account_name,
    b.budget_amount,
    act.actual_amount,
    ROUND(act.actual_amount - b.budget_amount, 2) AS variance_over_budget,
    RANK() OVER (ORDER BY (act.actual_amount - b.budget_amount) DESC) AS overspend_rank
FROM fact_budget b
JOIN actuals act ON act.entity_id = b.entity_id AND act.account_id = b.account_id
JOIN dim_entity  e ON e.entity_id  = b.entity_id
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_date    d ON d.date_id    = b.date_id
WHERE d.year = 2025 AND d.month = 6
ORDER BY overspend_rank
LIMIT 10;
