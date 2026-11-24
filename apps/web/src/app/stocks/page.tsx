"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ChevronLeft, ChevronRight, DownloadCloud, Globe, Search, ShieldCheck, X } from "lucide-react";
import { useAppContext } from "@/components/CurrencyContext";
import { TableSkeleton } from "@/components/Skeletons";
import { apiFetch, apiFetchCached, invalidateApiCache } from "@/lib/api";
import { formatMoney, formatNumber, formatPct, pctColorClass } from "@/lib/format";

interface ScreenerRow {
  symbol: string;
  name: string;
  asset_class?: string;
  currency?: string;
  exchange?: string;
  country?: string;
  sector_or_category: string;
  is_shariah_compliant: boolean;
  last_price: string;
  change_pct: string;
  volume: string;
  market_cap_mn: string;
  pe_ratio: string | null;
  pb_ratio: string | null;
  roe_pct: string | null;
  dividend_yield_pct: string;
  is_kse100: boolean;
  is_kmi30: boolean;
}

const PAGE_SIZE = 50;

export default function PsxScreenerPage() {
  const { currency, usdToPkr } = useAppContext();
  const [allRows, setAllRows] = useState<ScreenerRow[]>([]);
  const [loading, setLoading] = useState(true);

  const [market, setMarket] = useState<"ALL" | "PSX" | "INTL">("ALL");
  const [q, setQ] = useState("");
  const [sector, setSector] = useState("");
  const [shariahOnly, setShariahOnly] = useState(false);
  const [kse100Only, setKse100Only] = useState(false);
  const [kmi30Only, setKmi30Only] = useState(false);
  const [maxPe, setMaxPe] = useState("");
  const [minDiv, setMinDiv] = useState("");
  const [sortBy, setSortBy] = useState("market_cap_mn");
  const [page, setPage] = useState(1);
  const [showAll, setShowAll] = useState(false);

  // Live yfinance Ticker Importer state
  const [importSymbol, setImportSymbol] = useState("");
  const [importShariah, setImportShariah] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importStatus, setImportStatus] = useState<string | null>(null);

  const loadDirectory = useCallback((forceRefresh = false) => {
    setLoading(true);
    apiFetchCached<ScreenerRow[]>("/api/v1/market/stocks?market=ALL", 90_000, {
      forceRefresh,
      onUpdate: setAllRows,
    })
      .then((res) => setAllRows(res))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadDirectory(false);
  }, [loadDirectory]);

  const sectors = useMemo(
    () => Array.from(new Set(allRows.map((r) => r.sector_or_category).filter(Boolean))).sort(),
    [allRows],
  );

  // Instant 0ms client-side filtering & sorting across all 550+ equities
  const filteredRows = useMemo(() => {
    const qLow = q.trim().toLowerCase();
    const maxPeNum = maxPe.trim() !== "" ? Number(maxPe) : null;
    const minDivNum = minDiv.trim() !== "" ? Number(minDiv) : null;

    const matched = allRows.filter((r) => {
      if (market === "PSX" && !String(r.asset_class || "").startsWith("PSX")) return false;
      if (market === "INTL" && !String(r.asset_class || "").startsWith("INTL")) return false;
      if (
        qLow &&
        !r.symbol.toLowerCase().includes(qLow) &&
        !r.name.toLowerCase().includes(qLow) &&
        !(r.sector_or_category || "").toLowerCase().includes(qLow)
      ) {
        return false;
      }
      if (sector && r.sector_or_category !== sector) return false;
      if (shariahOnly && !r.is_shariah_compliant) return false;
      if (kse100Only && !r.is_kse100) return false;
      if (kmi30Only && !r.is_kmi30) return false;
      if (maxPeNum != null && Number.isFinite(maxPeNum)) {
        const pe = r.pe_ratio != null ? Number(r.pe_ratio) : NaN;
        if (!Number.isFinite(pe) || pe <= 0 || pe > maxPeNum) return false;
      }
      if (minDivNum != null && Number.isFinite(minDivNum)) {
        if (Number(r.dividend_yield_pct || 0) < minDivNum) return false;
      }
      return true;
    });

    matched.sort((a, b) => {
      if (sortBy === "pe_ratio") {
        const pa = a.pe_ratio != null && Number(a.pe_ratio) > 0 ? Number(a.pe_ratio) : 99999;
        const pb = b.pe_ratio != null && Number(b.pe_ratio) > 0 ? Number(b.pe_ratio) : 99999;
        return pa - pb;
      }
      if (sortBy === "dividend_yield_pct") return Number(b.dividend_yield_pct || 0) - Number(a.dividend_yield_pct || 0);
      if (sortBy === "change_pct") return Number(b.change_pct || 0) - Number(a.change_pct || 0);
      if (sortBy === "volume") return Number(b.volume || 0) - Number(a.volume || 0);
      return Number(b.market_cap_mn || 0) - Number(a.market_cap_mn || 0);
    });

    return matched;
  }, [allRows, market, q, sector, shariahOnly, kse100Only, kmi30Only, maxPe, minDiv, sortBy]);

  useEffect(() => {
    setPage(1);
  }, [market, q, sector, shariahOnly, kse100Only, kmi30Only, maxPe, minDiv, sortBy]);

  const totalPages = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
  const displayedRows = showAll ? filteredRows : filteredRows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const resetFilters = () => {
    setMarket("ALL");
    setQ("");
    setSector("");
    setShariahOnly(false);
    setKse100Only(false);
    setKmi30Only(false);
    setMaxPe("");
    setMinDiv("");
    setSortBy("market_cap_mn");
  };

  async function handleYFinanceImport(e: React.FormEvent) {
    e.preventDefault();
    if (!importSymbol.trim()) return;
    setImporting(true);
    setImportStatus(null);
    try {
      const res = await apiFetch<ScreenerRow>("/api/v1/market/intl/import", {
        method: "POST",
        body: JSON.stringify({
          symbol: importSymbol.trim().toUpperCase(),
          is_shariah_compliant: importShariah ? true : null,
        }),
      });
      setImportStatus(`Synced ${res.symbol} (${res.name}) @ $${Number(res.last_price).toFixed(2)} via Yahoo Finance.`);
      setImportSymbol("");
      invalidateApiCache("/api/v1/market/stocks");
      invalidateApiCache("");
      loadDirectory(true);
    } catch (err) {
      setImportStatus(err instanceof Error ? err.message : "Failed to import ticker from Yahoo Finance.");
    } finally {
      setImporting(false);
    }
  }

  function formatStockPrice(r: ScreenerRow) {
    const isUsd = r.currency === "USD";
    const px = Number(r.last_price || 0);
    if (isUsd) {
      if (currency === "USD") {
        return `$${formatNumber(px, 2)}`;
      }
      return `$${formatNumber(px, 2)} (Rs ${formatNumber(px * usdToPkr, 0)})`;
    }
    return formatMoney(px, currency, usdToPkr);
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">PSX &amp; Global Stock Directory</h1>
          <p className="text-sm okz-muted">
            Instant client-side screening across {allRows.length.toLocaleString()} Pakistan Stock Exchange (PSX) &amp;
            US/Global equities by Shariah compliance, P/E, ROE, and dividend yield.
          </p>
        </div>

        <div className="inline-flex rounded-lg border border-[var(--border)] bg-[var(--card)] p-1 text-xs font-medium self-start">
          <button
            type="button"
            onClick={() => setMarket("ALL")}
            className={`px-3 py-1.5 rounded-md transition-colors ${
              market === "ALL" ? "bg-emerald-600 text-white" : "okz-muted hover:text-[var(--text)]"
            }`}
          >
            All Markets
          </button>
          <button
            type="button"
            onClick={() => setMarket("PSX")}
            className={`px-3 py-1.5 rounded-md transition-colors ${
              market === "PSX" ? "bg-emerald-600 text-white" : "okz-muted hover:text-[var(--text)]"
            }`}
          >
            PSX Pakistan
          </button>
          <button
            type="button"
            onClick={() => {
              setMarket("INTL");
              setKse100Only(false);
              setKmi30Only(false);
            }}
            className={`px-3 py-1.5 rounded-md transition-colors ${
              market === "INTL" ? "bg-emerald-600 text-white" : "okz-muted hover:text-[var(--text)]"
            }`}
          >
            US &amp; Global
          </button>
        </div>
      </div>

      {/* 1-Click Smart Presets */}
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="okz-muted mr-1 font-medium">Quick Presets:</span>
        <button
          type="button"
          onClick={() => {
            resetFilters();
            setMarket("PSX");
            setKse100Only(true);
          }}
          className="px-2.5 py-1 rounded-md border border-[var(--border)] bg-[var(--card)] hover:border-emerald-500"
        >
          KSE-100 Blue Chips
        </button>
        <button
          type="button"
          onClick={() => {
            resetFilters();
            setMarket("PSX");
            setKmi30Only(true);
            setShariahOnly(true);
          }}
          className="px-2.5 py-1 rounded-md border border-[var(--border)] bg-[var(--card)] hover:border-emerald-500"
        >
          KMI-30 Shariah
        </button>
        <button
          type="button"
          onClick={() => {
            resetFilters();
            setMinDiv("8");
            setSortBy("dividend_yield_pct");
          }}
          className="px-2.5 py-1 rounded-md border border-[var(--border)] bg-[var(--card)] hover:border-emerald-500"
        >
          High Dividend (&ge;8%)
        </button>
        <button
          type="button"
          onClick={() => {
            resetFilters();
            setMaxPe("8");
            setSortBy("pe_ratio");
          }}
          className="px-2.5 py-1 rounded-md border border-[var(--border)] bg-[var(--card)] hover:border-emerald-500"
        >
          Deep Value (P/E &le;8x)
        </button>
        <button
          type="button"
          onClick={resetFilters}
          className="px-2.5 py-1 rounded-md text-rose-400 border border-[var(--border)] hover:border-rose-400"
        >
          Reset
        </button>
      </div>

      {/* On-Demand Yahoo Finance (yfinance) Live Global Importer */}
      <form
        onSubmit={handleYFinanceImport}
        className="okz-card p-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-emerald-500/30"
      >
        <div className="flex items-center gap-2.5 text-xs">
          <Globe className="w-4 h-4 text-emerald-500 shrink-0" />
          <div>
            <span className="font-semibold">Live Global Ticker Importer (yfinance):</span>{" "}
            <span className="okz-muted">
              Pull real-time price, 5Y OHLCV bars, P/E, dividend yield &amp; corporate actions for any US/Global stock or ETF.
            </span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={importSymbol}
            onChange={(e) => setImportSymbol(e.target.value)}
            placeholder="e.g. AMZN, GOOGL, HLAL, VWRA.L"
            className="px-3 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs w-52"
          />
          <label className="inline-flex items-center gap-1.5 text-xs okz-muted cursor-pointer">
            <input
              type="checkbox"
              checked={importShariah}
              onChange={(e) => setImportShariah(e.target.checked)}
            />
            <span>Halal / Shariah</span>
          </label>
          <button
            type="submit"
            disabled={importing || !importSymbol.trim()}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-xs font-medium"
          >
            <DownloadCloud className="w-3.5 h-3.5" />
            {importing ? "Fetching..." : "Import / Sync"}
          </button>
        </div>
      </form>
      {importStatus && (
        <div className="text-xs px-3 py-2 rounded-md bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
          {importStatus}
        </div>
      )}

      <div className="okz-card p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-7 gap-3 items-end">
        <div className="lg:col-span-2">
          <label className="block text-xs okz-muted mb-1">Instant Search (Symbol, Company or Sector)</label>
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-2.5 okz-muted pointer-events-none" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Type MEBL, Meezan, Cement, NVDA..."
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
          <label className="block text-xs okz-muted mb-1">Sector</label>
          <select
            value={sector}
            onChange={(e) => setSector(e.target.value)}
            className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
          >
            <option value="">All Sectors ({sectors.length})</option>
            {sectors.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs okz-muted mb-1">Max P/E Ratio</label>
          <input
            type="number"
            value={maxPe}
            onChange={(e) => setMaxPe(e.target.value)}
            placeholder="Any"
            className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
          />
        </div>

        <div>
          <label className="block text-xs okz-muted mb-1">Min Div Yield %</label>
          <input
            type="number"
            value={minDiv}
            onChange={(e) => setMinDiv(e.target.value)}
            placeholder="Any"
            className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
          />
        </div>

        <div>
          <label className="block text-xs okz-muted mb-1">Sort By</label>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
          >
            <option value="market_cap_mn">Market Cap</option>
            <option value="dividend_yield_pct">Dividend Yield</option>
            <option value="pe_ratio">Lowest P/E</option>
            <option value="change_pct">Daily % Change</option>
            <option value="volume">Volume</option>
          </select>
        </div>

        <div className="flex flex-col gap-1 text-xs">
          <label className="inline-flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={shariahOnly} onChange={(e) => setShariahOnly(e.target.checked)} />
            <span>Shariah Only</span>
          </label>
          <label className="inline-flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={kse100Only} onChange={(e) => setKse100Only(e.target.checked)} />
            <span>KSE-100 Index</span>
          </label>
          <label className="inline-flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={kmi30Only} onChange={(e) => setKmi30Only(e.target.checked)} />
            <span>KMI-30 Index</span>
          </label>
        </div>
      </div>

      {/* Pagination & Count Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs okz-muted">
        <span>
          Showing <strong className="text-[var(--text)]">{displayedRows.length}</strong> of{" "}
          <strong className="text-[var(--text)]">{filteredRows.length}</strong> matching securities
        </span>
        {filteredRows.length > PAGE_SIZE && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowAll((v) => !v)}
              className="px-2.5 py-1 rounded border border-[var(--border)] hover:border-emerald-500 font-medium text-[var(--text)]"
            >
              {showAll ? "Paginate (50/page)" : `Show All (${filteredRows.length})`}
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

      {loading && allRows.length === 0 ? (
        <TableSkeleton rows={10} cols={8} />
      ) : (
        <div className="okz-card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-[var(--border)] text-xs okz-muted text-left">
              <tr>
                <th className="py-3 px-4">Symbol</th>
                <th className="py-3 px-4">Market</th>
                <th className="py-3 px-4">Sector</th>
                <th className="py-3 px-4 text-right">Price</th>
                <th className="py-3 px-4 text-right">Change</th>
                <th className="py-3 px-4 text-right">P/E</th>
                <th className="py-3 px-4 text-right">P/B</th>
                <th className="py-3 px-4 text-right">ROE %</th>
                <th className="py-3 px-4 text-right">Div Yield</th>
                <th className="py-3 px-4 text-right">MCap (Mn)</th>
                <th className="py-3 px-4 text-center">Shariah</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)]">
              {displayedRows.length === 0 ? (
                <tr>
                  <td colSpan={11} className="py-10 text-center text-xs okz-muted">
                    No securities matched your filters. Try clearing search or resetting filters.
                  </td>
                </tr>
              ) : (
                displayedRows.map((r) => (
                  <tr key={r.symbol} className="hover:bg-[var(--card-muted)]/50">
                    <td className="py-3 px-4">
                      <Link href={`/stocks/${r.symbol}`} className="font-bold text-emerald-500 hover:underline font-mono">
                        {r.symbol}
                      </Link>
                      <div className="text-xs okz-muted truncate max-w-44">{r.name}</div>
                    </td>
                    <td className="py-3 px-4">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${
                          r.currency === "USD"
                            ? "bg-sky-500/15 text-sky-400"
                            : "bg-emerald-500/15 text-emerald-400"
                        }`}
                      >
                        {r.exchange || (r.currency === "USD" ? "US" : "PSX")} · {r.currency || "PKR"}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-xs okz-muted">{r.sector_or_category}</td>
                    <td className="py-3 px-4 text-right font-semibold font-mono">{formatStockPrice(r)}</td>
                    <td className={`py-3 px-4 text-right font-medium font-mono ${pctColorClass(r.change_pct)}`}>
                      {formatPct(r.change_pct)}
                    </td>
                    <td className="py-3 px-4 text-right font-mono">{r.pe_ratio ?? "—"}</td>
                    <td className="py-3 px-4 text-right font-mono">{r.pb_ratio ?? "—"}</td>
                    <td className="py-3 px-4 text-right font-mono">{r.roe_pct ? `${r.roe_pct}%` : "—"}</td>
                    <td className="py-3 px-4 text-right text-emerald-500 font-medium font-mono">
                      {r.dividend_yield_pct}%
                    </td>
                    <td className="py-3 px-4 text-right font-mono">{formatNumber(r.market_cap_mn, 0)}</td>
                    <td className="py-3 px-4 text-center">
                      {r.is_shariah_compliant ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-500 text-xs font-medium">
                          <ShieldCheck className="w-3.5 h-3.5" /> {r.is_kmi30 ? "KMI-30" : "Halal"}
                        </span>
                      ) : (
                        <span className="text-xs okz-muted">Conv.</span>
                      )}
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
