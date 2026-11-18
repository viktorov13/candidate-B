#!/usr/bin/env python3
"""Отчёт об оплате работ по договорам на заданную дату.

Запуск:  python report.py --date 2026-11-10 [--data data] [--csv report.csv]

Только стандартная библиотека, Python 3.9+. Деньги хранятся в копейках (int),
float нигде не используется. Решения по расчёту описаны в DECISIONS.md.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

DONE, PLANNED, CANCELLED = "done", "planned", "cancelled"
PAID, QUEUED, REFUNDED, LATER = "paid", "queued", "refunded", "later"

MONEY_RE = re.compile(r"^(\d+)(?:\.(\d{1,2}))?$")


class DataError(Exception):
    """Ошибка во входных данных."""


def parse_money(text: str) -> int:
    """'300000.00' -> 30000000 копеек."""
    m = MONEY_RE.match(text.strip())
    if not m:
        raise ValueError(f"сумма {text!r} не распознана (ожидается вида 1234.56)")
    rub, kop = m.groups()
    return int(rub) * 100 + int((kop or "").ljust(2, "0"))


def parse_date(text: str) -> date:
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"дата {text!r} не распознана (ожидается ГГГГ-ММ-ДД или ДД.ММ.ГГГГ)")


def fmt_money(kop: int) -> str:
    """30000000 -> '300 000,00'; отрицательные со знаком минус."""
    sign = "-" if kop < 0 else ""
    rub, k = divmod(abs(kop), 100)
    return f"{sign}{rub:,}".replace(",", " ") + f",{k:02d}"


def fmt_money_csv(kop: int) -> str:
    """Для CSV под русский Excel: без пробелов, запятая как разделитель."""
    sign = "-" if kop < 0 else ""
    rub, k = divmod(abs(kop), 100)
    return f"{sign}{rub},{k:02d}"


def fmt_date(d: date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


# ---------------------------------------------------------------- модель данных

@dataclass(frozen=True)
class Contract:
    contract_id: str
    contractor: str
    amount: int
    signed_at: date


@dataclass(frozen=True)
class Work:
    work_id: str
    contract_id: str
    title: str
    price: int
    status: str
    completed_at: date | None
    cancelled_at: date | None

    def status_on(self, d: date) -> str:
        """Статус работы на дату d (статус в выгрузке - на момент выгрузки)."""
        if self.status == DONE and self.completed_at <= d:
            return DONE
        if self.status == CANCELLED and self.cancelled_at <= d:
            return CANCELLED
        return PLANNED


@dataclass(frozen=True)
class Payment:
    payment_id: str
    contract_id: str
    work_id: str | None
    purpose: str
    amount: int
    status: str
    created_at: date
    paid_at: date | None
    refunded_at: date | None

    def state_on(self, d: date) -> str | None:
        """Состояние платежа на дату d.

        PAID - деньги у подрядчика; QUEUED - заведён, но деньги ещё не ушли;
        REFUNDED - ушли и уже вернулись; None - на эту дату платежа ещё нет.
        """
        if self.created_at > d:
            return None
        if self.paid_at is None or self.paid_at > d:
            return QUEUED
        if self.refunded_at is not None and self.refunded_at <= d:
            return REFUNDED
        return PAID


# ---------------------------------------------------------------- чтение файлов

def read_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    if not path.exists():
        raise DataError(f"нет файла {path}")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            if None in row:
                raise DataError(f"{path.name}, строка {reader.line_num}: лишние колонки")
            if not any((v or "").strip() for v in row.values()):
                continue
            rows.append((reader.line_num, {k.strip(): (v or "").strip() for k, v in row.items()}))
        return rows


def _req(row: dict[str, str], key: str) -> str:
    value = row.get(key)
    if value is None:
        raise ValueError(f"нет колонки {key!r}")
    if value == "":
        raise ValueError(f"пустое поле {key!r}")
    return value


def _opt_date(row: dict[str, str], key: str) -> date | None:
    value = row.get(key, "")
    return parse_date(value) if value else None


def _parse_file(path: Path, build):
    result = []
    for line, row in read_rows(path):
        try:
            result.append(build(row))
        except ValueError as e:
            raise DataError(f"{path.name}, строка {line}: {e}") from None
    return result


def build_contract(r: dict[str, str]) -> Contract:
    return Contract(_req(r, "contract_id"), _req(r, "contractor"),
                    parse_money(_req(r, "amount")), parse_date(_req(r, "signed_at")))


def build_work(r: dict[str, str]) -> Work:
    w = Work(_req(r, "work_id"), _req(r, "contract_id"), _req(r, "title"),
             parse_money(_req(r, "price")), _req(r, "status"),
             _opt_date(r, "completed_at"), _opt_date(r, "cancelled_at"))
    if w.status not in (DONE, PLANNED, CANCELLED):
        raise ValueError(f"неизвестный статус работы {w.status!r}")
    if w.status == DONE and not w.completed_at:
        raise ValueError("работа done без completed_at")
    if w.status == CANCELLED and not w.cancelled_at:
        raise ValueError("работа cancelled без cancelled_at")
    return w


def build_payment(r: dict[str, str]) -> Payment:
    p = Payment(_req(r, "payment_id"), _req(r, "contract_id"), r.get("work_id") or None,
                r.get("purpose", ""), parse_money(_req(r, "amount")), _req(r, "status"),
                parse_date(_req(r, "created_at")),
                _opt_date(r, "paid_at"), _opt_date(r, "refunded_at"))
    if p.status not in (PAID, QUEUED, REFUNDED):
        raise ValueError(f"неизвестный статус платежа {p.status!r}")
    if p.status in (PAID, REFUNDED) and not p.paid_at:
        raise ValueError(f"платёж {p.status} без paid_at")
    if p.status == REFUNDED and not p.refunded_at:
        raise ValueError("платёж refunded без refunded_at")
    if p.status == QUEUED and p.paid_at:
        raise ValueError("платёж queued с заполненным paid_at")
    if p.refunded_at and p.paid_at and p.refunded_at < p.paid_at:
        raise ValueError("возврат раньше оплаты")
    return p


def load(data_dir: Path):
    contracts = _parse_file(data_dir / "contracts.csv", build_contract)
    works = _parse_file(data_dir / "works.csv", build_work)
    payments = _parse_file(data_dir / "payments.csv", build_payment)

    def unique(items, attr, name):
        seen = set()
        for it in items:
            key = getattr(it, attr)
            if key in seen:
                raise DataError(f"{name}: повторяется номер {key}")
            seen.add(key)
    unique(contracts, "contract_id", "contracts.csv")
    unique(works, "work_id", "works.csv")
    unique(payments, "payment_id", "payments.csv")

    contract_ids = {c.contract_id for c in contracts}
    work_contract = {w.work_id: w.contract_id for w in works}
    for w in works:
        if w.contract_id not in contract_ids:
            raise DataError(f"работа {w.work_id}: нет договора {w.contract_id}")
    for p in payments:
        if p.contract_id not in contract_ids:
            raise DataError(f"платёж {p.payment_id}: нет договора {p.contract_id}")
        if p.work_id is not None:
            if p.work_id not in work_contract:
                raise DataError(f"платёж {p.payment_id}: нет работы {p.work_id}")
            if work_contract[p.work_id] != p.contract_id:
                raise DataError(f"платёж {p.payment_id}: работа {p.work_id} "
                                f"относится к договору {work_contract[p.work_id]}, "
                                f"а не к {p.contract_id}")
    return contracts, works, payments


# ---------------------------------------------------------------- расчёт

@dataclass
class WorkRow:
    work: Work
    status: str          # статус на дату отчёта
    price: int           # к оплате: цена работы, для отменённой - 0
    direct: int = 0      # выплачено платежами с привязкой к этой работе
    allocated: int = 0   # разнесено из платежей по договору без привязки

    @property
    def paid(self) -> int:
        return self.direct + self.allocated

    @property
    def remaining(self) -> int:
        return self.price - self.paid


@dataclass
class ContractReport:
    contract: Contract
    rows: list[WorkRow]
    unlinked_paid: int   # выплачено по договору без привязки к работе
    unallocated: int     # из них не на что разнести - переплата
    queued: int          # заведено, но ещё не оплачено на дату
    refunded: int        # ушло и вернулось к дате
    warnings: list[str]

    @property
    def price(self) -> int:
        return sum(r.price for r in self.rows)

    @property
    def paid(self) -> int:
        return sum(r.direct for r in self.rows) + self.unlinked_paid

    @property
    def remaining(self) -> int:
        return sum(max(r.remaining, 0) for r in self.rows)

    @property
    def remaining_done(self) -> int:
        return sum(max(r.remaining, 0) for r in self.rows if r.status == DONE)

    @property
    def overpaid(self) -> int:
        return self.unallocated + sum(-r.remaining for r in self.rows if r.remaining < 0)


def allocation_order(rows: list[WorkRow]) -> list[WorkRow]:
    """Порядок разнесения платежей без привязки.

    Сначала выполненные работы - по дате приёмки (раньше принята - раньше
    закрывается), затем запланированные - в порядке номеров. Отменённые
    не участвуют: платить за них не нужно.
    """
    done = sorted((r for r in rows if r.status == DONE),
                  key=lambda r: (r.work.completed_at, r.work.work_id))
    planned = sorted((r for r in rows if r.status == PLANNED), key=lambda r: r.work.work_id)
    return done + planned


def report_for(contract: Contract, works: list[Work], payments: list[Payment],
               on: date) -> ContractReport:
    rows = {w.work_id: WorkRow(w, w.status_on(on), 0 if w.status_on(on) == CANCELLED else w.price)
            for w in works}
    warnings: list[str] = []
    unlinked = queued = refunded = 0

    for p in payments:
        state = p.state_on(on)
        if state == QUEUED:
            queued += p.amount
            warnings.append(f"Платёж {p.payment_id} «{p.purpose}» на {fmt_money(p.amount)} руб. "
                            f"заведён {fmt_date(p.created_at)}, но на дату не оплачен - "
                            f"в «выплачено» не входит.")
        elif state == REFUNDED:
            refunded += p.amount
        elif state == PAID:
            if p.work_id is None:
                unlinked += p.amount
            else:
                row = rows[p.work_id]
                row.direct += p.amount
                if row.status == CANCELLED:
                    warnings.append(f"Платёж {p.payment_id} привязан к отменённой работе "
                                    f"{p.work_id} - числится переплатой по ней.")
            if p.paid_at < contract.signed_at:
                warnings.append(f"Платёж {p.payment_id} оплачен {fmt_date(p.paid_at)}, "
                                f"раньше подписания договора {fmt_date(contract.signed_at)}.")

    left = unlinked
    for row in allocation_order(list(rows.values())):
        if left == 0:
            break
        take = min(left, max(row.remaining, 0))
        row.allocated += take
        left -= take

    rep = ContractReport(contract, list(rows.values()), unlinked, left, queued, refunded, warnings)

    active = rep.price
    if active != contract.amount:
        warnings.append(f"Сумма договора {fmt_money(contract.amount)} руб. не совпадает с ценой "
                        f"неотменённых работ {fmt_money(active)} руб. - расчёт ведётся по работам.")
    if rep.overpaid:
        warnings.append(f"Переплата {fmt_money(rep.overpaid)} руб.: выплачено больше, "
                        f"чем стоят неотменённые работы.")
    return rep


# ---------------------------------------------------------------- вывод

STATUS_RU = {DONE: "выполнена", PLANNED: "запланирована", CANCELLED: "отменена"}
WORK_COLUMNS = ["Работа", "Название", "Статус на дату", "Цена", "Выплачено",
                "из них по работе", "из них из платежей по договору", "Осталось оплатить"]


def work_cells(r: WorkRow, money) -> list[str]:
    return [r.work.work_id, r.work.title, STATUS_RU[r.status], money(r.price),
            money(r.paid), money(r.direct), money(r.allocated), money(r.remaining)]


def table(text_rows: list[list[str]], right_from: int) -> str:
    widths = [max(len(c) for c in col) for col in zip(*text_rows)]
    out = []
    for i, row in enumerate(text_rows):
        cells = [c.rjust(w) if j >= right_from else c.ljust(w)
                 for j, (c, w) in enumerate(zip(row, widths))]
        out.append("  ".join(cells).rstrip())
        if i == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)


def print_report(reports: list[ContractReport], on: date) -> None:
    print(f"Отчёт об оплате работ на {fmt_date(on)}")
    print("Выплачено = деньги ушли подрядчику не позже этой даты и не вернулись.")
    print("Платежи по договору без привязки к работе разнесены по работам: сначала "
          "выполненные по дате приёмки, затем запланированные. Отменённые работы к оплате не идут.")
    for rep in reports:
        c = rep.contract
        print()
        print(f"Договор {c.contract_id}, {c.contractor}, от {fmt_date(c.signed_at)}, "
              f"сумма {fmt_money(c.amount)} руб.")
        rows = [WORK_COLUMNS] + [work_cells(r, fmt_money) for r in rep.rows]
        rows.append(["Итого", "", "", fmt_money(rep.price), fmt_money(rep.paid),
                     "", "", fmt_money(rep.remaining)])
        print(table(rows, right_from=3))
        print(f"  Выплачено по договору всего: {fmt_money(rep.paid)} руб.")
        print(f"  Осталось оплатить: {fmt_money(rep.remaining)} руб., из них за уже "
              f"выполненные работы: {fmt_money(rep.remaining_done)} руб.")
        if rep.queued:
            print(f"  В очереди на оплату: {fmt_money(rep.queued)} руб.")
        if rep.refunded:
            print(f"  Возвращено подрядчиком (в расчёте не участвует): {fmt_money(rep.refunded)} руб.")
        for w in rep.warnings:
            print(f"  ! {w}")
    print()
    print(f"Всего по договорам: выплачено {fmt_money(sum(r.paid for r in reports))} руб., "
          f"осталось оплатить {fmt_money(sum(r.remaining for r in reports))} руб.")


def write_csv(path: Path, reports: list[ContractReport], on: date) -> None:
    """CSV под русский Excel: разделитель ';', десятичная запятая, UTF-8 с BOM."""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Дата отчёта", fmt_date(on)])
        w.writerow(["Договор", "Подрядчик"] + WORK_COLUMNS)
        for rep in reports:
            head = [rep.contract.contract_id, rep.contract.contractor]
            for r in rep.rows:
                w.writerow(head + work_cells(r, fmt_money_csv))
            w.writerow(head + ["ИТОГО", "", "", fmt_money_csv(rep.price), fmt_money_csv(rep.paid),
                               "", "", fmt_money_csv(rep.remaining)])


# ---------------------------------------------------------------- запуск

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Отчёт об оплате работ по договорам на дату.")
    ap.add_argument("--date", required=True, help="дата отчёта: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ")
    ap.add_argument("--data", default=str(Path(__file__).parent / "data"),
                    help="папка с contracts.csv, works.csv, payments.csv")
    ap.add_argument("--csv", help="дополнительно сохранить отчёт в CSV-файл")
    args = ap.parse_args(argv)
    try:
        on = parse_date(args.date)
        contracts, works, payments = load(Path(args.data))
    except (DataError, ValueError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1

    reports = [report_for(c, [w for w in works if w.contract_id == c.contract_id],
                          [p for p in payments if p.contract_id == c.contract_id], on)
               for c in contracts]
    print_report(reports, on)
    if args.csv:
        write_csv(Path(args.csv), reports, on)
        print(f"\nCSV сохранён: {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


