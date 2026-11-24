"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { DownloadCloud, Globe, Search, ShieldCheck } from "lucide-react";
import { useAppContext } from "@/components/CurrencyContext";
import { apiFetch } from "@/lib/api";
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

export default function PsxScreenerPage() {
  const { currency, usdToPkr } = useAppContext();
  const [rows, setRows] = useState<ScreenerRow[]>([]);
  const [sectors, setSectors] = useState<string[]>([]);
  const [market, setMarket] = useState<"ALL" | "PSX" | "INTL">("ALL");
  const [q, setQ] = useState("");
  const [sector, setSector] = useState("");
  const [shariahOnly, setShariahOnly] = useState(false);
  const [kse100Only, setKse100Only] = useState(false);
  const [kmi30Only, setKmi30Only] = useState(false);
  const [maxPe, setMaxPe] = useState("");
  const [minDiv, setMinDiv] = useState("");
  const [sortBy, setSortBy] = useState("market_cap_mn");

  // Live yfinance Ticker Importer state
  const [importSymbol, setImportSymbol] = useState("");
  const [importShariah, setImportShariah] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importStatus, setImportStatus] = useState<string | null>(null);

  const loadScreener = useCallback(() => {
    const params = new URLSearchParams();
    params.set("market", market);
    if (q) params.set("q", q);
    if (sector) params.set("sector", sector);
    if (shariahOnly) params.set("shariah_only", "true");
    if (kse100Only) params.set("kse100_only", "true");
    if (kmi30Only) params.set("kmi30_only", "true");
    if (maxPe) params.set("max_pe", maxPe);
    if (minDiv) params.set("min_div_yield", minDiv);
    params.set("sort_by", sortBy);

    apiFetch<ScreenerRow[]>(`/api/v1/market/stocks?${params.toString()}`)
      .then((res) => {
        setRows(res);
        setSectors(Array.from(new Set(res.map((r) => r.sector_or_category).filter(Boolean))).sort());
      })
      .catch(() => {});
  }, [market, q, sector, shariahOnly, kse100Only, kmi30Only, maxPe, minDiv, sortBy]);

  useEffect(() => {
    loadScreener();
  }, [loadScreener]);

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
      loadScreener();
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
          <h1 className="text-2xl font-bold">PSX & Global Stock Screener</h1>
          <p className="text-sm okz-muted">
            Screen Pakistan Stock Exchange (PSX) & US/Global equities/ETFs (`yfinance`) by Shariah compliance, P/E, and yield.
          </p>
        </div>

        <div className="inline-flex rounded-lg border border-[var(--border)] bg-[var(--card)] p-1 text-xs font-medium">
          <button
            type="button"
            onClick={() => setMarket("ALL")}
            className={`px-3 py-1.5 rounded-md transition-colors ${
              market === "ALL" ? "bg-emerald-600 text-white" : "okz-muted hover:text-[var(--fg)]"
            }`}
          >
            All Markets
          </button>
          <button
            type="button"
            onClick={() => setMarket("PSX")}
            className={`px-3 py-1.5 rounded-md transition-colors ${
              market === "PSX" ? "bg-emerald-600 text-white" : "okz-muted hover:text-[var(--fg)]"
            }`}
          >
            PSX Pakistan 🇵🇰
          </button>
          <button
            type="button"
            onClick={() => {
              setMarket("INTL");
              setKse100Only(false);
              setKmi30Only(false);
            }}
            className={`px-3 py-1.5 rounded-md transition-colors ${
              market === "INTL" ? "bg-emerald-600 text-white" : "okz-muted hover:text-[var(--fg)]"
            }`}
          >
            US & Global 🌎
          </button>
        </div>
      </div>

      {/* On-Demand Yahoo Finance (yfinance) Live Global Importer */}
      <form
        onSubmit={handleYFinanceImport}
        className="okz-card p-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-emerald-500/30"
      >
        <div className="flex items-center gap-2.5 text-xs">
          <Globe className="w-4 h-4 text-emerald-500 shrink-0" />
          <div>
            <span className="font-semibold">Live Global Ticker Importer (`yfinance`):</span>{" "}
            <span className="okz-muted">
              Pull real-time price, 1Y OHLCV bars, P/E, dividend yield & corporate actions for any US/Global stock or ETF.
            </span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={importSymbol}
            onChange={(e) => setImportSymbol(e.target.value)}
            placeholder="Ticker e.g. AMZN, GOOGL, HLAL,SGOV..."
            className="px-3 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs w-48"
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
          <label className="block text-xs okz-muted mb-1">Search Symbol / Company</label>
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-2.5 okz-muted" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="e.g. MEBL, NVDA, SPUS, SYS..."
              className="w-full pl-9 pr-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            />
          </div>
        </div>

        <div>
          <label className="block text-xs okz-muted mb-1">Sector</label>
          <select
            value={sector}
            onChange={(e) => setSector(e.target.value)}
            className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
          >
            <option value="">All Sectors</option>
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
            placeholder="e.g. 15"
            className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
          />
        </div>

        <div>
          <label className="block text-xs okz-muted mb-1">Min Div Yield %</label>
          <input
            type="number"
            value={minDiv}
            onChange={(e) => setMinDiv(e.target.value)}
            placeholder="e.g. 5"
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
            {rows.map((r) => (
              <tr key={r.symbol} className="hover:bg-[var(--card-muted)]/50">
                <td className="py-3 px-4">
                  <Link href={`/stocks/${r.symbol}`} className="font-bold text-emerald-500 hover:underline">
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
                <td className="py-3 px-4 text-right font-semibold">{formatStockPrice(r)}</td>
                <td className={`py-3 px-4 text-right font-medium ${pctColorClass(r.change_pct)}`}>
                  {formatPct(r.change_pct)}
                </td>
                <td className="py-3 px-4 text-right">{r.pe_ratio ?? "—"}</td>
                <td className="py-3 px-4 text-right">{r.pb_ratio ?? "—"}</td>
                <td className="py-3 px-4 text-right">{r.roe_pct ? `${r.roe_pct}%` : "—"}</td>
                <td className="py-3 px-4 text-right text-emerald-500 font-medium">{r.dividend_yield_pct}%</td>
                <td className="py-3 px-4 text-right">{formatNumber(r.market_cap_mn, 0)}</td>
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
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
