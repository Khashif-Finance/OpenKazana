"use client";

import React, { useEffect, useState } from "react";
import { useAppContext } from "@/components/CurrencyContext";
import { apiFetch } from "@/lib/api";
import { formatMoney } from "@/lib/format";

interface OverviewAsset {
  symbol: string;
  last_price: string;
}

interface YearlyScheduleRow {
  year: number;
  invested_pkr: string;
  projected_value_pkr: string;
  gain_pkr: string;
}

export default function CalculatorsPage() {
  const { user, currency, usdToPkr } = useAppContext();
  const [tab, setTab] = useState<"sip" | "daily_div" | "cgt" | "zakat">("sip");

  // Daily Dividend Fund (Constant-NAV DRIP vs Cash) state
  const [dailyDivInput, setDailyDivInput] = useState({
    principal_pkr: "1000000",
    constant_par_nav: "50",
    annual_yield_pct: "17.85",
    days: 365,
    filer_status: "ACTIVE_FILER",
    mode: "REINVEST",
  });
  const [dailyDivResult, setDailyDivResult] = useState<{
    principal_pkr: string;
    initial_units: string;
    final_units: string;
    final_value_pkr: string;
    total_gross_dividend_pkr: string;
    total_wht_pkr: string;
    total_net_dividend_pkr: string;
    effective_net_annual_yield_pct: string;
    daily_gross_dividend_day1_pkr: string;
    daily_net_dividend_day1_pkr: string;
    monthly_schedule: Array<{
      month: number;
      days_elapsed: number;
      units: string;
      cumulative_gross_pkr: string;
      cumulative_wht_pkr: string;
      cumulative_net_pkr: string;
      total_wealth_pkr: string;
    }>;
  } | null>(null);

  // SIP state
  const [sipInput, setSipInput] = useState({
    initial_investment_pkr: "100000",
    monthly_contribution_pkr: "25000",
    annual_return_pct: "18",
    years: 10,
    front_end_load_pct: "1.5",
    ter_pct: "2.0",
  });
  const [sipResult, setSipResult] = useState<{
    total_invested_pkr: string;
    total_load_paid_pkr: string;
    projected_value_pkr: string;
    net_gain_pkr: string;
    effective_cagr_pct: string;
    yearly_schedule?: YearlyScheduleRow[];
  } | null>(null);

  // Brokerage & CGT state (Pakistan FBR Acquisition Regimes + 1-leg intraday)
  const [cgtInput, setCgtInput] = useState({
    buy_price: "200",
    sell_price: "250",
    quantity: "500",
    broker_rate_bps: 15,
    filer_status: "ACTIVE_FILER",
    acquisition_date: "2024-08-01",
    is_intraday_square_off: false,
  });
  const [cgtResult, setCgtResult] = useState<{
    buy_charges_pkr: string;
    sell_charges_pkr: string;
    total_charges_pkr: string;
    gross_profit_pkr: string;
    cgt_rate_pct: string;
    cgt_amount_pkr: string;
    net_profit_pkr: string;
    breakeven_sell_price: string;
  } | null>(null);

  // Zakat state
  const [zakatInput, setZakatInput] = useState({
    cash_and_bank_pkr: "350000",
    psx_stocks_pkr: "1200000",
    mutual_funds_pkr: "500000",
    gold_and_silver_pkr: "400000",
    forex_and_crypto_pkr: "250000",
    short_term_liabilities_pkr: "100000",
    nisab_standard: "SILVER",
    silver_price_per_gram_pkr: "285",
    gold_price_per_gram_pkr: "24200",
  });
  const [spotRateSource, setSpotRateSource] = useState<"default" | "live">("default");
  const [loadingPortfolioZakat, setLoadingPortfolioZakat] = useState(false);
  const [zakatNotice, setZakatNotice] = useState<string | null>(null);
  const [zakatResult, setZakatResult] = useState<{
    net_zakatable_wealth_pkr: string;
    nisab_threshold_pkr: string;
    is_zakat_due: boolean;
    zakat_payable_pkr: string;
  } | null>(null);

  useEffect(() => {
    apiFetch<{ gold_and_fx?: OverviewAsset[] }>("/api/v1/market/overview")
      .then((ov) => {
        const items = ov.gold_and_fx || [];
        const gold = items.find((a) => a.symbol === "GOLD_24K_GRAM");
        const silver = items.find((a) => a.symbol === "SILVER_GRAM");
        if (gold || silver) {
          setZakatInput((prev) => ({
            ...prev,
            gold_price_per_gram_pkr: gold ? String(Math.round(Number(gold.last_price) * 100) / 100) : prev.gold_price_per_gram_pkr,
            silver_price_per_gram_pkr: silver ? String(Math.round(Number(silver.last_price) * 100) / 100) : prev.silver_price_per_gram_pkr,
          }));
          setSpotRateSource("live");
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!user?.tax_profile) return;
    setCgtInput((prev) => ({
      ...prev,
      broker_rate_bps: user.tax_profile?.default_broker_rate_bps ?? prev.broker_rate_bps,
      filer_status: user.tax_profile?.filer_status === "FILER" ? "ACTIVE_FILER" : user.tax_profile?.filer_status || prev.filer_status,
    }));
  }, [user]);

  const handleLoadZakatFromPortfolio = async () => {
    if (!user) return;
    setLoadingPortfolioZakat(true);
    setZakatNotice(null);
    try {
      const portfolios = await apiFetch<Array<{ id: string; name: string }>>("/api/v1/portfolios");
      if (portfolios.length === 0) {
        setZakatNotice("No portfolios found in your account.");
        return;
      }
      const sum = await apiFetch<{
        holdings: Array<{ asset_class: string; market_value_pkr: string }>;
      }>(`/api/v1/portfolios/${portfolios[0].id}/summary`);

      let cash = 0;
      let stocks = 0;
      let funds = 0;
      let metals = 0;
      let fxCrypto = 0;
      for (const h of sum.holdings) {
        const val = Math.max(0, Number(h.market_value_pkr) || 0);
        if (h.asset_class === "CASH" || h.asset_class === "NSS") cash += val;
        else if (h.asset_class === "PSX_EQUITY" || h.asset_class === "PSX_ETF") stocks += val;
        else if (h.asset_class === "MUTUAL_FUND") funds += val;
        else if (h.asset_class === "GOLD") metals += val;
        else if (h.asset_class === "FX" || h.asset_class === "CRYPTO") fxCrypto += val;
        else stocks += val;
      }
      setZakatInput((prev) => ({
        ...prev,
        cash_and_bank_pkr: String(Math.round(cash)),
        psx_stocks_pkr: String(Math.round(stocks)),
        mutual_funds_pkr: String(Math.round(funds)),
        gold_and_silver_pkr: String(Math.round(metals)),
        forex_and_crypto_pkr: String(Math.round(fxCrypto)),
      }));
      setZakatNotice(`Loaded live market valuations from "${portfolios[0].name}".`);
    } catch (err) {
      setZakatNotice(err instanceof Error ? err.message : "Failed to load portfolio balances.");
    } finally {
      setLoadingPortfolioZakat(false);
    }
  };

  const calcSip = async (e: React.FormEvent) => {
    e.preventDefault();
    const res = await apiFetch<typeof sipResult>("/api/v1/calculators/sip", {
      method: "POST",
      body: JSON.stringify(sipInput),
    });
    setSipResult(res);
  };

  const calcCgt = async (e: React.FormEvent) => {
    e.preventDefault();
    const res = await apiFetch<typeof cgtResult>("/api/v1/calculators/brokerage-cgt", {
      method: "POST",
      body: JSON.stringify(cgtInput),
    });
    setCgtResult(res);
  };

  const calcZakat = async (e: React.FormEvent) => {
    e.preventDefault();
    const res = await apiFetch<typeof zakatResult>("/api/v1/calculators/zakat", {
      method: "POST",
      body: JSON.stringify(zakatInput),
    });
    setZakatResult(res);
  };

  const calcDailyDiv = async (e: React.FormEvent) => {
    e.preventDefault();
    const res = await apiFetch<typeof dailyDivResult>("/api/v1/calculators/daily-dividend", {
      method: "POST",
      body: JSON.stringify(dailyDivInput),
    });
    setDailyDivResult(res);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Pakistan Financial Calculators</h1>
        <p className="text-sm okz-muted">
          Mutual fund SIP compounder, Constant-NAV Daily Dividend Fund (MRAF/NIDDF/MICF) DRIP vs Cash simulator, PSX Brokerage/FBR CGT breakeven, and Zakat Nisab calculator.
        </p>
      </div>

      <div className="flex flex-wrap gap-2 border-b border-[var(--border)]">
        {[
          { id: "sip", label: "SIP & Mutual Fund Compounder" },
          { id: "daily_div", label: "Daily Dividend Fund (DRIP vs Cash)" },
          { id: "cgt", label: "PSX Brokerage & CGT" },
          { id: "zakat", label: "Zakat Calculator" },
        ].map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id as typeof tab)}
            className={`px-4 py-2 text-sm font-semibold border-b-2 ${
              tab === t.id ? "border-emerald-500 text-emerald-500" : "border-transparent okz-muted"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "sip" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <form onSubmit={calcSip} className="okz-card p-6 space-y-4">
            <h2 className="font-bold">SIP Inputs (PKR)</h2>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <label className="block text-xs okz-muted mb-1">Initial Investment</label>
                <input
                  type="number"
                  value={sipInput.initial_investment_pkr}
                  onChange={(e) => setSipInput({ ...sipInput, initial_investment_pkr: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Monthly SIP</label>
                <input
                  type="number"
                  value={sipInput.monthly_contribution_pkr}
                  onChange={(e) => setSipInput({ ...sipInput, monthly_contribution_pkr: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Expected Gross Return %</label>
                <input
                  type="number"
                  step="0.1"
                  value={sipInput.annual_return_pct}
                  onChange={(e) => setSipInput({ ...sipInput, annual_return_pct: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Horizon (Years)</label>
                <input
                  type="number"
                  value={sipInput.years}
                  onChange={(e) => setSipInput({ ...sipInput, years: Number(e.target.value) })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Front-End Load (FEL %)</label>
                <input
                  type="number"
                  step="0.1"
                  value={sipInput.front_end_load_pct}
                  onChange={(e) => setSipInput({ ...sipInput, front_end_load_pct: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Expense Ratio (TER %)</label>
                <input
                  type="number"
                  step="0.1"
                  value={sipInput.ter_pct}
                  onChange={(e) => setSipInput({ ...sipInput, ter_pct: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
            </div>
            <button type="submit" className="px-4 py-2 rounded bg-emerald-600 text-white text-sm font-semibold">
              Calculate Projection
            </button>
          </form>

          <div className="okz-card p-6 space-y-3">
            <h2 className="font-bold">Projected Outcome</h2>
            {sipResult ? (
              <div className="space-y-3 text-sm">
                <div className="p-4 rounded-lg bg-emerald-500/10 border border-emerald-500/30">
                  <div className="text-xs okz-muted">Projected Maturity Value</div>
                  <div className="text-2xl font-bold text-emerald-500">
                    {formatMoney(sipResult.projected_value_pkr, currency, usdToPkr, true)}
                  </div>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Total Cash Invested</span>
                  <span className="font-semibold">{formatMoney(sipResult.total_invested_pkr, currency, usdToPkr)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Net Investment Gain</span>
                  <span className="font-semibold text-emerald-500">
                    {formatMoney(sipResult.net_gain_pkr, currency, usdToPkr)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Front-End Load Paid</span>
                  <span className="font-semibold text-rose-500">
                    {formatMoney(sipResult.total_load_paid_pkr, currency, usdToPkr)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Effective Net CAGR</span>
                  <span className="font-semibold">{sipResult.effective_cagr_pct}% / yr</span>
                </div>

                {sipResult.yearly_schedule && sipResult.yearly_schedule.length > 0 && (
                  <div className="pt-3 border-t border-[var(--border)] space-y-2">
                    <div className="text-xs font-semibold okz-muted uppercase tracking-wider">
                      Year-by-Year Compounding Curve (Principal vs Gain)
                    </div>
                    <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
                      {(() => {
                        const maxVal = Math.max(
                          ...sipResult.yearly_schedule.map((r) => Number(r.projected_value_pkr) || 1),
                          1
                        );
                        return sipResult.yearly_schedule.map((row) => {
                          const total = Math.max(Number(row.projected_value_pkr) || 0, 0);
                          const inv = Math.max(Number(row.invested_pkr) || 0, 0);
                          const invPct = Math.min((inv / maxVal) * 100, 100);
                          const gainPct = Math.max(Math.min(((total - inv) / maxVal) * 100, 100 - invPct), 0);
                          return (
                            <div key={row.year} className="space-y-0.5 text-xs">
                              <div className="flex justify-between">
                                <span className="okz-muted">Yr {row.year}</span>
                                <span className="font-semibold">
                                  {formatMoney(row.projected_value_pkr, currency, usdToPkr, true)}
                                </span>
                              </div>
                              <div className="h-2 rounded bg-[var(--bg-elevated)] overflow-hidden flex">
                                <div className="bg-sky-500/80 h-full" style={{ width: `${invPct}%` }} title="Invested Principal" />
                                <div className="bg-emerald-500 h-full" style={{ width: `${gainPct}%` }} title="Compounded Gain" />
                              </div>
                            </div>
                          );
                        });
                      })()}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm okz-muted">Adjust parameters and click Calculate Projection.</p>
            )}
          </div>
        </div>
      )}

      {tab === "daily_div" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <form onSubmit={calcDailyDiv} className="okz-card p-6 space-y-4">
            <h2 className="font-bold">Constant-NAV Daily Dividend Fund Simulator</h2>
            <p className="text-xs okz-muted">
              Model Pakistani daily dividend money market funds (e.g. Meezan Rozana Amdani `MRAF` Par NAV Rs 50, NBP Islamic Daily Dividend `NIDDF` Par NAV Rs 10, Mahaana Islamic Cash `MICF`) with daily WHT deduction (15% / 22.5% / 30%) and Daily DRIP vs Cash Payout.
            </p>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <label className="block text-xs okz-muted mb-1">Principal Investment (PKR)</label>
                <input
                  type="number"
                  value={dailyDivInput.principal_pkr}
                  onChange={(e) => setDailyDivInput({ ...dailyDivInput, principal_pkr: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Constant Par NAV (PKR)</label>
                <input
                  type="number"
                  step="any"
                  value={dailyDivInput.constant_par_nav}
                  onChange={(e) => setDailyDivInput({ ...dailyDivInput, constant_par_nav: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Annualized Yield % (MUFAP)</label>
                <input
                  type="number"
                  step="0.01"
                  value={dailyDivInput.annual_yield_pct}
                  onChange={(e) => setDailyDivInput({ ...dailyDivInput, annual_yield_pct: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Holding Days (1–1825)</label>
                <input
                  type="number"
                  min={1}
                  max={1825}
                  value={dailyDivInput.days}
                  onChange={(e) => setDailyDivInput({ ...dailyDivInput, days: Number(e.target.value) })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Taxpayer Status (ATL WHT)</label>
                <select
                  value={dailyDivInput.filer_status}
                  onChange={(e) => setDailyDivInput({ ...dailyDivInput, filer_status: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                >
                  <option value="ACTIVE_FILER">Active Filer (15% WHT)</option>
                  <option value="LATE_FILER">Late Filer (22.5% WHT)</option>
                  <option value="NON_FILER">Non-Filer (30% WHT)</option>
                </select>
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Payout / Accrual Mode</label>
                <select
                  value={dailyDivInput.mode}
                  onChange={(e) => setDailyDivInput({ ...dailyDivInput, mode: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                >
                  <option value="REINVEST">Daily DRIP Reinvestment (Compound Units)</option>
                  <option value="CASH">Daily Cash Payout to Bank</option>
                </select>
              </div>
            </div>
            <button type="submit" className="px-4 py-2 rounded bg-emerald-600 text-white text-sm font-semibold">
              Simulate Daily Accrual
            </button>
          </form>

          <div className="okz-card p-6 space-y-3">
            <h2 className="font-bold">Daily Dividend Accrual Breakdown</h2>
            {dailyDivResult ? (
              <div className="space-y-3 text-sm">
                <div className="p-4 rounded-lg bg-emerald-500/10 border border-emerald-500/30">
                  <div className="text-xs okz-muted">Ending Total Wealth (Principal + Net Dividends)</div>
                  <div className="text-2xl font-bold text-emerald-500">
                    {formatMoney(dailyDivResult.final_value_pkr, currency, usdToPkr, true)}
                  </div>
                  <div className="text-xs okz-muted mt-1">
                    Units: {dailyDivResult.initial_units} &rarr; <span className="font-semibold text-[var(--fg)]">{dailyDivResult.final_units}</span>
                  </div>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Day-1 Net Daily Payout</span>
                  <span className="font-semibold text-emerald-500">
                    {formatMoney(dailyDivResult.daily_net_dividend_day1_pkr, currency, usdToPkr)} / day
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Total Gross Dividends Accrued</span>
                  <span className="font-semibold">
                    {formatMoney(dailyDivResult.total_gross_dividend_pkr, currency, usdToPkr)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Total WHT Deducted at Source</span>
                  <span className="font-semibold text-rose-500">
                    -{formatMoney(dailyDivResult.total_wht_pkr, currency, usdToPkr)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Total Net Dividends (After WHT)</span>
                  <span className="font-semibold text-emerald-500">
                    +{formatMoney(dailyDivResult.total_net_dividend_pkr, currency, usdToPkr)}
                  </span>
                </div>
                <div className="flex justify-between pt-2 border-t border-[var(--border)]">
                  <span className="okz-muted">Effective Net Annualized Yield (After Tax &amp; DRIP)</span>
                  <span className="font-bold text-amber-400">{dailyDivResult.effective_net_annual_yield_pct}% p.a.</span>
                </div>

                {dailyDivResult.monthly_schedule && dailyDivResult.monthly_schedule.length > 0 && (
                  <div className="pt-3 border-t border-[var(--border)] space-y-2">
                    <div className="text-xs font-semibold okz-muted uppercase tracking-wider">
                      30-Day Rollup Schedule
                    </div>
                    <div className="max-h-48 overflow-y-auto text-xs">
                      <table className="w-full">
                        <thead className="text-left okz-muted border-b border-[var(--border)]">
                          <tr>
                            <th className="py-1">Month</th>
                            <th className="py-1 text-right">Units</th>
                            <th className="py-1 text-right">Net Div</th>
                            <th className="py-1 text-right">Total Wealth</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-[var(--border)]">
                          {dailyDivResult.monthly_schedule.map((m) => (
                            <tr key={m.month}>
                              <td className="py-1">M{m.month} ({m.days_elapsed}d)</td>
                              <td className="py-1 text-right font-mono">{Number(m.units).toFixed(2)}</td>
                              <td className="py-1 text-right text-emerald-500">
                                {formatMoney(m.cumulative_net_pkr, currency, usdToPkr)}
                              </td>
                              <td className="py-1 text-right font-semibold">
                                {formatMoney(m.total_wealth_pkr, currency, usdToPkr)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm okz-muted">Configure principal &amp; yield and click Simulate Daily Accrual.</p>
            )}
          </div>
        </div>
      )}

      {tab === "cgt" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <form onSubmit={calcCgt} className="okz-card p-6 space-y-4">
            <h2 className="font-bold">PSX Round-Trip Trade &amp; FBR CGT</h2>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <label className="block text-xs okz-muted mb-1">Buy Price (PKR)</label>
                <input
                  type="number"
                  step="any"
                  value={cgtInput.buy_price}
                  onChange={(e) => setCgtInput({ ...cgtInput, buy_price: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Sell Price (PKR)</label>
                <input
                  type="number"
                  step="any"
                  value={cgtInput.sell_price}
                  onChange={(e) => setCgtInput({ ...cgtInput, sell_price: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Shares</label>
                <input
                  type="number"
                  value={cgtInput.quantity}
                  onChange={(e) => setCgtInput({ ...cgtInput, quantity: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Taxpayer Status (ATL)</label>
                <select
                  value={cgtInput.filer_status}
                  onChange={(e) => setCgtInput({ ...cgtInput, filer_status: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                >
                  <option value="ACTIVE_FILER">Active Filer (15% post-Jul 2024)</option>
                  <option value="LATE_FILER">Late Filer (20% CGT / 22.5% WHT)</option>
                  <option value="NON_FILER">Non-Filer (Progressive 15%-45%)</option>
                </select>
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Lot Acquisition Date</label>
                <input
                  type="date"
                  value={cgtInput.acquisition_date}
                  onChange={(e) => setCgtInput({ ...cgtInput, acquisition_date: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">Brokerage Rate (bps, 15 = 0.15%)</label>
                <input
                  type="number"
                  value={cgtInput.broker_rate_bps}
                  onChange={(e) => setCgtInput({ ...cgtInput, broker_rate_bps: Number(e.target.value) })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
            </div>

            <label className="flex items-center gap-2 text-xs font-medium cursor-pointer">
              <input
                type="checkbox"
                checked={cgtInput.is_intraday_square_off}
                onChange={(e) => setCgtInput({ ...cgtInput, is_intraday_square_off: e.target.checked })}
                className="rounded border-[var(--border)]"
              />
              Same-Day Intraday Square-Off (PSX 1-leg brokerage waiver on sell leg)
            </label>

            <div className="text-[11px] okz-muted">
              FBR Regimes: &lt;Jul 2013 (0%) • Jul 2013–Jun 2022 (12.5%) • Jul 2022–Jun 2024 (tapered) • &ge;Jul 2024 (flat 15% Filer).
            </div>

            <button type="submit" className="px-4 py-2 rounded bg-emerald-600 text-white text-sm font-semibold">
              Compute Net Profit &amp; Breakeven
            </button>
          </form>

          <div className="okz-card p-6 space-y-3">
            <h2 className="font-bold">Charges &amp; Net Tax Breakdown</h2>
            {cgtResult ? (
              <div className="space-y-2.5 text-sm">
                <div className="flex justify-between">
                  <span className="okz-muted">Breakeven Sell Price</span>
                  <span className="font-bold">PKR {cgtResult.breakeven_sell_price}</span>
                </div>
                <div className="flex justify-between text-xs okz-muted">
                  <span>Buy Leg Charges</span>
                  <span>{formatMoney(cgtResult.buy_charges_pkr, currency, usdToPkr)}</span>
                </div>
                <div className="flex justify-between text-xs okz-muted">
                  <span>Sell Leg Charges</span>
                  <span>{formatMoney(cgtResult.sell_charges_pkr, currency, usdToPkr)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Total Brokerage + SST + CDC + Levy</span>
                  <span className="text-rose-500">{formatMoney(cgtResult.total_charges_pkr, currency, usdToPkr)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">FBR CGT ({cgtResult.cgt_rate_pct}%)</span>
                  <span className="text-rose-500">{formatMoney(cgtResult.cgt_amount_pkr, currency, usdToPkr)}</span>
                </div>
                <div className="flex justify-between pt-2 border-t border-[var(--border)] font-bold">
                  <span>Net Take-Home Profit</span>
                  <span className="text-emerald-500">{formatMoney(cgtResult.net_profit_pkr, currency, usdToPkr)}</span>
                </div>
              </div>
            ) : (
              <p className="text-sm okz-muted">Enter trade details and click Compute.</p>
            )}
          </div>
        </div>
      )}

      {tab === "zakat" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <form onSubmit={calcZakat} className="okz-card p-6 space-y-3 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-bold text-base">Zakatable Assets (PKR)</h2>
              {user && (
                <button
                  type="button"
                  onClick={handleLoadZakatFromPortfolio}
                  disabled={loadingPortfolioZakat}
                  className="px-3 py-1.5 rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-500 text-xs font-semibold hover:bg-emerald-500/20 disabled:opacity-60"
                >
                  {loadingPortfolioZakat ? "Loading..." : "Load from My Portfolio"}
                </button>
              )}
            </div>
            {zakatNotice && <p className="text-xs text-emerald-500">{zakatNotice}</p>}
            <div className="grid grid-cols-2 gap-3">
              {[
                ["cash_and_bank_pkr", "Cash & Bank"],
                ["psx_stocks_pkr", "PSX Equities"],
                ["mutual_funds_pkr", "Mutual Funds"],
                ["gold_and_silver_pkr", "Gold & Silver"],
                ["forex_and_crypto_pkr", "Forex & Crypto"],
                ["short_term_liabilities_pkr", "Deductible Liabilities"],
              ].map(([key, label]) => (
                <div key={key}>
                  <label className="block text-xs okz-muted mb-1">{label}</label>
                  <input
                    type="number"
                    value={zakatInput[key as keyof typeof zakatInput]}
                    onChange={(e) => setZakatInput({ ...zakatInput, [key]: e.target.value })}
                    className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                  />
                </div>
              ))}
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div>
                <label className="block text-xs okz-muted mb-1">Nisab Benchmark</label>
                <select
                  value={zakatInput.nisab_standard}
                  onChange={(e) => setZakatInput({ ...zakatInput, nisab_standard: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                >
                  <option value="SILVER">Silver Nisab (52.5 Tola — Recommended)</option>
                  <option value="GOLD">Gold Nisab (7.5 Tola)</option>
                </select>
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">
                  Silver/g ({spotRateSource === "live" ? "Live" : "Ref"})
                </label>
                <input
                  type="number"
                  step="any"
                  value={zakatInput.silver_price_per_gram_pkr}
                  onChange={(e) => setZakatInput({ ...zakatInput, silver_price_per_gram_pkr: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
              <div>
                <label className="block text-xs okz-muted mb-1">
                  Gold 24K/g ({spotRateSource === "live" ? "Live" : "Ref"})
                </label>
                <input
                  type="number"
                  step="any"
                  value={zakatInput.gold_price_per_gram_pkr}
                  onChange={(e) => setZakatInput({ ...zakatInput, gold_price_per_gram_pkr: e.target.value })}
                  className="w-full px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
                />
              </div>
            </div>
            <button type="submit" className="px-4 py-2 rounded bg-emerald-600 text-white font-semibold">
              Calculate Zakat (2.5%)
            </button>
          </form>

          <div className="okz-card p-6 space-y-3">
            <h2 className="font-bold">Zakat Assessment</h2>
            {zakatResult ? (
              <div className="space-y-3 text-sm">
                <div className="p-4 rounded-lg bg-emerald-500/10 border border-emerald-500/30">
                  <div className="text-xs okz-muted">Zakat Payable (2.5%)</div>
                  <div className="text-2xl font-bold text-emerald-500">
                    {formatMoney(zakatResult.zakat_payable_pkr, currency, usdToPkr)}
                  </div>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Net Zakatable Wealth</span>
                  <span className="font-semibold">
                    {formatMoney(zakatResult.net_zakatable_wealth_pkr, currency, usdToPkr)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Nisab Threshold</span>
                  <span className="font-semibold">
                    {formatMoney(zakatResult.nisab_threshold_pkr, currency, usdToPkr)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Status</span>
                  <span className="font-semibold">{zakatResult.is_zakat_due ? "Zakat Due" : "Below Nisab"}</span>
                </div>
              </div>
            ) : (
              <p className="text-sm okz-muted">Enter asset balances and click Calculate Zakat.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
