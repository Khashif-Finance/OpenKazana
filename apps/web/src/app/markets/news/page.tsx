"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ExternalLink, Newspaper, RefreshCw, Search, X } from "lucide-react";
import { CardListSkeleton } from "@/components/Skeletons";
import { TickerAutocomplete } from "@/components/TickerAutocomplete";
import { apiFetch, apiFetchCached, invalidateApiCache } from "@/lib/api";

interface NewsItem {
  id: string;
  title: string;
  summary: string;
  url: string;
  source_key: string;
  source_name: string;
  category: string;
  matched_symbols: string[];
  matched_sectors: string[];
  published_at: string;
}

interface NewsPayload {
  total: number;
  items: NewsItem[];
  sources: Array<{ source_key: string; source_name: string; count: number }>;
  categories: Array<{ category: string; count: number }>;
  sectors: Array<{ sector: string; count: number }>;
}

export default function PakistanNewsHubPage() {
  const [data, setData] = useState<NewsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [source, setSource] = useState("ALL");
  const [category, setCategory] = useState("ALL");
  const [sector, setSector] = useState("ALL");
  const [symbol, setSymbol] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    if (typeof window !== "undefined") {
      const sp = new URLSearchParams(window.location.search);
      if (sp.get("symbol")) setSymbol(sp.get("symbol") || "");
      if (sp.get("sector")) setSector(sp.get("sector") || "ALL");
    }
  }, []);

  const fetchNews = useCallback(async (forceRefresh = false) => {
    setLoading(true);
    try {
      const res = await apiFetchCached<NewsPayload>("/api/v1/market/news?limit=150", 60_000, {
        forceRefresh,
        onUpdate: setData,
      });
      setData(res);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchNews(false);
  }, [fetchNews]);

  const handleRefreshFeeds = async () => {
    setRefreshing(true);
    try {
      await apiFetch("/api/v1/market/news/refresh", { method: "POST" });
      invalidateApiCache("/api/v1/market/news");
      await fetchNews(true);
    } finally {
      setRefreshing(false);
    }
  };

  // Instant 0ms client-side filtering across all loaded articles
  const filteredItems = useMemo(() => {
    const items = data?.items || [];
    const qLow = q.trim().toLowerCase();
    const symUp = symbol.trim().toUpperCase();
    const secLow = sector !== "ALL" ? sector.toLowerCase() : "";
    const srcLow = source !== "ALL" ? source.toLowerCase() : "";
    const catLow = category !== "ALL" ? category.toLowerCase() : "";

    return items.filter((art) => {
      if (srcLow && art.source_key.toLowerCase() !== srcLow) return false;
      if (catLow && art.category.toLowerCase() !== catLow) return false;
      if (symUp) {
        const hitSym =
          art.matched_symbols.some((s) => s.toUpperCase() === symUp) ||
          art.title.toUpperCase().includes(symUp);
        if (!hitSym) return false;
      }
      if (secLow && !art.matched_sectors.some((s) => s.toLowerCase().includes(secLow))) return false;
      if (qLow && !art.title.toLowerCase().includes(qLow) && !(art.summary || "").toLowerCase().includes(qLow)) {
        return false;
      }
      return true;
    });
  }, [data, source, category, sector, symbol, q]);

  return (
    <div className="space-y-6">
      <div className="okz-card p-6 bg-gradient-to-br from-sky-500/10 via-transparent to-emerald-500/10 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Newspaper className="w-6 h-6 text-sky-400" />
            Pakistan Financial News Hub
          </h1>
          <p className="text-sm okz-muted">
            Live RSS coverage from Business Recorder, Profit (Pakistan Today), Dawn Business, Express Tribune &amp; Investing.com — auto-tagged to 501 PSX tickers and 37 official sectors.
          </p>
        </div>
        <button
          type="button"
          onClick={handleRefreshFeeds}
          disabled={refreshing}
          className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold inline-flex items-center gap-1.5 self-start disabled:opacity-60"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
          {refreshing ? "Fetching Live Feeds..." : "Refresh Live Feeds"}
        </button>
      </div>

      {/* Filters */}
      <div className="okz-card p-4 space-y-3 text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-52">
            <Search className="w-3.5 h-3.5 okz-muted absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Instant filter headlines (SBP, IMF, cement, circular debt, dividend)..."
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
          <TickerAutocomplete
            value={symbol}
            onChange={(val) => setSymbol(val.toUpperCase())}
            onSelect={(item) => setSymbol(item.symbol)}
            placeholder="Ticker (e.g. MEBL)..."
            className="w-44"
          />
          <select
            value={sector}
            onChange={(e) => setSector(e.target.value)}
            className="px-3 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs max-w-60"
          >
            <option value="ALL">All Sectors</option>
            {(data?.sectors || []).map((s) => (
              <option key={s.sector} value={s.sector}>
                {s.sector} ({s.count})
              </option>
            ))}
          </select>
        </div>

        {/* Source & Category Chips */}
        <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-[var(--border)]">
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => setSource("ALL")}
              className={`px-2.5 py-1 rounded-md font-semibold ${
                source === "ALL" ? "bg-emerald-600 text-white" : "bg-[var(--card-muted)] okz-muted"
              }`}
            >
              All Sources
            </button>
            {(data?.sources || []).map((src) => (
              <button
                key={src.source_key}
                type="button"
                onClick={() => setSource(src.source_key)}
                className={`px-2.5 py-1 rounded-md font-semibold ${
                  source === src.source_key ? "bg-sky-600 text-white" : "bg-[var(--card-muted)] okz-muted hover:text-[var(--text)]"
                }`}
              >
                {src.source_name} ({src.count})
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            {["ALL", "Markets", "Corporate", "Macro", "Economy", "IPO", "Commodities"].map((cat) => (
              <button
                key={cat}
                type="button"
                onClick={() => setCategory(cat)}
                className={`px-2 py-1 rounded-md font-semibold ${
                  category === cat ? "bg-amber-500/20 text-amber-400 border border-amber-500/40" : "okz-muted hover:text-[var(--text)]"
                }`}
              >
                {cat}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Articles Grid */}
      {loading && !data ? (
        <CardListSkeleton count={6} cols="grid-cols-1 md:grid-cols-2" />
      ) : filteredItems.length === 0 ? (
        <div className="okz-card p-8 text-center text-sm okz-muted">
          No news articles matched your filters. Clear filters or click &ldquo;Refresh Live Feeds&rdquo;.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filteredItems.map((art) => (
            <article key={art.id} className="okz-card p-5 flex flex-col justify-between gap-3 hover:border-emerald-500/40 transition">
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2 text-[11px]">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="px-2 py-0.5 rounded bg-sky-500/15 text-sky-400 font-semibold">{art.source_name}</span>
                    <span className="px-2 py-0.5 rounded bg-[var(--card-muted)] okz-muted font-medium">{art.category}</span>
                  </div>
                  <span className="okz-muted whitespace-nowrap">
                    {new Date(art.published_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
                  </span>
                </div>
                <a
                  href={art.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block font-bold text-sm leading-snug hover:text-emerald-400 transition"
                >
                  {art.title}
                </a>
                {art.summary && <p className="text-xs okz-muted leading-relaxed line-clamp-3">{art.summary}</p>}
              </div>

              <div className="pt-2 border-t border-[var(--border)] flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1">
                  {art.matched_symbols.map((sym) => (
                    <Link
                      key={sym}
                      href={`/stocks/${encodeURIComponent(sym)}`}
                      className="text-[10px] font-mono font-bold px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25"
                    >
                      {sym}
                    </Link>
                  ))}
                  {art.matched_sectors.slice(0, 2).map((sec) => (
                    <button
                      key={sec}
                      type="button"
                      onClick={() => setSector(sec)}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--card-muted)] okz-muted hover:text-[var(--text)]"
                    >
                      {sec}
                    </button>
                  ))}
                </div>
                <a
                  href={art.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-sky-400 hover:underline inline-flex items-center gap-1 font-semibold"
                >
                  Read <ExternalLink className="w-3 h-3" />
                </a>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
