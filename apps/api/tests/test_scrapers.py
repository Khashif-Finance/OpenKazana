from datetime import date, datetime, timezone
from decimal import Decimal

from app.scrapers.base import is_psx_market_open
from app.scrapers.fx_gold import parse_fx_metals_rates
from app.scrapers.mufap import parse_mufap_nav_html
from app.scrapers.psx import (
    _to_decimal,
    parse_market_watch_html,
    parse_psx_announcements_html,
    parse_psx_company_fundamentals_html,
    parse_psx_indices_html,
    parse_timeseries_json,
)


def test_psx_market_hours_pkt():
    # Monday 10:30 PKT = 05:30 UTC
    assert is_psx_market_open(datetime(2026, 11, 9, 5, 30, tzinfo=timezone.utc)) is True
    # Friday Jummah break 13:00 PKT = 08:00 UTC
    assert is_psx_market_open(datetime(2026, 11, 13, 8, 0, tzinfo=timezone.utc)) is False
    # Sunday closed
    assert is_psx_market_open(datetime(2026, 11, 15, 6, 0, tzinfo=timezone.utc)) is False


def test_parse_psx_market_watch_html_ohlcv():
    html = """
    <table><tbody>
      <tr>
        <td>MEBL</td><td>Commercial Banks</td><td>-</td><td>240.00</td>
        <td>241.00</td><td>248.00</td><td>239.50</td><td>246.80</td>
        <td>6.80</td><td>2.83%</td><td>3,150,400</td>
      </tr>
    </tbody></table>
    """
    rows = parse_market_watch_html(html)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "MEBL"
    assert rows[0]["prev_close"] == Decimal("240.00")
    assert rows[0]["open"] == Decimal("241.00")
    assert rows[0]["high"] == Decimal("248.00")
    assert rows[0]["low"] == Decimal("239.50")
    assert rows[0]["last_price"] == Decimal("246.80")
    assert rows[0]["volume"] == Decimal("3150400")


def test_parse_psx_timeseries_json():
    payload = {"status": 1, "data": [[1731456000, 246.80, 3150400, 241.00, 248.00, 239.50]]}
    bars = parse_timeseries_json(payload)
    assert len(bars) == 1
    assert bars[0]["close"] == Decimal("246.80")
    assert bars[0]["high"] == Decimal("248.00")


def test_parse_mufap_nav_html():
    html = """
    <table><tbody>
      <tr>
        <td>Meezan Islamic Fund</td>
        <td>Al Meezan Investment Management</td>
        <td>Shariah Compliant Equity</td>
        <td>102.00</td>
        <td>104.62</td>
        <td>101.50</td>
      </tr>
    </tbody></table>
    """
    funds = parse_mufap_nav_html(html)
    assert len(funds) == 1
    assert funds[0]["nav"] == Decimal("104.62")
    assert funds[0]["is_shariah"] is True


def test_parse_psx_indices_fundamentals_and_pucars():
    indices_html = """
    <table><tbody>
      <tr><td>KSE100</td><td>94,100.00</td><td>93,200.00</td><td>94,550.25</td><td>+1.25%</td><td>220,500,000</td></tr>
      <tr><td>KMI30</td><td>142,000.00</td><td>140,800.00</td><td>142,500.00</td><td>+0.80%</td><td>95,000,000</td></tr>
    </tbody></table>
    """
    indices = parse_psx_indices_html(indices_html)
    by_sym = {i["symbol"]: i for i in indices}
    assert by_sym["KSE100"]["last_price"] == Decimal("94550.25")
    assert by_sym["KMI30"]["change_pct"] == Decimal("0.80")

    company_html = """
    <div class="stats_item"><div class="stats_label">52-Week Range</div><div class="stats_value">180.50 - 265.00</div></div>
    <div class="stats_item"><div class="stats_label">Market Cap</div><div class="stats_value">420,500</div></div>
    <div class="stats_item"><div class="stats_label">P/E Ratio</div><div class="stats_value">6.85</div></div>
    <div class="stats_item"><div class="stats_label">Dividend Yield</div><div class="stats_value">9.20%</div></div>
    <table>
      <thead><tr><th>Ex-Date</th><th>Payout Entitlement</th><th>Book Closure</th></tr></thead>
      <tbody>
        <tr><td>2025-11-10</td><td>100% (I) Cash Dividend</td><td>2025-11-12</td></tr>
        <tr><td>2025-04-01</td><td>20% Bonus Shares</td><td>2025-04-03</td></tr>
      </tbody>
    </table>
    """
    fund = parse_psx_company_fundamentals_html(company_html, "MEBL")
    assert fund["pe_ratio"] == Decimal("6.85")
    assert fund["dividend_yield_pct"] == Decimal("9.20")
    assert fund["low_52w"] == Decimal("180.50")
    assert fund["high_52w"] == Decimal("265.00")
    assert len(fund["payouts"]) == 2
    assert fund["payouts"][0]["action_type"] == "CASH_DIVIDEND"
    assert fund["payouts"][0]["payout_per_share"] == Decimal("10.00")
    assert fund["payouts"][1]["action_type"] == "BONUS_ISSUE"
    assert fund["payouts"][1]["ex_date"] == date(2025, 4, 1)

    ann_html = """
    <table><tbody>
      <tr><td>2025-11-05</td><td>MEBL</td><td>Board Meeting &amp; Interim Cash Dividend Announcement</td><td><a href="/announcements/123">PDF</a></td></tr>
    </tbody></table>
    """
    anns = parse_psx_announcements_html(ann_html)
    assert len(anns) == 1
    assert anns[0]["symbol"] == "MEBL"
    assert anns[0]["source_url"].endswith("/announcements/123")


def test_parse_mufap_expanded_and_fx_silver_rates():
    mufap_html = """
    <table>
      <thead>
        <tr><th>Fund Name</th><th>AMC</th><th>Category</th><th>Offer</th><th>Repurchase NAV</th><th>Redemption</th><th>TER</th><th>1Y Return</th></tr>
      </thead>
      <tbody>
        <tr><td>Al Ameen Islamic Voluntary Pension Equity</td><td>UBL Fund Managers</td><td>VPS Equity</td><td>310.00</td><td>312.50</td><td>310.00</td><td>1.85%</td><td>42.50%</td></tr>
      </tbody>
    </table>
    """
    funds = parse_mufap_nav_html(mufap_html)
    assert len(funds) == 1
    assert funds[0]["asset_class"] == "VPS_PENSION"
    assert funds[0]["ter_pct"] == Decimal("1.85")
    assert funds[0]["return_1y_pct"] == Decimal("42.50")

    metals = parse_fx_metals_rates({"PKR": 280, "EUR": 0.92, "AED": 3.6725}, gold_usd_oz=Decimal("2600"), silver_usd_oz=Decimal("31"))
    assert metals["USD_PKR_INTERBANK"][3] == Decimal("280")
    assert metals["USD_PKR_OPEN"][3] > Decimal("280")
    assert "EURPKR" in metals
    assert metals["SILVER_GRAM"][3] > Decimal("250")
    assert metals["GOLD_24K_GRAM"][3] > Decimal("23000")


def test_parse_psx_trillion_mcap_margins_reg_vs_fut_and_post_announcements():
    assert _to_decimal("1.0T") == Decimal("1000000.0")

    live_company_html = """
    <div class="quote__close">Rs.556.25</div>
    <div class="quote__change">-1.93 (-0.35%)</div>
    <div class="stats_item"><div class="stats_label">LDCP</div><div class="stats_value">558.18</div></div>
    <div class="stats_item"><div class="stats_label">Volume</div><div class="stats_value">372,147</div></div>
    <div class="stats_item"><div class="stats_label">P/E Ratio (TTM) **</div><div class="stats_value">10.43</div></div>
    <div class="stats_item"><div class="stats_label">52-WEEK RANGE ^</div><div class="stats_value">410.00 — 605.00</div></div>
    <div class="stats_item"><div class="stats_label">LDCP</div><div class="stats_value">564.65</div></div>
    <div class="stats_item"><div class="stats_label">Volume</div><div class="stats_value">500</div></div>
    <table><tbody>
      <tr><th>Market Cap (000's)</th><td>1,005,197,399.20</td></tr>
      <tr><th>Free Float</th><td>451,774,112</td></tr>
      <tr><th>Free Float</th><td>25.00%</td></tr>
    </tbody></table>
    <table>
      <thead><tr><th>Date</th><th>Title</th><th>Document</th></tr></thead>
      <tbody>
        <tr><td>Oct 22, 2026</td><td>FINANCIAL RESULTS FOR THE QUARTER AND NINE MONTHS ENDED SEPTEMBER 30, 2026</td><td><a href="javascript:">View</a> <a href="/download/document/288001.pdf">View PDF</a></td></tr>
      </tbody>
    </table>
    <table>
      <thead><tr><th></th><th>2025</th><th>2024</th></tr></thead>
      <tbody>
        <tr><td>Mark-up Earned</td><td>420,462,297</td><td>494,296,852</td></tr>
        <tr><td>Profit after Taxation</td><td>89,041,250</td><td>101,507,524</td></tr>
        <tr><td>EPS</td><td>49.54</td><td>56.62</td></tr>
      </tbody>
    </table>
    <table>
      <thead><tr><th></th><th>Q3 2026</th><th>Q2 2026</th></tr></thead>
      <tbody>
        <tr><td>Mark-up Earned</td><td>130,193,985</td><td>116,955,530</td></tr>
        <tr><td>Profit after Taxation</td><td>25,653,854</td><td>25,475,187</td></tr>
        <tr><td>EPS</td><td>14.20</td><td>14.15</td></tr>
      </tbody>
    </table>
    <table>
      <thead><tr><th></th><th>2025</th><th>2024</th></tr></thead>
      <tbody>
        <tr><td>Net Profit Margin (%)</td><td>21.18</td><td>20.54</td></tr>
      </tbody>
    </table>
    """
    parsed = parse_psx_company_fundamentals_html(live_company_html, "MEBL")
    assert parsed["spot_price"] == Decimal("556.25")
    assert parsed["prev_close"] == Decimal("558.18")
    assert parsed["volume"] == Decimal("372147")
    assert parsed["pe_ratio"] == Decimal("10.43")
    assert parsed["roe_pct"] is None
    assert parsed["market_cap_mn"] == Decimal("1005197.40")
    assert isinstance(parsed["financial_summary_json"], dict)
    ann_rows = parsed["financial_summary_json"]["annual_periods"]
    qtr_rows = parsed["financial_summary_json"]["quarterly_periods"]
    assert ann_rows[-1]["period"] == "2025"
    assert ann_rows[-1]["profit_after_tax"] == 89041.25
    assert ann_rows[-1]["net_margin_pct"] == 21.18
    assert len(qtr_rows) == 2
    assert qtr_rows[0]["period"] == "Q3 2026"
    assert qtr_rows[0]["eps"] == 14.20
    assert len(parsed["announcements"]) == 1
    assert parsed["announcements"][0]["source_url"].endswith("/download/document/288001.pdf")

    post_ann_html = """
    <table><tbody>
      <tr>
        <td>Nov 13, 2026</td>
        <td>4:29 PM</td>
        <td><a href="/company/MACTER">MACTER</a></td>
        <td><a href="/company/MACTER">Macter International Limited</a></td>
        <td>Briefing</td>
        <td><a href="javascript:">View</a> <a href="/download/document/289197.pdf">View PDF</a></td>
      </tr>
    </tbody></table>
    """
    post_anns = parse_psx_announcements_html(post_ann_html)
    assert len(post_anns) == 1
    assert post_anns[0]["symbol"] == "MACTER"
    assert post_anns[0]["title"] == "Briefing"
    assert post_anns[0]["source_url"].endswith("/download/document/289197.pdf")
