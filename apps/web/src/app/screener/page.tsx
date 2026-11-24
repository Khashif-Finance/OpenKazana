"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Download, Filter, Globe2, Plus, Search, ShieldCheck, SlidersHorizontal, TrendingUp, X } from "lucide-react";
import { TableSkeleton } from "@/components/Skeletons";
import { apiFetch, invalidateApiCache } from "@/lib/api";
import { formatNumber, formatPct, pctColorClass } from "@/lib/format";

interface IntlSearchHit {
  symbol: string;
  name: string;
  exchange: string;
  quote_type: string;
  sector: string;
  is_shariah_hint: boolean;
}

interface ScreenerRow {
  symbol: string;
  name: string;
  asset_class: string;
  currency: string;
  sector_or_category: string;
  is_shariah_compliant: boolean;
  last_price: string;
  change_pct: string;
  volume: string;
  market_cap_mn: string;
  pe_ratio: string | null;
  pb_ratio: string | null;
  roe_pct?: number | null;
  sector_median_pe?: number | null;
  pe_vs_sector_pct?: number | null;
  is_kse100?: boolean;
  is_kmi30?: boolean;
  div_streak_years?: number;
  deep_source?: string | null;
  dividend_yield_pct: string;
  quality_score: number | null;
  piotroski_f_score: number | null;
  dcf_upside_pct: number | null;
  rsi_14: number | null;
  adx_14: number | null;
  macd_hist: number | null;
  supertrend_signal: string | null;
  golden_cross: boolean;
  above_sma_50: boolean;
  above_sma_200: boolean;
  return_1m_pct: number | null;
  return_3m_pct: number | null;
  return_1y_tr_pct: number | null;
  overall_signal: string;
  bar_count: number;
}

export default function CrossMarketScreenerPage() {
  const [rows, setRows] = useState<ScreenerRow[]>([]);
  const [loading, setLoading] = useState(true);

  const [market, setMarket] = useState<"ALL" | "PSX" | "INTL">("ALL");
  const [indexOnly, setIndexOnly] = useState<"ALL" | "KSE100" | "KMI30">("ALL");
  const [q, setQ] = useState("");
  const [signal, setSignal] = useState<string>("ALL");
  const [rsiMin, setRsiMin] = useState<string>("");
  const [rsiMax, setRsiMax] = useState<string>("");
  const [minPiotroski, setMinPiotroski] = useState<string>("");
  const [minQuality, setMinQuality] = useState<string>("");
  const [minRoe, setMinRoe] = useState<string>("");
  const [minDivStreak, setMinDivStreak] = useState<string>("");
  const [minDivYield, setMinDivYield] = useState<string>("");
  const [maxPe, setMaxPe] = useState<string>("");
  const [shariahOnly, setShariahOnly] = useState(false);
  const [belowSectorPe, setBelowSectorPe] = useState(false);
  const [goldenCrossOnly, setGoldenCrossOnly] = useState(false);
  const [supertrendBullOnly, setSupertrendBullOnly] = useState(false);
  const [sortBy, setSortBy] = useState("quality_score");

  const [intlQuery, setIntlQuery] = useState("");
  const [intlHits, setIntlHits] = useState<IntlSearchHit[]>([]);
  const [intlSearching, setIntlSearching] = useState(false);
  const [importingSym, setImportingSym] = useState<string | null>(null);
  const [importNotice, setImportNotice] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  const runScan = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    try {
      const p = new URLSearchParams();
      p.set("market", market);
      if (indexOnly !== "ALL") p.set("index_only", indexOnly);
      if (q.trim()) p.set("q", q.trim());
      if (signal !== "ALL") p.set("signal", signal);
      if (rsiMin !== "") p.set("rsi_min", rsiMin);
      if (rsiMax !== "") p.set("rsi_max", rsiMax);
      if (minPiotroski !== "") p.set("min_piotroski", minPiotroski);
      if (minQuality !== "") p.set("min_quality_score", minQuality);
      if (minRoe !== "") p.set("min_roe_pct", minRoe);
      if (minDivStreak !== "") p.set("min_div_streak", minDivStreak);
      if (minDivYield !== "") p.set("min_dividend_yield", minDivYield);
      if (maxPe !== "") p.set("max_pe", maxPe);
      if (shariahOnly) p.set("shariah_only", "true");
      if (belowSectorPe) p.set("below_sector_pe", "true");
      if (goldenCrossOnly) p.set("golden_cross_only", "true");
      if (supertrendBullOnly) p.set("supertrend_bull_only", "true");
      p.set("sort_by", sortBy);
      p.set("limit", "150");
      const data = await apiFetch<ScreenerRow[]>(`/api/v1/market/screener/scan?${p.toString()}`, {
        signal: controller.signal,
      });
      setRows(data);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setRows([]);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [
    market,
    indexOnly,
    q,
    signal,
    rsiMin,
    rsiMax,
    minPiotroski,
    minQuality,
    minRoe,
    minDivStreak,
    minDivYield,
    maxPe,
    shariahOnly,
    belowSectorPe,
    goldenCrossOnly,
    supertrendBullOnly,
    sortBy,
  ]);

  const handleGlobalSearch = async () => {
    if (!intlQuery.trim()) return;
    setIntlSearching(true);
    setImportNotice(null);
    try {
      const res = await apiFetch<IntlSearchHit[]>(
        `/api/v1/market/intl/search?q=${encodeURIComponent(intlQuery.trim())}&limit=8`
      );
      setIntlHits(res);
    } catch {
      setIntlHits([]);
    } finally {
      setIntlSearching(false);
    }
  };

  const handleGlobalImport = async (sym: string) => {
    setImportingSym(sym);
    setImportNotice(null);
    try {
      await apiFetch("/api/v1/market/intl/import", {
        method: "POST",
        body: JSON.stringify({ symbol: sym }),
      });
      setImportNotice(`Imported ${sym} with 5Y daily history & deep fundamentals.`);
      setIntlHits([]);
      setIntlQuery("");
      invalidateApiCache("");
      runScan();
    } catch (err) {
      setImportNotice(err instanceof Error ? err.message : `Failed to import ${sym}.`);
    } finally {
      setImportingSym(null);
    }
  };

  useEffect(() => {
    const t = setTimeout(() => {
      runScan();
    }, 300);
    return () => {
      clearTimeout(t);
      abortRef.current?.abort();
    };
  }, [runScan]);

  const exportCsv = () => {
    const headers = [
      "Symbol",
      "Name",
      "Class",
      "Currency",
      "Price",
      "ChangePct",
      "Signal",
      "RSI14",
      "ADX14",
      "GoldenCross",
      "Supertrend",
      "QualityScore",
      "PiotroskiF",
      "DCFUpsidePct",
      "PE",
      "PB",
      "DivYieldPct",
      "Ret1MPct",
      "Ret3MPct",
      "Ret1YPct",
      "Bars",
    ];
    const lines = rows.map((r) =>
      [
        r.symbol,
        `"${r.name.replace(/"/g, '""')}"`,
        r.asset_class,
        r.currency,
        r.last_price,
        r.change_pct,
        r.overall_signal,
        r.rsi_14 ?? "",
        r.adx_14 ?? "",
        r.golden_cross ? "YES" : "NO",
        r.supertrend_signal ?? "",
        r.quality_score ?? "",
        r.piotroski_f_score ?? "",
        r.dcf_upside_pct ?? "",
        r.pe_ratio ?? "",
        r.pb_ratio ?? "",
        r.dividend_yield_pct,
        r.return_1m_pct ?? "",
        r.return_3m_pct ?? "",
        r.return_1y_tr_pct ?? "",
        r.bar_count,
      ].join(","),
    );
    const blob = new Blob([[headers.join(","), ...lines].join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `openkhazana-screener-${market.toLowerCase()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <SlidersHorizontal className="w-6 h-6 text-emerald-500" />
            Cross-Market Technical &amp; Fundamental Screener
          </h1>
          <p className="text-sm okz-muted">
            Scan PSX and International equities by RSI(14), Golden Cross (SMA50 &gt; SMA200), Supertrend, Piotroski
            F-Score, DCF Upside, Quality Score, and Dividend Yield.
          </p>
        </div>
        <button
          type="button"
          onClick={exportCsv}
          disabled={rows.length === 0}
          className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-[var(--border)] hover:border-emerald-500 text-xs font-semibold disabled:opacity-50"
        >
          <Download className="w-3.5 h-3.5" />
          Export CSV ({rows.length})
        </button>
      </div>

      {/* Market Segment + Quick Presets */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex rounded-lg bg-[var(--card-muted)] p-1 text-xs font-semibold">
          {(["ALL", "PSX", "INTL"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMarket(m)}
              className={`px-3 py-1.5 rounded-md transition-colors ${
                market === m ? "bg-[var(--card)] text-emerald-500 shadow" : "okz-muted"
              }`}
            >
              {m === "ALL" ? "All Markets" : m === "PSX" ? "PSX Pakistan" : "US / Global (yfinance)"}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="okz-muted flex items-center gap-1 mr-1">
            <Filter className="w-3.5 h-3.5" /> Quick Scans:
          </span>
          <button
            type="button"
            onClick={() => {
              setSignal("ALL");
              setRsiMin("");
              setRsiMax("35");
              setMinQuality("50");
              setSortBy("rsi_14");
            }}
            className="px-2.5 py-1 rounded-md border border-[var(--border)] hover:border-emerald-500"
          >
            Oversold Quality (RSI &le; 35)
          </button>
          <button
            type="button"
            onClick={() => {
              setSignal("BULLISH");
              setGoldenCrossOnly(true);
              setSupertrendBullOnly(true);
              setRsiMin("");
              setRsiMax("70");
              setSortBy("return_1m_pct");
            }}
            className="px-2.5 py-1 rounded-md border border-[var(--border)] hover:border-emerald-500"
          >
            Golden Cross + Supertrend Bull
          </button>
          <button
            type="button"
            onClick={() => {
              setMinPiotroski("7");
              setMinQuality("65");
              setSortBy("dcf_upside_pct");
            }}
            className="px-2.5 py-1 rounded-md border border-[var(--border)] hover:border-emerald-500"
          >
            Deep Value (F-Score &ge; 7)
          </button>
          <button
            type="button"
            onClick={() => {
              setQ("");
              setSignal("ALL");
              setRsiMin("");
              setRsiMax("");
              setMinPiotroski("");
              setMinQuality("");
              setMinDivYield("");
              setMaxPe("");
              setShariahOnly(false);
              setGoldenCrossOnly(false);
              setSupertrendBullOnly(false);
              setSortBy("quality_score");
            }}
            className="px-2.5 py-1 rounded-md text-rose-400 border border-[var(--border)] hover:border-rose-400"
          >
            Reset
          </button>
        </div>
      </div>

      {/* Live Global / UCITS Ticker Search & 1-Click 5Y Import */}
      <div className="okz-card p-4 space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-center gap-2">
          <div className="flex items-center gap-1.5 text-xs font-semibold shrink-0">
            <Globe2 className="w-4 h-4 text-sky-400" />
            <span>Add Global / UCITS / Shariah Ticker (5Y):</span>
          </div>
          <div className="flex flex-1 gap-2">
            <input
              value={intlQuery}
              onChange={(e) => setIntlQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleGlobalSearch();
              }}
              placeholder="Search VWRA.L, ISWD.L, AMZN, GOOGL, QQQ, ARAMCO..."
              className="flex-1 px-3 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
            />
            <button
              type="button"
              onClick={handleGlobalSearch}
              disabled={intlSearching}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-md bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold disabled:opacity-60"
            >
              <Search className="w-3.5 h-3.5" />
              {intlSearching ? "Searching..." : "Search Yahoo"}
            </button>
          </div>
        </div>
        {importNotice && (
          <div className="text-xs px-3 py-1.5 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
            {importNotice}
          </div>
        )}
        {intlHits.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 text-xs">
            {intlHits.map((hit) => (
              <div
                key={hit.symbol}
                className="p-2.5 rounded border border-[var(--border)] bg-[var(--bg-elevated)] flex items-center justify-between gap-2"
              >
                <div className="min-w-0">
                  <div className="font-mono font-bold truncate">
                    {hit.symbol}{" "}
                    <span className="text-[10px] font-normal okz-muted">{hit.exchange}</span>
                  </div>
                  <div className="text-[11px] okz-muted truncate">{hit.name}</div>
                </div>
                <button
                  type="button"
                  onClick={() => handleGlobalImport(hit.symbol)}
                  disabled={importingSym === hit.symbol}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] font-semibold shrink-0 disabled:opacity-50"
                >
                  <Plus className="w-3 h-3" />
                  {importingSym === hit.symbol ? "..." : "Import"}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Filters Grid */}
      <div className="okz-card p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-3 items-end text-xs">
        <div>
          <label className="block okz-muted mb-1">Search Ticker / Company</label>
          <div className="relative">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="MEBL, Meezan, LUCK, AAPL..."
              className="w-full px-3 pr-8 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            />
            {q && (
              <button
                type="button"
                onClick={() => setQ("")}
                aria-label="Clear ticker search"
                className="absolute right-2.5 top-2.5 okz-muted hover:text-[var(--text)]"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        <div>
          <label className="block okz-muted mb-1">Technical Signal</label>
          <select
            value={signal}
            onChange={(e) => setSignal(e.target.value)}
            className="w-full px-2.5 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
          >
            <option value="ALL">All Signals</option>
            <option value="BULLISH">BULLISH</option>
            <option value="NEUTRAL">NEUTRAL</option>
            <option value="BEARISH">BEARISH</option>
          </select>
        </div>

        <div className="grid grid-cols-2 gap-1.5">
          <div>
            <label className="block okz-muted mb-1">RSI Min</label>
            <input
              type="number"
              min="0"
              max="100"
              value={rsiMin}
              onChange={(e) => setRsiMin(e.target.value)}
              placeholder="0"
              className="w-full px-2 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            />
          </div>
          <div>
            <label className="block okz-muted mb-1">RSI Max</label>
            <input
              type="number"
              min="0"
              max="100"
              value={rsiMax}
              onChange={(e) => setRsiMax(e.target.value)}
              placeholder="100"
              className="w-full px-2 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-1.5">
          <div>
            <label className="block okz-muted mb-1">Min Quality</label>
            <input
              type="number"
              min="0"
              max="100"
              value={minQuality}
              onChange={(e) => setMinQuality(e.target.value)}
              placeholder="0-100"
              className="w-full px-2 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            />
          </div>
          <div>
            <label className="block okz-muted mb-1">Min F-Score</label>
            <input
              type="number"
              min="0"
              max="9"
              value={minPiotroski}
              onChange={(e) => setMinPiotroski(e.target.value)}
              placeholder="0-9"
              className="w-full px-2 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-1.5">
          <div>
            <label className="block okz-muted mb-1">Max P/E</label>
            <input
              type="number"
              step="0.5"
              value={maxPe}
              onChange={(e) => setMaxPe(e.target.value)}
              placeholder="25"
              className="w-full px-2 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            />
          </div>
          <div>
            <label className="block okz-muted mb-1">Min Div %</label>
            <input
              type="number"
              step="0.5"
              value={minDivYield}
              onChange={(e) => setMinDivYield(e.target.value)}
              placeholder="5"
              className="w-full px-2 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            />
          </div>
        </div>

        <div>
          <label className="block okz-muted mb-1">Sort Results By</label>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="w-full px-2.5 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
          >
            <option value="quality_score">Quality Score (High &rarr; Low)</option>
            <option value="piotroski_f_score">Piotroski F-Score</option>
            <option value="roe_pct">ROE % (High &rarr; Low)</option>
            <option value="div_streak_years">Dividend Streak (Yrs)</option>
            <option value="dcf_upside_pct">DCF Intrinsic Upside %</option>
            <option value="rsi_14">RSI (14)</option>
            <option value="dividend_yield_pct">Dividend Yield %</option>
            <option value="return_1m_pct">1M Momentum %</option>
            <option value="pe_ratio">Lowest P/E Ratio</option>
            <option value="market_cap_mn">Market Cap</option>
          </select>
        </div>

        <div className="lg:col-span-6 grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2.5 pt-1 border-t border-[var(--border)] items-end">
          <div>
            <label className="block okz-muted mb-1">PSX Index</label>
            <select
              value={indexOnly}
              onChange={(e) => setIndexOnly(e.target.value as "ALL" | "KSE100" | "KMI30")}
              className="w-full px-2.5 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
            >
              <option value="ALL">All Listed</option>
              <option value="KSE100">KSE-100 Only</option>
              <option value="KMI30">KMI-30 Only</option>
            </select>
          </div>
          <div>
            <label className="block okz-muted mb-1">Min ROE %</label>
            <input
              type="number"
              value={minRoe}
              onChange={(e) => setMinRoe(e.target.value)}
              placeholder="15"
              className="w-full px-2 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
            />
          </div>
          <div>
            <label className="block okz-muted mb-1">Min Div Streak (Yrs)</label>
            <input
              type="number"
              min="0"
              max="10"
              value={minDivStreak}
              onChange={(e) => setMinDivStreak(e.target.value)}
              placeholder="3"
              className="w-full px-2 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
            />
          </div>
          <label className="inline-flex items-center gap-2 cursor-pointer pb-1.5">
            <input type="checkbox" checked={belowSectorPe} onChange={(e) => setBelowSectorPe(e.target.checked)} />
            <span>P/E &lt; Sector Median</span>
          </label>
          <label className="inline-flex items-center gap-2 cursor-pointer pb-1.5">
            <input type="checkbox" checked={shariahOnly} onChange={(e) => setShariahOnly(e.target.checked)} />
            <span>KMI / Shariah Only</span>
          </label>
          <div className="flex items-center gap-3 pb-1.5">
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input type="checkbox" checked={goldenCrossOnly} onChange={(e) => setGoldenCrossOnly(e.target.checked)} />
              <span>GoldenX</span>
            </label>
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={supertrendBullOnly}
                onChange={(e) => setSupertrendBullOnly(e.target.checked)}
              />
              <span>ST:BULL</span>
            </label>
          </div>
        </div>
        <div className="lg:col-span-6 flex items-center justify-end text-xs okz-muted">
          {loading ? "Scanning..." : `${rows.length} matching securities`}
        </div>
      </div>

      {/* Results Table */}
      {loading && rows.length === 0 ? (
        <TableSkeleton rows={10} cols={8} />
      ) : (
      <div className="okz-card overflow-x-auto">
        <table className="w-full text-xs sm:text-sm">
          <thead className="border-b border-[var(--border)] text-xs okz-muted text-left">
            <tr>
              <th className="py-3 px-4">Ticker &amp; Sector</th>
              <th className="py-3 px-3 text-right">Price</th>
              <th className="py-3 px-3 text-center">Signal</th>
              <th className="py-3 px-3 text-right">RSI(14)</th>
              <th className="py-3 px-3 text-center">Trend Badges</th>
              <th className="py-3 px-3 text-right">Quality</th>
              <th className="py-3 px-3 text-right">Piotroski</th>
              <th className="py-3 px-3 text-right">DCF Upside</th>
              <th className="py-3 px-3 text-right">P/E &bull; P/B</th>
              <th className="py-3 px-3 text-right">Div %</th>
              <th className="py-3 px-3 text-right">1M / 3M</th>
              <th className="py-3 px-3 text-right">Bars</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)]">
            {rows.length === 0 ? (
              <tr>
                <td colSpan={12} className="py-10 text-center text-xs okz-muted">
                  {loading
                    ? "Computing technicals and fundamentals..."
                    : "No assets matched all criteria. Try loosening RSI/Piotroski filters."}
                </td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.symbol} className="hover:bg-[var(--card-muted)]/50">
                  <td className="py-2.5 px-4">
                    <Link href={`/stocks/${encodeURIComponent(r.symbol)}`} className="group">
                      <div className="font-bold flex items-center gap-1.5 group-hover:text-emerald-500">
                        <span>{r.symbol}</span>
                        {r.is_shariah_compliant && <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />}
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--card-muted)] okz-muted font-normal">
                          {r.asset_class.startsWith("INTL") ? "INTL" : "PSX"}
                        </span>
                      </div>
                      <div className="text-[11px] okz-muted truncate max-w-52">
                        {r.name} &bull; {r.sector_or_category}
                      </div>
                    </Link>
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono">
                    <div className="font-semibold">
                      {r.currency === "USD" ? "$" : "Rs "}
                      {formatNumber(r.last_price, 2)}
                    </div>
                    <div className={`text-[11px] ${pctColorClass(r.change_pct)}`}>{formatPct(r.change_pct)}</div>
                  </td>
                  <td className="py-2.5 px-3 text-center">
                    <span
                      className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                        r.overall_signal === "BULLISH"
                          ? "bg-emerald-500/15 text-emerald-400"
                          : r.overall_signal === "BEARISH"
                            ? "bg-rose-500/15 text-rose-400"
                            : "bg-amber-500/15 text-amber-400"
                      }`}
                    >
                      {r.overall_signal}
                    </span>
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono">
                    {r.rsi_14 != null ? (
                      <span
                        className={
                          r.rsi_14 >= 70
                            ? "text-rose-400 font-bold"
                            : r.rsi_14 <= 30
                              ? "text-emerald-400 font-bold"
                              : ""
                        }
                      >
                        {r.rsi_14.toFixed(1)}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-2.5 px-3 text-center">
                    <div className="inline-flex flex-wrap items-center justify-center gap-1 text-[10px]">
                      {r.golden_cross && (
                        <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 font-semibold">
                          GoldenX
                        </span>
                      )}
                      {r.supertrend_signal && (
                        <span
                          className={`px-1.5 py-0.5 rounded font-semibold ${
                            r.supertrend_signal === "BULL"
                              ? "bg-emerald-500/15 text-emerald-400"
                              : "bg-rose-500/15 text-rose-400"
                          }`}
                        >
                          ST:{r.supertrend_signal}
                        </span>
                      )}
                      {r.above_sma_50 && (
                        <span className="px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-400">&gt;SMA50</span>
                      )}
                    </div>
                  </td>
                  <td className="py-2.5 px-3 text-right font-bold">
                    {r.quality_score != null ? (
                      <span className={r.quality_score >= 65 ? "text-emerald-400" : ""}>{r.quality_score}/100</span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono">
                    {r.piotroski_f_score != null ? (
                      <span
                        className={`px-1.5 py-0.5 rounded font-bold ${
                          r.piotroski_f_score >= 7
                            ? "bg-emerald-500/15 text-emerald-400"
                            : r.piotroski_f_score <= 3
                              ? "bg-rose-500/15 text-rose-400"
                              : "bg-[var(--card-muted)]"
                        }`}
                      >
                        {r.piotroski_f_score}/9
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className={`py-2.5 px-3 text-right font-semibold ${pctColorClass(r.dcf_upside_pct)}`}>
                    {r.dcf_upside_pct != null ? formatPct(r.dcf_upside_pct) : "—"}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono text-xs">
                    <div>
                      {formatNumber(r.pe_ratio, 1)} &bull; {formatNumber(r.pb_ratio, 2)}
                    </div>
                    <div className="text-[10px] okz-muted">
                      {r.roe_pct != null ? `ROE ${r.roe_pct.toFixed(1)}%` : "ROE —"}
                      {r.pe_vs_sector_pct != null && (
                        <span className={r.pe_vs_sector_pct < 0 ? " text-emerald-400 ml-1" : " text-amber-400 ml-1"}>
                          ({r.pe_vs_sector_pct > 0 ? "+" : ""}
                          {r.pe_vs_sector_pct}% sec)
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="py-2.5 px-3 text-right text-emerald-500 font-semibold">
                    <div>{formatNumber(r.dividend_yield_pct, 2)}%</div>
                    {(r.div_streak_years ?? 0) > 0 && (
                      <div className="text-[10px] okz-muted font-normal">{r.div_streak_years}Y streak</div>
                    )}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono text-xs">
                    <span className={pctColorClass(r.return_1m_pct)}>
                      {r.return_1m_pct != null ? `${r.return_1m_pct > 0 ? "+" : ""}${r.return_1m_pct.toFixed(1)}%` : "—"}
                    </span>
                    {" / "}
                    <span className={pctColorClass(r.return_3m_pct)}>
                      {r.return_3m_pct != null ? `${r.return_3m_pct > 0 ? "+" : ""}${r.return_3m_pct.toFixed(1)}%` : "—"}
                    </span>
                  </td>
                  <td className="py-2.5 px-3 text-right text-xs okz-muted">
                    <TrendingUp className="w-3 h-3 inline mr-0.5 opacity-60" />
                    {r.bar_count}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      )}
    </div>
  );
}
