"""Проверки расчёта. Запуск: python -m unittest test_report -v"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import report  # noqa: E402

DATA = Path(__file__).parent / "data"


def build(on: date):
    contracts, works, payments = report.load(DATA)
    return {c.contract_id: report.report_for(
        c, [w for w in works if w.contract_id == c.contract_id],
        [p for p in payments if p.contract_id == c.contract_id], on) for c in contracts}


def by_work(rep):
    return {r.work.work_id: (r.price, r.paid, r.remaining) for r in rep.rows}


R = 100  # копеек в рубле


class Money(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(report.parse_money("300000.00"), 30_000_000)
        self.assertEqual(report.parse_money("0.5"), 50)
        self.assertEqual(report.parse_money("7"), 700)
        for bad in ("1,5", "-1.00", "1.234", "abc", ""):
            with self.assertRaises(ValueError):
                report.parse_money(bad)

    def test_format(self):
        self.assertEqual(report.fmt_money(31_500_000), "315 000,00")
        self.assertEqual(report.fmt_money(-111_112), "-1 111,12")
        self.assertEqual(report.fmt_money_csv(5), "0,05")


class ControlDate(unittest.TestCase):
    """10.11.2026 - контрольная дата из письма бухгалтера."""

    def setUp(self):
        self.reps = build(date(2026, 11, 10))

    def test_d1(self):
        d1 = self.reps["D-1"]
        self.assertEqual(by_work(d1), {
            "W-11": (100_000 * R, 100_000 * R, 0),
            "W-12": (100_000 * R, 100_000 * R, 0),
            "W-13": (0, 0, 0),
            "W-14": (100_000 * R, 100_000 * R, 0),
            "W-15": (100_000 * R, 15_000 * R, 85_000 * R),
            "W-16": (100_000 * R, 0, 100_000 * R),
        })
        self.assertEqual(d1.paid, 315_000 * R)      # P-1 + P-3; P-2 вернули, P-4 в очереди
        self.assertEqual(d1.remaining, 185_000 * R)
        self.assertEqual(d1.queued, 150_000 * R)
        self.assertEqual(d1.refunded, 50_000 * R)

    def test_d2(self):
        d2 = self.reps["D-2"]
        self.assertEqual(by_work(d2), {
            "W-21": (20_000 * R, 20_000 * R, 0),
            "W-22": (30_000 * R, 30_000 * R, 0),
            "W-23": (40_000 * R, 25_000 * R, 15_000 * R),  # принята 11.11, P-8 от 12.11 не входит
        })
        self.assertEqual(d2.rows[2].status, report.PLANNED)
        self.assertEqual(d2.paid, 75_000 * R)
        self.assertEqual(d2.overpaid, 0)

    def test_totals_balance(self):
        for rep in self.reps.values():
            self.assertEqual(sum(r.paid for r in rep.rows) + rep.unallocated, rep.paid)


class OtherDates(unittest.TestCase):
    def test_refund_counts_until_returned(self):
        self.assertEqual(build(date(2026, 9, 9))["D-1"].paid, 350_000 * R)
        self.assertEqual(build(date(2026, 9, 10))["D-1"].paid, 300_000 * R)

    def test_overpayment_after_last_payment(self):
        d2 = build(date(2026, 11, 12))["D-2"]
        self.assertEqual(d2.paid, 95_000 * R)
        self.assertEqual(d2.remaining, 0)
        self.assertEqual(d2.overpaid, 5_000 * R)

    def test_before_cancellation_work_is_payable(self):
        d1 = build(date(2026, 10, 11))["D-1"]
        self.assertEqual(d1.price, 600_000 * R)


if __name__ == "__main__":
    unittest.main()
