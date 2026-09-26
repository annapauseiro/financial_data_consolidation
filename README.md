# P&V Legal Group — Multi-Office Consolidation Portfolio Project

Built for roles in accounting, audit, controlling, or FP&A that specifically
call for: reconciliations, month-end close and consolidation; SQL (joins,
aggregations, window functions, CTEs); advanced Excel including Power Query;
and data modeling (grain, keys, fact and dimension tables). Every artifact
below exists to demonstrate one or more of those five requirements directly,
not as a generic "here's a spreadsheet" sample.

## Concept

A synthetic 3-office professional-services group — Pauseiro & Vance Legal
Group — with a Miami HQ (which bills shared services to the branches) plus
Chicago and Dallas offices. 18 months of history (Jan 2024 – Jun 2025), with
June 2025 modeled as the "open" month currently in close.

## Files

| File | What it is |
|---|---|
| `finance_consolidation_model.xlsx` | The Excel deliverable — see tabs below |
| `firm_finance.db` | SQLite database (star schema) built from the same data |
| `schema.sql` | DDL with grain/key documentation for every table |
| `queries.sql` | 10 queries: joins, aggregations, window functions, CTEs, a recursive CTE, and a full elimination-based consolidation query — all tested against `firm_finance.db` |
| `power_query_steps.md` | M code for the ETL that lands the 3 offices' source exports into the workbook's data tables |
| `generate_data.py` | Generates the synthetic dataset (double-entry journals, bank statement, budget) reproducibly |
| `source_data/` | Raw per-office "source system" exports (what Power Query connects to) |
| `data/` | Modeled (post-ETL) flat files — what Power Query lands, and what feeds both the SQLite load and the workbook |

## Workbook tabs

- **README** — same overview, inside the workbook.
- **Close_Checklist** — the month-end close task list for the open period, sequencing accruals, reconciliations, intercompany settlement, elimination entries, variance review, and period lock, with owners and due dates.
- **Trial_Balance** — net movement by account, by office and combined, live off `PQ_GL_Data` via `SUMIFS`.
- **Bank_Reconciliation** — book-to-bank tie-out per office: book balance plus unbooked bank items equals adjusted book balance; bank balance plus outstanding items equals adjusted bank balance. Both sides agree to the cent.
- **Consolidation** — the Group P&L: each office, the combined total, and the intercompany elimination for the HQ shared-services billing — including the (correct, and worth knowing) fact that eliminating it doesn't move Operating Income, since there's no markup on the intercompany charge; it only corrects the presented revenue and expense totals.
- **Budget_vs_Actual** — variance by office/account for the open month, ranked by overspend.
- **Dashboard** — Group KPI tiles and trend charts.
- **PQ_* tabs** — the raw, ETL-landed data everything above is formula-driven from. Nothing upstream is a hardcoded number.

## How this maps to the five requirements

**Accounting / audit / controlling / FP&A** — the whole workbook is one close
cycle: accruals booked and reversed, a checklist that sequences the work,
budget-vs-actual review, and a Group P&L a controller would actually sign off
on.

**Reconciliations, month-end close & consolidation** — `Bank_Reconciliation`
(a real book-to-bank tie-out, not just a balance comparison), the
`Close_Checklist` (task sequencing and status, including a deliberately
"Blocked" item to show the workbook reflects a close in progress, not a
finished demo), and `Consolidation` (multi-entity roll-up with an
intercompany elimination and the reasoning behind it).

**SQL — joins, aggregations, window functions, CTEs** — `queries.sql`:
trial-balance joins (query 1), monthly aggregation (2), a running-total
window function (3), `LAG()` for MoM variance (4), `RANK()` for top expenses
(5), a moving average (6), a multi-step CTE bank-rec working paper (7), a
**recursive CTE** rolling the chart of accounts up through however many
levels it has (8), and a CTE-driven consolidation query with intercompany
elimination (9), plus a budget-variance ranking query (10). Every query is
tested and returns results with no errors.

**Advanced Excel including Power Query** — `SUMIFS` and structured
references throughout, Excel Tables, conditional formatting (close-task
status, bank-rec tie-out, budget-variance color scale), native charts, and
zero hardcoded results — everything traces back to the `PQ_*` tables.
`power_query_steps.md` documents the M code that lands the three offices'
source exports into those tables (folder-style combine, type conversion,
dimension merges, calculated columns).

**Data modeling — grain, keys, fact & dimension tables** — `schema.sql`:
`dim_entity` and `dim_account` are self-referencing hierarchies (so Group
roll-ups and chart-of-accounts roll-ups don't need a separate mapping
table), and every fact table's grain and keys are documented inline —
`fact_gl_transactions` (one row per journal line), `fact_bank_transactions`
(one row per bank statement line), `fact_budget` (one row per entity ×
account × month, deliberately coarser than the GL's grain).

## 👩‍💻 About Me

Hey there! I’m Anna Pauseiro, an IT professional passionate about data and the stories we can tell through it.
