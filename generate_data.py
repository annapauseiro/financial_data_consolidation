"""
generate_data.py
-----------------
Builds the synthetic dataset for the P&V Legal Group multi-office
consolidation portfolio project.

Produces:
  - source_data/entity_<code>_gl_export.csv   (raw "source system" exports, one per entity -
                                                 these are what Power Query pulls from)
  - source_data/bank_statement_<code>.csv       (raw bank statement export per entity)
  - source_data/budget_<code>.csv                (raw budget export per entity)
  - firm_finance.db                              (SQLite star schema, loaded from the above)
  - schema tables also exported as consolidated CSVs under /data for convenience

Scenario
--------
Pauseiro & Vance Legal Group: a 3-office professional-services group.
  - MIA  P&V Miami      (Group HQ - bills shared services to the branches)
  - CHI  P&V Chicago     (branch)
  - DAL  P&V Dallas      (branch)

18 months of history: Jan 2024 - Jun 2025 (Jun 2025 is the "open" month-end close).
Double-entry journals with realistic AP/payroll accrual lag, an intercompany
shared-services billing that must be eliminated on consolidation, and bank
reconciling items seeded into the most recent month only.
"""

import csv
import os
import random
import sqlite3
from datetime import date, timedelta
from calendar import monthrange

random.seed(42)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "source_data")
DB_PATH = os.path.join(BASE_DIR, "firm_finance.db")
os.makedirs(SRC_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# 1. DIMENSIONS
# ---------------------------------------------------------------------------

ENTITIES = [
    # entity_id, code, name,              city,      entity_type, parent_entity_id, is_group_hq
    (1, "MIA", "P&V Miami (HQ)", "Miami, FL", "Office", None, 1),
    (2, "CHI", "P&V Chicago", "Chicago, IL", "Office", 1, 0),
    (3, "DAL", "P&V Dallas", "Dallas, TX", "Office", 1, 0),
]

# account_id, code, name, account_type, parent_account_id, is_intercompany, normal_balance
ACCOUNTS = [
    # --- Balance sheet ---
    (1, "1000", "Cash - Operating", "Asset", None, 0, "Debit"),
    (2, "1100", "Accounts Receivable", "Asset", None, 0, "Debit"),
    (3, "1200", "Prepaid Expenses", "Asset", None, 0, "Debit"),
    (4, "1300", "Intercompany Receivable", "Asset", None, 1, "Debit"),
    (5, "1500", "Fixed Assets, Net", "Asset", None, 0, "Debit"),
    (6, "2000", "Accounts Payable", "Liability", None, 0, "Credit"),
    (7, "2100", "Accrued Payroll", "Liability", None, 0, "Credit"),
    (8, "2200", "Intercompany Payable", "Liability", None, 1, "Credit"),
    (9, "3000", "Owner's Equity / Retained Earnings", "Equity", None, 0, "Credit"),
    # --- Revenue ---
    (10, "4000", "Legal Fee Revenue", "Revenue", None, 0, "Credit"),
    (11, "4010", "Litigation Fees", "Revenue", 10, 0, "Credit"),
    (12, "4020", "Corporate & Transactional Fees", "Revenue", 10, 0, "Credit"),
    (13, "4030", "Family Law Fees", "Revenue", 10, 0, "Credit"),
    (14, "4090", "Intercompany Shared Services Revenue", "Revenue", None, 1, "Credit"),
    # --- Direct costs ---
    (15, "5000", "Direct Case Costs", "COGS", None, 0, "Debit"),
    (16, "5010", "Court Filing Fees", "COGS", 15, 0, "Debit"),
    (17, "5020", "Expert Witness Fees", "COGS", 15, 0, "Debit"),
    # --- Operating expenses ---
    (18, "6000", "Operating Expenses", "Opex", None, 0, "Debit"),
    (19, "6010", "Attorney & Staff Payroll", "Opex", 18, 0, "Debit"),
    (20, "6020", "Payroll Taxes & Benefits", "Opex", 18, 0, "Debit"),
    (21, "6030", "Office Rent", "Opex", 18, 0, "Debit"),
    (22, "6040", "Marketing & Business Development", "Opex", 18, 0, "Debit"),
    (23, "6050", "Professional Insurance", "Opex", 18, 0, "Debit"),
    (24, "6060", "Software & IT", "Opex", 18, 0, "Debit"),
    (25, "6070", "Shared Services Allocation (Intercompany)", "Opex", 18, 1, "Debit"),
    (26, "7000", "Depreciation Expense", "Opex", None, 0, "Debit"),
    (27, "8000", "Interest Expense", "Opex", None, 0, "Debit"),
]

MONTHS = []
y, m = 2024, 1
for _ in range(18):  # Jan 2024 - Jun 2025
    MONTHS.append((y, m))
    m += 1
    if m > 12:
        m = 1
        y += 1
CURRENT_MONTH = MONTHS[-1]  # (2025, 6) - the month "in close"

REV_ACCTS = [11, 12, 13]
COGS_ACCTS = [16, 17]

# Entity revenue scale (Miami HQ is the largest office)
ENTITY_SCALE = {1: 1.00, 2: 0.62, 3: 0.55}


def month_end(y, m):
    return date(y, m, monthrange(y, m)[1])


def biz_day(y, m, target_day):
    """Nearest business day to target_day, WITHOUT crossing into another month
    (rolls forward when there's room in the month, otherwise backward)."""
    last_day = monthrange(y, m)[1]
    d = min(target_day, last_day)
    dt = date(y, m, d)
    while dt.weekday() >= 5:
        if dt.day < last_day:
            dt += timedelta(days=1)
        else:
            dt -= timedelta(days=1)
    return dt


# ---------------------------------------------------------------------------
# 2. GL TRANSACTION GENERATION
# ---------------------------------------------------------------------------

gl_rows = []          # (txn_id, journal_id, entity_id, account_id, date, debit, credit, description, source, counterparty_entity_id)
budget_rows = []       # (entity_id, account_id, month_date, budget_amount)

txn_id = 1
journal_id = 1

# track prior-month accrual balances to reverse/settle
prior_ap_accrual = {e[0]: 0.0 for e in ENTITIES}
prior_payroll_accrual = {e[0]: 0.0 for e in ENTITIES}
prior_ic_balance = {e[0]: 0.0 for e in ENTITIES}  # branch payable / HQ receivable, keyed by branch entity_id


def add_journal(lines, entity_id, dt, description, source, counterparty_entity_id=None):
    """lines: list of (account_id, debit, credit). Must balance."""
    global txn_id, journal_id
    total_d = round(sum(l[1] for l in lines), 2)
    total_c = round(sum(l[2] for l in lines), 2)
    assert abs(total_d - total_c) < 0.01, f"Unbalanced journal {journal_id}: {total_d} vs {total_c}"
    for acct, d, c in lines:
        gl_rows.append((txn_id, journal_id, entity_id, acct, dt.isoformat(),
                         round(d, 2), round(c, 2), description, source, counterparty_entity_id))
        txn_id += 1
    journal_id += 1


for (y, m) in MONTHS:
    for ent_id, code, name, city, etype, parent, is_hq in ENTITIES:
        scale = ENTITY_SCALE[ent_id]
        month_idx = MONTHS.index((y, m))
        growth = 1 + 0.012 * month_idx  # slow organic growth over the 18 months
        noise = random.uniform(0.9, 1.12)

        # --- 1. Revenue (billed to AR), split across 3 fee types ---
        base_rev = round(92000 * scale * growth * noise, 2)
        r1 = round(base_rev * 0.42, 2)
        r2 = round(base_rev * 0.33, 2)
        r3 = round(base_rev - r1 - r2, 2)  # remainder absorbs rounding, keeps journal balanced
        rev_split = [r1, r2, r3]
        rev_lines = [(2, round(sum(rev_split), 2), 0.0)]
        for acct, amt in zip(REV_ACCTS, rev_split):
            rev_lines.append((acct, 0.0, amt))
        add_journal(rev_lines, ent_id, biz_day(y, m, 12), f"{code} legal fees billed - {y}-{m:02d}", "Billing")
        budget_rows.append((ent_id, 11, date(y, m, 1), round(rev_split[0] * random.uniform(0.93, 1.03), 2)))
        budget_rows.append((ent_id, 12, date(y, m, 1), round(rev_split[1] * random.uniform(0.93, 1.03), 2)))
        budget_rows.append((ent_id, 13, date(y, m, 1), round(rev_split[2] * random.uniform(0.93, 1.03), 2)))

        # --- 2. Cash collections on AR (88-96% collected same month) ---
        collect_rate = random.uniform(0.88, 0.96)
        collected = round(base_rev * collect_rate, 2)
        add_journal([(1, collected, 0.0), (2, 0.0, collected)], ent_id, biz_day(y, m, 24),
                    f"{code} client fee collections - {y}-{m:02d}", "Cash Receipt")

        # --- 3. Direct case costs (mostly cash, a slice to AP) ---
        base_cogs = round(9500 * scale * growth * noise, 2)
        c1 = round(base_cogs * 0.55, 2)
        c2 = round(base_cogs - c1, 2)
        cogs_split = [c1, c2]
        cash_part = round(base_cogs * 0.7, 2)
        ap_part = round(base_cogs - cash_part, 2)
        add_journal([(16, cogs_split[0], 0.0), (17, cogs_split[1], 0.0),
                     (1, 0.0, cash_part), (6, 0.0, ap_part)],
                    ent_id, biz_day(y, m, 15), f"{code} direct case costs - {y}-{m:02d}", "AP/Cash")

        # --- 4. Payroll (90% paid same month, 10% accrued and reversed next month) ---
        base_payroll = round(41000 * scale * growth, 2)
        payroll_tax = round(base_payroll * 0.14, 2)
        total_payroll_cost = round(base_payroll + payroll_tax, 2)
        cash_pay = round(total_payroll_cost * 0.90, 2)
        accrued_pay = round(total_payroll_cost - cash_pay, 2)
        # reverse prior month's accrual (paid out in cash this month)
        reversal = prior_payroll_accrual[ent_id]
        add_journal(
            [(19, base_payroll, 0.0), (20, payroll_tax, 0.0),
             (1, 0.0, cash_pay + reversal), (7, 0.0, accrued_pay), (7, reversal, 0.0)],
            ent_id, biz_day(y, m, 28), f"{code} payroll run - {y}-{m:02d}", "Payroll")
        prior_payroll_accrual[ent_id] = accrued_pay
        budget_rows.append((ent_id, 19, date(y, m, 1), round(base_payroll * random.uniform(0.97, 1.02), 2)))
        budget_rows.append((ent_id, 20, date(y, m, 1), round(payroll_tax * random.uniform(0.97, 1.02), 2)))

        # --- 5. Rent (cash) ---
        rent = 6800 * scale * (1 + 0.005 * month_idx)
        add_journal([(21, rent, 0.0), (1, 0.0, rent)], ent_id, biz_day(y, m, 1),
                    f"{code} office rent - {y}-{m:02d}", "Cash")
        budget_rows.append((ent_id, 21, date(y, m, 1), round(rent * random.uniform(0.98, 1.02), 2)))

        # --- 6. Marketing (cash + a little AP) ---
        mkt = round(3200 * scale * noise, 2)
        mkt_cash = round(mkt * 0.8, 2)
        mkt_ap = round(mkt - mkt_cash, 2)
        add_journal([(22, mkt, 0.0), (1, 0.0, mkt_cash), (6, 0.0, mkt_ap)],
                    ent_id, biz_day(y, m, 18), f"{code} marketing spend - {y}-{m:02d}", "AP/Cash")
        budget_rows.append((ent_id, 22, date(y, m, 1), round(mkt * random.uniform(0.85, 1.15), 2)))

        # --- 7. Insurance (amortize prepaid) ---
        ins = 1450 * scale
        add_journal([(23, ins, 0.0), (3, 0.0, ins)], ent_id, biz_day(y, m, 1),
                    f"{code} malpractice insurance amortization - {y}-{m:02d}", "Prepaid Amort")
        budget_rows.append((ent_id, 23, date(y, m, 1), round(ins * random.uniform(0.98, 1.02), 2)))

        # --- 8. Software & IT (cash) ---
        it = 2100 * scale * (1 + 0.01 * month_idx)
        add_journal([(24, it, 0.0), (1, 0.0, it)], ent_id, biz_day(y, m, 9),
                    f"{code} software & IT subscriptions - {y}-{m:02d}", "Cash")
        budget_rows.append((ent_id, 24, date(y, m, 1), round(it * random.uniform(0.95, 1.05), 2)))

        # --- 9. Depreciation ---
        dep = 1100 * scale
        add_journal([(26, dep, 0.0), (5, 0.0, dep)], ent_id, month_end(y, m),
                    f"{code} monthly depreciation - {y}-{m:02d}", "Adjusting Entry")
        budget_rows.append((ent_id, 26, date(y, m, 1), round(dep * random.uniform(0.98, 1.02), 2)))

        # --- 10. AP settlement of PRIOR month's remaining AP balance (paid this month) ---
        if prior_ap_accrual[ent_id] > 0:
            add_journal([(6, prior_ap_accrual[ent_id], 0.0), (1, 0.0, prior_ap_accrual[ent_id])],
                        ent_id, biz_day(y, m, 5), f"{code} AP payment run (prior month invoices) - {y}-{m:02d}",
                        "Cash")
        prior_ap_accrual[ent_id] = ap_part + mkt_ap  # remains open, settled next month

    # --- 11. Intercompany shared-services billing: HQ bills each branch monthly ---
    for branch_id in (2, 3):
        ic_amount = round(5200 * ENTITY_SCALE[branch_id] * (1 + 0.01 * MONTHS.index((y, m))), 2)
        # HQ side
        add_journal([(4, ic_amount, 0.0), (14, 0.0, ic_amount)], 1, biz_day(y, m, 20),
                    f"Shared services billed to branch {branch_id} - {y}-{m:02d}", "Intercompany",
                    counterparty_entity_id=branch_id)
        # Branch side
        add_journal([(25, ic_amount, 0.0), (8, 0.0, ic_amount)], branch_id, biz_day(y, m, 20),
                    f"Shared services allocation from HQ - {y}-{m:02d}", "Intercompany",
                    counterparty_entity_id=1)
        budget_rows.append((branch_id, 25, date(y, m, 1), round(ic_amount, 2)))

        # --- 12. Settle PRIOR month's intercompany balance (1-month lag) ---
        prior_bal = prior_ic_balance[branch_id]
        if prior_bal > 0:
            add_journal([(8, prior_bal, 0.0), (1, 0.0, prior_bal)], branch_id, biz_day(y, m, 25),
                        f"Intercompany settlement paid to HQ - {y}-{m:02d}", "Intercompany Cash",
                        counterparty_entity_id=1)
            add_journal([(1, prior_bal, 0.0), (4, 0.0, prior_bal)], 1, biz_day(y, m, 25),
                        f"Intercompany settlement received from branch {branch_id} - {y}-{m:02d}",
                        "Intercompany Cash", counterparty_entity_id=branch_id)
        prior_ic_balance[branch_id] = ic_amount  # this month's billing stays open until next month

print(f"Generated {len(gl_rows)} GL transaction lines across {journal_id - 1} journals.")

# ---------------------------------------------------------------------------
# 3. BANK TRANSACTIONS  (derived from GL cash lines; reconciling items seeded
#    into the current/open month only)
# ---------------------------------------------------------------------------

bank_rows = []  # (bank_txn_id, entity_id, date, amount, description, category, matched_gl_txn_id)
bank_id = 1

cash_lines_by_entity = {e[0]: [] for e in ENTITIES}
for row in gl_rows:
    (tid, jid, ent, acct, dt, deb, cred, desc, src, cp) = row
    if acct == 1:  # Cash - Operating
        net = round(deb - cred, 2)
        if net != 0:
            cash_lines_by_entity[ent].append((tid, dt, net, desc))

cur_month_str = f"{CURRENT_MONTH[0]}-{CURRENT_MONTH[1]:02d}"
# Small, realistic-sized items eligible to be held out as an outstanding
# check / deposit in transit (never the big revenue-collection or payroll
# lines, which would produce an implausibly huge "outstanding" balance).
HOLDOUT_CANDIDATES = ("marketing spend", "software & IT subscriptions", "AP payment run")

for ent_id in cash_lines_by_entity:
    held_out_this_entity = False
    for (tid, dt, net, desc) in cash_lines_by_entity[ent_id]:
        is_current_month = dt[:7] == cur_month_str
        is_holdout_candidate = any(k in desc for k in HOLDOUT_CANDIDATES)
        # Hold out exactly one small item per entity in the open month so the
        # bank rec has one real (and plausibly sized) outstanding item.
        if is_current_month and is_holdout_candidate and not held_out_this_entity:
            held_out_this_entity = True
            continue  # outstanding check - not yet cleared on the bank statement
        bank_rows.append((bank_id, ent_id, dt, net, desc, "Matched", tid))
        bank_id += 1

# Seed explicit reconciling items for the current month only
cur_y, cur_m = CURRENT_MONTH
for ent_id in (1, 2, 3):
    # Bank fee not yet booked in GL
    bank_rows.append((bank_id, ent_id, biz_day(cur_y, cur_m, 30).isoformat(), -round(35 + 15 * ENTITY_SCALE[ent_id], 2),
                       "Monthly account service fee", "Unmatched - Book Adjustment Needed", None))
    bank_id += 1
    # Interest income not yet booked in GL
    bank_rows.append((bank_id, ent_id, biz_day(cur_y, cur_m, 30).isoformat(), round(18 + 22 * ENTITY_SCALE[ent_id], 2),
                       "Interest income", "Unmatched - Book Adjustment Needed", None))
    bank_id += 1

print(f"Generated {len(bank_rows)} bank statement lines.")

# ---------------------------------------------------------------------------
# 4. DATE DIMENSION
# ---------------------------------------------------------------------------

all_dates = sorted(set([r[4] for r in gl_rows] + [r[2] for r in bank_rows] +
                        [b[2].isoformat() for b in budget_rows]))
date_rows = []
for d in all_dates:
    dt = date.fromisoformat(d)
    q = (dt.month - 1) // 3 + 1
    is_me = dt.day == monthrange(dt.year, dt.month)[1]
    date_rows.append((d, dt.year, dt.month, dt.strftime("%B"), q,
                       f"FY{dt.year}-P{dt.month:02d}", int(is_me)))

# ---------------------------------------------------------------------------
# 5. WRITE SQLITE DB (schema.sql must exist alongside this script)
# ---------------------------------------------------------------------------

if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.executescript(open(os.path.join(BASE_DIR, "schema.sql")).read())

cur.executemany("INSERT INTO dim_entity VALUES (?,?,?,?,?,?,?)", ENTITIES)
cur.executemany("INSERT INTO dim_account VALUES (?,?,?,?,?,?,?)", ACCOUNTS)
cur.executemany("INSERT INTO dim_date VALUES (?,?,?,?,?,?,?)", date_rows)
cur.executemany("INSERT INTO fact_gl_transactions VALUES (?,?,?,?,?,?,?,?,?,?)", gl_rows)
cur.executemany("INSERT INTO fact_bank_transactions VALUES (?,?,?,?,?,?,?)", bank_rows)
budget_rows_str = [(e, a, d.isoformat(), amt) for (e, a, d, amt) in budget_rows]
cur.executemany("INSERT INTO fact_budget (entity_id, account_id, date_id, budget_amount) VALUES (?,?,?,?)",
                 budget_rows_str)

conn.commit()

# sanity check: every journal balances
bad = cur.execute("""
    SELECT journal_id, ROUND(SUM(debit),2) d, ROUND(SUM(credit),2) c
    FROM fact_gl_transactions GROUP BY journal_id HAVING ABS(d - c) > 0.01
""").fetchall()
assert not bad, f"Unbalanced journals found: {bad[:5]}"
print(f"All {journal_id - 1} journals balance (debits = credits).")

conn.close()
print(f"SQLite database written to {DB_PATH}")

# ---------------------------------------------------------------------------
# 6. WRITE PER-ENTITY "SOURCE SYSTEM" EXPORT CSVs
#    (these are what Power Query connects to in power_query_steps.md)
# ---------------------------------------------------------------------------

account_lookup = {a[0]: a for a in ACCOUNTS}
entity_lookup = {e[0]: e for e in ENTITIES}

for ent_id, code, name, city, etype, parent, is_hq in ENTITIES:
    gl_path = os.path.join(SRC_DIR, f"entity_{code}_gl_export.csv")
    with open(gl_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["TxnID", "JournalID", "Date", "AccountCode", "AccountName", "Debit", "Credit",
                    "Description", "Source", "CounterpartyEntity"])
        for row in gl_rows:
            (tid, jid, ent, acct, dt, deb, cred, desc, src, cp) = row
            if ent != ent_id:
                continue
            acct_row = account_lookup[acct]
            cp_code = entity_lookup[cp][1] if cp else ""
            w.writerow([tid, jid, dt, acct_row[1], acct_row[2], deb, cred, desc, src, cp_code])

    bank_path = os.path.join(SRC_DIR, f"bank_statement_{code}.csv")
    with open(bank_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["BankTxnID", "Date", "Amount", "Description", "Category", "MatchedTxnID"])
        for row in bank_rows:
            (bid, ent, dt, amt, desc, cat, matched) = row
            if ent != ent_id:
                continue
            w.writerow([bid, dt, amt, desc, cat, matched if matched else ""])

    budget_path = os.path.join(SRC_DIR, f"budget_{code}.csv")
    with open(budget_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Month", "AccountCode", "AccountName", "BudgetAmount"])
        for (e, a, d, amt) in budget_rows:
            if e != ent_id:
                continue
            acct_row = account_lookup[a]
            w.writerow([d.isoformat(), acct_row[1], acct_row[2], amt])

print(f"Per-entity source exports written to {SRC_DIR}/")

# ---------------------------------------------------------------------------
# 7. WRITE MODELED (POST-ETL) FLAT FILES - what Power Query lands after
#    combining/appending the per-entity source exports above. These feed
#    build_workbook.py directly so the workbook and the SQL model agree.
# ---------------------------------------------------------------------------

MODEL_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(MODEL_DIR, exist_ok=True)

with open(os.path.join(MODEL_DIR, "dim_entity.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["EntityID", "EntityCode", "EntityName", "City", "EntityType", "ParentEntityID", "IsGroupHQ"])
    w.writerows(ENTITIES)

with open(os.path.join(MODEL_DIR, "dim_account.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["AccountID", "AccountCode", "AccountName", "AccountType", "ParentAccountID",
                "IsIntercompany", "NormalBalance"])
    w.writerows(ACCOUNTS)

with open(os.path.join(MODEL_DIR, "dim_date.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["DateID", "Year", "Month", "MonthName", "Quarter", "FiscalPeriod", "IsMonthEnd"])
    w.writerows(date_rows)

with open(os.path.join(MODEL_DIR, "fact_gl_transactions.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["TxnID", "JournalID", "EntityCode", "Date", "AccountCode", "AccountName", "AccountType",
                "IsIntercompany", "Debit", "Credit", "Description", "Source", "CounterpartyEntity", "MonthKey"])
    for row in gl_rows:
        (tid, jid, ent, acct, dt, deb, cred, desc, src, cp) = row
        a = account_lookup[acct]
        e = entity_lookup[ent]
        cp_code = entity_lookup[cp][1] if cp else ""
        month_key = dt[:7]
        w.writerow([tid, jid, e[1], dt, a[1], a[2], a[3], "Yes" if a[5] else "No", deb, cred, desc, src,
                    cp_code, month_key])

with open(os.path.join(MODEL_DIR, "fact_bank_transactions.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["BankTxnID", "EntityCode", "Date", "Amount", "Description", "Category", "MatchedTxnID", "MonthKey"])
    for row in bank_rows:
        (bid, ent, dt, amt, desc, cat, matched) = row
        e = entity_lookup[ent]
        w.writerow([bid, e[1], dt, amt, desc, cat, matched if matched else "", dt[:7]])

with open(os.path.join(MODEL_DIR, "fact_budget.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["BudgetID", "EntityCode", "Month", "AccountCode", "AccountName", "BudgetAmount", "MonthKey"])
    bid = 1
    for (e, a, d, amt) in budget_rows:
        ent = entity_lookup[e]
        acct = account_lookup[a]
        w.writerow([bid, ent[1], d.isoformat(), acct[1], acct[2], amt, d.isoformat()[:7]])
        bid += 1

print(f"Modeled (post-ETL) flat files written to {MODEL_DIR}/")
print(f"Current (open) close month: {CURRENT_MONTH[0]}-{CURRENT_MONTH[1]:02d}")
