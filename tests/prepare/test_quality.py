from smerisk.contracts import MAX_IGNORED_LISTED, AbsenceMonth, parse_records
from smerisk.prepare.quality import QualityReport
from smerisk.prepare.series import fit_series

ROW = {"period": "2026-01", "absence_days": 12, "theoretical_days": 210, "headcount": 10}


def test_records_coverage_and_ignored() -> None:
    parsed = parse_records(AbsenceMonth, [ROW, {**ROW, "period": "2026-03"}, {**ROW, "headcount": 0}], name="absences")
    report = QualityReport()
    report.records("absence_months", parsed, (r.period for r in parsed.records))
    quality = report.build()
    assert quality.coverage["absence_months"].model_dump() == {"first": "2026-01", "last": "2026-03"}
    assert quality.ignored_counts == {"absence_months": 1}
    entry = quality.ignored_records[0]
    assert (entry.contract, entry.index, entry.field) == ("absence_months", 2, "headcount")
    assert quality.usable  # no requirement recorded


def test_at_most_100_ignored_records_across_contracts() -> None:
    bad = parse_records(AbsenceMonth, [{}] * 80, name="absences")
    report = QualityReport()
    report.records("a", bad)
    report.records("b", bad)
    quality = report.build()
    assert len(quality.ignored_records) == MAX_IGNORED_LISTED and quality.ignored_counts == {"a": 80, "b": 80}
    assert "a" in quality.coverage or quality.coverage == {}


def test_series_gaps_and_outliers() -> None:
    values = {f"2026-{m:02d}": 100.0 for m in range(1, 11) if m != 4} | {"2026-10": 900.0}
    report = QualityReport()
    report.series("overtime", values, fit_series(values), "2026-01", "2026-12")
    quality = report.build()
    assert quality.missing_periods == {"overtime": ["2026-04", "2026-11", "2026-12"]}
    assert [(o.source, o.ref, o.value) for o in quality.outliers] == [("overtime", "2026-10", 900.0)]
    assert quality.coverage["overtime"].last == "2026-10"


def test_requirements_decide_usability() -> None:
    report = QualityReport()
    assert report.require("payslips.months", 12, 14)
    assert not report.require("absence_months.months", 6, 3)
    quality = report.build()
    assert [r.met for r in quality.requirements] == [True, False] and not quality.usable


def test_outlier_from_a_contract() -> None:
    report = QualityReport()
    report.outlier("ledger_entries", "VT-118", 98_000, 12.5)
    assert report.build().outliers[0].ref == "VT-118"
