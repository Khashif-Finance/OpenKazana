"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  BookmarkPlus,
  Briefcase,
  Building2,
  CheckCircle2,
  ChevronDown,
  Download,
  ExternalLink,
  GitCompareArrows,
  Layers,
  Newspaper,
  Printer,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  TrendingDown,
  TrendingUp,
  XCircle,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useAppContext } from "@/components/CurrencyContext";
import { FinancialReportsPanel, FinancialReportsPayload } from "@/components/FinancialReportsPanel";
import { BarPoint, PriceChart } from "@/components/PriceChart";
import { SeasonalityHeatmap, SeasonalityPayload } from "@/components/SeasonalityHeatmap";
import { TechnicalGauge, TechnicalGaugePayload } from "@/components/TechnicalGauge";
import { apiFetch, apiFetchCached } from "@/lib/api";
import { formatMoney, formatNumber, formatPct, pctColorClass } from "@/lib/format";

interface AnnualFinancialRow {
  period?: string;
  year?: string;
  revenue?: number | null;
  revenue_mn?: number | null;
  revenue_bn?: number | null;
  profit_after_tax?: number | null;
  net_profit_mn?: number | null;
  net_profit_bn?: number | null;
  eps?: number | null;
  net_margin_pct?: number | null;
  dividend_pkr?: number | null;
}

interface EquityDeepSnapshot {
  fiscal_period: string;
  bvps?: string | null;
  total_assets_mn?: string | null;
  total_debt_mn?: string | null;
  cash_equivalents_mn?: string | null;
  shareholders_equity_mn?: string | null;
  operating_cashflow_mn?: string | null;
  capex_mn?: string | null;
  free_cashflow_mn?: string | null;
  current_ratio?: string | null;
  debt_to_equity?: string | null;
  roe_pct?: string | null;
  roa_pct?: string | null;
  roic_pct?: string | null;
  operating_margin_pct?: string | null;
  payout_ratio_pct?: string | null;
  analyst_target_price?: string | null;
  analyst_rating?: string | null;
  piotroski_f_score?: number | null;
  altman_z_score?: string | null;
  dcf_intrinsic_value?: string | null;
  metrics_json?: Record<string, unknown>;
  source: string;
}

interface StockDetailPayload {
  asset: {
    symbol: string;
    name: string;
    asset_class?: string;
    currency?: string;
    sector_or_category: string;
    is_shariah_compliant: boolean;
    last_price: string;
    prev_close: string;
    change_pct: string;
    volume: string;
  };
  profile: {
    exchange?: string;
    country?: string;
    default_wht_rate_pct?: string | null;
    market_cap_mn: string;
    pe_ratio: string | null;
    pb_ratio: string | null;
    eps: string | null;
    roe_pct: string | null;
    dividend_yield_pct: string;
    high_52w: string;
    low_52w: string;
    is_kse100: boolean;
    is_kmi30: boolean;
    financial_summary_json:
      | AnnualFinancialRow[]
      | { annual_periods?: AnnualFinancialRow[]; quarterly_periods?: AnnualFinancialRow[] }
      | Record<string, unknown>;
  } | null;
  shariah: {
    fiscal_period: string;
    core_business_compliant: boolean;
    is_kmi_all_share: boolean;
    debt_to_assets_pct: string;
    non_compliant_inv_pct: string;
    non_compliant_income_pct: string;
    purification_rate_pct?: string;
    illiquid_assets_pct: string;
    passed: boolean;
  } | null;
  technicals?: {
    overall_signal: "BULLISH" | "BEARISH" | "NEUTRAL" | string;
    bull_score: number;
    bear_score: number;
    signals: string[];
    latest: {
      close?: number | null;
      rsi_14?: number | null;
      stoch_rsi_k?: number | null;
      stoch_rsi_d?: number | null;
      adx_14?: number | null;
      sma_20?: number | null;
      sma_50?: number | null;
      ema_20?: number | null;
      vwap_20?: number | null;
      macd?: number | null;
      macd_signal?: number | null;
      macd_hist?: number | null;
      bb_upper?: number | null;
      bb_middle?: number | null;
      bb_lower?: number | null;
      atr_14?: number | null;
      pivot?: number | null;
      r1?: number | null;
      r2?: number | null;
      s1?: number | null;
      s2?: number | null;
      fib_r1?: number | null;
      fib_r2?: number | null;
      fib_s1?: number | null;
      fib_s2?: number | null;
      high_52w?: number | null;
      low_52w?: number | null;
      range_52w_pct?: number | null;
      annualized_volatility_pct?: number | null;
      max_drawdown_pct?: number | null;
    };
  } | null;
  fundamentals?: {
    currency: string;
    quality_score: number;
    valuation_score: number;
    profitability_score: number;
    growth_score: number;
    solvency_score: number;
    valuation_verdict: "UNDERVALUED" | "FAIR_VALUE" | "OVERVALUED" | string;
    graham_intrinsic_value?: number | null;
    dcf_intrinsic_value?: number | null;
    piotroski_f_score?: number | null;
    altman_z_score?: number | null;
    margin_of_safety_pct?: number | null;
    implied_bvps?: number | null;
    earnings_yield_pct?: number | null;
    risk_free_rate_pct: number;
    equity_risk_premium_pct?: number | null;
    revenue_cagr_pct?: number | null;
    eps_cagr_pct?: number | null;
    latest_net_margin_pct?: number | null;
    net_margin_trend?: string | null;
    annual_financials: AnnualFinancialRow[];
    signals: string[];
  } | null;
  deep_snapshot?: EquityDeepSnapshot | null;
  peer_valuation?: {
    sector: string;
    peer_count: number;
    sector_median_pe: number | null;
    sector_median_pb: number | null;
    sector_median_roe_pct: number | null;
    sector_median_div_yield_pct: number | null;
    pe_vs_sector_pct: number | null;
    peers: Array<{
      symbol: string;
      name: string;
      is_current: boolean;
      last_price: number;
      change_pct: number;
      market_cap_mn: number;
      pe_ratio: number | null;
      pb_ratio: number | null;
      roe_pct: number | null;
      dividend_yield_pct: number;
      piotroski_f_score: number | null;
      is_shariah_compliant: boolean;
    }>;
  } | null;
  dividend_history?: {
    annual_payouts: Array<{ year: string; dps: number }>;
    streak_years: number;
    ttm_dps: number;
    ttm_yield_pct: number | null;
    payout_ratio_pct: number | null;
    upcoming_ex_date: string | null;
    bonus_count: number;
  } | null;
  corporate_actions: Array<{
    id: string;
    action_type: string;
    ex_date: string;
    payout_per_share: string;
    description: string;
  }>;
  announcements: Array<{
    id: string;
    title: string;
    category: string;
    published_at: string;
    source_url?: string | null;
    summary: string;
  }>;
  valuation_lab?: {
    dcf_lab: {
      available: boolean;
      reason_unavailable?: string | null;
      free_cashflow_mn?: number | null;
      shares_mn?: number | null;
      total_debt_mn: number;
      cash_mn: number;
      base_growth_pct: number;
      base_wacc_pct: number;
      terminal_growth_pct: number;
      risk_free_rate_pct?: number | null;
      current_price: number;
      base_intrinsic_value?: number | null;
      wacc_steps: number[];
      sensitivity_rows: Array<{
        growth_pct: number;
        cells: Array<{
          wacc_pct: number;
          intrinsic_value: number | null;
          upside_pct: number | null;
        }>;
      }>;
    };
    dupont_roe?: {
      net_margin_pct?: number | null;
      asset_turnover?: number | null;
      equity_multiplier?: number | null;
      roe_dupont_pct?: number | null;
    };
    piotroski_score?: number | null;
    piotroski_checklist: Array<{
      group: string;
      criterion: string;
      passed: boolean | null;
      metric: string;
    }>;
    bull_bear_digest: {
      bull_points: string[];
      bear_points: string[];
      red_flags: string[];
      cash_conversion_ratio?: number | null;
      shariah_debt_headroom_pct?: number | null;
      is_financial_sector: boolean;
    };
  } | null;
  institutional_holders?: Array<{
    fund_symbol: string;
    fund_name: string;
    amc_name: string;
    weight_pct: number;
    fund_aum_mn?: number | null;
    est_value_mn?: number | null;
    report_label: string;
    is_shariah_compliant: boolean;
  }>;
  technical_gauge?: TechnicalGaugePayload | null;
  seasonality?: SeasonalityPayload | null;
  factor_ranks?: {
    symbol: string;
    sector: string;
    sector_rank: number;
    sector_total: number;
    market_total: number;
    value_percentile: number | null;
    quality_percentile: number | null;
    income_percentile: number | null;
    size_percentile: number | null;
    composite_score: number | null;
  } | null;
  financial_reports?: FinancialReportsPayload | null;
}

function computeClientDcf(
  fcfMn: number,
  sharesMn: number,
  totalDebtMn: number,
  cashMn: number,
  growthPct: number,
  waccPct: number,
  rfPct: number,
): number | null {
  if (!fcfMn || fcfMn <= 0 || !sharesMn || sharesMn <= 0) return null;
  const wacc = Math.max(0.075, Math.min(0.24, waccPct / 100));
  const g1 = Math.max(-0.05, Math.min(0.25, growthPct / 100));
  const gTerm = Math.min(rfPct > 8.0 ? 0.055 : 0.03, wacc - 0.025);
  let pvSum = 0;
  let fcf = fcfMn;
  for (let yr = 1; yr <= 5; yr += 1) {
    fcf = fcf * (1 + g1 * Math.pow(0.92, yr - 1));
    pvSum += fcf / Math.pow(1 + wacc, yr);
  }
  const termVal = (fcf * (1 + gTerm)) / Math.max(wacc - gTerm, 0.02);
  const pvTerm = termVal / Math.pow(1 + wacc, 5);
  const equityValMn = pvSum + pvTerm + Math.max(0, cashMn) - Math.max(0, totalDebtMn);
  if (equityValMn <= 0) return null;
  return equityValMn / sharesMn;
}

export default function StockDetailPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = String(params.symbol || "").toUpperCase();
  const { user, currency, usdToPkr } = useAppContext();
  const [detail, setDetail] = useState<StockDetailPayload | null>(null);
  const [bars, setBars] = useState<BarPoint[]>([]);
  const [timeframe, setTimeframe] = useState<"1D" | "5M">("1D");
  const [returnMode, setReturnMode] = useState<"PRICE" | "GROSS_TR" | "NET_FILER" | "NET_NON_FILER">("PRICE");
  const [applyPurification, setApplyPurification] = useState(false);
  const [watchNotice, setWatchNotice] = useState<string | null>(null);
  const [dcfGrowth, setDcfGrowth] = useState<number>(12);
  const [dcfWacc, setDcfWacc] = useState<number>(17.5);
  const [activeTab, setActiveTab] = useState<
    "overview" | "reports" | "technicals" | "valuation" | "dividends" | "research" | "news"
  >("overview");
  const [piotroskiOpen, setPiotroskiOpen] = useState<boolean>(false);
  const [stockNewsPayload, setStockNewsPayload] = useState<{
    stock_news: Array<{ id: string; title: string; summary: string; url: string; source_name: string; category: string; published_at: string }>;
    sector_news: Array<{ id: string; title: string; summary: string; url: string; source_name: string; category: string; published_at: string }>;
  } | null>(null);
  const [stockResearchPayload, setStockResearchPayload] = useState<{
    stock_reports: Array<{
      id: string;
      broker_name: string;
      title: string;
      summary: string;
      url: string;
      pdf_url: string | null;
      proxy_download_url: string | null;
      report_type: string;
      stance: string | null;
      target_price: number | null;
      published_at: string;
    }>;
    sector_reports: Array<{
      id: string;
      broker_name: string;
      title: string;
      summary: string;
      url: string;
      pdf_url: string | null;
      proxy_download_url: string | null;
      report_type: string;
      stance: string | null;
      target_price: number | null;
      published_at: string;
    }>;
    portals: Array<{ broker_key: string; broker_name: string; short_name: string; url: string }>;
  } | null>(null);

  useEffect(() => {
    if (!symbol) return;
    apiFetch<StockDetailPayload>(`/api/v1/market/stocks/${symbol}`)
      .then((res) => {
        setDetail(res);
        if (res.valuation_lab?.dcf_lab) {
          setDcfGrowth(res.valuation_lab.dcf_lab.base_growth_pct);
          setDcfWacc(res.valuation_lab.dcf_lab.base_wacc_pct);
        }
      })
      .catch(() => {});
    apiFetch<{
      stock_news: Array<{ id: string; title: string; summary: string; url: string; source_name: string; category: string; published_at: string }>;
      sector_news: Array<{ id: string; title: string; summary: string; url: string; source_name: string; category: string; published_at: string }>;
    }>(`/api/v1/market/stocks/${encodeURIComponent(symbol)}/news`)
      .then(setStockNewsPayload)
      .catch(() => setStockNewsPayload({ stock_news: [], sector_news: [] }));
    apiFetchCached<{
      stock_reports: Array<{
        id: string;
        broker_name: string;
        title: string;
        summary: string;
        url: string;
        pdf_url: string | null;
        proxy_download_url: string | null;
        report_type: string;
        stance: string | null;
        target_price: number | null;
        published_at: string;
      }>;
      sector_reports: Array<{
        id: string;
        broker_name: string;
        title: string;
        summary: string;
        url: string;
        pdf_url: string | null;
        proxy_download_url: string | null;
        report_type: string;
        stance: string | null;
        target_price: number | null;
        published_at: string;
      }>;
      portals: Array<{ broker_key: string; broker_name: string; short_name: string; url: string }>;
    }>(`/api/v1/market/stocks/${encodeURIComponent(symbol)}/research`, 120_000)
      .then(setStockResearchPayload)
      .catch(() => setStockResearchPayload({ stock_reports: [], sector_reports: [], portals: [] }));
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    const lim = timeframe === "1D" ? 360 : 120;
    const trParams =
      timeframe === "1D"
        ? `&return_mode=${returnMode}&apply_shariah_purification=${applyPurification}`
        : "";
    apiFetch<BarPoint[]>(`/api/v1/market/assets/${symbol}/bars?timeframe=${timeframe}&limit=${lim}${trParams}`)
      .then(setBars)
      .catch(() => setBars([]));

    if (timeframe === "1D") {
      apiFetch<StockDetailPayload["technicals"]>(
        `/api/v1/market/assets/${symbol}/technicals?return_mode=${returnMode}&apply_shariah_purification=${applyPurification}`,
      )
        .then((tech) => {
          if (!tech) return;
          setDetail((prev) => (prev ? { ...prev, technicals: tech } : prev));
        })
        .catch(() => {});
    }
  }, [symbol, timeframe, returnMode, applyPurification]);

  const handleAddToWatchlist = async () => {
    if (!user) {
      setWatchNotice("Sign in or create an account to save to watchlists.");
      return;
    }
    try {
      let lists = await apiFetch<Array<{ id: string; name: string }>>("/api/v1/watchlists");
      if (lists.length === 0) {
        const created = await apiFetch<{ id: string; name: string }>("/api/v1/watchlists", {
          method: "POST",
          body: JSON.stringify({ name: "Primary Watchlist" }),
        });
        lists = [created];
      }
      await apiFetch(`/api/v1/watchlists/${lists[0].id}/items/${encodeURIComponent(symbol)}`, {
        method: "POST",
      });
      setWatchNotice(`Added ${symbol} to "${lists[0].name}".`);
    } catch (err) {
      setWatchNotice(err instanceof Error ? err.message : "Unable to add to watchlist.");
    }
  };

  if (!detail) {
    return <div className="okz-card p-8 text-sm okz-muted">Loading {symbol} fundamentals &amp; chart...</div>;
  }

  const {
    asset,
    profile,
    shariah,
    technicals,
    fundamentals,
    deep_snapshot,
    valuation_lab,
    institutional_holders,
    technical_gauge,
    seasonality,
    factor_ranks,
    financial_reports,
  } = detail;
  const low52 = Number(profile?.low_52w || 0);
  const high52 = Number(profile?.high_52w || 0);
  const spotPx = Number(asset.last_price || 0);
  const pos52Pct = high52 > low52 && spotPx >= low52 ? Math.min(100, Math.max(0, ((spotPx - low52) / (high52 - low52)) * 100)) : null;
  const isUsdAsset = asset.currency === "USD";
  const ccyPrefix = isUsdAsset ? "$" : "Rs ";
  const liveSliderDcf =
    valuation_lab?.dcf_lab.available && valuation_lab.dcf_lab.free_cashflow_mn && valuation_lab.dcf_lab.shares_mn
      ? computeClientDcf(
          valuation_lab.dcf_lab.free_cashflow_mn,
          valuation_lab.dcf_lab.shares_mn,
          valuation_lab.dcf_lab.total_debt_mn,
          valuation_lab.dcf_lab.cash_mn,
          dcfGrowth,
          dcfWacc,
          valuation_lab.dcf_lab.risk_free_rate_pct ?? 12.0,
        )
      : null;
  const liveSliderUpside =
    liveSliderDcf != null && Number(asset.last_price) > 0
      ? ((liveSliderDcf - Number(asset.last_price)) / Number(asset.last_price)) * 100
      : null;
  const nativePriceStr = isUsdAsset
    ? `$${formatNumber(asset.last_price, 2)} (Rs ${formatNumber(Number(asset.last_price) * usdToPkr, 0)})`
    : formatMoney(asset.last_price, currency, usdToPkr);

  return (
    <div className="space-y-6">
      <div className="okz-card p-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-2xl font-bold">{asset.symbol}</h1>
            <span className="text-xs px-2 py-0.5 rounded bg-sky-500/15 text-sky-400 font-medium">
              {profile?.exchange || (isUsdAsset ? "US" : "PSX")} &middot; {asset.currency || "PKR"}
            </span>
            <span className="text-xs px-2 py-0.5 rounded bg-[var(--card-muted)] okz-muted">
              {asset.sector_or_category}
            </span>
            {isUsdAsset && (
              <span className="text-xs px-2 py-0.5 rounded bg-amber-500/15 text-amber-400 font-medium">
                W-8BEN Div WHT: {profile?.default_wht_rate_pct ?? "15.00"}%
              </span>
            )}
            {asset.is_shariah_compliant && (
              <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-500 font-semibold">
                <ShieldCheck className="w-3.5 h-3.5" /> Shariah Compliant
              </span>
            )}
          </div>
          <p className="text-sm okz-muted mt-1">{asset.name}</p>
          {pos52Pct != null && (
            <div className="mt-3 max-w-xs space-y-1">
              <div className="flex justify-between text-[10px] font-mono okz-muted">
                <span>52W Low: {formatNumber(low52, 2)}</span>
                <span>52W High: {formatNumber(high52, 2)}</span>
              </div>
              <div className="h-1.5 w-full rounded-full bg-[var(--border)] relative overflow-hidden">
                <div className="h-full bg-gradient-to-r from-rose-500 via-amber-400 to-emerald-500" style={{ width: `${pos52Pct}%` }} />
              </div>
            </div>
          )}
        </div>

        <div className="sm:text-right space-y-2">
          <div>
            <div className="text-2xl font-bold">{nativePriceStr}</div>
            <div className={`text-sm font-semibold ${pctColorClass(asset.change_pct)}`}>
              {formatPct(asset.change_pct)} &bull; Vol {formatNumber(asset.volume, 0)}
            </div>
          </div>
          <div className="flex flex-wrap sm:justify-end gap-2">
            <Link
              href={`/stocks/compare?symbols=${encodeURIComponent(asset.symbol)}`}
              className="px-3 py-1.5 rounded-md border border-[var(--border)] hover:border-sky-500 text-xs font-semibold inline-flex items-center gap-1.5"
            >
              <GitCompareArrows className="w-3.5 h-3.5 text-sky-400" /> Compare
            </Link>
            <button
              type="button"
              onClick={() => window.print()}
              title="Export clean 1-page Institutional Research Tear-Sheet PDF"
              className="okz-no-print px-3 py-1.5 rounded-md border border-[var(--border)] hover:border-emerald-500 text-xs font-semibold inline-flex items-center gap-1.5"
            >
              <Printer className="w-3.5 h-3.5 text-emerald-500" /> Print Tear-Sheet
            </button>
            <button
              type="button"
              onClick={handleAddToWatchlist}
              className="okz-no-print px-3 py-1.5 rounded-md border border-[var(--border)] hover:border-emerald-500 text-xs font-semibold inline-flex items-center gap-1.5"
            >
              <BookmarkPlus className="w-3.5 h-3.5 text-emerald-500" /> Add to Watchlist
            </button>
            <Link
              href="/portfolio"
              className="okz-no-print px-3 py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold inline-flex items-center gap-1.5"
            >
              <Briefcase className="w-3.5 h-3.5" /> Log Trade
            </Link>
          </div>
          {watchNotice && <div className="text-xs text-emerald-500 okz-no-print">{watchNotice}</div>}
        </div>
      </div>

      {/* Print-Only Institutional Equity Tear-Sheet Banner */}
      <div className="okz-print-only border-b-2 border-emerald-700 pb-2 mb-3 text-xs">
        <div className="flex justify-between items-baseline">
          <strong className="text-base">OpenKhazana — Institutional Equity Research Tear-Sheet</strong>
          <span>Generated: {new Date().toLocaleDateString()}</span>
        </div>
        <div className="text-slate-600">
          {asset.symbol} ({asset.name}) &middot; {asset.sector_or_category} &middot; Informational research memo only.
        </div>
      </div>

      {/* Institutional 7-Tab Workbench Bar */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-[var(--border)] pb-2 text-xs">
        {(
          [
            ["overview", "Overview & Chart"],
            [
              "reports",
              `Financials & Reports (${(financial_reports?.annual_rows?.length || 0) + (financial_reports?.quarterly_rows?.length || 0)})`,
            ],
            ["technicals", "Technicals & Seasonality"],
            ["valuation", "Valuation & Quality Lab"],
            ["dividends", `Dividends & Filings (${detail.announcements.length + detail.corporate_actions.length})`],
            [
              "research",
              `Brokerage Research (${(stockResearchPayload?.stock_reports?.length || 0) + (stockResearchPayload?.sector_reports?.length || 0)})`,
            ],
            [
              "news",
              `News & Sector (${(stockNewsPayload?.stock_news?.length || 0) + (stockNewsPayload?.sector_news?.length || 0)})`,
            ],
          ] as const
        ).map(([tabKey, label]) => (
          <button
            key={tabKey}
            type="button"
            onClick={() => setActiveTab(tabKey)}
            className={`px-3.5 py-2 rounded-lg font-semibold transition ${
              activeTab === tabKey
                ? "bg-emerald-600 text-white shadow-sm"
                : "bg-[var(--card)] border border-[var(--border)] okz-muted hover:text-[var(--text)] hover:border-emerald-500/50"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* TAB 1: OVERVIEW & CHART */}
      {activeTab === "overview" && (
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <div className="okz-card p-5 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">
                Price History &amp; Technical Overlays ({timeframe === "5M" ? "5M Intraday Lazy-Cached" : `1D (${returnMode})`})
              </h2>
              <div className="flex flex-wrap items-center gap-2">
                {timeframe === "1D" && (
                  <>
                    <select
                      value={returnMode}
                      onChange={(e) => setReturnMode(e.target.value as typeof returnMode)}
                      className="px-2 py-1 rounded border border-[var(--border)] bg-[var(--bg)] text-xs font-medium"
                    >
                      <option value="PRICE">Raw Price</option>
                      <option value="GROSS_TR">Gross Total Return (Div + Bonus)</option>
                      <option value="NET_FILER">Net Total Return (Filer WHT)</option>
                      <option value="NET_NON_FILER">Net Total Return (Non-Filer)</option>
                    </select>
                    {shariah && returnMode !== "PRICE" && (
                      <label className="inline-flex items-center gap-1 text-[11px] okz-muted cursor-pointer">
                        <input
                          type="checkbox"
                          checked={applyPurification}
                          onChange={(e) => setApplyPurification(e.target.checked)}
                        />
                        <span>Purify</span>
                      </label>
                    )}
                  </>
                )}
                <div className="flex rounded-md bg-[var(--card-muted)] p-0.5 text-xs font-semibold">
                  <button
                    onClick={() => setTimeframe("5M")}
                    className={`px-2.5 py-1 rounded ${timeframe === "5M" ? "bg-[var(--card)] shadow text-emerald-500" : "okz-muted"}`}
                  >
                    5M Intraday
                  </button>
                  <button
                    onClick={() => setTimeframe("1D")}
                    className={`px-2.5 py-1 rounded ${timeframe === "1D" ? "bg-[var(--card)] shadow text-emerald-500" : "okz-muted"}`}
                  >
                    1D Daily
                  </button>
                </div>
              </div>
            </div>
            <PriceChart bars={bars} mode="candlestick" intraday={timeframe === "5M"} showVolume />
          </div>

          {institutional_holders && institutional_holders.length > 0 && (
            <div className="okz-card p-5 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="text-sm font-semibold flex items-center gap-1.5">
                    <Building2 className="w-4 h-4 text-sky-400" />
                    Held by Pakistani Mutual Funds (SECP FMR Radar)
                  </h2>
                  <p className="text-[11px] okz-muted">
                    Cross-linked directly from parsed SECP monthly Fund Manager Reports Top-10 equity disclosures.
                  </p>
                </div>
                <span className="text-xs px-2 py-0.5 rounded bg-sky-500/15 text-sky-400 font-semibold">
                  {institutional_holders.length} Fund{institutional_holders.length > 1 ? "s" : ""} Disclosed
                </span>
              </div>
              <div className="overflow-x-auto border border-[var(--border)] rounded-lg">
                <table className="w-full text-xs">
                  <thead className="bg-[var(--bg-elevated)] okz-muted text-left">
                    <tr>
                      <th className="py-2 px-3">Mutual Fund / VPS</th>
                      <th className="py-2 px-3">AMC</th>
                      <th className="py-2 px-3 text-right">FMR Weight</th>
                      <th className="py-2 px-3 text-right">Est. Stake (PKR Mn)</th>
                      <th className="py-2 px-3 text-right">FMR Month</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border)]">
                    {institutional_holders.map((h) => (
                      <tr key={h.fund_symbol} className="hover:bg-[var(--card-muted)]/50">
                        <td className="py-2 px-3">
                          <Link
                            href={`/funds/${encodeURIComponent(h.fund_symbol)}`}
                            className="font-bold text-emerald-500 hover:underline"
                          >
                            {h.fund_symbol}
                          </Link>
                          <div className="text-[11px] okz-muted truncate max-w-64">{h.fund_name}</div>
                        </td>
                        <td className="py-2 px-3 okz-muted">{h.amc_name}</td>
                        <td className="py-2 px-3 text-right font-mono font-bold text-emerald-400">
                          {h.weight_pct.toFixed(2)}%
                        </td>
                        <td className="py-2 px-3 text-right font-mono">
                          {h.est_value_mn != null ? `Rs ${formatNumber(h.est_value_mn, 1)} Mn` : "—"}
                        </td>
                        <td className="py-2 px-3 text-right okz-muted">{h.report_label}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        <div className="space-y-4">
          <TechnicalGauge gauge={technical_gauge} />

          {factor_ranks && factor_ranks.composite_score != null && (
            <div className="okz-card p-5 space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold">Pandas Factor Percentile Rank</h2>
                  <p className="text-[11px] okz-muted">
                    Sector Rank #{factor_ranks.sector_rank} of {factor_ranks.sector_total} ({factor_ranks.sector})
                  </p>
                </div>
                <span className="px-2.5 py-1 rounded bg-emerald-500/15 text-emerald-400 font-mono font-bold text-xs">
                  {factor_ranks.composite_score.toFixed(0)} / 100
                </span>
              </div>
              <div className="space-y-2 text-xs">
                {(
                  [
                    ["Value (P/E & P/B)", factor_ranks.value_percentile, "bg-emerald-500"],
                    ["Quality (ROE & F-Score)", factor_ranks.quality_percentile, "bg-sky-500"],
                    ["Dividend Income", factor_ranks.income_percentile, "bg-amber-500"],
                    ["Market Cap Size", factor_ranks.size_percentile, "bg-purple-500"],
                  ] as const
                ).map(([lbl, pct, barColor]) => (
                  <div key={lbl} className="space-y-1">
                    <div className="flex justify-between">
                      <span className="okz-muted">{lbl}</span>
                      <span className="font-mono font-semibold">{pct != null ? `${pct.toFixed(0)}th pct` : "—"}</span>
                    </div>
                    <div className="h-1.5 w-full rounded bg-[var(--border)] overflow-hidden">
                      <div className={`h-full ${barColor}`} style={{ width: `${Math.min(100, pct ?? 0)}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {profile && (
            <div className="okz-card p-5 space-y-3">
              <h2 className="text-sm font-semibold">Key Fundamentals</h2>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <div className="okz-muted">Market Cap</div>
                  <div className="font-semibold text-sm">
                    {isUsdAsset ? "USD" : "PKR"} {formatNumber(profile.market_cap_mn, 0)} Mn
                  </div>
                </div>
                <div>
                  <div className="okz-muted">P/E Ratio</div>
                  <div className="font-semibold text-sm">{profile.pe_ratio ?? "—"}</div>
                </div>
                <div>
                  <div className="okz-muted">P/B Ratio</div>
                  <div className="font-semibold text-sm">{profile.pb_ratio ?? "—"}</div>
                </div>
                <div>
                  <div className="okz-muted">EPS (TTM)</div>
                  <div className="font-semibold text-sm">{profile.eps ?? "—"}</div>
                </div>
                <div>
                  <div className="okz-muted">ROE</div>
                  <div className="font-semibold text-sm">{profile.roe_pct ? `${profile.roe_pct}%` : "—"}</div>
                </div>
                <div>
                  <div className="okz-muted">Dividend Yield</div>
                  <div className="font-semibold text-sm text-emerald-500">{profile.dividend_yield_pct}%</div>
                </div>
                <div className="col-span-2">
                  <div className="okz-muted">52-Week Range</div>
                  <div className="font-semibold text-sm">
                    {profile.low_52w} — {profile.high_52w}
                  </div>
                </div>
              </div>
            </div>
          )}

          {shariah && (
            <div className="okz-card p-5 space-y-2.5">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold">KMI / Shariah Audit ({shariah.fiscal_period})</h2>
                {shariah.passed ? (
                  <ShieldCheck className="w-4 h-4 text-emerald-500" />
                ) : (
                  <ShieldAlert className="w-4 h-4 text-rose-500" />
                )}
              </div>
              <div className="space-y-1.5 text-xs">
                <div className="flex justify-between">
                  <span className="okz-muted">Debt / Total Assets (&lt;37%)</span>
                  <span className="font-semibold">{shariah.debt_to_assets_pct}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Non-Compliant Inv. (&lt;33%)</span>
                  <span className="font-semibold">{shariah.non_compliant_inv_pct}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Non-Compliant Income (&lt;5%)</span>
                  <span className="font-semibold">{shariah.non_compliant_income_pct}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Illiquid Assets (&ge;25%)</span>
                  <span className="font-semibold">{shariah.illiquid_assets_pct}%</span>
                </div>
                <div className="flex justify-between pt-1.5 border-t border-[var(--border)]">
                  <span className="okz-muted">Dividend Purification Rate</span>
                  <span className="font-semibold text-amber-500">
                    {shariah.purification_rate_pct ?? shariah.non_compliant_income_pct}%
                  </span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
      )}

      {/* TAB 2: ANNUAL & QUARTERLY FINANCIAL REPORTS & PDF ANALYZER */}
      {activeTab === "reports" && (
        <FinancialReportsPanel
          symbol={asset.symbol}
          currency={asset.currency || "PKR"}
          initialData={financial_reports}
          onUpdated={(fresh) => setDetail((prev) => (prev ? { ...prev, financial_reports: fresh } : prev))}
        />
      )}

      {/* TAB 3: TECHNICALS & PANDAS SEASONALITY HEATMAP */}
      {activeTab === "technicals" && (
        <div className="space-y-6">
          <TechnicalGauge gauge={technical_gauge} showTables />
          <SeasonalityHeatmap data={seasonality} />
          {technicals && (
            <div className="okz-card p-5 space-y-4">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div className="flex items-center gap-2">
                  <Activity className="w-4 h-4 text-emerald-500" />
                  <h2 className="text-sm font-semibold">Technical Analysis &amp; Momentum Signals (`ta` + `pandas`)</h2>
                </div>
                <span
                  className={`px-2.5 py-1 rounded text-xs font-bold ${
                    technicals.overall_signal === "BULLISH"
                      ? "bg-emerald-500/15 text-emerald-400"
                      : technicals.overall_signal === "BEARISH"
                        ? "bg-rose-500/15 text-rose-400"
                        : "bg-amber-500/15 text-amber-400"
                  }`}
                >
                  {technicals.overall_signal} (Bull {technicals.bull_score} / Bear {technicals.bear_score})
                </span>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="okz-muted">RSI (14) / StochRSI K</div>
                  <div className="text-sm font-bold mt-0.5">
                    {technicals.latest.rsi_14 ?? "—"} / {technicals.latest.stoch_rsi_k ?? "—"}
                  </div>
                </div>
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="okz-muted">MACD / Hist</div>
                  <div className="text-sm font-bold mt-0.5">
                    {technicals.latest.macd ?? "—"} / {technicals.latest.macd_hist ?? "—"}
                  </div>
                </div>
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="okz-muted">SMA 20 / SMA 50</div>
                  <div className="text-sm font-bold mt-0.5">
                    {technicals.latest.sma_20 ?? "—"} / {technicals.latest.sma_50 ?? "—"}
                  </div>
                </div>
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="okz-muted">20D VWAP / ADX(14)</div>
                  <div className="text-sm font-bold mt-0.5">
                    {technicals.latest.vwap_20 ?? "—"} / {technicals.latest.adx_14 ?? "—"}
                  </div>
                </div>
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="okz-muted">ATR (14) / Realized Vol</div>
                  <div className="text-sm font-bold mt-0.5">
                    {technicals.latest.atr_14 ?? "—"} /{" "}
                    {technicals.latest.annualized_volatility_pct != null
                      ? `${technicals.latest.annualized_volatility_pct}%`
                      : "—"}
                  </div>
                </div>
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="okz-muted">Max Drawdown (1Y)</div>
                  <div className="text-sm font-bold mt-0.5 text-rose-400">
                    {technicals.latest.max_drawdown_pct != null ? `${technicals.latest.max_drawdown_pct}%` : "—"}
                  </div>
                </div>
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="okz-muted">52W Range Position</div>
                  <div className="text-sm font-bold mt-0.5">
                    {technicals.latest.range_52w_pct != null ? `${technicals.latest.range_52w_pct}%` : "—"}
                  </div>
                </div>
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="okz-muted">Bollinger Upper / Lower</div>
                  <div className="text-sm font-bold mt-0.5">
                    {technicals.latest.bb_upper ?? "—"} / {technicals.latest.bb_lower ?? "—"}
                  </div>
                </div>
              </div>

              {technicals.latest.pivot != null && (
                <div className="p-3 rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)] space-y-2">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-semibold">Classic &amp; Fibonacci Support / Resistance Pivots</span>
                    <span className="okz-muted">
                      Fib S1: {technicals.latest.fib_s1 ?? "—"} &middot; Fib R1: {technicals.latest.fib_r1 ?? "—"}
                    </span>
                  </div>
                  <div className="grid grid-cols-5 gap-2 text-center text-xs">
                    <div className="p-1.5 rounded bg-emerald-500/10 text-emerald-400">
                      <div className="text-[10px] uppercase opacity-75">Support S2</div>
                      <div className="font-bold">{technicals.latest.s2 ?? "—"}</div>
                    </div>
                    <div className="p-1.5 rounded bg-emerald-500/15 text-emerald-400">
                      <div className="text-[10px] uppercase opacity-75">Support S1</div>
                      <div className="font-bold">{technicals.latest.s1 ?? "—"}</div>
                    </div>
                    <div className="p-1.5 rounded bg-[var(--card-muted)] font-bold">
                      <div className="text-[10px] uppercase okz-muted">Pivot (P)</div>
                      <div>{technicals.latest.pivot}</div>
                    </div>
                    <div className="p-1.5 rounded bg-rose-500/15 text-rose-400">
                      <div className="text-[10px] uppercase opacity-75">Resist R1</div>
                      <div className="font-bold">{technicals.latest.r1 ?? "—"}</div>
                    </div>
                    <div className="p-1.5 rounded bg-rose-500/10 text-rose-400">
                      <div className="text-[10px] uppercase opacity-75">Resist R2</div>
                      <div className="font-bold">{technicals.latest.r2 ?? "—"}</div>
                    </div>
                  </div>
                </div>
              )}

              {technicals.signals.length > 0 && (
                <ul className="space-y-1 text-xs okz-muted list-disc list-inside">
                  {technicals.signals.map((sig, i) => (
                    <li key={i}>{sig}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}

      {/* TAB 4: VALUATION & QUALITY LAB */}
      {activeTab === "valuation" && (
        <div className="space-y-6">
          {fundamentals && (
            <div className="okz-card p-5 space-y-4">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div>
                  <h2 className="text-sm font-semibold">
                    Fundamental Valuation &amp; Quality Engine (Graham Intrinsic Value &amp; ERP)
                  </h2>
                  <p className="text-xs okz-muted">
                    Composite 0–100 Fundamental Quality Score across Valuation, Profitability, Multi-Year Growth &amp; Balance Sheet Solvency.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="px-2.5 py-1 rounded text-xs font-bold bg-sky-500/15 text-sky-400">
                    Quality {fundamentals.quality_score}/100
                  </span>
                  <span
                    className={`px-2.5 py-1 rounded text-xs font-bold ${
                      fundamentals.valuation_verdict === "UNDERVALUED"
                        ? "bg-emerald-500/15 text-emerald-400"
                        : fundamentals.valuation_verdict === "OVERVALUED"
                          ? "bg-rose-500/15 text-rose-400"
                          : "bg-amber-500/15 text-amber-400"
                    }`}
                  >
                    {fundamentals.valuation_verdict}
                  </span>
                </div>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="flex justify-between">
                    <span className="okz-muted">Valuation</span>
                    <span className="font-bold">{fundamentals.valuation_score}/25</span>
                  </div>
                  <div className="w-full h-1.5 rounded bg-[var(--border)] mt-1.5 overflow-hidden">
                    <div
                      className="h-full bg-emerald-500"
                      style={{ width: `${Math.min(100, (fundamentals.valuation_score / 25) * 100)}%` }}
                    />
                  </div>
                </div>
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="flex justify-between">
                    <span className="okz-muted">Profitability</span>
                    <span className="font-bold">{fundamentals.profitability_score}/25</span>
                  </div>
                  <div className="w-full h-1.5 rounded bg-[var(--border)] mt-1.5 overflow-hidden">
                    <div
                      className="h-full bg-sky-500"
                      style={{ width: `${Math.min(100, (fundamentals.profitability_score / 25) * 100)}%` }}
                    />
                  </div>
                </div>
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="flex justify-between">
                    <span className="okz-muted">Growth</span>
                    <span className="font-bold">{fundamentals.growth_score}/25</span>
                  </div>
                  <div className="w-full h-1.5 rounded bg-[var(--border)] mt-1.5 overflow-hidden">
                    <div
                      className="h-full bg-indigo-500"
                      style={{ width: `${Math.min(100, (fundamentals.growth_score / 25) * 100)}%` }}
                    />
                  </div>
                </div>
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="flex justify-between">
                    <span className="okz-muted">Solvency</span>
                    <span className="font-bold">{fundamentals.solvency_score}/25</span>
                  </div>
                  <div className="w-full h-1.5 rounded bg-[var(--border)] mt-1.5 overflow-hidden">
                    <div
                      className="h-full bg-amber-500"
                      style={{ width: `${Math.min(100, (fundamentals.solvency_score / 25) * 100)}%` }}
                    />
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                <div className="p-2.5 rounded border border-[var(--border)]">
                  <div className="okz-muted">Graham Intrinsic Value</div>
                  <div className="text-sm font-bold mt-0.5">
                    {fundamentals.graham_intrinsic_value != null
                      ? `${ccyPrefix}${formatNumber(fundamentals.graham_intrinsic_value, 2)}`
                      : "—"}
                  </div>
                  <div className="text-[11px] okz-muted">
                    Implied BVPS:{" "}
                    {fundamentals.implied_bvps != null ? `${ccyPrefix}${formatNumber(fundamentals.implied_bvps, 2)}` : "—"}
                  </div>
                </div>
                <div className="p-2.5 rounded border border-[var(--border)]">
                  <div className="okz-muted">Margin of Safety</div>
                  <div
                    className={`text-sm font-bold mt-0.5 ${
                      (fundamentals.margin_of_safety_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"
                    }`}
                  >
                    {fundamentals.margin_of_safety_pct != null ? formatPct(fundamentals.margin_of_safety_pct) : "—"}
                  </div>
                  <div className="text-[11px] okz-muted">vs Current Spot Price</div>
                </div>
                <div className="p-2.5 rounded border border-[var(--border)]">
                  <div className="okz-muted">Earnings Yield (E/P)</div>
                  <div className="text-sm font-bold mt-0.5">
                    {fundamentals.earnings_yield_pct != null ? `${fundamentals.earnings_yield_pct}%` : "—"}
                  </div>
                  <div className="text-[11px] okz-muted">
                    vs {fundamentals.risk_free_rate_pct}% Risk-Free (ERP{" "}
                    {fundamentals.equity_risk_premium_pct != null ? `${fundamentals.equity_risk_premium_pct}%` : "—"})
                  </div>
                </div>
                <div className="p-2.5 rounded border border-[var(--border)]">
                  <div className="okz-muted">Multi-Year Rev / EPS CAGR</div>
                  <div className="text-sm font-bold mt-0.5">
                    {fundamentals.revenue_cagr_pct != null ? `${fundamentals.revenue_cagr_pct}%` : "—"} /{" "}
                    {fundamentals.eps_cagr_pct != null ? `${fundamentals.eps_cagr_pct}%` : "—"}
                  </div>
                  <div className="text-[11px] okz-muted">
                    Net Margin: {fundamentals.latest_net_margin_pct != null ? `${fundamentals.latest_net_margin_pct}%` : "—"}{" "}
                    ({fundamentals.net_margin_trend || "STABLE"})
                  </div>
                </div>
              </div>

              {fundamentals.signals.length > 0 && (
                <ul className="space-y-1 text-xs okz-muted list-disc list-inside">
                  {fundamentals.signals.map((sig, idx) => (
                    <li key={idx}>{sig}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {deep_snapshot && (
            <div className="okz-card p-5 space-y-4">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div>
                  <h2 className="text-sm font-semibold">
                    Deep Balance Sheet, Cash Flow &amp; Quantitative Quality ({deep_snapshot.fiscal_period})
                  </h2>
                  <p className="text-[11px] okz-muted">
                    Source: {deep_snapshot.source} &bull; Live SBP / Treasury Risk-Free Discounting
                  </p>
                </div>
                <div className="flex items-center gap-2 text-xs flex-wrap">
                  <span
                    className={`px-2 py-1 rounded font-semibold ${
                      deep_snapshot.source.includes(".KA")
                        ? "bg-sky-500/15 text-sky-400"
                        : "bg-emerald-500/15 text-emerald-400"
                    }`}
                  >
                    Source: {deep_snapshot.source}
                  </span>
                  {deep_snapshot.piotroski_f_score != null && (
                    <span
                      className={`px-2.5 py-1 rounded font-bold ${
                        deep_snapshot.piotroski_f_score >= 7
                          ? "bg-emerald-500/15 text-emerald-400"
                          : deep_snapshot.piotroski_f_score <= 3
                            ? "bg-rose-500/15 text-rose-400"
                            : "bg-amber-500/15 text-amber-400"
                      }`}
                    >
                      Piotroski F-Score: {deep_snapshot.piotroski_f_score} / 9
                    </span>
                  )}
                  {deep_snapshot.altman_z_score != null && (
                    <span
                      className={`px-2.5 py-1 rounded font-bold ${
                        Number(deep_snapshot.altman_z_score) >= 2.99
                          ? "bg-emerald-500/15 text-emerald-400"
                          : Number(deep_snapshot.altman_z_score) < 1.81
                            ? "bg-rose-500/15 text-rose-400"
                            : "bg-amber-500/15 text-amber-400"
                      }`}
                    >
                      Altman Z: {formatNumber(deep_snapshot.altman_z_score, 2)}
                    </span>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="okz-muted">DCF Intrinsic Value</div>
                  <div className="text-sm font-bold mt-0.5 text-emerald-500">
                    {deep_snapshot.dcf_intrinsic_value != null
                      ? `${ccyPrefix}${formatNumber(deep_snapshot.dcf_intrinsic_value, 2)}`
                      : "—"}
                  </div>
                  <div className="text-[11px] okz-muted">
                    BVPS: {deep_snapshot.bvps != null ? `${ccyPrefix}${formatNumber(deep_snapshot.bvps, 2)}` : "—"}
                  </div>
                </div>
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="okz-muted">ROE / ROA / ROIC</div>
                  <div className="text-sm font-bold mt-0.5">
                    {formatNumber(deep_snapshot.roe_pct, 1)}% / {formatNumber(deep_snapshot.roa_pct, 1)}% /{" "}
                    {formatNumber(deep_snapshot.roic_pct, 1)}%
                  </div>
                  <div className="text-[11px] okz-muted">
                    Op Margin: {formatNumber(deep_snapshot.operating_margin_pct, 1)}%
                  </div>
                </div>
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="okz-muted">Operating CF / FCF</div>
                  <div className="text-sm font-bold mt-0.5">
                    {formatNumber(deep_snapshot.operating_cashflow_mn, 0)} /{" "}
                    {formatNumber(deep_snapshot.free_cashflow_mn, 0)} Mn
                  </div>
                  <div className="text-[11px] okz-muted">CapEx: {formatNumber(deep_snapshot.capex_mn, 0)} Mn</div>
                </div>
                <div className="p-2.5 rounded bg-[var(--card-muted)]">
                  <div className="okz-muted">Debt / Cash / D/E</div>
                  <div className="text-sm font-bold mt-0.5">
                    {formatNumber(deep_snapshot.total_debt_mn, 0)} /{" "}
                    {formatNumber(deep_snapshot.cash_equivalents_mn, 0)} Mn
                  </div>
                  <div className="text-[11px] okz-muted">
                    Current: {formatNumber(deep_snapshot.current_ratio, 2)}x &bull; D/E:{" "}
                    {formatNumber(deep_snapshot.debt_to_equity, 2)}x
                  </div>
                </div>
              </div>

              {deep_snapshot.analyst_target_price != null && (
                <div className="text-xs flex items-center justify-between px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg-elevated)]">
                  <span className="okz-muted">Street Consensus Target Price</span>
                  <span className="font-bold">
                    {ccyPrefix}
                    {formatNumber(deep_snapshot.analyst_target_price, 2)}{" "}
                    {deep_snapshot.analyst_rating ? `(${deep_snapshot.analyst_rating.toUpperCase()})` : ""}
                  </span>
                </div>
              )}
            </div>
          )}

          {valuation_lab && (
            <div className="okz-card p-5 space-y-5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="text-sm font-semibold flex items-center gap-1.5">
                    <SlidersHorizontal className="w-4 h-4 text-emerald-500" />
                    Interactive Valuation Lab &amp; DCF Sensitivity Matrix
                  </h2>
                  <p className="text-[11px] okz-muted">
                    Stress-test 5Y Free Cash Flow growth &amp; Sovereign WACC (Rf {valuation_lab.dcf_lab.risk_free_rate_pct ?? 12}% + 5.5% ERP) against live price.
                  </p>
                </div>
                {valuation_lab.dcf_lab.available && liveSliderDcf != null && (
                  <div className="text-right">
                    <div className="text-[10px] uppercase tracking-wider okz-muted">Interactive Fair Value</div>
                    <div className="flex items-center gap-2 justify-end">
                      <span className="text-base font-bold text-emerald-400">
                        {ccyPrefix}
                        {formatNumber(liveSliderDcf, 2)}
                      </span>
                      {liveSliderUpside != null && (
                        <span
                          className={`text-xs font-bold px-2 py-0.5 rounded ${
                            liveSliderUpside >= 0
                              ? "bg-emerald-500/15 text-emerald-400"
                              : "bg-rose-500/15 text-rose-400"
                          }`}
                        >
                          {liveSliderUpside >= 0 ? "+" : ""}
                          {liveSliderUpside.toFixed(1)}%
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>

              {valuation_lab.dcf_lab.available ? (
                <div className="space-y-4">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 p-3.5 rounded-lg bg-[var(--card-muted)]/60 border border-[var(--border)] text-xs">
                    <div className="space-y-1.5">
                      <div className="flex justify-between">
                        <span className="okz-muted">5-Year FCF Growth Assumption</span>
                        <span className="font-mono font-bold text-emerald-400">{dcfGrowth.toFixed(1)}%</span>
                      </div>
                      <input
                        type="range"
                        min={2}
                        max={25}
                        step={0.5}
                        value={dcfGrowth}
                        onChange={(e) => setDcfGrowth(Number(e.target.value))}
                        className="w-full accent-emerald-500"
                      />
                      <div className="flex justify-between text-[10px] okz-muted">
                        <span>2% (Bear)</span>
                        <span>Base: {valuation_lab.dcf_lab.base_growth_pct}%</span>
                        <span>25% (Bull)</span>
                      </div>
                    </div>

                    <div className="space-y-1.5">
                      <div className="flex justify-between">
                        <span className="okz-muted">Discount Rate (WACC %)</span>
                        <span className="font-mono font-bold text-sky-400">{dcfWacc.toFixed(1)}%</span>
                      </div>
                      <input
                        type="range"
                        min={8}
                        max={24}
                        step={0.5}
                        value={dcfWacc}
                        onChange={(e) => setDcfWacc(Number(e.target.value))}
                        className="w-full accent-sky-500"
                      />
                      <div className="flex justify-between text-[10px] okz-muted">
                        <span>8% (Low Rate)</span>
                        <span>Base: {valuation_lab.dcf_lab.base_wacc_pct}%</span>
                        <span>24% (High Risk)</span>
                      </div>
                    </div>
                  </div>

                  {valuation_lab.dcf_lab.sensitivity_rows.length > 0 && (
                    <div className="space-y-1.5">
                      <div className="text-[11px] font-semibold uppercase tracking-wider okz-muted">
                        5&times;5 Intrinsic Value Sensitivity Grid (Growth % &darr; vs WACC % &rarr;)
                      </div>
                      <div className="overflow-x-auto border border-[var(--border)] rounded-lg">
                        <table className="w-full text-xs">
                          <thead className="bg-[var(--bg-elevated)] okz-muted">
                            <tr>
                              <th className="py-2 px-2.5 text-left">Growth \ WACC</th>
                              {valuation_lab.dcf_lab.wacc_steps.map((w) => (
                                <th key={w} className="py-2 px-2.5 text-right font-mono">
                                  {w.toFixed(1)}%
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-[var(--border)]">
                            {valuation_lab.dcf_lab.sensitivity_rows.map((row) => (
                              <tr key={row.growth_pct}>
                                <td className="py-2 px-2.5 font-mono font-bold bg-[var(--bg-elevated)]/50">
                                  {row.growth_pct.toFixed(1)}%
                                </td>
                                {row.cells.map((cell) => {
                                  const up = cell.upside_pct;
                                  const cellBg =
                                    up == null
                                      ? ""
                                      : up >= 15
                                        ? "bg-emerald-500/20 text-emerald-400 font-bold"
                                        : up >= 0
                                          ? "bg-emerald-500/10 text-emerald-300"
                                          : up <= -20
                                            ? "bg-rose-500/20 text-rose-400"
                                            : "bg-rose-500/10 text-rose-300";
                                  return (
                                    <td key={cell.wacc_pct} className={`py-2 px-2.5 text-right font-mono ${cellBg}`}>
                                      <div>{cell.intrinsic_value != null ? `${ccyPrefix}${formatNumber(cell.intrinsic_value, 1)}` : "—"}</div>
                                      {up != null && (
                                        <div className="text-[10px] opacity-85">
                                          {up >= 0 ? "+" : ""}
                                          {up.toFixed(0)}%
                                        </div>
                                      )}
                                    </td>
                                  );
                                })}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="p-3.5 rounded-lg border border-sky-500/30 bg-sky-500/5 text-xs space-y-2">
                  <div className="font-semibold text-sky-400">
                    {valuation_lab.dcf_lab.reason_unavailable || "DCF valuation unavailable for this issuer."}
                  </div>
                  {valuation_lab.bull_bear_digest.is_financial_sector && profile && (
                    <div className="grid grid-cols-3 gap-3 pt-1">
                      <div className="p-2 rounded bg-[var(--card-muted)]">
                        <div className="okz-muted text-[10px]">Bank P/B Multiple</div>
                        <div className="font-bold text-sm">{profile.pb_ratio ? `${profile.pb_ratio}x` : "—"}</div>
                      </div>
                      <div className="p-2 rounded bg-[var(--card-muted)]">
                        <div className="okz-muted text-[10px]">Return on Equity</div>
                        <div className="font-bold text-sm text-emerald-400">
                          {deep_snapshot?.roe_pct ? `${formatNumber(deep_snapshot.roe_pct, 1)}%` : profile.roe_pct ? `${profile.roe_pct}%` : "—"}
                        </div>
                      </div>
                      <div className="p-2 rounded bg-[var(--card-muted)]">
                        <div className="okz-muted text-[10px]">Dividend Yield</div>
                        <div className="font-bold text-sm text-emerald-400">{profile.dividend_yield_pct}%</div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* 3-Factor DuPont ROE Decomposition */}
              {valuation_lab.dupont_roe && valuation_lab.dupont_roe.roe_dupont_pct != null && (
                <div className="pt-2 border-t border-[var(--border)] space-y-2">
                  <div className="text-xs font-semibold flex items-center gap-1.5">
                    <Layers className="w-3.5 h-3.5 text-sky-400" />
                    3-Factor DuPont ROE Decomposition
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-xs">
                    <div className="p-2.5 rounded bg-[var(--card-muted)]">
                      <div className="okz-muted text-[10px]">Net Profit Margin</div>
                      <div className="font-bold text-sm mt-0.5">{formatNumber(valuation_lab.dupont_roe.net_margin_pct, 2)}%</div>
                    </div>
                    <div className="p-2.5 rounded bg-[var(--card-muted)]">
                      <div className="okz-muted text-[10px]">&times; Asset Turnover</div>
                      <div className="font-bold text-sm mt-0.5">{formatNumber(valuation_lab.dupont_roe.asset_turnover, 2)}x</div>
                    </div>
                    <div className="p-2.5 rounded bg-[var(--card-muted)]">
                      <div className="okz-muted text-[10px]">&times; Equity Multiplier</div>
                      <div className="font-bold text-sm mt-0.5">{formatNumber(valuation_lab.dupont_roe.equity_multiplier, 2)}x</div>
                    </div>
                    <div className="p-2.5 rounded bg-emerald-500/10 border border-emerald-500/30">
                      <div className="text-emerald-400 text-[10px] font-semibold">= DuPont ROE</div>
                      <div className="font-bold text-sm text-emerald-400 mt-0.5">
                        {formatNumber(valuation_lab.dupont_roe.roe_dupont_pct, 2)}%
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* 9-Point Piotroski Accounting Checklist (Collapsible Accordion) */}
              {valuation_lab.piotroski_checklist && valuation_lab.piotroski_checklist.length > 0 && (
                <div className="pt-2 border-t border-[var(--border)] space-y-2">
                  <button
                    type="button"
                    onClick={() => setPiotroskiOpen((v) => !v)}
                    className="w-full flex items-center justify-between text-xs py-1 hover:text-emerald-400 transition"
                  >
                    <span className="font-semibold flex items-center gap-2">
                      9-Point Piotroski Accounting Checklist
                      {valuation_lab.piotroski_score != null && (
                        <span className="font-mono font-bold text-emerald-400 px-2 py-0.5 rounded bg-emerald-500/15">
                          {valuation_lab.piotroski_score} / 9
                        </span>
                      )}
                    </span>
                    <ChevronDown className={`w-4 h-4 okz-muted transition-transform ${piotroskiOpen ? "rotate-180" : ""}`} />
                  </button>
                  {piotroskiOpen && (
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs">
                      {valuation_lab.piotroski_checklist.map((item) => (
                        <div
                          key={item.criterion}
                          className="p-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)]/40 flex items-start gap-2"
                        >
                          {item.passed === true ? (
                            <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
                          ) : item.passed === false ? (
                            <XCircle className="w-4 h-4 text-rose-500 shrink-0 mt-0.5" />
                          ) : (
                            <span className="w-4 h-4 rounded-full border border-[var(--border)] text-[9px] flex items-center justify-center okz-muted shrink-0 mt-0.5">
                              —
                            </span>
                          )}
                          <div className="min-w-0">
                            <div className="font-medium leading-tight">{item.criterion}</div>
                            <div className="text-[10px] okz-muted mt-0.5">
                              {item.group} &bull; <span className="font-mono">{item.metric}</span>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Automated Bull vs Bear Thesis & Red-Flag Digest */}
              <div className="pt-2 border-t border-[var(--border)] space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                  <span className="font-semibold">Automated Bull vs Bear Thesis &amp; Red-Flag Digest</span>
                  <div className="flex flex-wrap items-center gap-2 text-[11px]">
                    {valuation_lab.bull_bear_digest.cash_conversion_ratio != null && (
                      <span className="px-2 py-0.5 rounded bg-[var(--card-muted)] font-mono">
                        OCF/NI: {valuation_lab.bull_bear_digest.cash_conversion_ratio.toFixed(2)}x
                      </span>
                    )}
                    {valuation_lab.bull_bear_digest.shariah_debt_headroom_pct != null && (
                      <span className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 font-mono">
                        KMI Debt Headroom: +{valuation_lab.bull_bear_digest.shariah_debt_headroom_pct.toFixed(1)}%
                      </span>
                    )}
                  </div>
                </div>

                {valuation_lab.bull_bear_digest.red_flags.length > 0 && (
                  <div className="p-3 rounded-lg border border-rose-500/35 bg-rose-500/10 space-y-1 text-xs">
                    <div className="font-bold text-rose-400 flex items-center gap-1.5">
                      <AlertTriangle className="w-3.5 h-3.5" />
                      Accounting &amp; Risk Red Flags
                    </div>
                    <ul className="list-disc pl-5 space-y-0.5 text-rose-300">
                      {valuation_lab.bull_bear_digest.red_flags.map((rf, i) => (
                        <li key={i}>{rf}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                  <div className="p-3 rounded-lg border border-emerald-500/25 bg-emerald-500/5 space-y-1.5">
                    <div className="font-bold text-emerald-400 flex items-center gap-1.5">
                      <TrendingUp className="w-3.5 h-3.5" />
                      Bull Case Signals
                    </div>
                    {valuation_lab.bull_bear_digest.bull_points.length === 0 ? (
                      <div className="okz-muted">No strong quantitative bull catalysts triggered yet.</div>
                    ) : (
                      <ul className="list-disc pl-4 space-y-1">
                        {valuation_lab.bull_bear_digest.bull_points.map((pt, i) => (
                          <li key={i}>{pt}</li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <div className="p-3 rounded-lg border border-amber-500/25 bg-amber-500/5 space-y-1.5">
                    <div className="font-bold text-amber-400 flex items-center gap-1.5">
                      <TrendingDown className="w-3.5 h-3.5" />
                      Bear Case &amp; Watch Items
                    </div>
                    {valuation_lab.bull_bear_digest.bear_points.length === 0 ? (
                      <div className="okz-muted">No major quantitative bear flags triggered on latest financials.</div>
                    ) : (
                      <ul className="list-disc pl-4 space-y-1">
                        {valuation_lab.bull_bear_digest.bear_points.map((pt, i) => (
                          <li key={i}>{pt}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {detail.peer_valuation && detail.peer_valuation.peers.length > 0 && (
            <div className="okz-card p-5 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="text-sm font-semibold">
                    Sector Peer Valuation Benchmark ({detail.peer_valuation.sector})
                  </h2>
                  <p className="text-xs okz-muted">
                    Relative multiples across {detail.peer_valuation.peer_count} sector peers — Median P/E:{" "}
                    <strong>{detail.peer_valuation.sector_median_pe ?? "—"}</strong> &bull; Median P/B:{" "}
                    <strong>{detail.peer_valuation.sector_median_pb ?? "—"}</strong> &bull; Median ROE:{" "}
                    <strong>
                      {detail.peer_valuation.sector_median_roe_pct != null
                        ? `${detail.peer_valuation.sector_median_roe_pct}%`
                        : "—"}
                    </strong>{" "}
                    &bull; Median Div Yield:{" "}
                    <strong>
                      {detail.peer_valuation.sector_median_div_yield_pct != null
                        ? `${detail.peer_valuation.sector_median_div_yield_pct}%`
                        : "—"}
                    </strong>
                  </p>
                </div>
                {detail.peer_valuation.pe_vs_sector_pct != null && (
                  <span
                    className={`px-2.5 py-1 rounded text-xs font-bold ${
                      detail.peer_valuation.pe_vs_sector_pct < 0
                        ? "bg-emerald-500/15 text-emerald-400"
                        : "bg-amber-500/15 text-amber-400"
                    }`}
                  >
                    P/E {detail.peer_valuation.pe_vs_sector_pct > 0 ? "+" : ""}
                    {detail.peer_valuation.pe_vs_sector_pct}% vs Sector
                  </span>
                )}
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="border-b border-[var(--border)] okz-muted text-left">
                    <tr>
                      <th className="py-2">Ticker</th>
                      <th className="py-2 text-right">Price</th>
                      <th className="py-2 text-right">Mkt Cap (Mn)</th>
                      <th className="py-2 text-right">P/E</th>
                      <th className="py-2 text-right">P/B</th>
                      <th className="py-2 text-right">ROE %</th>
                      <th className="py-2 text-right">Div Yield</th>
                      <th className="py-2 text-right">F-Score</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border)]">
                    {detail.peer_valuation.peers.map((p) => (
                      <tr key={p.symbol} className={p.is_current ? "bg-emerald-500/10 font-semibold" : "hover:bg-[var(--card-muted)]/40"}>
                        <td className="py-2">
                          <Link href={`/stocks/${encodeURIComponent(p.symbol)}`} className="font-mono font-bold hover:text-emerald-500">
                            {p.symbol}
                          </Link>
                          <span className="okz-muted ml-2 hidden sm:inline">{p.name.slice(0, 32)}</span>
                        </td>
                        <td className="py-2 text-right font-mono">{formatNumber(p.last_price, 2)}</td>
                        <td className="py-2 text-right font-mono">{formatNumber(p.market_cap_mn, 0)}</td>
                        <td className="py-2 text-right font-mono">{p.pe_ratio != null ? p.pe_ratio.toFixed(2) : "—"}</td>
                        <td className="py-2 text-right font-mono">{p.pb_ratio != null ? p.pb_ratio.toFixed(2) : "—"}</td>
                        <td className="py-2 text-right font-mono">{p.roe_pct != null ? `${p.roe_pct.toFixed(1)}%` : "—"}</td>
                        <td className="py-2 text-right font-mono text-emerald-500">{p.dividend_yield_pct.toFixed(2)}%</td>
                        <td className="py-2 text-right font-mono">{p.piotroski_f_score != null ? `${p.piotroski_f_score}/9` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* TAB 5: DIVIDENDS & FILINGS */}
      {activeTab === "dividends" && (
        <div className="space-y-6">
          {detail.dividend_history && detail.dividend_history.annual_payouts.length > 0 && (
        <div className="okz-card p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold">Dividend History &amp; Payout Consistency Radar</h2>
              <p className="text-xs okz-muted">
                Annual Cash Dividend per Share ({ccyPrefix.trim()}), Trailing-12M Yield &amp; Bonus Issue Track Record
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="px-2.5 py-1 rounded bg-emerald-500/15 text-emerald-400 font-bold">
                {detail.dividend_history.streak_years}Y Consecutive Streak
              </span>
              <span className="px-2.5 py-1 rounded bg-[var(--card-muted)] font-semibold">
                TTM DPS: {ccyPrefix}
                {formatNumber(detail.dividend_history.ttm_dps, 2)}
                {detail.dividend_history.ttm_yield_pct != null ? ` (${detail.dividend_history.ttm_yield_pct}%)` : ""}
              </span>
              {detail.dividend_history.payout_ratio_pct != null && (
                <span className="px-2.5 py-1 rounded bg-sky-500/15 text-sky-400 font-semibold">
                  Payout Ratio: {detail.dividend_history.payout_ratio_pct}%
                </span>
              )}
              {detail.dividend_history.upcoming_ex_date && (
                <span className="px-2.5 py-1 rounded bg-amber-500/15 text-amber-400 font-bold">
                  Upcoming Ex-Date: {detail.dividend_history.upcoming_ex_date}
                </span>
              )}
            </div>
          </div>
          <div className="h-44">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={detail.dividend_history.annual_payouts}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="year" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <RechartsTooltip
                  contentStyle={{
                    backgroundColor: "var(--card)",
                    borderColor: "var(--border)",
                    fontSize: "12px",
                    borderRadius: "8px",
                  }}
                />
                <Bar dataKey="dps" name={`Cash DPS (${ccyPrefix.trim()})`} fill="#10b981" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="okz-card p-5 space-y-3">
          <h2 className="text-sm font-semibold">Corporate Actions &amp; Payouts</h2>
          {detail.corporate_actions.length === 0 ? (
            <p className="text-xs okz-muted">No corporate actions recorded.</p>
          ) : (
            <div className="divide-y divide-[var(--border)] text-xs">
              {detail.corporate_actions.map((ca) => (
                <div key={ca.id} className="py-2.5 flex justify-between gap-2">
                  <div>
                    <div className="font-semibold">{ca.description}</div>
                    <div className="okz-muted">{ca.action_type}</div>
                  </div>
                  <div className="okz-muted whitespace-nowrap">Ex: {ca.ex_date}</div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="okz-card p-5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">PUCARS Announcements &amp; Official PDF Downloads</h2>
            <div className="flex items-center gap-2 text-xs">
              <a
                href={`/api/v1/market/stocks/${encodeURIComponent(asset.symbol)}/reports/export`}
                download={`${asset.symbol}_financials.csv`}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-[var(--border)] hover:border-emerald-500 font-semibold"
              >
                <Download className="w-3 h-3 text-emerald-500" /> Financials CSV
              </a>
              <Link
                href={`/stocks/downloads?symbol=${encodeURIComponent(asset.symbol)}`}
                className="text-sky-400 hover:underline font-semibold"
              >
                All Filings &rarr;
              </Link>
            </div>
          </div>
          {detail.announcements.length === 0 ? (
            <p className="text-xs okz-muted">No recent announcements.</p>
          ) : (
            <div className="divide-y divide-[var(--border)] text-xs">
              {detail.announcements.map((a) => {
                const pubDate = a.published_at ? new Date(a.published_at).toISOString().slice(0, 10) : "";
                const dlName = `${asset.symbol}_${pubDate}_${a.title.slice(0, 40).replace(/[^A-Za-z0-9]+/g, "_")}.pdf`;
                return (
                  <div key={a.id} className="py-3 flex flex-col sm:flex-row sm:items-start justify-between gap-2">
                    <div className="space-y-1 min-w-0">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--card-muted)] okz-muted font-semibold">
                          {a.category}
                        </span>
                        <span className="text-[10px] okz-muted font-mono">{pubDate}</span>
                      </div>
                      <div className="font-semibold text-xs leading-snug">{a.title}</div>
                      {a.summary && <p className="text-[11px] okz-muted line-clamp-2">{a.summary}</p>}
                    </div>
                    {a.source_url && (
                      <div className="flex items-center gap-1.5 shrink-0 pt-0.5">
                        <a
                          href={`/api/v1/market/downloads/proxy?url=${encodeURIComponent(a.source_url)}&filename=${encodeURIComponent(dlName)}`}
                          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-emerald-600/15 hover:bg-emerald-600/25 text-emerald-400 font-semibold whitespace-nowrap"
                        >
                          <Download className="w-3 h-3" /> PDF
                        </a>
                        <a
                          href={a.source_url}
                          target="_blank"
                          rel="noreferrer"
                          title="Open original PUCARS filing"
                          className="p-1.5 rounded-md border border-[var(--border)] hover:border-sky-500 text-sky-400"
                        >
                          <ExternalLink className="w-3 h-3" />
                        </a>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
        </div>
      )}

      {/* TAB 6: BROKERAGE EQUITY RESEARCH REPORTS */}
      {activeTab === "research" && (
        <div className="space-y-5">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="okz-card p-5 space-y-3">
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-sm font-semibold flex items-center gap-1.5">
                  <BookOpen className="w-4 h-4 text-emerald-500" />
                  {asset.symbol} Stock-Specific Brokerage Research
                </h2>
                <Link
                  href={`/stocks/research?symbol=${encodeURIComponent(asset.symbol)}`}
                  className="text-xs text-sky-400 hover:underline"
                >
                  Full Research Hub &rarr;
                </Link>
              </div>
              {!stockResearchPayload || stockResearchPayload.stock_reports.length === 0 ? (
                <p className="text-xs okz-muted py-4">
                  No brokerage research reports specifically tagged to {asset.symbol} yet. Open the{" "}
                  <Link href="/stocks/research" className="text-emerald-400 hover:underline">
                    Brokerage Research Hub
                  </Link>{" "}
                  to sync feeds or bookmark an external AHL / SCS / IMS / KASB / Chase PDF report for {asset.symbol}.
                </p>
              ) : (
                <div className="divide-y divide-[var(--border)]">
                  {stockResearchPayload.stock_reports.map((rep) => (
                    <div key={rep.id} className="py-3 space-y-1.5">
                      <div className="flex items-center gap-1.5 flex-wrap text-[10px]">
                        <span className="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-400 font-bold">
                          {rep.broker_name}
                        </span>
                        <span className="px-1.5 py-0.5 rounded bg-[var(--card-muted)] okz-muted">
                          {rep.report_type.replace("_", " ")}
                        </span>
                        {rep.stance && (
                          <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 font-bold">
                            {rep.stance}
                          </span>
                        )}
                        {rep.target_price != null && (
                          <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 font-mono font-bold">
                            TP Rs {rep.target_price.toLocaleString()}
                          </span>
                        )}
                        <span className="okz-muted ml-auto font-mono">
                          {new Date(rep.published_at).toLocaleDateString()}
                        </span>
                      </div>
                      <a
                        href={rep.pdf_url || rep.url}
                        target="_blank"
                        rel="noreferrer"
                        className="block text-xs font-semibold hover:text-emerald-400 leading-snug"
                      >
                        {rep.title}
                      </a>
                      {rep.summary && <p className="text-[11px] okz-muted line-clamp-2">{rep.summary}</p>}
                      <div className="flex items-center gap-2 pt-0.5 text-[11px]">
                        {rep.proxy_download_url && (
                          <a
                            href={rep.proxy_download_url}
                            className="inline-flex items-center gap-1 text-emerald-400 hover:underline font-semibold"
                          >
                            <Download className="w-3 h-3" /> PDF
                          </a>
                        )}
                        <a
                          href={rep.pdf_url || rep.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-sky-400 hover:underline font-semibold"
                        >
                          Open Report <ExternalLink className="w-3 h-3" />
                        </a>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="okz-card p-5 space-y-3">
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-sm font-semibold flex items-center gap-1.5">
                  <Layers className="w-4 h-4 text-sky-400" />
                  {asset.sector_or_category} Sector Research &amp; Strategy
                </h2>
                <Link href="/stocks/research" className="text-xs text-sky-400 hover:underline">
                  All Reports &rarr;
                </Link>
              </div>
              {!stockResearchPayload || stockResearchPayload.sector_reports.length === 0 ? (
                <p className="text-xs okz-muted py-4">
                  No sector-wide brokerage notes matched {asset.sector_or_category} in the current sync window.
                </p>
              ) : (
                <div className="divide-y divide-[var(--border)]">
                  {stockResearchPayload.sector_reports.map((rep) => (
                    <div key={rep.id} className="py-3 space-y-1.5">
                      <div className="flex items-center gap-1.5 flex-wrap text-[10px]">
                        <span className="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-400 font-bold">
                          {rep.broker_name}
                        </span>
                        <span className="px-1.5 py-0.5 rounded bg-[var(--card-muted)] okz-muted">
                          {rep.report_type.replace("_", " ")}
                        </span>
                        <span className="okz-muted ml-auto font-mono">
                          {new Date(rep.published_at).toLocaleDateString()}
                        </span>
                      </div>
                      <a
                        href={rep.pdf_url || rep.url}
                        target="_blank"
                        rel="noreferrer"
                        className="block text-xs font-semibold hover:text-emerald-400 leading-snug"
                      >
                        {rep.title}
                      </a>
                      {rep.summary && <p className="text-[11px] okz-muted line-clamp-2">{rep.summary}</p>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {stockResearchPayload && stockResearchPayload.portals.length > 0 && (
            <div className="okz-card p-4 flex flex-wrap items-center justify-between gap-3">
              <span className="text-xs font-semibold okz-muted">
                Check direct Pakistan institutional research desks for {asset.symbol}:
              </span>
              <div className="flex flex-wrap gap-2">
                {stockResearchPayload.portals.map((p) => (
                  <a
                    key={p.broker_key}
                    href={p.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-[var(--border)] hover:border-emerald-500 text-xs font-semibold"
                  >
                    {p.short_name} <ExternalLink className="w-3 h-3 text-sky-400" />
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* TAB 7: STOCK-SPECIFIC & INDUSTRY NEWS */}
      {activeTab === "news" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="okz-card p-5 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold flex items-center gap-1.5">
                <Newspaper className="w-4 h-4 text-emerald-500" />
                {asset.symbol} Company-Specific News
              </h2>
              <Link href={`/markets/news?symbol=${encodeURIComponent(asset.symbol)}`} className="text-xs text-sky-400 hover:underline">
                Full News Hub &rarr;
              </Link>
            </div>
            {!stockNewsPayload || stockNewsPayload.stock_news.length === 0 ? (
              <p className="text-xs okz-muted py-4">
                No company-specific headlines matched {asset.symbol} in the latest feed window. Check Industry &amp; Sector News on the right or refresh feeds in News Hub.
              </p>
            ) : (
              <div className="divide-y divide-[var(--border)]">
                {stockNewsPayload.stock_news.map((art) => (
                  <a
                    key={art.id}
                    href={art.url}
                    target="_blank"
                    rel="noreferrer"
                    className="block py-3 hover:opacity-85 space-y-1"
                  >
                    <div className="flex items-center gap-2 text-[10px]">
                      <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 font-semibold">{art.source_name}</span>
                      <span className="px-1.5 py-0.5 rounded bg-[var(--card-muted)] okz-muted">{art.category}</span>
                      <span className="okz-muted">{new Date(art.published_at).toLocaleDateString()}</span>
                    </div>
                    <div className="text-xs font-semibold leading-snug">{art.title}</div>
                    {art.summary && <p className="text-[11px] okz-muted line-clamp-2">{art.summary}</p>}
                  </a>
                ))}
              </div>
            )}
          </div>

          <div className="okz-card p-5 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold flex items-center gap-1.5">
                <Layers className="w-4 h-4 text-sky-400" />
                {asset.sector_or_category} Industry News
              </h2>
              <Link href="/markets/sectors" className="text-xs text-sky-400 hover:underline">
                Sector Explorer &rarr;
              </Link>
            </div>
            {!stockNewsPayload || stockNewsPayload.sector_news.length === 0 ? (
              <p className="text-xs okz-muted py-4">No recent sector-tagged news articles for {asset.sector_or_category}.</p>
            ) : (
              <div className="divide-y divide-[var(--border)]">
                {stockNewsPayload.sector_news.map((art) => (
                  <a
                    key={art.id}
                    href={art.url}
                    target="_blank"
                    rel="noreferrer"
                    className="block py-3 hover:opacity-85 space-y-1"
                  >
                    <div className="flex items-center gap-2 text-[10px]">
                      <span className="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-400 font-semibold">{art.source_name}</span>
                      <span className="px-1.5 py-0.5 rounded bg-[var(--card-muted)] okz-muted">{art.category}</span>
                      <span className="okz-muted">{new Date(art.published_at).toLocaleDateString()}</span>
                    </div>
                    <div className="text-xs font-semibold leading-snug">{art.title}</div>
                    {art.summary && <p className="text-[11px] okz-muted line-clamp-2">{art.summary}</p>}
                  </a>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
