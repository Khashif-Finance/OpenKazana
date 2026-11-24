"use client";

import React, { useEffect, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  Download,
  ExternalLink,
  FileSpreadsheet,
  FileText,
  RefreshCw,
  Sparkles,
  Upload,
} from "lucide-react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiFetch } from "@/lib/api";
import { formatNumber, formatPct, pctColorClass } from "@/lib/format";

export interface FinancialPeriodRow {
  period_type: string;
  period_label: string;
  period_end_date: string;
  fiscal_year: number;
  source_label: string;
  source_pdf_url?: string | null;
  auditor_opinion?: string | null;
  dividend_bonus_note?: string | null;
  revenue_mn?: number | null;
  cost_of_sales_mn?: number | null;
  gross_profit_mn?: number | null;
  operating_profit_mn?: number | null;
  finance_cost_mn?: number | null;
  profit_before_tax_mn?: number | null;
  taxation_mn?: number | null;
  net_profit_mn?: number | null;
  eps?: number | null;
  dps?: number | null;
  total_assets_mn?: number | null;
  current_assets_mn?: number | null;
  cash_equivalents_mn?: number | null;
  inventory_mn?: number | null;
  receivables_mn?: number | null;
  total_liabilities_mn?: number | null;
  current_liabilities_mn?: number | null;
  total_debt_mn?: number | null;
  shareholders_equity_mn?: number | null;
  operating_cashflow_mn?: number | null;
  capex_mn?: number | null;
  free_cashflow_mn?: number | null;
  dividends_paid_mn?: number | null;
  gross_margin_pct?: number | null;
  operating_margin_pct?: number | null;
  net_margin_pct?: number | null;
  effective_tax_rate_pct?: number | null;
  fcf_margin_pct?: number | null;
  roe_pct?: number | null;
  roa_pct?: number | null;
  debt_to_equity?: number | null;
  debt_to_assets_pct?: number | null;
  current_ratio?: number | null;
  cash_conversion_x?: number | null;
  dso_days?: number | null;
  dio_days?: number | null;
  revenue_growth_pct?: number | null;
  net_profit_growth_pct?: number | null;
  eps_growth_pct?: number | null;
  equity_growth_pct?: number | null;
}

export interface FinancialReportsPayload {
  annual_rows: FinancialPeriodRow[];
  quarterly_rows: FinancialPeriodRow[];
  cagrs: {
    revenue_cagr_3y_pct?: number | null;
    revenue_cagr_5y_pct?: number | null;
    net_profit_cagr_3y_pct?: number | null;
    net_profit_cagr_5y_pct?: number | null;
    eps_cagr_3y_pct?: number | null;
    equity_cagr_3y_pct?: number | null;
  };
  ttm_summary: {
    latest_annual_period?: string | null;
    latest_quarterly_period?: string | null;
    latest_revenue_mn?: number | null;
    latest_net_profit_mn?: number | null;
    latest_eps?: number | null;
    latest_ocf_mn?: number | null;
    latest_fcf_mn?: number | null;
    latest_net_margin_pct?: number | null;
    margin_change_bps?: number | null;
  };
  latest_disclosures: {
    auditor_opinion?: string | null;
    directors_highlights?: string[];
    dividend_bonus_note?: string | null;
    segment_notes?: Array<{ segment: string; value_mn: number }>;
    source_pdf_url?: string | null;
    period_label?: string | null;
  };
  earnings_quality: {
    cash_conversion_x?: number | null;
    sloan_accruals_pct?: number | null;
    quality_verdict?: string;
  };
}

export function FinancialReportsPanel({
  symbol,
  currency,
  initialData,
  onUpdated,
}: {
  symbol: string;
  currency: string;
  initialData: FinancialReportsPayload | null | undefined;
  onUpdated?: (fresh: FinancialReportsPayload) => void;
}) {
  const [data, setData] = useState<FinancialReportsPayload | null>(initialData ?? null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [periodView, setPeriodView] = useState<"ANNUAL" | "QUARTERLY">("ANNUAL");
  const [stmtTab, setStmtTab] = useState<"INCOME" | "BALANCE" | "CASHFLOW">("INCOME");
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const [disclosuresOpen, setDisclosuresOpen] = useState(true);

  // Keep state synced when navigating between stocks or when parent reloads
  useEffect(() => {
    setData(initialData ?? null);
    setUploadMsg(null);
    setLoadError(null);
  }, [symbol, initialData]);

  // Automatic fallback fetch if initialData was omitted or empty
  const fetchReports = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await apiFetch<FinancialReportsPayload>(
        `/api/v1/market/stocks/${encodeURIComponent(symbol)}/reports`,
      );
      setData(res);
      onUpdated?.(res);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load financial statements.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const hasRows =
      (initialData?.annual_rows?.length || 0) > 0 || (initialData?.quarterly_rows?.length || 0) > 0;
    if (!hasRows && symbol) {
      fetchReports();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  const reportData = data ?? initialData ?? null;
  const annualRows = reportData?.annual_rows || [];
  const quarterlyRows = reportData?.quarterly_rows || [];

  // Auto-switch to QUARTERLY if annual is empty and quarterly has rows
  useEffect(() => {
    if (periodView === "ANNUAL" && annualRows.length === 0 && quarterlyRows.length > 0) {
      setPeriodView("QUARTERLY");
    }
  }, [annualRows.length, quarterlyRows.length, periodView]);

  const rows = periodView === "ANNUAL" ? annualRows : quarterlyRows;
  const ccy = currency === "USD" ? "USD" : "PKR";
  const ttm = reportData?.ttm_summary || {};
  const disc = reportData?.latest_disclosures;
  const cagrs = reportData?.cagrs || {};
  const eqQual = reportData?.earnings_quality || {};

  const handleUploadPdf = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadMsg(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await apiFetch<{
        periods_extracted: number;
        extracted_labels: string[];
        report_analysis: FinancialReportsPayload;
      }>(`/api/v1/market/stocks/${encodeURIComponent(symbol)}/reports/upload`, {
        method: "POST",
        body: fd,
      });
      setData(res.report_analysis);
      onUpdated?.(res.report_analysis);
      setUploadMsg(
        `Parsed ${res.periods_extracted} period(s) from ${file.name}: ${res.extracted_labels.join(", ")}.`,
      );
    } catch (err) {
      setUploadMsg(err instanceof Error ? err.message : "Failed to parse financial report PDF.");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const chartData = rows.slice(-8).map((r) => ({
    period: r.period_label,
    revenue_mn: r.revenue_mn ?? 0,
    net_profit_mn: r.net_profit_mn ?? 0,
    net_margin_pct: r.net_margin_pct ?? null,
  }));

  const hasBalanceData = rows.some(
    (r) => r.total_assets_mn != null || r.shareholders_equity_mn != null || r.total_debt_mn != null,
  );
  const hasCashflowData = rows.some(
    (r) => r.operating_cashflow_mn != null || r.free_cashflow_mn != null || r.roe_pct != null,
  );

  return (
    <div className="space-y-5">
      <div className="okz-card p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-bold flex items-center gap-2">
              <FileSpreadsheet className="w-4 h-4 text-emerald-500" />
              Annual &amp; Quarterly Financial Statements ({symbol})
            </h2>
            <p className="text-xs okz-muted">
              Multi-period Income Statement, Balance Sheet &amp; Cash Flow powered by Pandas — upload any official PUCARS PDF to extract full 3-statement comparative tables.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <button
              type="button"
              onClick={fetchReports}
              disabled={loading}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-[var(--border)] hover:border-emerald-500 font-semibold"
              title="Reload financial periods"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              <span>{loading ? "Loading..." : "Reload"}</span>
            </button>
            <label className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold cursor-pointer">
              <Upload className="w-3.5 h-3.5" />
              <span>{uploading ? "Parsing PDF..." : "Upload Annual/Qtr PDF"}</span>
              <input type="file" accept=".pdf" onChange={handleUploadPdf} disabled={uploading} className="hidden" />
            </label>
            <a
              href={`/api/v1/market/stocks/${encodeURIComponent(symbol)}/reports/export`}
              download={`${symbol}_financial_statements.csv`}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] hover:border-emerald-500 font-semibold"
            >
              <Download className="w-3.5 h-3.5 text-sky-400" />
              Download CSV
            </a>
          </div>
        </div>

        {uploadMsg && (
          <div className="px-3.5 py-2 rounded-lg text-xs bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
            {uploadMsg}
          </div>
        )}
        {loadError && (
          <div className="px-3.5 py-2 rounded-lg text-xs bg-rose-500/10 border border-rose-500/30 text-rose-400">
            {loadError}
          </div>
        )}

        {/* Headline Latest Annual & Multi-Year CAGR Strip */}
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2.5 text-xs">
          <div className="p-2.5 rounded-lg bg-[var(--card-muted)]/70">
            <div className="okz-muted text-[10px]">Latest Rev ({ttm.latest_annual_period || "Annual"})</div>
            <div className="text-sm font-bold font-mono mt-0.5">
              {ttm.latest_revenue_mn != null ? `${formatNumber(ttm.latest_revenue_mn, 0)} Mn` : "—"}
            </div>
          </div>
          <div className="p-2.5 rounded-lg bg-[var(--card-muted)]/70">
            <div className="okz-muted text-[10px]">Latest Net Profit</div>
            <div className="text-sm font-bold font-mono mt-0.5 text-emerald-400">
              {ttm.latest_net_profit_mn != null ? `${formatNumber(ttm.latest_net_profit_mn, 0)} Mn` : "—"}
            </div>
          </div>
          <div className="p-2.5 rounded-lg bg-[var(--card-muted)]/70">
            <div className="okz-muted text-[10px]">Latest EPS / Margin</div>
            <div className="text-sm font-bold font-mono mt-0.5">
              {ttm.latest_eps != null ? ttm.latest_eps.toFixed(2) : "—"}
              {ttm.latest_net_margin_pct != null ? ` (${ttm.latest_net_margin_pct.toFixed(1)}%)` : ""}
            </div>
          </div>
          <div className="p-2.5 rounded-lg bg-[var(--card-muted)]/70">
            <div className="okz-muted text-[10px]">3Y Revenue CAGR</div>
            <div className={`text-sm font-bold font-mono mt-0.5 ${pctColorClass(cagrs.revenue_cagr_3y_pct)}`}>
              {cagrs.revenue_cagr_3y_pct != null ? formatPct(cagrs.revenue_cagr_3y_pct) : "—"}
            </div>
          </div>
          <div className="p-2.5 rounded-lg bg-[var(--card-muted)]/70">
            <div className="okz-muted text-[10px]">3Y Net Profit CAGR</div>
            <div className={`text-sm font-bold font-mono mt-0.5 ${pctColorClass(cagrs.net_profit_cagr_3y_pct)}`}>
              {cagrs.net_profit_cagr_3y_pct != null ? formatPct(cagrs.net_profit_cagr_3y_pct) : "—"}
            </div>
          </div>
          <div className="p-2.5 rounded-lg bg-[var(--card-muted)]/70">
            <div className="okz-muted text-[10px]">3Y EPS CAGR</div>
            <div className={`text-sm font-bold font-mono mt-0.5 ${pctColorClass(cagrs.eps_cagr_3y_pct)}`}>
              {cagrs.eps_cagr_3y_pct != null ? formatPct(cagrs.eps_cagr_3y_pct) : "—"}
            </div>
          </div>
          <div className="p-2.5 rounded-lg bg-[var(--card-muted)]/70">
            <div className="okz-muted text-[10px]">OCF / PAT Ratio</div>
            <div className="text-sm font-bold font-mono mt-0.5 text-sky-400">
              {eqQual.cash_conversion_x != null ? `${eqQual.cash_conversion_x.toFixed(2)}x` : "—"}
            </div>
          </div>
          <div className="p-2.5 rounded-lg bg-[var(--card-muted)]/70">
            <div className="okz-muted text-[10px]">Sloan Accruals</div>
            <div className="text-sm font-bold font-mono mt-0.5">
              {eqQual.sloan_accruals_pct != null ? `${eqQual.sloan_accruals_pct.toFixed(1)}%` : "—"}
            </div>
          </div>
        </div>

        {/* Period & Statement Toggle Pills */}
        <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-[var(--border)] text-xs">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setPeriodView("ANNUAL")}
              className={`px-3 py-1.5 rounded-md font-semibold border ${
                periodView === "ANNUAL"
                  ? "bg-emerald-600 text-white border-emerald-600"
                  : "border-[var(--border)] okz-muted hover:text-[var(--text)]"
              }`}
            >
              Annual ({annualRows.length})
            </button>
            <button
              type="button"
              onClick={() => setPeriodView("QUARTERLY")}
              className={`px-3 py-1.5 rounded-md font-semibold border ${
                periodView === "QUARTERLY"
                  ? "bg-emerald-600 text-white border-emerald-600"
                  : "border-[var(--border)] okz-muted hover:text-[var(--text)]"
              }`}
            >
              Quarterly / Interim ({quarterlyRows.length})
            </button>
          </div>
          <div className="flex items-center gap-1.5">
            {(
              [
                ["INCOME", "Income Statement"],
                ["BALANCE", "Balance Sheet"],
                ["CASHFLOW", "Cash Flow & Ratios"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setStmtTab(key)}
                className={`px-2.5 py-1.5 rounded-md font-semibold ${
                  stmtTab === key
                    ? "bg-sky-500/20 text-sky-400 border border-sky-500/40"
                    : "bg-[var(--card-muted)] okz-muted hover:text-[var(--text)]"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Revenue, Profit & Net Margin Chart */}
        {chartData.length >= 2 && (
          <div className="h-60 w-full border border-[var(--border)] rounded-lg p-2 bg-[var(--bg-elevated)]/30">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartData} margin={{ top: 8, right: 12, left: 4, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="period" tick={{ fontSize: 10, fill: "#94a3b8" }} />
                <YAxis yAxisId="left" tick={{ fontSize: 10, fill: "#94a3b8" }} width={58} />
                <YAxis yAxisId="right" orientation="right" unit="%" tick={{ fontSize: 10, fill: "#94a3b8" }} width={42} />
                <RechartsTooltip
                  contentStyle={{
                    backgroundColor: "var(--card)",
                    borderColor: "var(--border)",
                    fontSize: "11px",
                    borderRadius: "8px",
                  }}
                />
                <Legend wrapperStyle={{ fontSize: "11px" }} />
                <Bar yAxisId="left" dataKey="revenue_mn" name={`Revenue (${ccy} Mn)`} fill="#38bdf8" radius={[3, 3, 0, 0]} />
                <Bar yAxisId="left" dataKey="net_profit_mn" name={`Net Profit (${ccy} Mn)`} fill="#10b981" radius={[3, 3, 0, 0]} />
                <Line yAxisId="right" type="monotone" dataKey="net_margin_pct" name="Net Margin %" stroke="#f59e0b" strokeWidth={2.2} dot={{ r: 3 }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Multi-Period 3-Statement Table */}
        {loading && rows.length === 0 ? (
          <div className="py-8 text-center text-xs okz-muted">Loading {symbol} financial statements...</div>
        ) : rows.length === 0 ? (
          <div className="py-8 text-center text-xs okz-muted space-y-2">
            <div>
              No {periodView.toLowerCase()} periods stored yet for {symbol}.
            </div>
            <div>
              Upload an Annual or Quarterly PUCARS PDF report above to parse instant 3-statement tables!
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {stmtTab === "BALANCE" && !hasBalanceData && (
              <div className="px-3 py-2 rounded-lg border border-amber-500/30 bg-amber-500/5 text-xs text-amber-400">
                PSX DPS summary tables publish Income Statement rows for all companies; upload {symbol}&apos;s latest PUCARS Annual/Quarterly PDF above to populate full multi-year Balance Sheet lines immediately.
              </div>
            )}
            {stmtTab === "CASHFLOW" && !hasCashflowData && (
              <div className="px-3 py-2 rounded-lg border border-amber-500/30 bg-amber-500/5 text-xs text-amber-400">
                Upload {symbol}&apos;s official PUCARS Annual or Quarterly PDF above to extract Operating Cash Flow, CapEx, Free Cash Flow &amp; working capital days (DSO/DIO).
              </div>
            )}
            <div className="overflow-x-auto border border-[var(--border)] rounded-lg">
              {stmtTab === "INCOME" && (
                <table className="w-full text-xs">
                  <thead className="bg-[var(--bg-elevated)] okz-muted text-right">
                    <tr>
                      <th className="py-2 px-3 text-left">Period</th>
                      <th className="py-2 px-3">Revenue ({ccy} Mn)</th>
                      <th className="py-2 px-3">YoY %</th>
                      <th className="py-2 px-3">Gross Profit</th>
                      <th className="py-2 px-3">Op. Profit</th>
                      <th className="py-2 px-3">Net Profit (PAT)</th>
                      <th className="py-2 px-3">PAT YoY %</th>
                      <th className="py-2 px-3">Net Margin</th>
                      <th className="py-2 px-3">EPS</th>
                      <th className="py-2 px-3 text-left">Source / PDF</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border)] font-mono">
                    {rows.map((r) => (
                      <tr key={`${r.period_type}-${r.period_label}`} className="hover:bg-[var(--card-muted)]/40">
                        <td className="py-2 px-3 text-left font-bold">{r.period_label}</td>
                        <td className="py-2 px-3 text-right">{r.revenue_mn != null ? formatNumber(r.revenue_mn, 0) : "—"}</td>
                        <td className={`py-2 px-3 text-right ${pctColorClass(r.revenue_growth_pct)}`}>
                          {r.revenue_growth_pct != null ? formatPct(r.revenue_growth_pct) : "—"}
                        </td>
                        <td className="py-2 px-3 text-right">{r.gross_profit_mn != null ? formatNumber(r.gross_profit_mn, 0) : "—"}</td>
                        <td className="py-2 px-3 text-right">{r.operating_profit_mn != null ? formatNumber(r.operating_profit_mn, 0) : "—"}</td>
                        <td className="py-2 px-3 text-right font-bold text-emerald-400">
                          {r.net_profit_mn != null ? formatNumber(r.net_profit_mn, 0) : "—"}
                        </td>
                        <td className={`py-2 px-3 text-right ${pctColorClass(r.net_profit_growth_pct)}`}>
                          {r.net_profit_growth_pct != null ? formatPct(r.net_profit_growth_pct) : "—"}
                        </td>
                        <td className="py-2 px-3 text-right">
                          {r.net_margin_pct != null ? `${r.net_margin_pct.toFixed(1)}%` : "—"}
                        </td>
                        <td className="py-2 px-3 text-right font-semibold">{r.eps != null ? r.eps.toFixed(2) : "—"}</td>
                        <td className="py-2 px-3 text-left font-sans text-[10px]">
                          {r.source_pdf_url ? (
                            <a
                              href={`/api/v1/market/downloads/proxy?url=${encodeURIComponent(r.source_pdf_url)}&filename=${encodeURIComponent(`${symbol}_${r.period_label}_Report.pdf`)}`}
                              className="inline-flex items-center gap-1 text-sky-400 hover:underline font-semibold"
                            >
                              <Download className="w-3 h-3" /> PDF
                            </a>
                          ) : (
                            <span className="okz-muted">{r.source_label}</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {stmtTab === "BALANCE" && (
                <table className="w-full text-xs">
                  <thead className="bg-[var(--bg-elevated)] okz-muted text-right">
                    <tr>
                      <th className="py-2 px-3 text-left">Period</th>
                      <th className="py-2 px-3">Total Assets</th>
                      <th className="py-2 px-3">Cash &amp; Bank</th>
                      <th className="py-2 px-3">Current Assets</th>
                      <th className="py-2 px-3">Total Debt</th>
                      <th className="py-2 px-3">Total Liab.</th>
                      <th className="py-2 px-3">Shareholders Equity</th>
                      <th className="py-2 px-3">Current Ratio</th>
                      <th className="py-2 px-3">Debt / Equity</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border)] font-mono">
                    {rows.map((r) => (
                      <tr key={`${r.period_type}-${r.period_label}`} className="hover:bg-[var(--card-muted)]/40">
                        <td className="py-2 px-3 text-left font-bold">{r.period_label}</td>
                        <td className="py-2 px-3 text-right">{r.total_assets_mn != null ? formatNumber(r.total_assets_mn, 0) : "—"}</td>
                        <td className="py-2 px-3 text-right text-sky-400">{r.cash_equivalents_mn != null ? formatNumber(r.cash_equivalents_mn, 0) : "—"}</td>
                        <td className="py-2 px-3 text-right">{r.current_assets_mn != null ? formatNumber(r.current_assets_mn, 0) : "—"}</td>
                        <td className="py-2 px-3 text-right text-amber-400">{r.total_debt_mn != null ? formatNumber(r.total_debt_mn, 0) : "—"}</td>
                        <td className="py-2 px-3 text-right">{r.total_liabilities_mn != null ? formatNumber(r.total_liabilities_mn, 0) : "—"}</td>
                        <td className="py-2 px-3 text-right font-bold text-emerald-400">
                          {r.shareholders_equity_mn != null ? formatNumber(r.shareholders_equity_mn, 0) : "—"}
                        </td>
                        <td className="py-2 px-3 text-right">{r.current_ratio != null ? `${r.current_ratio.toFixed(2)}x` : "—"}</td>
                        <td className="py-2 px-3 text-right">{r.debt_to_equity != null ? `${r.debt_to_equity.toFixed(2)}x` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {stmtTab === "CASHFLOW" && (
                <table className="w-full text-xs">
                  <thead className="bg-[var(--bg-elevated)] okz-muted text-right">
                    <tr>
                      <th className="py-2 px-3 text-left">Period</th>
                      <th className="py-2 px-3">Operating CF</th>
                      <th className="py-2 px-3">CapEx</th>
                      <th className="py-2 px-3">Free Cash Flow</th>
                      <th className="py-2 px-3">Divs Paid</th>
                      <th className="py-2 px-3">OCF / PAT</th>
                      <th className="py-2 px-3">ROE %</th>
                      <th className="py-2 px-3">DSO (Days)</th>
                      <th className="py-2 px-3">DIO (Days)</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border)] font-mono">
                    {rows.map((r) => (
                      <tr key={`${r.period_type}-${r.period_label}`} className="hover:bg-[var(--card-muted)]/40">
                        <td className="py-2 px-3 text-left font-bold">{r.period_label}</td>
                        <td className="py-2 px-3 text-right text-sky-400">
                          {r.operating_cashflow_mn != null ? formatNumber(r.operating_cashflow_mn, 0) : "—"}
                        </td>
                        <td className="py-2 px-3 text-right">{r.capex_mn != null ? formatNumber(r.capex_mn, 0) : "—"}</td>
                        <td className={`py-2 px-3 text-right font-bold ${pctColorClass(r.free_cashflow_mn)}`}>
                          {r.free_cashflow_mn != null ? formatNumber(r.free_cashflow_mn, 0) : "—"}
                        </td>
                        <td className="py-2 px-3 text-right">{r.dividends_paid_mn != null ? formatNumber(r.dividends_paid_mn, 0) : "—"}</td>
                        <td className="py-2 px-3 text-right">{r.cash_conversion_x != null ? `${r.cash_conversion_x.toFixed(2)}x` : "—"}</td>
                        <td className="py-2 px-3 text-right text-emerald-400">{r.roe_pct != null ? `${r.roe_pct.toFixed(1)}%` : "—"}</td>
                        <td className="py-2 px-3 text-right">{r.dso_days != null ? r.dso_days.toFixed(0) : "—"}</td>
                        <td className="py-2 px-3 text-right">{r.dio_days != null ? r.dio_days.toFixed(0) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Collapsible Accordion: Qualitative Disclosures from Annual / Quarterly PDF */}
      {disc &&
        (disc.auditor_opinion ||
          (disc.directors_highlights && disc.directors_highlights.length > 0) ||
          disc.dividend_bonus_note ||
          (disc.segment_notes && disc.segment_notes.length > 0)) && (
          <div className="okz-card overflow-hidden">
            <button
              type="button"
              onClick={() => setDisclosuresOpen((v) => !v)}
              className="w-full px-5 py-3.5 flex items-center justify-between gap-2 hover:bg-[var(--card-muted)]/40 transition"
            >
              <span className="text-sm font-bold flex items-center gap-1.5">
                <FileText className="w-4 h-4 text-sky-400" />
                Extracted Auditor Opinion &amp; Directors&apos; Review {disc.period_label ? `(${disc.period_label})` : ""}
              </span>
              <ChevronDown className={`w-4 h-4 okz-muted transition-transform ${disclosuresOpen ? "rotate-180" : ""}`} />
            </button>

            {disclosuresOpen && (
              <div className="px-5 pb-5 pt-2 border-t border-[var(--border)] space-y-4">
                {disc.source_pdf_url && (
                  <div className="flex flex-wrap items-center gap-3 text-xs">
                    <a
                      href={`/api/v1/market/downloads/proxy?url=${encodeURIComponent(disc.source_pdf_url)}&filename=${encodeURIComponent(`${symbol}_${disc.period_label || "Latest"}_PUCARS.pdf`)}`}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-sky-500/15 text-sky-400 font-semibold hover:bg-sky-500/25"
                    >
                      <Download className="w-3.5 h-3.5" /> Download Official PUCARS PDF
                    </a>
                    <a
                      href={disc.source_pdf_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs okz-muted hover:underline inline-flex items-center gap-1"
                    >
                      View in New Tab <ExternalLink className="w-3 h-3" />
                    </a>
                  </div>
                )}

                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                  {disc.auditor_opinion && (
                    <div className="p-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5 space-y-1">
                      <div className="font-bold text-emerald-400 flex items-center gap-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        Independent Auditor&apos;s Opinion
                      </div>
                      <div className="text-[var(--text)]">{disc.auditor_opinion}</div>
                    </div>
                  )}
                  {disc.dividend_bonus_note && (
                    <div className="p-3 rounded-lg border border-amber-500/30 bg-amber-500/5 space-y-1">
                      <div className="font-bold text-amber-400 flex items-center gap-1.5">
                        <Sparkles className="w-3.5 h-3.5" />
                        Dividend / Bonus Declaration
                      </div>
                      <div className="text-[var(--text)]">{disc.dividend_bonus_note}</div>
                    </div>
                  )}
                </div>

                {disc.directors_highlights && disc.directors_highlights.length > 0 && (
                  <div className="space-y-1.5 text-xs">
                    <div className="font-semibold okz-muted uppercase tracking-wider text-[10px]">
                      Directors&apos; Report / Chairman&apos;s Review Highlights
                    </div>
                    <ul className="list-disc pl-5 space-y-1.5 text-[var(--text)]/90">
                      {disc.directors_highlights.map((hl, idx) => (
                        <li key={idx} className="leading-relaxed">
                          {hl}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {disc.segment_notes && disc.segment_notes.length > 0 && (
                  <div className="space-y-1.5 text-xs">
                    <div className="font-semibold okz-muted uppercase tracking-wider text-[10px]">
                      Operating Segments Mentioned ({ccy} Mn)
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {disc.segment_notes.map((seg) => (
                        <span key={seg.segment} className="px-2.5 py-1 rounded bg-[var(--card-muted)] font-mono">
                          {seg.segment}: <strong>{formatNumber(seg.value_mn, 0)}</strong>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
    </div>
  );
}
