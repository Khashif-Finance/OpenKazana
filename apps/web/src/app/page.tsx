"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Download, ExternalLink, Megaphone, Newspaper, RefreshCw, ShieldCheck, TrendingDown, TrendingUp } from "lucide-react";
import { useAppContext } from "@/components/CurrencyContext";
import { MarketHeatmap } from "@/components/MarketHeatmap";
import { BarPoint, PriceChart } from "@/components/PriceChart";
import { ChartSkeleton, KpiGridSkeleton } from "@/components/Skeletons";
import { apiFetchCached } from "@/lib/api";
import { formatMoney, formatNumber, formatPct, pctColorClass } from "@/lib/format";

interface AssetItem {
  id: string;
  symbol: string;
  name: string;
  asset_class: string;
  currency: string;
  sector_or_category: string;
  is_shariah_compliant: boolean;
  last_price: string;
  change_pct: string;
  volume: string;
}

interface ScreenerItem extends AssetItem {
  dividend_yield_pct: string;
  pe_ratio: string | null;
}

interface AnnouncementItem {
  id: string;
  symbol: string;
  asset_name: string;
  title: string;
  category: string;
  published_at: string;
  source_url?: string;
  summary: string;
}

interface OverviewPayload {
  indices: AssetItem[];
  fx_rates: AssetItem[];
  gold_rates: AssetItem[];
  crypto_rates: AssetItem[];
  top_gainers: AssetItem[];
  top_losers: AssetItem[];
  top_dividend_yield: ScreenerItem[];
  recent_announcements: AnnouncementItem[];
  usd_to_pkr: string;
}

const GRAMS_PER_TOLA = 11.6638038;

export default function MarketHubPage() {
  const { user, localMode, currency, usdToPkr, setUsdToPkr } = useAppContext();
  const [data, setData] = useState<OverviewPayload | null>(null);
  const [headlines, setHeadlines] = useState<
    Array<{
      id: string;
      title: string;
      url: string;
      source_name: string;
      category: string;
      matched_symbols: string[];
      published_at: string;
    }>
  >([]);
  const [error, setError] = useState("");
  const [goldUnit, setGoldUnit] = useState<"TOLA" | "10G" | "1G">("TOLA");
  const [goldKarat, setGoldKarat] = useState<"24K" | "22K">("24K");
  const [selectedIndex, setSelectedIndex] = useState<string>("KSE100");
  const [indexBars, setIndexBars] = useState<BarPoint[]>([]);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const loadOverview = useCallback(async (forceRefresh = false) => {
    setRefreshing(true);
    try {
      const res = await apiFetchCached<OverviewPayload>("/api/v1/market/overview", 60_000, {
        forceRefresh,
        onUpdate: (fresh) => {
          setData(fresh);
          if (Number(fresh.usd_to_pkr) > 0) setUsdToPkr(Number(fresh.usd_to_pkr));
        },
      });
      setData(res);
      setError("");
      if (Number(res.usd_to_pkr) > 0) setUsdToPkr(Number(res.usd_to_pkr));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load market overview");
    } finally {
      setRefreshing(false);
    }
  }, [setUsdToPkr]);

  useEffect(() => {
    loadOverview(false);
    apiFetchCached<{
      items: Array<{
        id: string;
        title: string;
        url: string;
        source_name: string;
        category: string;
        matched_symbols: string[];
        published_at: string;
      }>;
    }>("/api/v1/market/news?limit=6", 60_000, {
      onUpdate: (r) => setHeadlines(r.items || []),
    })
      .then((r) => setHeadlines(r.items || []))
      .catch(() => {});
  }, [loadOverview]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => {
      loadOverview(true);
    }, 30000);
    return () => clearInterval(id);
  }, [autoRefresh, loadOverview]);

  useEffect(() => {
    apiFetchCached<BarPoint[]>(
      `/api/v1/market/assets/${encodeURIComponent(selectedIndex)}/bars?timeframe=1D&limit=60`,
      120_000,
    )
      .then(setIndexBars)
      .catch(() => setIndexBars([]));
  }, [selectedIndex]);

  if (error) {
    return (
      <div className="okz-card p-6 text-sm text-rose-500">
        Unable to reach OpenKhazana API ({error}). Make sure backend services are running.
      </div>
    );
  }

  if (!data) {
    return (
      <div className="space-y-6">
        <KpiGridSkeleton count={8} cols="grid-cols-2 sm:grid-cols-4" />
        <ChartSkeleton heightClass="h-64" />
      </div>
    );
  }

  const macroCards: AssetItem[] = [
    ...data.indices,
    ...data.fx_rates.slice(0, 2),
    ...data.gold_rates.slice(0, 2),
    ...data.crypto_rates.slice(0, 1),
  ];

  const convertMetalDisplay = (gramPrice24k: string, isGold: boolean) => {
    const base = Number(gramPrice24k) || 0;
    const karatFactor = isGold && goldKarat === "22K" ? 22 / 24 : 1;
    const mult = goldUnit === "TOLA" ? GRAMS_PER_TOLA : goldUnit === "10G" ? 10 : 1;
    return String((base * karatFactor * mult).toFixed(2));
  };

  return (
    <div className="space-y-6">
      <section className="okz-card p-6 bg-gradient-to-br from-emerald-500/10 via-transparent to-sky-500/10 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="space-y-1.5">
          <h1 className="text-2xl font-bold tracking-tight">Pakistan Market Hub &amp; Multi-Asset Wealth Tracker</h1>
          <p className="text-sm okz-muted max-w-2xl">
            Live-scheduled PSX batch quotes, MUFAP mutual funds, Shariah purification screening, dual Interbank &amp;
            Open Market USD/PKR, Gold, NSS, and a Pakistan FBR CGT/Sec 116 ledger — 100% open source.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href={user || localMode ? "/portfolio" : "/auth"}
            className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold transition"
          >
            {user || localMode ? "Open My Portfolio" : "Sign In / Create Account"}
          </Link>
          <Link
            href="/markets/sectors"
            className="px-3.5 py-2 rounded-lg border border-[var(--border)] text-sm font-semibold hover:bg-[var(--card-muted)] inline-flex items-center gap-1.5"
          >
            Sectors &amp; Industries <ArrowRight className="w-4 h-4" />
          </Link>
          <Link
            href="/markets/ipos"
            className="px-3.5 py-2 rounded-lg border border-[var(--border)] text-sm font-semibold hover:bg-[var(--card-muted)] inline-flex items-center gap-1.5"
          >
            IPOs &amp; News
          </Link>
          <button
            type="button"
            onClick={() => loadOverview(true)}
            disabled={refreshing}
            className="px-3 py-2 rounded-lg border border-[var(--border)] text-xs font-semibold hover:bg-[var(--card-muted)] inline-flex items-center gap-1.5"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <button
            type="button"
            onClick={() => setAutoRefresh((v) => !v)}
            className={`px-3 py-2 rounded-lg border text-xs font-semibold inline-flex items-center gap-1.5 ${
              autoRefresh
                ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-500"
                : "border-[var(--border)] okz-muted hover:text-[var(--text)]"
            }`}
          >
            {autoRefresh ? "Auto 30s ON" : "Auto 30s OFF"}
          </button>
        </div>
      </section>

      <section className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {macroCards.map((item) => {
          const isGoldGram = item.symbol === "GOLD_24K_GRAM";
          const isSilverGram = item.symbol === "SILVER_GRAM";
          const isIndex = item.asset_class === "PSX_INDEX";
          const displayValue =
            isGoldGram || isSilverGram ? convertMetalDisplay(item.last_price, isGoldGram) : item.last_price;
          const isSelectedIndex = isIndex && selectedIndex === item.symbol;
          return (
            <div
              key={item.symbol}
              onClick={isIndex ? () => setSelectedIndex(item.symbol) : undefined}
              className={`okz-card p-4 space-y-1 ${isIndex ? "cursor-pointer hover:border-emerald-500/50" : ""} ${
                isSelectedIndex ? "border-emerald-500" : ""
              }`}
            >
              <div className="flex items-center justify-between text-xs okz-muted">
                <span className="font-semibold text-[var(--text)] truncate">
                  {isGoldGram
                    ? `GOLD ${goldKarat} (${goldUnit})`
                    : isSilverGram
                      ? `SILVER (${goldUnit})`
                      : item.symbol}
                </span>
                <span className={pctColorClass(item.change_pct)}>{formatPct(item.change_pct)}</span>
              </div>
              <div className="text-base font-bold">
                {item.asset_class === "PSX_INDEX"
                  ? formatNumber(displayValue, 2)
                  : item.currency === "USD"
                    ? `$${formatNumber(displayValue, 2)}`
                    : formatMoney(displayValue, currency, usdToPkr)}
              </div>
              {isGoldGram ? (
                <div className="flex flex-wrap items-center gap-1 pt-0.5">
                  {(["24K", "22K"] as const).map((k) => (
                    <button
                      key={k}
                      onClick={(e) => {
                        e.stopPropagation();
                        setGoldKarat(k);
                      }}
                      className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${
                        goldKarat === k ? "bg-amber-500/20 text-amber-500" : "bg-[var(--card-muted)] okz-muted"
                      }`}
                    >
                      {k}
                    </button>
                  ))}
                  {(["TOLA", "10G", "1G"] as const).map((u) => (
                    <button
                      key={u}
                      onClick={(e) => {
                        e.stopPropagation();
                        setGoldUnit(u);
                      }}
                      className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${
                        goldUnit === u ? "bg-emerald-500/20 text-emerald-500" : "bg-[var(--card-muted)] okz-muted"
                      }`}
                    >
                      {u}
                    </button>
                  ))}
                </div>
              ) : (
                <div className="text-[11px] okz-muted truncate">{item.name}</div>
              )}
            </div>
          );
        })}
      </section>

      <MarketHeatmap />

      {indexBars.length > 0 && (
        <section className="okz-card p-5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold">{selectedIndex} Benchmark Trend (60D Daily Close)</h2>
              <p className="text-xs okz-muted">Click an index card or switcher below to inspect PSX index trajectory.</p>
            </div>
            <div className="flex items-center gap-1 rounded-md border border-[var(--border)] p-0.5 text-xs">
              {(data.indices.length > 0 ? data.indices.map((i) => i.symbol) : ["KSE100", "KMI30"]).map((sym) => (
                <button
                  key={sym}
                  type="button"
                  onClick={() => setSelectedIndex(sym)}
                  className={`px-2.5 py-1 rounded font-semibold ${
                    selectedIndex === sym ? "bg-emerald-600 text-white" : "okz-muted hover:text-[var(--text)]"
                  }`}
                >
                  {sym}
                </button>
              ))}
            </div>
          </div>
          <PriceChart bars={indexBars} mode="area" height={175} />
        </section>
      )}

      <section className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="okz-card p-5 space-y-3">
          <h2 className="font-semibold text-sm flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-emerald-500" /> Top PSX Gainers
          </h2>
          <div className="divide-y divide-[var(--border)]">
            {data.top_gainers.map((s) => (
              <Link
                key={s.symbol}
                href={`/stocks/${s.symbol}`}
                className="py-2.5 flex items-center justify-between hover:opacity-80"
              >
                <div>
                  <div className="font-semibold text-sm flex items-center gap-1.5">
                    {s.symbol}
                    {s.is_shariah_compliant && <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />}
                  </div>
                  <div className="text-xs okz-muted truncate max-w-44">{s.name}</div>
                </div>
                <div className="text-right">
                  <div className="text-sm font-semibold">{formatMoney(s.last_price, currency, usdToPkr)}</div>
                  <div className={`text-xs font-medium ${pctColorClass(s.change_pct)}`}>{formatPct(s.change_pct)}</div>
                </div>
              </Link>
            ))}
          </div>
        </div>

        <div className="okz-card p-5 space-y-3">
          <h2 className="font-semibold text-sm flex items-center gap-2">
            <TrendingDown className="w-4 h-4 text-rose-500" /> Top PSX Losers
          </h2>
          <div className="divide-y divide-[var(--border)]">
            {data.top_losers.map((s) => (
              <Link
                key={s.symbol}
                href={`/stocks/${s.symbol}`}
                className="py-2.5 flex items-center justify-between hover:opacity-80"
              >
                <div>
                  <div className="font-semibold text-sm">{s.symbol}</div>
                  <div className="text-xs okz-muted truncate max-w-44">{s.name}</div>
                </div>
                <div className="text-right">
                  <div className="text-sm font-semibold">{formatMoney(s.last_price, currency, usdToPkr)}</div>
                  <div className={`text-xs font-medium ${pctColorClass(s.change_pct)}`}>{formatPct(s.change_pct)}</div>
                </div>
              </Link>
            ))}
          </div>
        </div>

        <div className="okz-card p-5 space-y-3">
          <h2 className="font-semibold text-sm flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-emerald-500" /> High Dividend Yield Leaders
          </h2>
          <div className="divide-y divide-[var(--border)]">
            {data.top_dividend_yield.map((s) => (
              <Link
                key={s.symbol}
                href={`/stocks/${s.symbol}`}
                className="py-2.5 flex items-center justify-between hover:opacity-80"
              >
                <div>
                  <div className="font-semibold text-sm">{s.symbol}</div>
                  <div className="text-xs okz-muted">P/E {s.pe_ratio ?? "—"}</div>
                </div>
                <div className="text-right">
                  <div className="text-sm font-semibold text-emerald-500">{s.dividend_yield_pct}% Yield</div>
                  <div className="text-xs okz-muted">
                    {s.currency === "USD" ? `$${formatNumber(s.last_price, 2)}` : formatMoney(s.last_price, currency, usdToPkr)}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        </div>
      </section>

      {headlines.length > 0 && (
        <section className="okz-card p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold text-sm flex items-center gap-2">
              <Newspaper className="w-4 h-4 text-sky-400" /> Latest Pakistan Business &amp; Market Headlines
            </h2>
            <Link href="/markets/news" className="text-xs text-emerald-500 hover:underline font-semibold">
              Open Full News Hub &rarr;
            </Link>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {headlines.map((h) => (
              <a
                key={h.id}
                href={h.url}
                target="_blank"
                rel="noopener noreferrer"
                className="p-3 rounded-lg border border-[var(--border)] hover:border-emerald-500/50 bg-[var(--bg-elevated)]/30 space-y-1.5 transition"
              >
                <div className="flex items-center justify-between gap-1 text-[10px]">
                  <span className="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-400 font-semibold">{h.source_name}</span>
                  <span className="okz-muted">{new Date(h.published_at).toLocaleDateString()}</span>
                </div>
                <div className="text-xs font-semibold leading-snug line-clamp-2">{h.title}</div>
                {h.matched_symbols.length > 0 && (
                  <div className="flex flex-wrap gap-1 pt-0.5">
                    {h.matched_symbols.slice(0, 3).map((sym) => (
                      <span key={sym} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">
                        {sym}
                      </span>
                    ))}
                  </div>
                )}
              </a>
            ))}
          </div>
        </section>
      )}

      <section className="okz-card p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-sm flex items-center gap-2">
            <Megaphone className="w-4 h-4 text-amber-500" /> Recent PSX PUCARS Announcements
          </h2>
          <Link href="/stocks/downloads" className="text-xs text-emerald-500 hover:underline font-semibold">
            Filings &amp; Downloads Hub &rarr;
          </Link>
        </div>
        <div className="divide-y divide-[var(--border)]">
          {data.recent_announcements.map((ann) => (
            <div key={ann.id} className="py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div>
                <div className="flex items-center gap-2 flex-wrap">
                  <Link href={`/stocks/${ann.symbol}`} className="font-bold text-xs px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-500">
                    {ann.symbol}
                  </Link>
                  <span className="text-xs px-2 py-0.5 rounded bg-[var(--card-muted)] okz-muted">{ann.category}</span>
                  <span className="font-medium text-sm">{ann.title}</span>
                </div>
                <p className="text-xs okz-muted mt-1">{ann.summary}</p>
              </div>
              <div className="flex items-center gap-2 whitespace-nowrap">
                <span className="text-[11px] okz-muted">
                  {new Date(ann.published_at).toLocaleDateString()}
                </span>
                {ann.source_url && (
                  <>
                    <a
                      href={`/api/v1/market/downloads/proxy?url=${encodeURIComponent(ann.source_url)}&filename=${encodeURIComponent(`${ann.symbol}_${ann.title.slice(0, 36).replace(/[^A-Za-z0-9]+/g, "_")}.pdf`)}`}
                      className="inline-flex items-center gap-1 px-2 py-1 rounded bg-emerald-500/15 text-emerald-400 text-[11px] font-semibold hover:bg-emerald-500/25"
                    >
                      <Download className="w-3 h-3" /> PDF
                    </a>
                    <a
                      href={ann.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[11px] text-sky-400 hover:underline inline-flex items-center gap-0.5"
                    >
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
