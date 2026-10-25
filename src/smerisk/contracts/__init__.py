"""§10: Pydantic models for raw data, as business applications already store it."""

from smerisk.contracts._base import MAX_IGNORED_LISTED, MAX_RECORDS, IgnoredRecord, Parsed, Record, Ref, parse_records
from smerisk.contracts.accounting import BankBalance, Invoice, LedgerEntry, Payment, ScheduledFlow
from smerisk.contracts.consulting import (
    Consultant,
    Deal,
    FixedPriceProject,
    Mission,
    ProjectRisk,
    RemainingTask,
    StageChange,
    TeamMember,
    Timesheet,
)
from smerisk.contracts.fec_reader import read_fec
from smerisk.contracts.forecasts import ForecastLogEntry
from smerisk.contracts.payroll import (
    AbsenceMonth,
    Contract,
    EmploymentGroups,
    EmploymentPeriod,
    Hours,
    PayrollBaseline,
    Payslip,
    PlannedHire,
)
from smerisk.contracts.retail import Product, Sale

__all__ = [
    "MAX_IGNORED_LISTED",
    "MAX_RECORDS",
    "AbsenceMonth",
    "BankBalance",
    "Consultant",
    "Contract",
    "Deal",
    "EmploymentGroups",
    "EmploymentPeriod",
    "FixedPriceProject",
    "ForecastLogEntry",
    "Hours",
    "IgnoredRecord",
    "Invoice",
    "LedgerEntry",
    "Mission",
    "Parsed",
    "Payment",
    "PayrollBaseline",
    "Payslip",
    "PlannedHire",
    "Product",
    "ProjectRisk",
    "Record",
    "Ref",
    "RemainingTask",
    "Sale",
    "ScheduledFlow",
    "StageChange",
    "TeamMember",
    "Timesheet",
    "parse_records",
    "read_fec",
]
