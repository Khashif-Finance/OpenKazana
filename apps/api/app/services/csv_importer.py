from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl import load_workbook

from app.schemas.portfolio import CsvColumnMapping

# Built-in column mapping presets for major Pakistani brokers, CDC, and AMCs
BROKER_CSV_PRESETS: dict[str, dict[str, str]] = {
    "generic": {
        "tx_date": "date",
        "symbol": "symbol",
        "tx_type": "type",
        "quantity": "quantity",
        "unit_price": "price",
        "fee_amount": "fee",
        "tax_withheld": "tax",
        "notes": "notes",
    },
    "cdc": {
        "tx_date": "Settlement Date",
        "symbol": "Security Symbol",
        "tx_type": "Transaction Type",
        "quantity": "Quantity",
        "unit_price": "Rate",
        "fee_amount": "CDC Charges",
        "tax_withheld": "Tax",
        "notes": "Remarks",
    },
    "ktrade": {
        "tx_date": "Trade Date",
        "symbol": "Symbol",
        "tx_type": "Side",
        "quantity": "Volume",
        "unit_price": "Price",
        "fee_amount": "Commission",
        "tax_withheld": "SST",
        "notes": "Order ID",
    },
    "finqalab": {
        "tx_date": "Date",
        "symbol": "Ticker",
        "tx_type": "Action",
        "quantity": "Shares",
        "unit_price": "Execution Price",
        "fee_amount": "Brokerage",
        "tax_withheld": "Taxes",
        "notes": "Reference",
    },
    "akd": {
        "tx_date": "Trx Date",
        "symbol": "Scrip",
        "tx_type": "Buy/Sell",
        "quantity": "Qty",
        "unit_price": "Avg Rate",
        "fee_amount": "Comm",
        "tax_withheld": "WHT/SST",
        "notes": "Narrative",
    },
    "amc_mutual_fund": {
        "tx_date": "Transaction Date",
        "symbol": "Fund Code",
        "tx_type": "Type",
        "quantity": "Units",
        "unit_price": "NAV",
        "fee_amount": "Front End Load",
        "tax_withheld": "CGT/WHT",
        "notes": "Folio No",
    },
    "ibkr": {
        "tx_date": "Date/Time",
        "symbol": "Symbol",
        "tx_type": "Code",
        "quantity": "Quantity",
        "unit_price": "T. Price",
        "fee_amount": "Comm/Fee",
        "tax_withheld": "Withholding Tax",
        "notes": "Exchange",
    },
    "elphinstone": {
        "tx_date": "Trade Date",
        "symbol": "Ticker",
        "tx_type": "Side",
        "quantity": "Shares",
        "unit_price": "Fill Price (USD)",
        "fee_amount": "Platform Fee",
        "tax_withheld": "US WHT (W-8BEN)",
        "notes": "Order Ref",
    },
    "sarwa": {
        "tx_date": "Date",
        "symbol": "Asset",
        "tx_type": "Activity",
        "quantity": "Quantity",
        "unit_price": "Price",
        "fee_amount": "Fee",
        "tax_withheld": "Tax",
        "notes": "Description",
    },
}

TX_TYPE_ALIASES: dict[str, str] = {
    "B": "BUY",
    "BUY": "BUY",
    "PURCHASE": "BUY",
    "INVESTMENT": "BUY",
    "SUBSCRIPTION": "BUY",
    "S": "SELL",
    "SELL": "SELL",
    "SALE": "SELL",
    "REDEMPTION": "SELL",
    "DIV": "DIVIDEND",
    "DIVIDEND": "DIVIDEND",
    "PAYOUT": "DIVIDEND",
    "DRIP": "REINVEST_DIVIDEND",
    "REINVEST": "REINVEST_DIVIDEND",
    "REINVEST_DIVIDEND": "REINVEST_DIVIDEND",
    "BONUS": "BONUS",
    "BON": "BONUS",
    "RIGHT": "RIGHTS",
    "RIGHTS": "RIGHTS",
    "SPLIT": "SPLIT",
    "OPEN": "OPENING_BALANCE",
    "OPENING": "OPENING_BALANCE",
    "OPENING_BALANCE": "OPENING_BALANCE",
    "DEPOSIT": "DEPOSIT",
    "WITHDRAWAL": "WITHDRAWAL",
    "FEE": "FEE",
}


def _parse_date(raw: str) -> date:
    cleaned = raw.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d-%b-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: '{raw}'")


def _parse_decimal(raw: str | None, default: Decimal = Decimal("0")) -> Decimal:
    if raw is None:
        return default
    cleaned = raw.strip().replace(",", "").replace("PKR", "").replace("Rs.", "").strip()
    if not cleaned or cleaned in {"-", "--", "N/A"}:
        return default
    try:
        return abs(Decimal(cleaned))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid numeric value: '{raw}'") from exc


def extract_csv_text_from_upload(filename: str, raw_bytes: bytes) -> str:
    """Normalize uploaded CSV or Excel (.xlsx/.xlsm) file bytes into clean UTF-8 CSV text."""
    lower_name = (filename or "").lower()
    if lower_name.endswith((".xlsx", ".xlsm")):
        wb = load_workbook(io.BytesIO(raw_bytes), data_only=True, read_only=True)
        ws = wb.active
        if ws is None:
            return ""
        raw_rows: list[list[str]] = []
        for row in ws.iter_rows(values_only=True):
            formatted: list[str] = []
            non_empty = 0
            for cell in row:
                if cell is None:
                    formatted.append("")
                elif isinstance(cell, datetime):
                    formatted.append(cell.date().isoformat())
                    non_empty += 1
                elif isinstance(cell, date):
                    formatted.append(cell.isoformat())
                    non_empty += 1
                else:
                    s = str(cell).strip()
                    formatted.append(s)
                    if s:
                        non_empty += 1
            if non_empty > 0:
                raw_rows.append(formatted)

        # Skip leading broker report banner rows until the first multi-column header row (>=3 populated cells)
        start_idx = 0
        for idx, r in enumerate(raw_rows):
            if sum(1 for c in r if c) >= 3:
                start_idx = idx
                break

        buf = io.StringIO()
        writer = csv.writer(buf)
        for r in raw_rows[start_idx:]:
            writer.writerow(r)
        return buf.getvalue()

    try:
        return raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw_bytes.decode("latin-1", errors="replace")


def make_tx_signature(tx_date: date, symbol: str, tx_type: str, quantity: Decimal, unit_price: Decimal) -> tuple[str, str, str, str, str]:
    """Deterministic fingerprint for duplicate transaction detection during imports."""
    return (
        tx_date.isoformat(),
        symbol.strip().upper(),
        tx_type.strip().upper(),
        str(quantity.quantize(Decimal("0.0001"))),
        str(unit_price.quantize(Decimal("0.01"))),
    )


def preview_csv(csv_text: str, preset: str = "generic") -> dict[str, Any]:
    """Parse CSV headers and sample rows, suggesting column mapping based on preset."""
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    headers = list(reader.fieldnames or [])
    sample_rows = []
    for idx, row in enumerate(reader):
        if idx >= 8:
            break
        sample_rows.append(row)

    template = BROKER_CSV_PRESETS.get(preset, BROKER_CSV_PRESETS["generic"])
    lower_headers = {h.lower().strip(): h for h in headers}
    suggested: dict[str, str | None] = {}
    for field_name, target_col in template.items():
        match = lower_headers.get(target_col.lower())
        suggested[field_name] = match

    return {
        "headers": headers,
        "sample_rows": sample_rows,
        "suggested_mapping": suggested,
        "available_presets": list(BROKER_CSV_PRESETS.keys()),
    }


def parse_mapped_csv(csv_text: str, mapping: CsvColumnMapping) -> tuple[list[dict], list[str]]:
    """Parse CSV rows using user-confirmed column mapping into normalized transaction dicts."""
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    parsed_rows: list[dict] = []
    errors: list[str] = []

    for row_num, row in enumerate(reader, start=2):
        try:
            raw_date = row.get(mapping.tx_date, "")
            raw_sym = (row.get(mapping.symbol) or "").strip().upper()
            raw_type = (row.get(mapping.tx_type) or "").strip().upper()

            if not raw_sym:
                raise ValueError("Missing asset symbol")

            tx_type = TX_TYPE_ALIASES.get(raw_type)
            if not tx_type:
                raise ValueError(f"Unsupported transaction type '{raw_type}'")

            tx_date = _parse_date(raw_date)
            quantity = _parse_decimal(row.get(mapping.quantity))
            unit_price = _parse_decimal(row.get(mapping.unit_price))
            fee_amount = _parse_decimal(row.get(mapping.fee_amount)) if mapping.fee_amount else Decimal("0")
            tax_withheld = _parse_decimal(row.get(mapping.tax_withheld)) if mapping.tax_withheld else Decimal("0")
            notes = (row.get(mapping.notes) or "").strip() if mapping.notes else ""

            parsed_rows.append(
                {
                    "tx_date": tx_date,
                    "symbol": raw_sym,
                    "tx_type": tx_type,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "fee_amount": fee_amount,
                    "tax_withheld": tax_withheld,
                    "notes": notes,
                }
            )
        except Exception as exc:
            errors.append(f"Row {row_num}: {exc}")

    return parsed_rows, errors
