"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  Award,
  Building2,
  ExternalLink,
  FileText,
  PieChart as PieChartIcon,
  Printer,
  Repeat,
  ShieldCheck,
  Upload,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { BarPoint, PriceChart } from "@/components/PriceChart";
import { apiFetch } from "@/lib/api";
import { formatNumber, formatPct, pctColorClass } from "@/lib/format";

const CHART_COLORS = [
  "#10b981",
  "#38bdf8",
  "#f59e0b",
  "#8b5cf6",
  "#ec4899",
  "#14b8a6",
  "#f97316",
  "#64748b",
];

interface FmrEnrichedHolding {
  symbol: string;
  name: string;
  sector: string;
  weight_pct: number;
  est_value_mn: number | null;
  last_price: number | null;
  change_pct: number | null;
  pe_ratio: number | null;
  dividend_yield_pct: number | null;
  is_shariah_compliant: boolean | null;
}

interface FundFmrData {
  report_month: string;
  report_label: string;
  amc_name: string;
  fund_manager: string | null;
  stability_rating: string | null;
  amc_rating: string | null;
  risk_profile: string | null;
  launch_date: string | null;
  benchmark_name: string | null;
  net_assets_mn: number | null;
  wam_days: number | null;
  duration_days: number | null;
  ytm_pct: number | null;
  ter_mtd_pct: number | null;
  ter_ytd_pct: number | null;
  govt_levy_pct: number | null;
  mgmt_fee_pct: number | null;
  asset_allocation: Array<{ label: string; pct: number }>;
  sector_allocation: Array<{ sector: string; pct: number }>;
  top_holdings: FmrEnrichedHolding[];
  credit_quality: Array<{ rating: string; pct: number }>;
  returns_table: Record<string, number>;
  manager_commentary: string | null;
  implied_portfolio_pe: number | null;
  implied_portfolio_div_yield_pct: number | null;
  top_10_concentration_pct: number | null;
  source_url: string | null;
  source_label: string;
  updated_at: string;
}

interface FundDetailPayload {
  fund: {
    symbol: string;
    name: string;
    asset_class: string;
    is_shariah_compliant: boolean;
    last_price: string;
    change_pct: string;
    amc_name: string;
    fund_category: string;
    risk_profile: string;
    ter_pct: string;
    management_fee_pct: string;
    front_end_load_pct: string;
    back_end_load_pct: string;
    aum_mn: string;
    is_daily_dividend: boolean;
    constant_par_nav: string | null;
    annualized_yield_pct: string | null;
    default_payout_mode: string;
    return_1m_pct: string | null;
    return_6m_pct: string | null;
    return_1y_pct: string | null;
    return_3y_pct: string | null;
    benchmark_name: string;
  };
  fmr: FundFmrData | null;
  daily_dividend_profile?: {
    is_daily_dividend: boolean;
    effective_annual_yield_pct: number;
    yield_source: string;
    par_nav: number;
    default_payout_mode: string;
    est_daily_payout_per_unit_pkr: number;
    per_100k_summary: {
      daily_gross_pkr: number;
      daily_net_filer_pkr: number;
      daily_net_non_filer_pkr: number;
      monthly_net_filer_cash_pkr: number;
      annual_gross_reinvest_pkr: number;
      annual_net_filer_reinvest_pkr: number;
      annual_net_non_filer_reinvest_pkr: number;
      compounding_bonus_filer_pkr: number;
    };
    wealth_curve_1y: Array<{
      month: string;
      date: string;
      gross_value: number;
      net_filer_value: number;
      net_non_filer_value: number;
      flat_nav_only: number;
    }>;
  } | null;
  analytics: {
    benchmark_symbol: string;
    risk_free_rate_pct: number;
    bar_count: number;
    cagr_pct: number | null;
    annualized_volatility_pct: number | null;
    sharpe_ratio: number | null;
    sortino_ratio: number | null;
    calmar_ratio: number | null;
    max_drawdown_pct: number | null;
    alpha_annualized_pct: number | null;
    beta: number | null;
    tracking_error_pct: number | null;
    information_ratio: number | null;
    fee_drag_5y_pct: number | null;
    nav_series: Array<{ date: string; nav: number; wealth_index: number; benchmark_index?: number | null }>;
    amc_peers: Array<{
      symbol: string;
      name: string;
      amc_name: string;
      nav: number;
      ter_pct: number;
      front_end_load_pct: number;
      return_1y_pct: number | null;
      annualized_yield_pct: number | null;
      is_daily_dividend: boolean;
    }>;
  };
}

export default function MutualFundDetailPage() {
  const params = useParams<{ symbol: string }>();
  const rawSym = decodeURIComponent(params?.symbol || "");

  const [detail, setDetail] = useState<FundDetailPayload | null>(null);
  const [navBars, setNavBars] = useState<BarPoint[]>([]);
  const [returnMode, setReturnMode] = useState<"GROSS_TR" | "NET_FILER" | "PRICE">("GROSS_TR");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [busyUpload, setBusyUpload] = useState(false);
  const [activeTab, setActiveTab] = useState<"fmr" | "daily_div" | "performance" | "peers">("fmr");
  const [simPrincipal, setSimPrincipal] = useState<number>(500000);
  const [simFiler, setSimFiler] = useState<"FILER" | "NON_FILER">("FILER");
  const [simMode, setSimMode] = useState<"REINVEST" | "CASH">("REINVEST");

  const loadFund = useCallback(async () => {
    if (!rawSym) return;
    setLoading(true);
    setError(null);
    try {
      const [det, bars] = await Promise.all([
        apiFetch<FundDetailPayload>(
          `/api/v1/market/funds/${encodeURIComponent(rawSym)}/detail?return_mode=${returnMode}`
        ),
        apiFetch<BarPoint[]>(
          `/api/v1/market/assets/${encodeURIComponent(rawSym)}/bars?timeframe=1D&limit=365&return_mode=${returnMode}`
        ).catch(() => [] as BarPoint[]),
      ]);
      setDetail(det);
      setNavBars(bars);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load mutual fund fact sheet.");
    } finally {
      setLoading(false);
    }
  }, [rawSym, returnMode]);

  useEffect(() => {
    loadFund();
  }, [loadFund]);

  const handleFmrPdfUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!detail || !e.target.files?.[0]) return;
    setBusyUpload(true);
    setUploadStatus(null);
    try {
      const fd = new FormData();
      fd.append("file", e.target.files[0]);
      const res = await apiFetch<{ matched_funds: number; report_label: string }>(
        `/api/v1/market/funds/fmr/upload?symbol=${encodeURIComponent(detail.fund.symbol)}&amc_hint=${encodeURIComponent(detail.fund.amc_name)}`,
        { method: "POST", body: fd }
      );
      setUploadStatus(`Parsed SECP FMR (${res.report_label}) — updated ${res.matched_funds} fund record(s).`);
      await loadFund();
    } catch (err) {
      setUploadStatus(err instanceof Error ? err.message : "Failed to parse uploaded FMR PDF.");
    } finally {
      setBusyUpload(false);
      e.target.value = "";
    }
  };

  const handleNavCsvUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!detail || !e.target.files?.[0]) return;
    setBusyUpload(true);
    setUploadStatus(null);
    try {
      const fd = new FormData();
      fd.append("file", e.target.files[0]);
      const res = await apiFetch<{ bars_inserted: number; date_from: string; date_to: string }>(
        `/api/v1/market/funds/${encodeURIComponent(detail.fund.symbol)}/import-history`,
        { method: "POST", body: fd }
      );
      setUploadStatus(`Imported ${res.bars_inserted} NAV bars (${res.date_from} → ${res.date_to}).`);
      await loadFund();
    } catch (err) {
      setUploadStatus(err instanceof Error ? err.message : "Failed to import NAV history.");
    } finally {
      setBusyUpload(false);
      e.target.value = "";
    }
  };

  if (loading && !detail) {
    return <div className="p-8 text-center text-sm okz-muted">Loading Mutual Fund Fact Sheet &amp; SECP FMR composition...</div>;
  }
  if (error || !detail) {
    return (
      <div className="okz-card p-6 space-y-3">
        <p className="text-sm text-rose-400">{error || "Fund not found."}</p>
        <Link href="/funds" className="text-xs text-emerald-500 hover:underline inline-flex items-center gap-1">
          <ArrowLeft className="w-3.5 h-3.5" /> Back to Mutual Funds
        </Link>
      </div>
    );
  }

  const { fund, fmr, daily_dividend_profile, analytics } = detail;

  const ddYield = daily_dividend_profile?.effective_annual_yield_pct ?? Number(fund.annualized_yield_pct || 0);
  const ddWhtRate = simFiler === "FILER" ? 0.15 : 0.30;
  const ddNetRate = (ddYield / 100) * (1 - ddWhtRate);
  const ddDailyGross = (simPrincipal * (ddYield / 100)) / 365;
  const ddDailyNet = ddDailyGross * (1 - ddWhtRate);
  const ddMonthlyNet = ddDailyNet * 30.4167;
  const ddAnnualNet =
    simMode === "REINVEST"
      ? simPrincipal * (Math.pow(1 + ddNetRate / 365, 365) - 1)
      : simPrincipal * ddNetRate;
  const ddAnnualTax =
    simMode === "REINVEST"
      ? simPrincipal * (Math.pow(1 + ddYield / 100 / 365, 365) - 1) - ddAnnualNet
      : simPrincipal * (ddYield / 100) * ddWhtRate;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <Link href="/funds" className="okz-muted hover:text-emerald-500 inline-flex items-center gap-1">
          <ArrowLeft className="w-3.5 h-3.5" /> Back to Mutual Funds &amp; VPS Directory
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <label className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 font-semibold cursor-pointer">
            <FileText className="w-3.5 h-3.5" />
            <span>{busyUpload ? "Parsing..." : "Upload SECP FMR PDF"}</span>
            <input type="file" accept=".pdf" onChange={handleFmrPdfUpload} disabled={busyUpload} className="hidden" />
          </label>
          <label className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)] hover:bg-[var(--card-muted)] font-semibold cursor-pointer">
            <Upload className="w-3.5 h-3.5 text-sky-400" />
            <span>Upload NAV CSV/XLSX</span>
            <input type="file" accept=".csv,.xlsx,.xlsm" onChange={handleNavCsvUpload} disabled={busyUpload} className="hidden" />
          </label>
          <button
            type="button"
            onClick={() => window.print()}
            title="Print or Save Mutual Fund Fact Sheet PDF"
            className="okz-no-print inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)] hover:border-emerald-500 font-semibold"
          >
            <Printer className="w-3.5 h-3.5 text-emerald-500" />
            <span>Print Fact Sheet</span>
          </button>
          <select
            value={returnMode}
            onChange={(e) => setReturnMode(e.target.value as typeof returnMode)}
            className="okz-no-print px-2.5 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--bg)] font-semibold"
          >
            <option value="GROSS_TR">Gross Total Return</option>
            <option value="NET_FILER">Net After Filer WHT (15%)</option>
            <option value="PRICE">Raw Quoted NAV</option>
          </select>
        </div>
      </div>

      {/* Print-Only Mutual Fund Fact Sheet Banner */}
      <div className="okz-print-only border-b-2 border-emerald-700 pb-2 mb-3 text-xs">
        <div className="flex justify-between items-baseline">
          <strong className="text-base">OpenKhazana — SECP FMR Mutual Fund Fact Sheet</strong>
          <span>Generated: {new Date().toLocaleDateString()}</span>
        </div>
        <div className="text-slate-600">
          {fund.name} ({fund.symbol}) &middot; {fund.amc_name} &middot; {fund.fund_category}
        </div>
      </div>

      {uploadStatus && (
        <div className="px-4 py-2.5 rounded-lg text-xs bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
          {uploadStatus}
        </div>
      )}

      {/* Hero Fact-Sheet Header */}
      <div className="okz-card p-6 space-y-4">
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
          <div className="space-y-1.5">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-2xl font-bold">{fund.name}</h1>
              <span className="px-2 py-0.5 rounded bg-[var(--card-muted)] font-mono text-xs font-bold">
                {fund.symbol}
              </span>
              {fund.is_shariah_compliant && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 text-xs font-semibold">
                  <ShieldCheck className="w-3.5 h-3.5" /> Shariah Compliant
                </span>
              )}
              {fund.is_daily_dividend && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-amber-500/15 text-amber-400 text-xs font-semibold">
                  <Repeat className="w-3.5 h-3.5" /> Daily Dividend
                </span>
              )}
              {fmr?.stability_rating && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-sky-500/15 text-sky-400 text-xs font-bold">
                  <Award className="w-3.5 h-3.5" /> {fmr.stability_rating}
                </span>
              )}
            </div>
            <div className="text-xs okz-muted flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="inline-flex items-center gap-1">
                <Building2 className="w-3.5 h-3.5" /> {fund.amc_name}
              </span>
              <span>&bull; {fund.fund_category}</span>
              <span>&bull; Risk: <strong>{fmr?.risk_profile || fund.risk_profile}</strong></span>
              {fmr?.amc_rating && <span>&bull; AMC Rating: <strong>{fmr.amc_rating}</strong></span>}
              {fmr?.launch_date && <span>&bull; Launch: {fmr.launch_date}</span>}
              {fmr?.fund_manager && <span>&bull; Manager: <strong>{fmr.fund_manager}</strong></span>}
            </div>
          </div>

          <div className="text-left md:text-right shrink-0">
            <div className="text-xs okz-muted">Latest NAV (PKR)</div>
            <div className="text-3xl font-bold font-mono">{formatNumber(fund.last_price, 4)}</div>
            <div className={`text-xs font-semibold ${pctColorClass(fund.change_pct)}`}>
              {formatPct(fund.change_pct)} 1D &bull; Benchmark: {analytics.benchmark_symbol}
            </div>
          </div>
        </div>

        {/* Key KPI Strip */}
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3 text-xs pt-2 border-t border-[var(--border)]">
          <div className="p-2.5 rounded bg-[var(--card-muted)]">
            <div className="okz-muted">Net Assets (AUM)</div>
            <div className="text-sm font-bold mt-0.5">
              {fmr?.net_assets_mn
                ? `PKR ${formatNumber(fmr.net_assets_mn, 0)} Mn`
                : Number(fund.aum_mn) > 0
                  ? `PKR ${formatNumber(fund.aum_mn, 0)} Mn`
                  : "—"}
            </div>
            <div className="text-[10px] okz-muted">{fmr ? `FMR ${fmr.report_label}` : "Awaiting FMR"}</div>
          </div>
          <div className="p-2.5 rounded bg-[var(--card-muted)]">
            <div className="okz-muted">Expense Ratio (TER)</div>
            <div className="text-sm font-bold mt-0.5">
              {fmr?.ter_ytd_pct != null ? `${fmr.ter_ytd_pct.toFixed(2)}%` : `${fund.ter_pct}%`}
            </div>
            <div className="text-[10px] okz-muted">
              Mgmt {fmr?.mgmt_fee_pct != null ? `${fmr.mgmt_fee_pct}%` : `${fund.management_fee_pct}%`}
              {fmr?.govt_levy_pct != null ? ` · Levy ${fmr.govt_levy_pct}%` : ""}
            </div>
          </div>
          <div className="p-2.5 rounded bg-[var(--card-muted)]">
            <div className="okz-muted">Front-End Load / 5Y Drag</div>
            <div className="text-sm font-bold mt-0.5">
              {fund.front_end_load_pct}%
              {analytics.fee_drag_5y_pct != null && (
                <span className="text-rose-400 text-xs ml-1">(-{analytics.fee_drag_5y_pct}%)</span>
              )}
            </div>
            <div className="text-[10px] okz-muted">Compounded 5Y fee drag</div>
          </div>
          <div className="p-2.5 rounded bg-[var(--card-muted)]">
            <div className="okz-muted">1Y / 3Y Return</div>
            <div className="text-sm font-bold mt-0.5 text-emerald-500">
              {formatPct(fund.return_1y_pct)} / {formatPct(fund.return_3y_pct)}
            </div>
            <div className="text-[10px] okz-muted">
              1M: {formatPct(fund.return_1m_pct)} &bull; 6M: {formatPct(fund.return_6m_pct)}
            </div>
          </div>
          <div className="p-2.5 rounded bg-[var(--card-muted)]">
            <div className="okz-muted">WAM / YTM</div>
            <div className="text-sm font-bold mt-0.5">
              {fmr?.wam_days != null ? `${fmr.wam_days} Days` : "—"}
            </div>
            <div className="text-[10px] okz-muted">
              {fmr?.ytm_pct != null ? `YTM: ${fmr.ytm_pct.toFixed(2)}%` : fmr?.duration_days != null ? `Mod Dur: ${fmr.duration_days}d` : "Fixed-Income metric"}
            </div>
          </div>
          <div className="p-2.5 rounded bg-[var(--card-muted)]">
            <div className="okz-muted">Sharpe / Alpha</div>
            <div className="text-sm font-bold mt-0.5">
              {analytics.sharpe_ratio != null ? analytics.sharpe_ratio.toFixed(2) : "—"} /{" "}
              <span className={pctColorClass(analytics.alpha_annualized_pct)}>
                {analytics.alpha_annualized_pct != null ? `${analytics.alpha_annualized_pct > 0 ? "+" : ""}${analytics.alpha_annualized_pct.toFixed(1)}%` : "—"}
              </span>
            </div>
            <div className="text-[10px] okz-muted">
              Beta: {analytics.beta != null ? analytics.beta.toFixed(2) : "—"} &bull; {analytics.bar_count} bars
            </div>
          </div>
        </div>
      </div>

      {/* Institutional Workbench Tab Bar */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-[var(--border)] pb-2 text-xs">
        <button
          type="button"
          onClick={() => setActiveTab("fmr")}
          className={`px-3.5 py-2 rounded-lg font-semibold transition ${
            activeTab === "fmr"
              ? "bg-emerald-600 text-white"
              : "bg-[var(--card)] border border-[var(--border)] okz-muted hover:text-[var(--text)]"
          }`}
        >
          SECP FMR Composition
        </button>
        {(fund.is_daily_dividend || daily_dividend_profile) && (
          <button
            type="button"
            onClick={() => setActiveTab("daily_div")}
            className={`inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg font-semibold transition ${
              activeTab === "daily_div"
                ? "bg-amber-500 text-slate-950"
                : "bg-amber-500/10 border border-amber-500/35 text-amber-400 hover:bg-amber-500/20"
            }`}
          >
            <Repeat className="w-3.5 h-3.5" />
            Daily Dividend Income Engine ({ddYield.toFixed(2)}% p.a.)
          </button>
        )}
        <button
          type="button"
          onClick={() => setActiveTab("performance")}
          className={`px-3.5 py-2 rounded-lg font-semibold transition ${
            activeTab === "performance"
              ? "bg-emerald-600 text-white"
              : "bg-[var(--card)] border border-[var(--border)] okz-muted hover:text-[var(--text)]"
          }`}
        >
          Performance &amp; Risk Quant
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("peers")}
          className={`px-3.5 py-2 rounded-lg font-semibold transition ${
            activeTab === "peers"
              ? "bg-emerald-600 text-white"
              : "bg-[var(--card)] border border-[var(--border)] okz-muted hover:text-[var(--text)]"
          }`}
        >
          Category Peers ({analytics.amc_peers.length})
        </button>
      </div>

      {/* Daily Dividend Income Simulator & Compounded Wealth Curve */}
      {activeTab === "daily_div" && daily_dividend_profile && (
        <div className="space-y-5">
          <div className="okz-card p-5 space-y-4 border-amber-500/30">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-bold flex items-center gap-2">
                  <Repeat className="w-4 h-4 text-amber-400" />
                  Daily Dividend Income Simulator &amp; Compounding Engine
                </h2>
                <p className="text-xs okz-muted">
                  Constant-NAV funds keep par NAV fixed at Rs {daily_dividend_profile.par_nav.toFixed(2)} and distribute profit daily as units/cash.
                  Source: <strong className="text-[var(--text)]">{daily_dividend_profile.yield_source}</strong> ({daily_dividend_profile.effective_annual_yield_pct.toFixed(2)}% p.a. &bull; Rs {daily_dividend_profile.est_daily_payout_per_unit_pkr.toFixed(5)}/unit/day).
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <label className="inline-flex items-center gap-1.5">
                  <span className="okz-muted">Investment (PKR):</span>
                  <input
                    type="number"
                    min={10000}
                    step={50000}
                    value={simPrincipal}
                    onChange={(e) => setSimPrincipal(Math.max(1000, Number(e.target.value) || 0))}
                    className="w-32 px-2.5 py-1.5 rounded border border-[var(--border)] bg-[var(--bg)] font-mono font-bold text-right"
                  />
                </label>
                <select
                  value={simFiler}
                  onChange={(e) => setSimFiler(e.target.value as "FILER" | "NON_FILER")}
                  className="px-2.5 py-1.5 rounded border border-[var(--border)] bg-[var(--bg)] font-semibold"
                >
                  <option value="FILER">Active Filer (15% WHT)</option>
                  <option value="NON_FILER">Non-Filer (30% WHT)</option>
                </select>
                <select
                  value={simMode}
                  onChange={(e) => setSimMode(e.target.value as "REINVEST" | "CASH")}
                  className="px-2.5 py-1.5 rounded border border-[var(--border)] bg-[var(--bg)] font-semibold"
                >
                  <option value="REINVEST">Daily Reinvest Units (DRIP)</option>
                  <option value="CASH">Cash Payout Mode</option>
                </select>
              </div>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
              <div className="p-3.5 rounded-lg bg-emerald-500/10 border border-emerald-500/30">
                <div className="okz-muted">Est. Daily Net Dividend</div>
                <div className="text-lg font-bold text-emerald-400 font-mono mt-0.5">
                  Rs {formatNumber(ddDailyNet, 2)}
                </div>
                <div className="text-[10px] okz-muted">Gross: Rs {formatNumber(ddDailyGross, 2)}/day</div>
              </div>
              <div className="p-3.5 rounded-lg bg-sky-500/10 border border-sky-500/30">
                <div className="okz-muted">Est. Monthly Net Income</div>
                <div className="text-lg font-bold text-sky-400 font-mono mt-0.5">
                  Rs {formatNumber(ddMonthlyNet, 0)}
                </div>
                <div className="text-[10px] okz-muted">~30.4 days net of {simFiler === "FILER" ? "15%" : "30%"} WHT</div>
              </div>
              <div className="p-3.5 rounded-lg bg-[var(--card-muted)]">
                <div className="okz-muted">1-Year Net Profit ({simMode})</div>
                <div className="text-lg font-bold text-emerald-400 font-mono mt-0.5">
                  +Rs {formatNumber(ddAnnualNet, 0)}
                </div>
                <div className="text-[10px] okz-muted">
                  Final Value: Rs {formatNumber(simPrincipal + ddAnnualNet, 0)}
                </div>
              </div>
              <div className="p-3.5 rounded-lg bg-[var(--card-muted)]">
                <div className="okz-muted">Est. Annual FBR WHT</div>
                <div className="text-lg font-bold text-amber-400 font-mono mt-0.5">
                  Rs {formatNumber(ddAnnualTax, 0)}
                </div>
                <div className="text-[10px] okz-muted">
                  Effective Net Yield: {((ddAnnualNet / Math.max(simPrincipal, 1)) * 100).toFixed(2)}% p.a.
                </div>
              </div>
            </div>

            <div className="space-y-1.5 pt-2">
              <div className="text-xs font-semibold">
                1-Year Compounded Daily-Dividend Wealth Growth (Rs 100,000 Invested — Gross vs Net Filer vs Flat Par NAV)
              </div>
              <div className="h-64 w-full border border-[var(--border)] rounded-lg p-2">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={daily_dividend_profile.wealth_curve_1y}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis dataKey="month" tick={{ fontSize: 10, fill: "#94a3b8" }} />
                    <YAxis domain={["auto", "auto"]} tickFormatter={(v) => `${(Number(v) / 1000).toFixed(0)}k`} tick={{ fontSize: 10, fill: "#94a3b8" }} width={46} />
                    <RechartsTooltip
                      contentStyle={{ backgroundColor: "var(--card)", borderColor: "var(--border)", fontSize: "12px", borderRadius: "8px" }}
                      formatter={(val) => [`Rs ${Number(val).toLocaleString()}`, ""]}
                    />
                    <Legend wrapperStyle={{ fontSize: "11px" }} />
                    <Line type="monotone" dataKey="gross_value" name="Gross Compounded (0% WHT)" stroke="#10b981" strokeWidth={2.5} dot={false} />
                    <Line type="monotone" dataKey="net_filer_value" name="Net Active Filer (15% WHT)" stroke="#38bdf8" strokeWidth={2.5} dot={false} />
                    <Line type="monotone" dataKey="net_non_filer_value" name="Net Non-Filer (30% WHT)" stroke="#f59e0b" strokeWidth={2} dot={false} />
                    <Line type="monotone" dataKey="flat_nav_only" name="Flat Quoted NAV (Ignoring Divs)" stroke="#64748b" strokeDasharray="4 4" strokeWidth={1.5} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* SECP FMR Composition Section */}
      {activeTab === "fmr" && (fmr ? (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <div className="flex items-center gap-2 font-semibold">
              <PieChartIcon className="w-4 h-4 text-emerald-500" />
              <span>SECP Fund Manager Report Composition — {fmr.report_label}</span>
              <span className="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 text-[10px]">
                {fmr.source_label}
              </span>
            </div>
            {fmr.source_url && (
              <a
                href={fmr.source_url}
                target="_blank"
                rel="noreferrer"
                className="text-sky-400 hover:underline inline-flex items-center gap-1"
              >
                Open Official FMR PDF <ExternalLink className="w-3 h-3" />
              </a>
            )}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Asset Allocation Donut & Bars */}
            {fmr.asset_allocation.length > 0 && (
              <div className="okz-card p-5 space-y-4">
                <h2 className="text-sm font-semibold">Asset Allocation (% of Total Assets)</h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-center">
                  <div className="h-52">
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie
                          data={fmr.asset_allocation}
                          dataKey="pct"
                          nameKey="label"
                          innerRadius={48}
                          outerRadius={78}
                          paddingAngle={2}
                        >
                          {fmr.asset_allocation.map((entry, idx) => (
                            <Cell key={entry.label} fill={CHART_COLORS[idx % CHART_COLORS.length]} />
                          ))}
                        </Pie>
                        <RechartsTooltip
                          formatter={(val: unknown) => [`${Number(val).toFixed(2)}%`, "Weight"]}
                          contentStyle={{
                            backgroundColor: "var(--card)",
                            borderColor: "var(--border)",
                            fontSize: "12px",
                            borderRadius: "8px",
                          }}
                        />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="space-y-2.5 text-xs">
                    {fmr.asset_allocation.map((row, idx) => (
                      <div key={row.label} className="space-y-1">
                        <div className="flex justify-between">
                          <span className="flex items-center gap-1.5">
                            <span
                              className="w-2.5 h-2.5 rounded-full inline-block"
                              style={{ backgroundColor: CHART_COLORS[idx % CHART_COLORS.length] }}
                            />
                            <span>{row.label}</span>
                          </span>
                          <span className="font-mono font-bold">{row.pct.toFixed(2)}%</span>
                        </div>
                        <div className="h-1.5 w-full rounded bg-[var(--border)] overflow-hidden">
                          <div
                            className="h-full"
                            style={{
                              width: `${Math.min(100, row.pct)}%`,
                              backgroundColor: CHART_COLORS[idx % CHART_COLORS.length],
                            }}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* Sector Allocation OR Credit Quality Breakdown */}
            {fmr.sector_allocation.length > 0 ? (
              <div className="okz-card p-5 space-y-3">
                <h2 className="text-sm font-semibold">Sector Allocation (% of Total Assets)</h2>
                <div className="h-56">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={fmr.sector_allocation.slice(0, 8)}
                      layout="vertical"
                      margin={{ top: 4, right: 20, left: 10, bottom: 4 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis type="number" unit="%" tick={{ fontSize: 10 }} />
                      <YAxis type="category" dataKey="sector" width={125} tick={{ fontSize: 10 }} />
                      <RechartsTooltip
                        formatter={(val: unknown) => [`${Number(val).toFixed(2)}%`, "Allocation"]}
                        contentStyle={{
                          backgroundColor: "var(--card)",
                          borderColor: "var(--border)",
                          fontSize: "12px",
                          borderRadius: "8px",
                        }}
                      />
                      <Bar dataKey="pct" fill="#10b981" radius={[0, 4, 4, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            ) : fmr.credit_quality.length > 0 ? (
              <div className="okz-card p-5 space-y-3">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold">Credit / Asset Quality Profile (% of Total Assets)</h2>
                  {fmr.stability_rating && (
                    <span className="px-2 py-0.5 rounded bg-sky-500/15 text-sky-400 text-xs font-bold">
                      {fmr.stability_rating}
                    </span>
                  )}
                </div>
                <div className="h-56">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={fmr.credit_quality} margin={{ top: 8, right: 16, left: 0, bottom: 4 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="rating" tick={{ fontSize: 11 }} />
                      <YAxis unit="%" tick={{ fontSize: 11 }} />
                      <RechartsTooltip
                        formatter={(val: unknown) => [`${Number(val).toFixed(2)}%`, "Credit Share"]}
                        contentStyle={{
                          backgroundColor: "var(--card)",
                          borderColor: "var(--border)",
                          fontSize: "12px",
                          borderRadius: "8px",
                        }}
                      />
                      <Bar dataKey="pct" fill="#38bdf8" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            ) : null}
          </div>

          {/* Top 10 Equity Holdings with Live PSX Quotes & Implied Valuation */}
          {fmr.top_holdings.length > 0 && (
            <div className="okz-card p-5 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="text-sm font-semibold">
                    Top {fmr.top_holdings.length} Equity Holdings (Look-Through to Live PSX Quotes)
                  </h2>
                  <p className="text-xs okz-muted">
                    Click any underlying stock to open its full Valuation Lab &amp; Technicals page.
                  </p>
                </div>
                <div className="flex flex-wrap gap-2 text-xs">
                  {fmr.top_10_concentration_pct != null && (
                    <span className="px-2.5 py-1 rounded bg-[var(--card-muted)] font-semibold">
                      Top-10 Concentration: {fmr.top_10_concentration_pct.toFixed(1)}%
                    </span>
                  )}
                  {fmr.implied_portfolio_pe != null && (
                    <span className="px-2.5 py-1 rounded bg-sky-500/15 text-sky-400 font-bold">
                      Implied P/E: {fmr.implied_portfolio_pe.toFixed(2)}x
                    </span>
                  )}
                  {fmr.implied_portfolio_div_yield_pct != null && (
                    <span className="px-2.5 py-1 rounded bg-emerald-500/15 text-emerald-400 font-bold">
                      Implied Div Yield: {fmr.implied_portfolio_div_yield_pct.toFixed(2)}%
                    </span>
                  )}
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="border-b border-[var(--border)] okz-muted text-left">
                    <tr>
                      <th className="py-2">#</th>
                      <th className="py-2">Stock &amp; Sector</th>
                      <th className="py-2 text-right">FMR Weight %</th>
                      <th className="py-2 text-right">Est. Stake (PKR Mn)</th>
                      <th className="py-2 text-right">Live PSX Price</th>
                      <th className="py-2 text-right">1D Chg</th>
                      <th className="py-2 text-right">P/E</th>
                      <th className="py-2 text-right">Div Yield</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border)]">
                    {fmr.top_holdings.map((h, i) => (
                      <tr key={h.symbol} className="hover:bg-[var(--card-muted)]/40">
                        <td className="py-2 okz-muted font-mono">{i + 1}</td>
                        <td className="py-2">
                          <Link
                            href={`/stocks/${encodeURIComponent(h.symbol)}`}
                            className="font-mono font-bold hover:text-emerald-500 inline-flex items-center gap-1"
                          >
                            {h.symbol}
                            {h.is_shariah_compliant && <ShieldCheck className="w-3 h-3 text-emerald-500" />}
                          </Link>
                          <div className="text-[11px] okz-muted">
                            {h.name} &bull; {h.sector}
                          </div>
                        </td>
                        <td className="py-2 text-right font-mono font-bold text-emerald-500">
                          {h.weight_pct.toFixed(2)}%
                        </td>
                        <td className="py-2 text-right font-mono">
                          {h.est_value_mn != null ? formatNumber(h.est_value_mn, 0) : "—"}
                        </td>
                        <td className="py-2 text-right font-mono">
                          {h.last_price != null ? `Rs ${formatNumber(h.last_price, 2)}` : "—"}
                        </td>
                        <td className={`py-2 text-right font-mono ${pctColorClass(h.change_pct)}`}>
                          {h.change_pct != null ? formatPct(h.change_pct) : "—"}
                        </td>
                        <td className="py-2 text-right font-mono">
                          {h.pe_ratio != null ? h.pe_ratio.toFixed(1) : "—"}
                        </td>
                        <td className="py-2 text-right font-mono text-emerald-500">
                          {h.dividend_yield_pct != null ? `${h.dividend_yield_pct.toFixed(2)}%` : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Fund Manager Commentary */}
          {fmr.manager_commentary && (
            <div className="okz-card p-5 space-y-2">
              <h2 className="text-sm font-semibold">Fund Manager Monthly Review ({fmr.report_label})</h2>
              <p className="text-xs okz-muted leading-relaxed">{fmr.manager_commentary}</p>
            </div>
          )}
        </div>
      ) : (
        <div className="okz-card p-5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div className="space-y-1">
            <h2 className="text-sm font-semibold">No SECP Fund Manager Report (FMR) Parsed Yet for {fund.symbol}</h2>
            <p className="text-xs okz-muted">
              Upload this AMC&apos;s latest monthly FMR PDF booklet (or single-fund FMR) to extract Asset Allocation,
              Top-10 Equity Holdings, Sector Mix, Credit Quality, WAM &amp; TER breakdown automatically.
            </p>
          </div>
          <label className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold cursor-pointer shrink-0">
            <FileText className="w-3.5 h-3.5" />
            <span>Upload SECP FMR PDF</span>
            <input type="file" accept=".pdf" onChange={handleFmrPdfUpload} disabled={busyUpload} className="hidden" />
          </label>
        </div>
      ))}

      {/* Risk-Adjusted Quant Analytics & Benchmark Wealth Curve */}
      {activeTab === "performance" && (
      <div className="okz-card p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">
              Risk-Adjusted Performance vs {analytics.benchmark_symbol} (Live SBP Rf = {analytics.risk_free_rate_pct.toFixed(2)}%)
            </h2>
            <p className="text-xs okz-muted">
              Normalized base-100 wealth index, drawdown, Sortino, Information Ratio &amp; Tracking Error
            </p>
          </div>
          <div className="flex flex-wrap gap-2 text-xs font-mono">
            <span className="px-2 py-1 rounded bg-[var(--card-muted)]">
              CAGR: <strong>{analytics.cagr_pct != null ? `${analytics.cagr_pct.toFixed(2)}%` : "—"}</strong>
            </span>
            <span className="px-2 py-1 rounded bg-[var(--card-muted)]">
              Vol: <strong>{analytics.annualized_volatility_pct != null ? `${analytics.annualized_volatility_pct.toFixed(2)}%` : "—"}</strong>
            </span>
            <span className="px-2 py-1 rounded bg-[var(--card-muted)]">
              Sortino: <strong>{analytics.sortino_ratio != null ? analytics.sortino_ratio.toFixed(2) : "—"}</strong>
            </span>
            <span className="px-2 py-1 rounded bg-[var(--card-muted)]">
              Max DD: <strong className="text-rose-400">{analytics.max_drawdown_pct != null ? `${analytics.max_drawdown_pct.toFixed(2)}%` : "—"}</strong>
            </span>
            <span className="px-2 py-1 rounded bg-[var(--card-muted)]">
              Info Ratio: <strong>{analytics.information_ratio != null ? analytics.information_ratio.toFixed(2) : "—"}</strong>
            </span>
          </div>
        </div>

        {analytics.nav_series.length >= 2 ? (
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={analytics.nav_series}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#94a3b8" }} minTickGap={25} />
                <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "#94a3b8" }} width={48} />
                <RechartsTooltip
                  contentStyle={{
                    backgroundColor: "var(--card)",
                    borderColor: "var(--border)",
                    fontSize: "12px",
                    borderRadius: "8px",
                  }}
                />
                <Legend wrapperStyle={{ fontSize: "11px" }} />
                <Line type="monotone" dataKey="wealth_index" name={`${fund.symbol} Wealth`} stroke="#10b981" strokeWidth={2.5} dot={false} />
                <Line type="monotone" dataKey="benchmark_index" name={analytics.benchmark_symbol} stroke="#38bdf8" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <PriceChart bars={navBars} mode="area" height={230} />
        )}
      </div>
      )}

      {/* Category Peers */}
      {activeTab === "peers" && (analytics.amc_peers.length > 0 ? (
        <div className="okz-card p-5 space-y-3">
          <h2 className="text-sm font-semibold">Category Peer Comparison ({fund.fund_category})</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="border-b border-[var(--border)] okz-muted text-left">
                <tr>
                  <th className="py-2">Fund</th>
                  <th className="py-2">AMC</th>
                  <th className="py-2 text-right">NAV</th>
                  <th className="py-2 text-right">TER / FEL</th>
                  <th className="py-2 text-right">1Y / Yield</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border)]">
                {analytics.amc_peers.slice(0, 10).map((p) => (
                  <tr
                    key={p.symbol}
                    className={p.symbol === fund.symbol ? "bg-emerald-500/10 font-semibold" : "hover:bg-[var(--card-muted)]/40"}
                  >
                    <td className="py-2">
                      <Link href={`/funds/${encodeURIComponent(p.symbol)}`} className="font-mono font-bold hover:text-emerald-500 mr-2">
                        {p.symbol}
                      </Link>
                      <span className="okz-muted">{p.name}</span>
                    </td>
                    <td className="py-2">{p.amc_name}</td>
                    <td className="py-2 text-right font-mono">{formatNumber(p.nav, 4)}</td>
                    <td className="py-2 text-right font-mono">
                      {p.ter_pct}% / {p.front_end_load_pct}%
                    </td>
                    <td className="py-2 text-right font-semibold text-emerald-500">
                      {p.return_1y_pct != null
                        ? `${p.return_1y_pct.toFixed(2)}%`
                        : p.annualized_yield_pct != null
                          ? `${p.annualized_yield_pct.toFixed(2)}% p.a.`
                          : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="okz-card p-5 text-xs okz-muted">No category peers found for {fund.fund_category}.</div>
      ))}
    </div>
  );
}
