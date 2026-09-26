-- =============================================================================
-- schema.sql
-- Pauseiro & Vance Legal Group - Multi-Office Consolidation Data Model
-- Star schema: 3 dimensions, 3 fact tables.
-- =============================================================================
-- DATA MODELING NOTES (grain, keys, fact/dim design)
--
-- Grain is declared explicitly on every fact table below because it is the
-- single most important design decision in a star schema: get it wrong and
-- every downstream SUM either double-counts or silently drops rows.
--
-- Surrogate integer keys (entity_id, account_id) are used on the dimensions
-- so that a hierarchy change (e.g. a branch office re-parented under a new
-- region) never requires rewriting fact rows. date_id is the ISO date string
-- itself (a "smart key") since dates never change identity - a common,
-- deliberate exception to the surrogate-key rule.
--
-- Both dim_account and dim_entity are self-referencing hierarchies
-- (parent_account_id / parent_entity_id), which lets the same table answer
-- both a detail query ("Litigation Fees for Chicago in March") and a rolled-
-- up query ("total Revenue for the Group") without a separate rollup table.
-- =============================================================================

DROP TABLE IF EXISTS fact_budget;
DROP TABLE IF EXISTS fact_bank_transactions;
DROP TABLE IF EXISTS fact_gl_transactions;
DROP TABLE IF EXISTS dim_date;
DROP TABLE IF EXISTS dim_account;
DROP TABLE IF EXISTS dim_entity;

-- -----------------------------------------------------------------------------
-- DIM_ENTITY  (one row per legal entity / office; self-referencing hierarchy
--              for Group consolidation)
-- -----------------------------------------------------------------------------
CREATE TABLE dim_entity (
    entity_id           INTEGER PRIMARY KEY,
    entity_code         TEXT NOT NULL UNIQUE,       -- e.g. 'MIA'
    entity_name         TEXT NOT NULL,
    city                TEXT,
    entity_type         TEXT NOT NULL,               -- 'Office'
    parent_entity_id    INTEGER REFERENCES dim_entity(entity_id),  -- NULL = Group HQ
    is_group_hq         INTEGER NOT NULL DEFAULT 0    -- 1 = bills shared services to branches
);

-- -----------------------------------------------------------------------------
-- DIM_ACCOUNT  (one row per chart-of-accounts line; self-referencing hierarchy
--               so P&L/BS rollups don't need a separate mapping table)
-- -----------------------------------------------------------------------------
CREATE TABLE dim_account (
    account_id          INTEGER PRIMARY KEY,
    account_code        TEXT NOT NULL UNIQUE,        -- e.g. '4010'
    account_name        TEXT NOT NULL,
    account_type        TEXT NOT NULL,                -- Asset/Liability/Equity/Revenue/COGS/Opex
    parent_account_id   INTEGER REFERENCES dim_account(account_id),
    is_intercompany      INTEGER NOT NULL DEFAULT 0,   -- 1 = must be eliminated on consolidation
    normal_balance       TEXT NOT NULL                 -- 'Debit' or 'Credit'
);

-- -----------------------------------------------------------------------------
-- DIM_DATE  (one row per calendar date that appears in a fact table)
-- -----------------------------------------------------------------------------
CREATE TABLE dim_date (
    date_id             TEXT PRIMARY KEY,             -- ISO date, e.g. '2025-06-30'
    year                INTEGER NOT NULL,
    month               INTEGER NOT NULL,
    month_name          TEXT NOT NULL,
    quarter             INTEGER NOT NULL,
    fiscal_period       TEXT NOT NULL,                 -- 'FY2025-P06'
    is_month_end        INTEGER NOT NULL DEFAULT 0
);

-- -----------------------------------------------------------------------------
-- FACT_GL_TRANSACTIONS
-- Grain: one row per general-ledger JOURNAL LINE (one debit or credit posting
--        to one account, in one entity, on one date). A single journal_id
--        groups the lines of one journal entry and always nets to zero
--        (sum(debit) = sum(credit)) - the double-entry integrity check.
-- Keys:  txn_id (PK, one physical posting) ; journal_id (groups the entry)
--        FKs: entity_id -> dim_entity, account_id -> dim_account,
--             date_id -> dim_date, counterparty_entity_id -> dim_entity
--             (populated only for intercompany lines, used to build the
--              elimination working paper)
-- -----------------------------------------------------------------------------
CREATE TABLE fact_gl_transactions (
    txn_id                  INTEGER PRIMARY KEY,
    journal_id              INTEGER NOT NULL,
    entity_id                INTEGER NOT NULL REFERENCES dim_entity(entity_id),
    account_id                INTEGER NOT NULL REFERENCES dim_account(account_id),
    date_id                    TEXT NOT NULL REFERENCES dim_date(date_id),
    debit                       REAL NOT NULL DEFAULT 0,
    credit                       REAL NOT NULL DEFAULT 0,
    description                   TEXT,
    source                          TEXT NOT NULL,        -- Billing / Cash Receipt / Payroll / Intercompany / Adjusting Entry ...
    counterparty_entity_id           INTEGER REFERENCES dim_entity(entity_id)
);
CREATE INDEX ix_gl_entity_date ON fact_gl_transactions(entity_id, date_id);
CREATE INDEX ix_gl_account ON fact_gl_transactions(account_id);
CREATE INDEX ix_gl_journal ON fact_gl_transactions(journal_id);

-- -----------------------------------------------------------------------------
-- FACT_BANK_TRANSACTIONS
-- Grain: one row per line on the monthly BANK STATEMENT for one entity.
-- Keys:  bank_txn_id (PK) ; FKs entity_id, date_id ;
--        matched_gl_txn_id -> fact_gl_transactions.txn_id (NULL = unmatched,
--        i.e. still an open reconciling item at month-end)
-- -----------------------------------------------------------------------------
CREATE TABLE fact_bank_transactions (
    bank_txn_id             INTEGER PRIMARY KEY,
    entity_id                INTEGER NOT NULL REFERENCES dim_entity(entity_id),
    date_id                    TEXT NOT NULL REFERENCES dim_date(date_id),
    amount                       REAL NOT NULL,          -- positive = deposit, negative = withdrawal
    description                   TEXT,
    category                        TEXT NOT NULL,        -- 'Matched' or 'Unmatched - Book Adjustment Needed'
    matched_gl_txn_id                 INTEGER REFERENCES fact_gl_transactions(txn_id)
);
CREATE INDEX ix_bank_entity_date ON fact_bank_transactions(entity_id, date_id);

-- -----------------------------------------------------------------------------
-- FACT_BUDGET
-- Grain: one row per (entity, account, month) - the FP&A budget grain is
--        deliberately coarser than the GL's transaction grain; date_id here
--        always holds the 1st of the month.
-- Keys:  budget_id (PK) ; natural key = (entity_id, account_id, date_id)
-- -----------------------------------------------------------------------------
CREATE TABLE fact_budget (
    budget_id             INTEGER PRIMARY KEY,
    entity_id               INTEGER NOT NULL REFERENCES dim_entity(entity_id),
    account_id                INTEGER NOT NULL REFERENCES dim_account(account_id),
    date_id                     TEXT NOT NULL REFERENCES dim_date(date_id),   -- month start
    budget_amount                 REAL NOT NULL,
    UNIQUE(entity_id, account_id, date_id)
);
