import io
from datetime import date
from decimal import Decimal

import openpyxl

from app.schemas.portfolio import CsvColumnMapping
from app.services.csv_importer import (
    extract_csv_text_from_upload,
    make_tx_signature,
    parse_mapped_csv,
    preview_csv,
)


def test_ktrade_preset_preview_and_parse():
    csv_text = """Trade Date,Symbol,Side,Volume,Price,Commission,SST,Order ID
2025-02-14,MEBL,BUY,500,220.50,165.38,21.50,ORD-101
15/03/2025,SYS,SELL,100,540.00,81.00,10.53,ORD-102
"""
    preview = preview_csv(csv_text, preset="ktrade")
    assert preview["suggested_mapping"]["symbol"] == "Symbol"
    assert preview["suggested_mapping"]["tx_date"] == "Trade Date"

    mapping = CsvColumnMapping(**preview["suggested_mapping"])
    rows, errors = parse_mapped_csv(csv_text, mapping)
    assert errors == []
    assert len(rows) == 2
    assert rows[0]["symbol"] == "MEBL"
    assert rows[0]["tx_type"] == "BUY"
    assert rows[0]["quantity"] == Decimal("500")
    assert rows[1]["tx_type"] == "SELL"


def test_drip_alias_and_historical_ticker_parsing():
    csv_text = """Transaction Date,Fund Code,Type,Units,NAV,Front End Load,CGT/WHT,Folio No
2025-04-01,MCF,DRIP,24.50,51.20,0,188.16,FOL-882
2025-04-15,DAWH,BUY,200,192.00,57.60,7.49,CDC-901
"""
    preview = preview_csv(csv_text, preset="amc_mutual_fund")
    mapping = CsvColumnMapping(**preview["suggested_mapping"])
    rows, errors = parse_mapped_csv(csv_text, mapping)
    assert errors == []
    assert len(rows) == 2
    assert rows[0]["tx_type"] == "REINVEST_DIVIDEND"
    assert rows[0]["quantity"] == Decimal("24.50")
    assert rows[1]["symbol"] == "DAWH"
    assert rows[1]["tx_type"] == "BUY"


def test_excel_xlsx_upload_extraction_and_duplicate_signature():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Trade Date", "Symbol", "Side", "Volume", "Price", "Commission"])
    ws.append(["2025-05-12", "ENGRO", "BUY", 100, 325.5, 48.83])
    buf = io.BytesIO()
    wb.save(buf)

    csv_text = extract_csv_text_from_upload("broker_statement.xlsx", buf.getvalue())
    preview = preview_csv(csv_text, preset="ktrade")
    mapping = CsvColumnMapping(**preview["suggested_mapping"])
    rows, errors = parse_mapped_csv(csv_text, mapping)

    assert errors == []
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ENGRO"
    assert rows[0]["unit_price"] == Decimal("325.5")

    sig_a = make_tx_signature(date(2025, 5, 12), "engro", "buy", Decimal("100"), Decimal("325.50"))
    sig_b = make_tx_signature(date(2025, 5, 12), "ENGRO", "BUY", Decimal("100.0000"), Decimal("325.5"))
    assert sig_a == sig_b
