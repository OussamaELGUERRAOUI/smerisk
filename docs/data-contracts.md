---
title: Raw data contracts for payslips, invoices and ledger entries
description: Generic record formats for payslips, invoices, timesheets and FEC ledger entries, and how invalid records are handled.
---

# Raw data contracts for payslips, invoices and ledger entries

**Status:** implemented (`smerisk.contracts`). The [use cases](reference.md#use-cases) that consume these records are implemented.

## Purpose

The formulas take parameters: a distribution, a rate, a transition matrix. A business application has something else
in its database: payslips, invoices, ledger entries, timesheets, sales. The raw data contracts describe those records
in a generic way, independent of any particular software, so that the use cases can estimate the parameters
themselves (specification §10 and §11).

All identifiers (`*_ref`) are opaque strings. No contract has a field for a name, and the FEC labels are dropped when
the file is read.

## Contracts

| Area | Contract | Model | One record is |
| --- | --- | --- | --- |
| Payroll | `payslips` | `Payslip` | one validated payslip |
| Payroll | `absence_months` | `AbsenceMonth` | absences of one month (and group), never of one employee |
| Payroll | `employment_periods` | `EmploymentPeriod` | one period of employment (or one contract, merged later) |
| Payroll | `payroll_baseline` | `PayrollBaseline` | the payroll engine's forecast for one month |
| Accounting | `ledger_entries` | `LedgerEntry` | one FEC line, under the official column names |
| Accounting | `invoices` | `Invoice` | one customer invoice and its payments |
| Accounting | `bank_balances` | `BankBalance` | a bank balance at a date |
| Accounting | `scheduled_flows` | `ScheduledFlow` | a known, dated inflow or outflow |
| Consulting | `timesheets` | `Timesheet` | days of one consultant on one mission in one month |
| Consulting | `missions` | `Mission` | one mission, with its planned and actual end |
| Consulting | `consultants` | `Consultant` | daily cost and available days of one consultant |
| Consulting | `deals` | `Deal` | one sales opportunity |
| Consulting | `fixed_price_projects` | `FixedPriceProject` | one fixed-price project, its remaining tasks and risks |
| Retail | `sales` | `Sale` | sales of a site over a day or an hour |
| Retail | `products` | `Product` | price, cost, and salvage value of a product |
| Reliability | `forecast_log` | `ForecastLogEntry` | one past forecast, with its outcome once known |

Optional fields improve the estimates; their absence is reported in the data quality report, never blocking.

## Invalid records do not fail the call

Raw data always contains a few broken records. `parse_records` validates each record on its own and sets aside the
invalid ones with their reason, instead of rejecting the whole list. The first 100 are listed, the others counted.

```python
from smerisk.contracts import Payslip, parse_records

rows = [
    {
        "employee_ref": "e1",
        "period": "2026-01",
        "gross": 2150,
        "employer_contributions": 610.4,
        "employer_cost": 2760.4,
    },
    {
        "employee_ref": "e2",
        "period": "2026-13",
        "gross": 1980,
        "employer_contributions": 560.0,
        "employer_cost": 2540.0,
    },
]
parsed = parse_records(Payslip, rows, name="payslips")
print(len(parsed.records), parsed.n_ignored)  # 1 1
print(parsed.ignored[0].index, parsed.ignored[0].field)  # 1 period
```

Within one record, the rules are strict: unknown fields, NaN, and inconsistent values (a due date before the issue
date, billable days without a mission, more than 31 days in a month) make the record invalid.

## Reading a FEC file

`read_fec` reads a FEC as exported by accounting software: a path, or the bytes of the file.

- Separator: tab or `|` (the official format), or `;` (spreadsheet exports), detected from the header.
- Encoding: UTF-8 with or without BOM, otherwise Windows-1252.
- Columns: matched regardless of case; `JournalCode`, `EcritureNum`, `EcritureDate`, `CompteNum`, and either `Debit`
  and `Credit` or `Montant` and `Sens` are required. Labels and other columns are dropped.
- Values: dates `AAAAMMJJ` or ISO, amounts with a decimal comma (`1234,56`), empty optional fields read as absent.
- A line with the wrong number of fields is set aside rather than read shifted.

```python
from smerisk.contracts import read_fec

fec = (
    "JournalCode\tEcritureNum\tEcritureDate\tCompteNum\tCompAuxNum\tEcritureLib\tDebit\tCredit\n"
    "VT\t118\t20260131\t411000\tC042\tInvoice 118\t1200,00\t0,00\n"
    "VT\t118\t20260131\t706000\t\tInvoice 118\t0,00\t1000,00\n"
    "VT\t118\t20260131\t445710\t\tInvoice 118\t0,00\t200,00\n"
).encode()
entries = read_fec(fec).records
print([(e.account, e.balance) for e in entries])  # [('411000', 1200.0), ('706000', -1000.0), ('445710', -200.0)]
```

In Python, `LedgerEntry` fields have snake_case names (`entry_date`, `account`, `auxiliary_account`); in JSON, a
record uses the FEC column names, so the same file can be sent as JSON.

## Limits

At most 1,000,000 records per contract and per call, and 200 MB per FEC file (error 413). The data is aggregated
before it reaches the formulas, which keep their own limits.
