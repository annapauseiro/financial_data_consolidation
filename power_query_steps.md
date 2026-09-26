# Power Query ETL — P&V Legal Group Consolidation Model

This documents the Power Query steps used to turn the three offices' raw
source exports (`/source_data/*.csv` — what each office's practice-management
system exports) into the modeled tables the workbook's `PQ_*` tabs load
(`Data > Get Data > From File > From Folder`, then transformed and loaded to
worksheet Tables). The M code below is what `Home > Advanced Editor` shows
for each query; it's written to be re-run against a refreshed folder of
monthly exports, not just this one snapshot.

Source files per office (one query per file, per entity):
```
/source_data/entity_MIA_gl_export.csv     /source_data/entity_CHI_gl_export.csv     /source_data/entity_DAL_gl_export.csv
/source_data/bank_statement_MIA.csv        /source_data/bank_statement_CHI.csv        /source_data/bank_statement_DAL.csv
/source_data/budget_MIA.csv                 /source_data/budget_CHI.csv                 /source_data/budget_DAL.csv
```

---

## 1. Dimension queries (loaded first — everything else merges against these)

### `Dim_Account`
Source: a small reference CSV (`dim_account.csv`) exported from the chart of
accounts. Converts the `IsIntercompany` 0/1 flag to `Yes`/`No` so it matches
the style used everywhere else in the workbook.

```m
let
    Source = Csv.Document(File.Contents("...\dim_account.csv"), [Delimiter=",", Encoding=65001]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    Typed = Table.TransformColumnTypes(Promoted, {
        {"AccountID", Int64.Type}, {"AccountCode", type text}, {"AccountName", type text},
        {"AccountType", type text}, {"ParentAccountID", Int64.Type},
        {"IsIntercompany", Int64.Type}, {"NormalBalance", type text}
    }),
    AddYesNo = Table.AddColumn(Typed, "IsIntercompanyFlag",
        each if [IsIntercompany] = 1 then "Yes" else "No", type text)
in
    AddYesNo
```

### `Dim_Entity`
Same pattern against `dim_entity.csv` — loaded once, referenced by every
`EntityCode` merge below.

---

## 2. `PQ_GL_Data` — combine, type, and enrich the 3 offices' GL exports

```m
let
    // one step per office, same shape as this one (repeated for CHI, DAL)
    GL_MIA = let
        Source = Csv.Document(File.Contents("...\source_data\entity_MIA_gl_export.csv"),
                               [Delimiter=",", Encoding=65001]),
        Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
        Typed = Table.TransformColumnTypes(Promoted, {
            {"TxnID", Int64.Type}, {"JournalID", Int64.Type}, {"Date", type date},
            {"AccountCode", type text}, {"AccountName", type text},
            {"Debit", type number}, {"Credit", type number},
            {"Description", type text}, {"Source", type text}, {"CounterpartyEntity", type text}
        }),
        AddEntity = Table.AddColumn(Typed, "EntityCode", each "MIA", type text)
    in
        AddEntity,

    // GL_CHI, GL_DAL built identically, swapping the file and the literal "CHI"/"DAL"

    Appended = Table.Combine({GL_MIA, GL_CHI, GL_DAL}),

    // bring in AccountType / IsIntercompany from the account dimension
    MergedAccount = Table.NestedJoin(Appended, {"AccountCode"}, Dim_Account, {"AccountCode"},
                                       "AccountDim", JoinKind.LeftOuter),
    Expanded = Table.ExpandTableColumn(MergedAccount, "AccountDim",
                                         {"AccountType", "IsIntercompanyFlag"},
                                         {"AccountType", "IsIntercompany"}),

    // derive the MonthKey used by every SUMIFS in the workbook
    AddMonthKey = Table.AddColumn(Expanded, "MonthKey",
        each Date.ToText([Date], "yyyy-MM"), type text),

    Reordered = Table.ReorderColumns(AddMonthKey,
        {"TxnID","JournalID","EntityCode","Date","AccountCode","AccountName","AccountType",
         "IsIntercompany","Debit","Credit","Description","Source","CounterpartyEntity","MonthKey"})
in
    Reordered
```

Loaded to the worksheet as **Table** named `PQ_GL_Data` (`Close & Load To... >
Table > existing worksheet`). Refreshing this query after a new month's
office exports land in `/source_data/` re-runs the whole chain — nothing in
the workbook's formulas needs to change.

---

## 3. `PQ_Bank_Data` — combine the 3 bank statement exports

```m
let
    Bank_MIA = let
        Source = Csv.Document(File.Contents("...\source_data\bank_statement_MIA.csv"),
                               [Delimiter=",", Encoding=65001]),
        Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
        Typed = Table.TransformColumnTypes(Promoted, {
            {"BankTxnID", Int64.Type}, {"Date", type date}, {"Amount", type number},
            {"Description", type text}, {"Category", type text}, {"MatchedTxnID", type text}
        }),
        AddEntity = Table.AddColumn(Typed, "EntityCode", each "MIA", type text)
    in
        AddEntity,
    // Bank_CHI, Bank_DAL identical, different file + literal

    Appended = Table.Combine({Bank_MIA, Bank_CHI, Bank_DAL}),
    AddMonthKey = Table.AddColumn(Appended, "MonthKey", each Date.ToText([Date], "yyyy-MM"), type text),
    Reordered = Table.ReorderColumns(AddMonthKey,
        {"BankTxnID","EntityCode","Date","Amount","Description","Category","MatchedTxnID","MonthKey"})
in
    Reordered
```

Loaded as Table `PQ_Bank_Data`. The `Category` column (`"Matched"` vs.
`"Unmatched - Book Adjustment Needed"`) is what the bank statement export
already flags on import — in a live refresh this would instead be computed
here with a merge back to `PQ_GL_Data` on `MatchedTxnID`, but keeping it as a
source-flagged column matches how P&V's actual bank feed tool tags items.

---

## 4. `PQ_Budget_Data` — combine the 3 offices' budget exports

```m
let
    Budget_MIA = let
        Source = Csv.Document(File.Contents("...\source_data\budget_MIA.csv"),
                               [Delimiter=",", Encoding=65001]),
        Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
        Typed = Table.TransformColumnTypes(Promoted, {
            {"Month", type date}, {"AccountCode", type text},
            {"AccountName", type text}, {"BudgetAmount", type number}
        }),
        AddEntity = Table.AddColumn(Typed, "EntityCode", each "MIA", type text),
        AddID = Table.AddIndexColumn(AddEntity, "BudgetID", 1, 1, Int64.Type)
    in
        AddID,
    // Budget_CHI, Budget_DAL identical

    Appended = Table.Combine({Budget_MIA, Budget_CHI, Budget_DAL}),
    AddMonthKey = Table.AddColumn(Appended, "MonthKey", each Date.ToText([Month], "yyyy-MM"), type text),
    Reordered = Table.ReorderColumns(AddMonthKey,
        {"BudgetID","EntityCode","Month","AccountCode","AccountName","BudgetAmount","MonthKey"})
in
    Reordered
```

Loaded as Table `PQ_Budget_Data`.

---

## 5. Refresh behaviour

All four data queries (`PQ_GL_Data`, `PQ_Bank_Data`, `PQ_Budget_Data`, plus
the two dimension queries) are set to **Refresh data when opening the file**
and are grouped in a single "Firm Data" query group in the Queries pane.
Because every downstream tab (Trial_Balance, Bank_Reconciliation,
Consolidation, Budget_vs_Actual, Dashboard) reads from these tables via
`SUMIFS`/structured references rather than pasted values, a full refresh
after next month's exports land only requires: drop the new CSVs into
`/source_data/`, `Data > Refresh All`, and update `CURRENT_MONTH_KEY` in one
place (or, in the live version, drive it off `List.Max(PQ_GL_Data[MonthKey])`
the same way `build_workbook.py`'s generator script does).
