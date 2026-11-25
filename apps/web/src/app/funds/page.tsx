"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Calculator,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileText,
  LineChart,
  Repeat,
  Search,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart as RechartsLineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { BarPoint, PriceChart } from "@/components/PriceChart";
import {
  AfterTaxSipComparatorPanel,
  AmcLeaderboardPanel,
  FmrHoldingsOverlapPanel,
} from "@/components/MutualFundsLabPanels";
import { TableSkeleton } from "@/components/Skeletons";
import { apiFetch, apiFetchCached, invalidateApiCache } from "@/lib/api";
import { formatNumber, formatPct, pctColorClass } from "@/lib/format";

interface MutualFundRow {
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
  front_end_load_pct: string;
  aum_mn: string;
  is_daily_dividend?: boolean;
  constant_par_nav?: string | null;
  annualized_yield_pct?: string | null;
  default_payout_mode?: string;
  return_1m_pct: string | null;
  return_6m_pct: string | null;
  return_1y_pct: string | null;
  return_3y_pct: string | null;
  benchmark_name: string;
  has_fmr?: boolean;
  fmr_month?: string | null;
  stability_rating?: string | null;
  wam_days?: string | null;
  asset_allocation_top?: Array<{ label: string; pct: number }>;
  top_holding_symbols?: string[];
}

interface FundAnalyticsPayload {
  symbol: string;
  name: string;
  amc_name: string;
  fund_category: string;
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
  nav_series: Array<{
    date: string;
    nav: number;
    wealth_index: number;
    benchmark_index?: number | null;
  }>;
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
}

interface FundScatterPoint {
  symbol: string;
  name: string;
  amc_name: string;
  fund_category: string;
  is_shariah_compliant: boolean;
  is_daily_dividend: boolean;
  nav: number;
  ter_pct: number;
  cagr_pct: number;
  volatility_pct: number;
  sharpe_ratio: number | null;
  bar_count: number;
}

const FUNDS_PAGE_SIZE = 50;

export default function MutualFundsPage() {
  const [allFunds, setAllFunds] = useState<MutualFundRow[]>([]);
  const [loadingFunds, setLoadingFunds] = useState(true);
  const [allScatterPoints, setAllScatterPoints] = useState<FundScatterPoint[]>([]);
  const [scatterRf, setScatterRf] = useState<number>(12.0);
  const [showScatter, setShowScatter] = useState<boolean>(true);
  const [activeTab, setActiveTab] = useState<"directory" | "lab" | "sip" | "overlap">("directory");

  const [q, setQ] = useState("");
  const [amc, setAmc] = useState("");
  const [category, setCategory] = useState("");
  const [maxTer, setMaxTer] = useState("");
  const [shariahOnly, setShariahOnly] = useState(false);
  const [dailyDividendOnly, setDailyDividendOnly] = useState(false);
  const [fmrOnly, setFmrOnly] = useState(false);
  const [sortBy, setSortBy] = useState("return_1y_pct");
  const [page, setPage] = useState(1);
  const [showAll, setShowAll] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);

  const [activeFund, setActiveFund] = useState<MutualFundRow | null>(null);
  const [fundAnalytics, setFundAnalytics] = useState<FundAnalyticsPayload | null>(null);
  const [fundReturnMode, setFundReturnMode] = useState<"GROSS_TR" | "NET_FILER" | "PRICE">("GROSS_TR");
  const [navBars, setNavBars] = useState<BarPoint[]>([]);
  const [loadingNav, setLoadingNav] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const [uploadingNav, setUploadingNav] = useState(false);
  const [fmrUploadMsg, setFmrUploadMsg] = useState<string | null>(null);
  const [uploadingFmr, setUploadingFmr] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const sp = new URLSearchParams(window.location.search);
      const initialQ = sp.get("q");
      if (initialQ) setQ(initialQ);
      if (sp.get("daily_dividend") === "true") setDailyDividendOnly(true);
      if (sp.get("fmr_only") === "true") setFmrOnly(true);
      const tab = sp.get("tab");
      if (tab === "lab" || tab === "sip" || tab === "overlap") setActiveTab(tab);
    }
  }, []);

  const loadDirectory = useCallback((forceRefresh = false) => {
    setLoadingFunds(true);
    Promise.all([
      apiFetchCached<MutualFundRow[]>("/api/v1/market/funds", 120_000, { forceRefresh, onUpdate: setAllFunds }),
      apiFetchCached<{ risk_free_rate_pct: number; points: FundScatterPoint[] }>(
        "/api/v1/market/funds/scatter",
        300_000,
        {
          forceRefresh,
          onUpdate: (res) => {
            setAllScatterPoints(res.points || []);
            if (res.risk_free_rate_pct) setScatterRf(res.risk_free_rate_pct);
          },
        },
      ),
    ])
      .then(([fundRows, scatterRes]) => {
        setAllFunds(fundRows);
        setAllScatterPoints(scatterRes.points || []);
        if (scatterRes.risk_free_rate_pct) setScatterRf(scatterRes.risk_free_rate_pct);
      })
      .catch(() => {})
      .finally(() => setLoadingFunds(false));
  }, []);

  useEffect(() => {
    loadDirectory(refreshTick > 0);
  }, [loadDirectory, refreshTick]);

  const amcOptions = useMemo(
    () => Array.from(new Set(allFunds.map((f) => f.amc_name).filter(Boolean))).sort(),
    [allFunds],
  );
  const categoryOptions = useMemo(
    () => Array.from(new Set(allFunds.map((f) => f.fund_category).filter(Boolean))).sort(),
    [allFunds],
  );

  // Instant 0ms client-side filtering & sorting across all 330+ funds
  const funds = useMemo(() => {
    const qLow = q.trim().toLowerCase();
    const maxTerNum = maxTer.trim() !== "" ? Number(maxTer) : null;

    const matched = allFunds.filter((f) => {
      if (
        qLow &&
        !f.symbol.toLowerCase().includes(qLow) &&
        !f.name.toLowerCase().includes(qLow) &&
        !f.amc_name.toLowerCase().includes(qLow) &&
        !f.fund_category.toLowerCase().includes(qLow)
      ) {
        return false;
      }
      if (amc && f.amc_name !== amc) return false;
      if (category && f.fund_category !== category) return false;
      if (shariahOnly && !f.is_shariah_compliant) return false;
      if (dailyDividendOnly && !f.is_daily_dividend) return false;
      if (fmrOnly && !f.has_fmr) return false;
      if (maxTerNum != null && Number.isFinite(maxTerNum) && Number(f.ter_pct || 0) > maxTerNum) return false;
      return true;
    });

    matched.sort((a, b) => {
      if (sortBy === "ter_pct") return Number(a.ter_pct || 99) - Number(b.ter_pct || 99);
      if (sortBy === "aum_mn") return Number(b.aum_mn || 0) - Number(a.aum_mn || 0);
      if (sortBy === "annualized_yield_pct") {
        return Number(b.annualized_yield_pct ?? -999) - Number(a.annualized_yield_pct ?? -999);
      }
      if (sortBy === "return_3y_pct") return Number(b.return_3y_pct ?? -999) - Number(a.return_3y_pct ?? -999);
      return Number(b.return_1y_pct ?? -999) - Number(a.return_1y_pct ?? -999);
    });
    return matched;
  }, [allFunds, q, amc, category, maxTer, shariahOnly, dailyDividendOnly, fmrOnly, sortBy]);

  const scatterPoints = useMemo(() => {
    const catLow = category.trim().toLowerCase();
    return allScatterPoints.filter((p) => {
      if (shariahOnly && !p.is_shariah_compliant) return false;
      if (catLow && !(p.fund_category || "").toLowerCase().includes(catLow)) return false;
      return true;
    });
  }, [allScatterPoints, shariahOnly, category]);

  useEffect(() => {
    setPage(1);
  }, [q, amc, category, maxTer, shariahOnly, dailyDividendOnly, fmrOnly, sortBy]);

  const totalPages = Math.max(1, Math.ceil(funds.length / FUNDS_PAGE_SIZE));
  const displayedFunds = showAll ? funds : funds.slice((page - 1) * FUNDS_PAGE_SIZE, page * FUNDS_PAGE_SIZE);

  const resetFundFilters = () => {
    setQ("");
    setAmc("");
    setCategory("");
    setMaxTer("");
    setShariahOnly(false);
    setDailyDividendOnly(false);
    setFmrOnly(false);
    setSortBy("return_1y_pct");
  };

  useEffect(() => {
    if (!activeFund) {
      setNavBars([]);
      setFundAnalytics(null);
      setUploadMsg(null);
      return;
    }
    setLoadingNav(true);
    Promise.all([
      apiFetch<BarPoint[]>(
        `/api/v1/market/assets/${encodeURIComponent(activeFund.symbol)}/bars?timeframe=1D&limit=180&return_mode=${fundReturnMode}`,
      ).catch(() => [] as BarPoint[]),
      apiFetch<FundAnalyticsPayload>(
        `/api/v1/market/funds/${encodeURIComponent(activeFund.symbol)}/analytics?return_mode=${fundReturnMode}`,
      ).catch(() => null),
    ])
      .then(([barsRes, analyticsRes]) => {
        setNavBars(barsRes);
        setFundAnalytics(analyticsRes);
      })
      .finally(() => setLoadingNav(false));
  }, [activeFund, fundReturnMode, refreshTick]);

  const handleNavFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!activeFund || !e.target.files?.[0]) return;
    const file = e.target.files[0];
    setUploadingNav(true);
    setUploadMsg(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await apiFetch<{ rows_parsed: number; bars_inserted: number; date_from: string; date_to: string }>(
        `/api/v1/market/funds/${encodeURIComponent(activeFund.symbol)}/import-history`,
        { method: "POST", body: fd }
      );
      setUploadMsg(`Imported ${res.bars_inserted} new NAV bars (${res.rows_parsed} parsed: ${res.date_from} → ${res.date_to}).`);
      invalidateApiCache("/api/v1/market/funds");
      setRefreshTick((t) => t + 1);
    } catch (err) {
      setUploadMsg(err instanceof Error ? err.message : "Failed to import NAV history file.");
    } finally {
      setUploadingNav(false);
      e.target.value = "";
    }
  };

  const handleFmrBookletUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files?.[0]) return;
    const file = e.target.files[0];
    setUploadingFmr(true);
    setFmrUploadMsg(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await apiFetch<{ matched_funds: number; report_label: string }>(
        `/api/v1/market/funds/fmr/upload${amc ? `?amc_hint=${encodeURIComponent(amc)}` : ""}`,
        { method: "POST", body: fd }
      );
      setFmrUploadMsg(`Parsed SECP FMR (${res.report_label}) — updated composition & holdings for ${res.matched_funds} fund(s).`);
      invalidateApiCache("/api/v1/market/funds");
      setRefreshTick((t) => t + 1);
    } catch (err) {
      setFmrUploadMsg(err instanceof Error ? err.message : "Failed to parse uploaded FMR PDF booklet.");
    } finally {
      setUploadingFmr(false);
      e.target.value = "";
    }
  };

  const toggleCompare = (sym: string) => {
    setSelected((prev) =>
      prev.includes(sym) ? prev.filter((s) => s !== sym) : prev.length < 4 ? [...prev, sym] : prev
    );
  };

  const comparedFunds = funds.filter((f) => selected.includes(f.symbol));

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">MUFAP, SECP FMR Composition &amp; Multi-AMC Mutual Funds Lab</h1>
          <p className="text-sm okz-muted">
            Direct feeds from Al Meezan, UBL/Al-Ameen, MCB/Alhamra, NBP, HBL, ABL, Atlas, JSIL &amp; Lakson with
            SECP Fund Manager Report composition, Top-10 holdings, credit quality, Sharpe/Alpha &amp; 5Y fee drag.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          <label className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 text-xs font-semibold cursor-pointer">
            <FileText className="w-3.5 h-3.5" />
            <span>{uploadingFmr ? "Parsing FMR PDF..." : "Upload AMC FMR PDF"}</span>
            <input
              type="file"
              accept=".pdf"
              onChange={handleFmrBookletUpload}
              disabled={uploadingFmr}
              className="hidden"
            />
          </label>
          <Link
            href="/calculators"
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold"
          >
            <Calculator className="w-3.5 h-3.5" />
            Daily Dividend DRIP Simulator
          </Link>
        </div>
      </div>

      {fmrUploadMsg && (
        <div className="px-4 py-2.5 rounded-lg text-xs bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
          {fmrUploadMsg}
        </div>
      )}

      {/* 4-Tab Mutual Funds Institutional Lab Switcher */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-[var(--border)] pb-2 text-xs">
        {(
          [
            ["directory", `Fund Directory & Frontier (${allFunds.length})`],
            ["lab", "AMC Leaderboard & Champions"],
            ["sip", "After-Tax & Fee SIP Lab"],
            ["overlap", "SECP FMR Holdings Overlap"],
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

      {activeTab === "lab" && (
        <AmcLeaderboardPanel
          onPickAmc={(amcName) => {
            setAmc(amcName);
            setActiveTab("directory");
          }}
        />
      )}

      {activeTab === "sip" && (
        <AfterTaxSipComparatorPanel
          allFunds={allFunds}
          initialSymbols={selected.length >= 2 ? selected : undefined}
        />
      )}

      {activeTab === "overlap" && <FmrHoldingsOverlapPanel />}

      {activeTab === "directory" && (
      <>
      {/* 1-Click Smart Fund Presets */}
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="okz-muted mr-1 font-medium">Quick Presets:</span>
        <button
          type="button"
          onClick={() => {
            resetFundFilters();
            setShariahOnly(true);
            setDailyDividendOnly(true);
            setSortBy("annualized_yield_pct");
          }}
          className="px-2.5 py-1 rounded-md border border-[var(--border)] bg-[var(--card)] hover:border-emerald-500"
        >
          Islamic Daily Dividend
        </button>
        <button
          type="button"
          onClick={() => {
            resetFundFilters();
            setFmrOnly(true);
          }}
          className="px-2.5 py-1 rounded-md border border-[var(--border)] bg-[var(--card)] hover:border-emerald-500"
        >
          SECP FMR Verified
        </button>
        <button
          type="button"
          onClick={() => {
            resetFundFilters();
            setMaxTer("1.5");
            setSortBy("ter_pct");
          }}
          className="px-2.5 py-1 rounded-md border border-[var(--border)] bg-[var(--card)] hover:border-emerald-500"
        >
          Low-Fee (&le;1.5% TER)
        </button>
        <button
          type="button"
          onClick={() => {
            resetFundFilters();
            setShariahOnly(true);
            setSortBy("return_1y_pct");
          }}
          className="px-2.5 py-1 rounded-md border border-[var(--border)] bg-[var(--card)] hover:border-emerald-500"
        >
          Top Shariah 1Y
        </button>
        <button
          type="button"
          onClick={resetFundFilters}
          className="px-2.5 py-1 rounded-md text-rose-400 border border-[var(--border)] hover:border-rose-400"
        >
          Reset
        </button>
      </div>

      <div className="okz-card p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-3 items-end">
        <div className="lg:col-span-2">
          <label className="block text-xs okz-muted mb-1">Instant Search (Fund, Symbol, or AMC)</label>
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-2.5 okz-muted pointer-events-none" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="e.g. MIF, MCF, Al Meezan, Cash Fund..."
              className="w-full pl-9 pr-8 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            />
            {q && (
              <button
                type="button"
                onClick={() => setQ("")}
                aria-label="Clear search"
                className="absolute right-2.5 top-2.5 okz-muted hover:text-[var(--text)]"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        <div>
          <label className="block text-xs okz-muted mb-1">AMC</label>
          <select
            value={amc}
            onChange={(e) => setAmc(e.target.value)}
            className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
          >
            <option value="">All AMCs</option>
            {amcOptions.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs okz-muted mb-1">Category</label>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
          >
            <option value="">All Categories</option>
            {categoryOptions.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="block text-xs okz-muted mb-1">Max TER %</label>
            <input
              type="number"
              step="0.1"
              min="0"
              value={maxTer}
              onChange={(e) => setMaxTer(e.target.value)}
              placeholder="2.5"
              className="w-full px-2.5 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            />
          </div>
          <div>
            <label className="block text-xs okz-muted mb-1">Sort By</label>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
              className="w-full px-2 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            >
              <option value="return_1y_pct">1Y Return</option>
              <option value="annualized_yield_pct">Annualized Yield %</option>
              <option value="return_3y_pct">3Y Return</option>
              <option value="aum_mn">AUM Size</option>
              <option value="ter_pct">Lowest TER</option>
            </select>
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <label className="inline-flex items-center gap-2 text-xs cursor-pointer">
            <input type="checkbox" checked={shariahOnly} onChange={(e) => setShariahOnly(e.target.checked)} />
            <span>Islamic / Shariah Only</span>
          </label>
          <label className="inline-flex items-center gap-2 text-xs cursor-pointer text-amber-400 font-medium">
            <input
              type="checkbox"
              checked={dailyDividendOnly}
              onChange={(e) => setDailyDividendOnly(e.target.checked)}
            />
            <span>Daily Dividend Funds Only</span>
          </label>
          <span className="text-[11px] okz-muted">Check up to 4 rows to compare</span>
        </div>
      </div>

      {comparedFunds.length >= 2 && (
        <div className="okz-card p-5 space-y-3 border-emerald-500/40">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-sm font-bold text-emerald-500">Side-by-Side Fund Comparison</h2>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setActiveTab("sip")}
                className="text-xs font-semibold text-sky-400 hover:underline"
              >
                Run After-Tax &amp; Fee SIP Projection &rarr;
              </button>
              <button onClick={() => setSelected([])} className="text-xs okz-muted hover:underline">
                Clear
              </button>
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {comparedFunds.map((f) => (
              <div key={f.symbol} className="p-3 rounded-lg bg-[var(--card-muted)]/60 space-y-1.5 text-xs">
                <div className="font-bold text-sm">{f.symbol}</div>
                <div className="okz-muted truncate">{f.name}</div>
                <div className="flex justify-between pt-1">
                  <span>NAV</span>
                  <span className="font-semibold">PKR {f.last_price}</span>
                </div>
                <div className="flex justify-between">
                  <span>TER / FEL</span>
                  <span>
                    {f.ter_pct}% / {f.front_end_load_pct}%
                  </span>
                </div>
                <div className="flex justify-between">
                  <span>1Y / 3Y Return</span>
                  <span className="font-semibold text-emerald-500">
                    {f.return_1y_pct ?? "—"}% / {f.return_3y_pct ?? "—"}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {scatterPoints.length > 0 && (
        <div className="okz-card p-5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold">
                Mutual Fund Risk vs Return Efficiency Frontier ({scatterPoints.length} Funds with Historical NAVs)
              </h2>
              <p className="text-xs okz-muted">
                Annualized Volatility % (X) vs Realized Annualized Return % (Y) — Live SBP Risk-Free Benchmark:{" "}
                <strong>{scatterRf.toFixed(2)}%</strong>. Click any fund dot to inspect Sharpe, Alpha &amp; TER drag.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setShowScatter((s) => !s)}
              className="px-2.5 py-1 rounded border border-[var(--border)] text-xs font-semibold hover:bg-[var(--card-muted)]"
            >
              {showScatter ? "Hide Scatter" : "Show Scatter"}
            </button>
          </div>
          {showScatter && (
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 10, right: 16, bottom: 10, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis
                    type="number"
                    dataKey="volatility_pct"
                    name="Volatility"
                    unit="%"
                    tick={{ fontSize: 11 }}
                  />
                  <YAxis
                    type="number"
                    dataKey="cagr_pct"
                    name="Return"
                    unit="%"
                    tick={{ fontSize: 11 }}
                  />
                  <ZAxis type="number" dataKey="bar_count" range={[45, 130]} name="NAV Bars" />
                  <RechartsTooltip
                    cursor={{ strokeDasharray: "3 3" }}
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null;
                      const pt = payload[0].payload as FundScatterPoint;
                      return (
                        <div className="p-2.5 rounded-lg border border-[var(--border)] bg-[var(--card)] text-xs shadow-lg space-y-0.5">
                          <div className="font-bold">
                            {pt.symbol} — {pt.name}
                          </div>
                          <div className="okz-muted">
                            {pt.amc_name} &bull; {pt.fund_category}
                          </div>
                          <div>
                            Return: <strong className="text-emerald-500">{pt.cagr_pct.toFixed(2)}%</strong> &bull; Vol:{" "}
                            <strong>{pt.volatility_pct.toFixed(2)}%</strong>
                          </div>
                          <div>
                            Sharpe: <strong>{pt.sharpe_ratio ?? "—"}</strong> &bull; TER: <strong>{pt.ter_pct}%</strong> &bull;{" "}
                            {pt.bar_count} bars
                          </div>
                        </div>
                      );
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: "11px" }} />
                  <Scatter
                    name="Shariah Compliant"
                    data={scatterPoints.filter((p) => p.is_shariah_compliant)}
                    fill="#10b981"
                    onClick={(node) => {
                      const sym = (node as unknown as { symbol?: string })?.symbol;
                      const found = funds.find((f) => f.symbol === sym);
                      if (found) setActiveFund(found);
                    }}
                  />
                  <Scatter
                    name="Conventional"
                    data={scatterPoints.filter((p) => !p.is_shariah_compliant)}
                    fill="#38bdf8"
                    onClick={(node) => {
                      const sym = (node as unknown as { symbol?: string })?.symbol;
                      const found = funds.find((f) => f.symbol === sym);
                      if (found) setActiveFund(found);
                    }}
                  />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      )}

      {/* Pagination & Result Count */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs okz-muted">
        <span>
          Showing <strong className="text-[var(--text)]">{displayedFunds.length}</strong> of{" "}
          <strong className="text-[var(--text)]">{funds.length}</strong> matching funds ({allFunds.length} total)
        </span>
        {funds.length > FUNDS_PAGE_SIZE && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowAll((v) => !v)}
              className="px-2.5 py-1 rounded border border-[var(--border)] hover:border-emerald-500 font-medium text-[var(--text)]"
            >
              {showAll ? "Paginate (50/page)" : `Show All (${funds.length})`}
            </button>
            {!showAll && (
              <div className="inline-flex items-center gap-1">
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  className="p-1 rounded border border-[var(--border)] disabled:opacity-40 hover:bg-[var(--card-muted)]"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                </button>
                <span className="px-2 font-mono">
                  {page} / {totalPages}
                </span>
                <button
                  type="button"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  className="p-1 rounded border border-[var(--border)] disabled:opacity-40 hover:bg-[var(--card-muted)]"
                >
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {loadingFunds && allFunds.length === 0 ? (
        <TableSkeleton rows={10} cols={8} />
      ) : (
        <div className="okz-card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-[var(--border)] text-xs okz-muted text-left">
              <tr>
                <th className="py-3 px-4">Compare</th>
                <th className="py-3 px-4">Fund &amp; AMC</th>
                <th className="py-3 px-4">Category &amp; Mode</th>
                <th className="py-3 px-4 text-right">NAV (PKR)</th>
                <th className="py-3 px-4 text-right">TER %</th>
                <th className="py-3 px-4 text-right">FEL %</th>
                <th className="py-3 px-4 text-right">1M</th>
                <th className="py-3 px-4 text-right">1Y / Yield</th>
                <th className="py-3 px-4 text-right">3Y</th>
                <th className="py-3 px-4 text-right">AUM (Mn)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)]">
              {displayedFunds.map((f) => (
              <tr key={f.symbol} className="hover:bg-[var(--card-muted)]/50">
                <td className="py-3 px-4">
                  <input
                    type="checkbox"
                    checked={selected.includes(f.symbol)}
                    onChange={() => toggleCompare(f.symbol)}
                  />
                </td>
                <td className="py-3 px-4">
                  <div className="space-y-1">
                    <div className="font-semibold flex items-center gap-1.5 flex-wrap">
                      <Link
                        href={`/funds/${encodeURIComponent(f.symbol)}`}
                        className="hover:text-emerald-500"
                      >
                        {f.name}
                      </Link>
                      {f.is_shariah_compliant && <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />}
                      {f.is_daily_dividend && (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 text-[10px] font-bold">
                          <Repeat className="w-3 h-3" /> Daily Div
                        </span>
                      )}
                      {f.has_fmr && (
                        <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 text-[10px] font-bold">
                          FMR {f.fmr_month || "Live"}
                        </span>
                      )}
                      {f.stability_rating && (
                        <span className="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-400 text-[10px] font-semibold">
                          {f.stability_rating}
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={() => setActiveFund(f)}
                        title="Quick Analytics Modal"
                        className="okz-muted hover:text-emerald-500"
                      >
                        <LineChart className="w-3.5 h-3.5" />
                      </button>
                    </div>
                    <div className="text-xs okz-muted flex items-center gap-2 flex-wrap">
                      <span>
                        {f.symbol} &bull; {f.amc_name}
                        {f.constant_par_nav ? ` · Par NAV Rs ${Number(f.constant_par_nav).toFixed(2)}` : ""}
                      </span>
                      <Link
                        href={`/funds/${encodeURIComponent(f.symbol)}`}
                        className="text-[11px] text-emerald-500 hover:underline inline-flex items-center gap-0.5"
                      >
                        Fact Sheet <ExternalLink className="w-2.5 h-2.5" />
                      </Link>
                    </div>
                    {f.asset_allocation_top && f.asset_allocation_top.length > 0 && (
                      <div className="flex flex-wrap gap-1 pt-0.5">
                        {f.asset_allocation_top.map((al) => (
                          <span
                            key={al.label}
                            className="px-1.5 py-0.5 rounded bg-[var(--card-muted)] text-[10px] font-mono okz-muted"
                          >
                            {al.label}: {al.pct.toFixed(0)}%
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </td>
                <td className="py-3 px-4 text-xs okz-muted">
                  <div>{f.fund_category}</div>
                  {f.is_daily_dividend && f.annualized_yield_pct && (
                    <div className="text-[11px] text-amber-400 font-medium mt-0.5">
                      Annualized Yield: {f.annualized_yield_pct}% p.a. ({f.default_payout_mode || "REINVEST"})
                    </div>
                  )}
                </td>
                <td className="py-3 px-4 text-right font-semibold">{formatNumber(f.last_price, 2)}</td>
                <td className="py-3 px-4 text-right">{f.ter_pct}%</td>
                <td className="py-3 px-4 text-right">{f.front_end_load_pct}%</td>
                <td className={`py-3 px-4 text-right ${pctColorClass(f.return_1m_pct)}`}>
                  {formatPct(f.return_1m_pct)}
                </td>
                <td className={`py-3 px-4 text-right font-semibold ${pctColorClass(f.return_1y_pct)}`}>
                  {formatPct(f.return_1y_pct)}
                </td>
                <td className={`py-3 px-4 text-right ${pctColorClass(f.return_3y_pct)}`}>
                  {formatPct(f.return_3y_pct)}
                </td>
                <td className="py-3 px-4 text-right">{formatNumber(f.aum_mn, 0)}</td>
              </tr>
            ))}
            </tbody>
          </table>
        </div>
      )}
      </>
      )}

      {activeFund && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 overflow-y-auto">
          <div className="okz-card w-full max-w-4xl max-h-[92vh] overflow-y-auto p-6 shadow-2xl space-y-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2 flex-wrap">
                  <h2 className="text-lg font-bold">{activeFund.name}</h2>
                  {activeFund.is_shariah_compliant && <ShieldCheck className="w-4 h-4 text-emerald-500" />}
                  {activeFund.is_daily_dividend && (
                    <span className="px-2 py-0.5 rounded bg-amber-500/15 text-amber-400 text-[10px] font-bold">
                      Constant-NAV Daily Dividend
                    </span>
                  )}
                </div>
                <p className="text-xs okz-muted">
                  {activeFund.symbol} &bull; {activeFund.amc_name} &bull; {activeFund.fund_category} &bull; Benchmark:{" "}
                  {fundAnalytics?.benchmark_symbol || activeFund.benchmark_name}
                </p>
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <label className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded border border-[var(--border)] bg-[var(--bg-elevated)] hover:bg-[var(--card-muted)] text-xs font-semibold cursor-pointer">
                  <Upload className="w-3.5 h-3.5 text-emerald-500" />
                  <span>{uploadingNav ? "Uploading..." : "Upload NAV CSV/XLSX"}</span>
                  <input
                    type="file"
                    accept=".csv,.xlsx,.xlsm"
                    onChange={handleNavFileUpload}
                    disabled={uploadingNav}
                    className="hidden"
                  />
                </label>
                <select
                  value={fundReturnMode}
                  onChange={(e) => setFundReturnMode(e.target.value as typeof fundReturnMode)}
                  className="px-2 py-1 rounded border border-[var(--border)] bg-[var(--bg)] text-xs font-semibold"
                >
                  <option value="GROSS_TR">Gross Total Return</option>
                  <option value="NET_FILER">Net After Filer Tax (15%)</option>
                  <option value="PRICE">Raw Quoted NAV</option>
                </select>
                <button
                  type="button"
                  onClick={() => setActiveFund(null)}
                  className="p-1 rounded-md hover:bg-[var(--bg-elevated)]"
                  aria-label="Close modal"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            {uploadMsg && (
              <div className="px-3 py-2 rounded-lg text-xs bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
                {uploadMsg}
              </div>
            )}

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
              <div className="p-2.5 rounded bg-[var(--bg-elevated)]">
                <div className="okz-muted">Current NAV</div>
                <div className="font-bold text-sm">PKR {formatNumber(activeFund.last_price, 4)}</div>
              </div>
              <div className="p-2.5 rounded bg-[var(--bg-elevated)]">
                <div className="okz-muted">TER / FEL (5Y Drag)</div>
                <div className="font-bold text-sm">
                  {activeFund.ter_pct}% / {activeFund.front_end_load_pct}%
                  {fundAnalytics?.fee_drag_5y_pct != null && (
                    <span className="text-rose-400 text-xs ml-1">(-{fundAnalytics.fee_drag_5y_pct}%)</span>
                  )}
                </div>
              </div>
              <div className="p-2.5 rounded bg-[var(--bg-elevated)]">
                <div className="okz-muted">1M / 6M / 1Y</div>
                <div className="font-bold text-sm text-emerald-500">
                  {formatPct(activeFund.return_1m_pct)} / {formatPct(activeFund.return_6m_pct)} /{" "}
                  {formatPct(activeFund.return_1y_pct)}
                </div>
              </div>
              <div className="p-2.5 rounded bg-[var(--bg-elevated)]">
                <div className="okz-muted">AUM / Risk</div>
                <div className="font-bold text-sm">
                  {formatNumber(activeFund.aum_mn, 0)} Mn ({activeFund.risk_profile})
                </div>
              </div>
            </div>

            {fundAnalytics && (
              <div className="grid grid-cols-2 sm:grid-cols-6 gap-2.5 text-xs">
                <div className="p-2.5 rounded border border-[var(--border)]">
                  <div className="okz-muted">Sharpe Ratio</div>
                  <div className="font-bold text-sm mt-0.5">
                    {fundAnalytics.sharpe_ratio != null ? fundAnalytics.sharpe_ratio.toFixed(2) : "—"}
                  </div>
                </div>
                <div className="p-2.5 rounded border border-[var(--border)]">
                  <div className="okz-muted">Sortino Ratio</div>
                  <div className="font-bold text-sm mt-0.5">
                    {fundAnalytics.sortino_ratio != null ? fundAnalytics.sortino_ratio.toFixed(2) : "—"}
                  </div>
                </div>
                <div className="p-2.5 rounded border border-[var(--border)]">
                  <div className="okz-muted">Alpha (vs Bench)</div>
                  <div className={`font-bold text-sm mt-0.5 ${pctColorClass(fundAnalytics.alpha_annualized_pct)}`}>
                    {fundAnalytics.alpha_annualized_pct != null
                      ? formatPct(fundAnalytics.alpha_annualized_pct)
                      : "—"}
                  </div>
                </div>
                <div className="p-2.5 rounded border border-[var(--border)]">
                  <div className="okz-muted">Beta / Info Ratio</div>
                  <div className="font-bold text-sm mt-0.5">
                    {fundAnalytics.beta != null ? fundAnalytics.beta.toFixed(2) : "—"} /{" "}
                    {fundAnalytics.information_ratio != null ? fundAnalytics.information_ratio.toFixed(2) : "—"}
                  </div>
                </div>
                <div className="p-2.5 rounded border border-[var(--border)]">
                  <div className="okz-muted">Max Drawdown</div>
                  <div className="font-bold text-sm text-rose-400 mt-0.5">
                    {fundAnalytics.max_drawdown_pct != null ? `${fundAnalytics.max_drawdown_pct.toFixed(2)}%` : "—"}
                  </div>
                </div>
                <div className="p-2.5 rounded border border-[var(--border)]">
                  <div className="okz-muted">CAGR / Vol ({fundAnalytics.bar_count}d)</div>
                  <div className="font-bold text-sm mt-0.5">
                    {fundAnalytics.cagr_pct != null ? `${fundAnalytics.cagr_pct.toFixed(1)}%` : "—"} /{" "}
                    {fundAnalytics.annualized_volatility_pct != null
                      ? `${fundAnalytics.annualized_volatility_pct.toFixed(1)}%`
                      : "—"}
                  </div>
                </div>
              </div>
            )}

            {loadingNav ? (
              <div className="h-56 flex items-center justify-center text-sm okz-muted">Loading NAV &amp; Risk Analytics...</div>
            ) : fundAnalytics && fundAnalytics.nav_series.length >= 3 && fundAnalytics.nav_series.some((p) => p.benchmark_index != null) ? (
              <div className="space-y-1.5">
                <div className="text-xs font-semibold okz-muted">
                  Normalized Total-Return Growth (Base 100) vs {fundAnalytics.benchmark_symbol} (Rf {fundAnalytics.risk_free_rate_pct.toFixed(1)}%)
                </div>
                <div className="h-56 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <RechartsLineChart data={fundAnalytics.nav_series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#94a3b8" }} minTickGap={20} />
                      <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "#94a3b8" }} width={42} />
                      <RechartsTooltip
                        contentStyle={{
                          backgroundColor: "var(--card)",
                          borderColor: "var(--border)",
                          fontSize: "11px",
                          borderRadius: "8px",
                        }}
                      />
                      <Legend wrapperStyle={{ fontSize: "11px" }} />
                      <Line type="monotone" dataKey="wealth_index" name={`${activeFund.symbol} Wealth`} stroke="#10b981" strokeWidth={2.5} dot={false} />
                      <Line type="monotone" dataKey="benchmark_index" name={fundAnalytics.benchmark_symbol} stroke="#38bdf8" strokeWidth={2} dot={false} />
                    </RechartsLineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            ) : (
              <PriceChart bars={navBars} mode="area" height={230} />
            )}

            {fundAnalytics && fundAnalytics.amc_peers.length > 1 && (
              <div className="space-y-2 pt-2 border-t border-[var(--border)]">
                <div className="text-xs font-bold uppercase tracking-wider okz-muted">
                  Category Peer Comparison ({activeFund.fund_category})
                </div>
                <div className="overflow-x-auto border border-[var(--border)] rounded-lg">
                  <table className="w-full text-xs">
                    <thead className="bg-[var(--bg-elevated)] okz-muted text-left">
                      <tr>
                        <th className="py-2 px-3">Fund</th>
                        <th className="py-2 px-3">AMC</th>
                        <th className="py-2 px-3 text-right">NAV</th>
                        <th className="py-2 px-3 text-right">TER / FEL</th>
                        <th className="py-2 px-3 text-right">1Y / Ann. Yield</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--border)]">
                      {fundAnalytics.amc_peers.slice(0, 6).map((p) => (
                        <tr
                          key={p.symbol}
                          className={p.symbol === activeFund.symbol ? "bg-emerald-500/10 font-semibold" : ""}
                        >
                          <td className="py-1.5 px-3">
                            <span className="font-mono font-bold mr-1.5">{p.symbol}</span>
                            <span className="okz-muted">{p.name}</span>
                          </td>
                          <td className="py-1.5 px-3">{p.amc_name}</td>
                          <td className="py-1.5 px-3 text-right font-mono">{formatNumber(p.nav, 4)}</td>
                          <td className="py-1.5 px-3 text-right font-mono">
                            {p.ter_pct}% / {p.front_end_load_pct}%
                          </td>
                          <td className="py-1.5 px-3 text-right font-semibold text-emerald-500">
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
            )}
          </div>
        </div>
      )}
    </div>
  );
}
