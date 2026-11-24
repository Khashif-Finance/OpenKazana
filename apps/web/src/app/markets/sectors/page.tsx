"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ChevronDown, GitCompareArrows, Layers, Search, ShieldCheck, TrendingDown, TrendingUp, X } from "lucide-react";
import { useAppContext } from "@/components/CurrencyContext";
import { CardListSkeleton } from "@/components/Skeletons";
import { apiFetchCached } from "@/lib/api";
import { formatNumber, formatPct, pctColorClass } from "@/lib/format";

interface SectorStockRow {
  symbol: string;
  name: string;
  currency: string;
  last_price: number;
  change_pct: number;
  volume: number;
  market_cap_mn: number;
  pe_ratio: number | null;
  pb_ratio: number | null;
  roe_pct: number | null;
  dividend_yield_pct: number;
  piotroski_f_score: number | null;
  is_shariah_compliant: boolean;
  is_kse100: boolean;
}

interface SectorGroupRow {
  sector: string;
  stock_count: number;
  shariah_count: number;
  shariah_pct: number;
  total_market_cap_mn: number;
  total_volume: number;
  cap_weighted_change_pct: number;
  equal_weighted_change_pct: number;
  advancers: number;
  decliners: number;
  unchanged: number;
  median_pe: number | null;
  median_pb: number | null;
  median_roe_pct: number | null;
  median_div_yield_pct: number | null;
  avg_f_score: number | null;
  top_leader: { symbol: string; market_cap_mn: number };
  top_gainer: { symbol: string; change_pct: number };
  stocks: SectorStockRow[];
}

interface SectorsPayload {
  market: string;
  sector_count: number;
  total_stocks: number;
  total_market_cap_mn: number;
  sectors: SectorGroupRow[];
}

export default function SectorsExplorerPage() {
  const { shariahOnlyDefault } = useAppContext();
  const [market, setMarket] = useState<"PSX" | "INTL" | "ALL">("PSX");
  const [shariahOnly, setShariahOnly] = useState<boolean>(false);
  const [q, setQ] = useState("");
  const [sortBy, setSortBy] = useState<"mcap" | "change" | "pe" | "yield" | "count">("mcap");
  const [data, setData] = useState<SectorsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (shariahOnlyDefault) setShariahOnly(true);
  }, [shariahOnlyDefault]);

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams({ market });
    if (shariahOnly) params.set("shariah_only", "true");
    apiFetchCached<SectorsPayload>(`/api/v1/market/sectors?${params.toString()}`, 120_000, {
      onUpdate: setData,
    })
      .then((res) => {
        setData(res);
        if (res.sectors.length > 0 && Object.keys(expanded).length === 0) {
          setExpanded({ [res.sectors[0].sector]: true });
        }
      })
      .catch(() => setData(null))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, shariahOnly]);

  // Instant 0ms client-side search & sorting across all sectors and constituents
  const sortedSectors = useMemo(() => {
    const qLow = q.trim().toLowerCase();
    const list = (data?.sectors || []).filter((sec) => {
      if (!qLow) return true;
      if (sec.sector.toLowerCase().includes(qLow)) return true;
      return sec.stocks.some(
        (st) => st.symbol.toLowerCase().includes(qLow) || st.name.toLowerCase().includes(qLow),
      );
    });
    return [...list].sort((a, b) => {
      if (sortBy === "change") return b.cap_weighted_change_pct - a.cap_weighted_change_pct;
      if (sortBy === "pe") return (a.median_pe ?? 999) - (b.median_pe ?? 999);
      if (sortBy === "yield") return (b.median_div_yield_pct ?? 0) - (a.median_div_yield_pct ?? 0);
      if (sortBy === "count") return b.stock_count - a.stock_count;
      return b.total_market_cap_mn - a.total_market_cap_mn;
    });
  }, [data, q, sortBy]);

  const toggleSector = (sec: string) => {
    setExpanded((prev) => ({ ...prev, [sec]: !prev[sec] }));
  };

  const expandAll = () => {
    const next: Record<string, boolean> = {};
    for (const s of sortedSectors) next[s.sector] = true;
    setExpanded(next);
  };

  const collapseAll = () => setExpanded({});

  return (
    <div className="space-y-6">
      <div className="okz-card p-6 bg-gradient-to-br from-emerald-500/10 via-transparent to-sky-500/10 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Layers className="w-6 h-6 text-emerald-500" />
            PSX Sectors &amp; Industry Explorer
          </h1>
          <p className="text-sm okz-muted">
            Cross-sectional Pandas analytics across all {data?.sector_count || 37} official industries — click any sector accordion to drill down into every constituent stock or launch a head-to-head comparison.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {(["PSX", "INTL", "ALL"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMarket(m)}
              className={`px-3 py-1.5 rounded-lg font-semibold border ${
                market === m
                  ? "bg-emerald-600 text-white border-emerald-600"
                  : "border-[var(--border)] okz-muted hover:text-[var(--text)]"
              }`}
            >
              {m === "PSX" ? "PSX Pakistan" : m === "INTL" ? "Global / US" : "All Markets"}
            </button>
          ))}
        </div>
      </div>

      {/* Filter & Sort Controls */}
      <div className="okz-card p-4 flex flex-wrap items-center justify-between gap-3 text-xs">
        <div className="flex flex-wrap items-center gap-2 flex-1 min-w-60">
          <div className="relative flex-1 max-w-sm">
            <Search className="w-3.5 h-3.5 okz-muted absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Instant filter: sector, ticker (MEBL, LUCK), or company..."
              className="w-full pl-8 pr-7 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
            />
            {q && (
              <button
                type="button"
                onClick={() => setQ("")}
                aria-label="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 okz-muted hover:text-[var(--text)]"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
          <label className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-[var(--border)] cursor-pointer select-none">
            <input type="checkbox" checked={shariahOnly} onChange={(e) => setShariahOnly(e.target.checked)} />
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />
            <span className="font-semibold">Shariah Only</span>
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
            className="px-2.5 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs font-medium"
          >
            <option value="mcap">Sort: Total Market Cap</option>
            <option value="change">Sort: 1D Cap-Weighted Return</option>
            <option value="pe">Sort: Lowest Median P/E</option>
            <option value="yield">Sort: Highest Div Yield</option>
            <option value="count">Sort: Company Count</option>
          </select>
          <button
            type="button"
            onClick={expandAll}
            className="px-2.5 py-1.5 rounded-md border border-[var(--border)] hover:bg-[var(--card-muted)] font-medium"
          >
            Expand All
          </button>
          <button
            type="button"
            onClick={collapseAll}
            className="px-2.5 py-1.5 rounded-md border border-[var(--border)] hover:bg-[var(--card-muted)] font-medium"
          >
            Collapse All
          </button>
        </div>
      </div>

      {/* Expandable Sector Accordions */}
      {loading && !data ? (
        <CardListSkeleton count={6} cols="grid-cols-1" />
      ) : sortedSectors.length === 0 ? (
        <div className="okz-card p-8 text-center text-sm okz-muted">No matching sectors found.</div>
      ) : (
        <div className="space-y-3">
          {sortedSectors.map((sec) => {
            const isOpen = Boolean(expanded[sec.sector]);
            const top4Syms = sec.stocks.slice(0, 4).map((s) => s.symbol).join(",");
            return (
              <div key={sec.sector} className="okz-card overflow-hidden">
                <button
                  type="button"
                  onClick={() => toggleSector(sec.sector)}
                  className="w-full px-5 py-4 text-left flex flex-col lg:flex-row lg:items-center justify-between gap-3 hover:bg-[var(--card-muted)]/40 transition"
                >
                  <div className="space-y-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-bold text-sm">{sec.sector}</span>
                      <span className="text-[11px] px-2 py-0.5 rounded bg-[var(--card-muted)] okz-muted font-mono">
                        {sec.stock_count} stock{sec.stock_count > 1 ? "s" : ""}
                      </span>
                      {sec.shariah_count > 0 && (
                        <span className="text-[11px] px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 font-semibold">
                          {sec.shariah_pct.toFixed(0)}% Shariah ({sec.shariah_count})
                        </span>
                      )}
                      <span className="text-[11px] okz-muted">
                        Leader: <strong className="text-[var(--text)]">{sec.top_leader.symbol}</strong>
                      </span>
                    </div>
                    <div className="flex items-center gap-3 text-[11px] okz-muted">
                      <span className="text-emerald-400 inline-flex items-center gap-0.5">
                        <TrendingUp className="w-3 h-3" /> {sec.advancers} Up
                      </span>
                      <span className="text-rose-400 inline-flex items-center gap-0.5">
                        <TrendingDown className="w-3 h-3" /> {sec.decliners} Down
                      </span>
                      <span>Top Mover: {sec.top_gainer.symbol} ({formatPct(sec.top_gainer.change_pct)})</span>
                    </div>
                  </div>

                  <div className="flex items-center flex-wrap gap-4 lg:gap-6 text-xs font-mono">
                    <div className="text-right">
                      <div className="text-[10px] font-sans okz-muted">Mkt Cap</div>
                      <div className="font-bold">{formatNumber(sec.total_market_cap_mn, 0)} Mn</div>
                    </div>
                    <div className="text-right">
                      <div className="text-[10px] font-sans okz-muted">1D Cap-Wtd</div>
                      <div className={`font-bold ${pctColorClass(sec.cap_weighted_change_pct)}`}>
                        {formatPct(sec.cap_weighted_change_pct)}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-[10px] font-sans okz-muted">Med P/E</div>
                      <div className="font-semibold">{sec.median_pe != null ? `${sec.median_pe.toFixed(1)}x` : "—"}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-[10px] font-sans okz-muted">Med ROE</div>
                      <div className="font-semibold text-sky-400">
                        {sec.median_roe_pct != null ? `${sec.median_roe_pct.toFixed(1)}%` : "—"}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-[10px] font-sans okz-muted">Med Div Yld</div>
                      <div className="font-semibold text-emerald-400">
                        {sec.median_div_yield_pct != null ? `${sec.median_div_yield_pct.toFixed(1)}%` : "—"}
                      </div>
                    </div>
                    <ChevronDown className={`w-4 h-4 okz-muted transition-transform ${isOpen ? "rotate-180" : ""}`} />
                  </div>
                </button>

                {isOpen && (
                  <div className="border-t border-[var(--border)] bg-[var(--bg-elevated)]/25 p-4 space-y-3">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                      <span className="okz-muted">
                        Constituent companies ranked by Market Capitalization ({sec.stocks.length})
                      </span>
                      <div className="flex items-center gap-2">
                        {sec.stocks.length >= 2 && (
                          <Link
                            href={`/stocks/compare?symbols=${encodeURIComponent(top4Syms)}`}
                            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-sky-500/15 text-sky-400 font-semibold hover:bg-sky-500/25"
                          >
                            <GitCompareArrows className="w-3.5 h-3.5" /> Compare Top {Math.min(4, sec.stocks.length)} Peers
                          </Link>
                        )}
                        <Link
                          href={`/markets/news?sector=${encodeURIComponent(sec.sector)}`}
                          className="text-emerald-400 hover:underline font-semibold"
                        >
                          Sector News &rarr;
                        </Link>
                      </div>
                    </div>
                    <div className="overflow-x-auto border border-[var(--border)] rounded-lg bg-[var(--card)]">
                      <table className="w-full text-xs">
                        <thead className="bg-[var(--bg-elevated)] okz-muted text-right">
                          <tr>
                            <th className="py-2 px-3 text-left">Company</th>
                            <th className="py-2 px-3">Price</th>
                            <th className="py-2 px-3">1D %</th>
                            <th className="py-2 px-3">Mkt Cap (Mn)</th>
                            <th className="py-2 px-3">P/E</th>
                            <th className="py-2 px-3">P/B</th>
                            <th className="py-2 px-3">ROE %</th>
                            <th className="py-2 px-3">Div Yield</th>
                            <th className="py-2 px-3">F-Score</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-[var(--border)] font-mono">
                          {sec.stocks.map((st) => (
                            <tr key={st.symbol} className="hover:bg-[var(--card-muted)]/40">
                              <td className="py-2 px-3 text-left font-sans">
                                <Link
                                  href={`/stocks/${encodeURIComponent(st.symbol)}`}
                                  className="font-mono font-bold text-emerald-500 hover:underline inline-flex items-center gap-1"
                                >
                                  {st.symbol}
                                  {st.is_shariah_compliant && <ShieldCheck className="w-3 h-3 text-emerald-500" />}
                                </Link>
                                {st.is_kse100 && (
                                  <span className="ml-1.5 text-[9px] px-1 py-0.5 rounded bg-sky-500/15 text-sky-400 font-semibold">
                                    KSE100
                                  </span>
                                )}
                                <div className="text-[11px] okz-muted truncate max-w-60">{st.name}</div>
                              </td>
                              <td className="py-2 px-3 text-right font-semibold">{formatNumber(st.last_price, 2)}</td>
                              <td className={`py-2 px-3 text-right font-semibold ${pctColorClass(st.change_pct)}`}>
                                {formatPct(st.change_pct)}
                              </td>
                              <td className="py-2 px-3 text-right">{formatNumber(st.market_cap_mn, 0)}</td>
                              <td className="py-2 px-3 text-right">{st.pe_ratio != null ? st.pe_ratio.toFixed(1) : "—"}</td>
                              <td className="py-2 px-3 text-right">{st.pb_ratio != null ? st.pb_ratio.toFixed(2) : "—"}</td>
                              <td className="py-2 px-3 text-right text-sky-400">
                                {st.roe_pct != null ? `${st.roe_pct.toFixed(1)}%` : "—"}
                              </td>
                              <td className="py-2 px-3 text-right text-emerald-400">
                                {st.dividend_yield_pct > 0 ? `${st.dividend_yield_pct.toFixed(1)}%` : "—"}
                              </td>
                              <td className="py-2 px-3 text-right">
                                {st.piotroski_f_score != null ? `${st.piotroski_f_score}/9` : "—"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
