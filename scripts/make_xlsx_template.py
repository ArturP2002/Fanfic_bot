"""Создаёт Excel-шаблон стартовой базы из CSV."""

from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "templates" / "works_template.csv"
XLSX_PATH = ROOT / "data" / "templates" / "works_template.xlsx"


def main() -> None:
    """Читает CSV и сохраняет XLSX с теми же данными."""
    import csv

    wb = Workbook()
    ws = wb.active
    ws.title = "works"
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            ws.append(row)
    wb.save(XLSX_PATH)
    print(f"Сохранён шаблон: {XLSX_PATH}")


if __name__ == "__main__":
    main()
