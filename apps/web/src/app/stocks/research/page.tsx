"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  BookOpen,
  BookmarkPlus,
  Building2,
  Download,
  ExternalLink,
  FileText,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { TableSkeleton } from "@/components/Skeletons";
import { TickerAutocomplete } from "@/components/TickerAutocomplete";
import { apiFetch, apiFetchCached, invalidateApiCache } from "@/lib/api";

interface BrokeragePortal {
  broker_key: string;
  broker_name: string;
  short_name: string;
  url: string;
  specialty: string;
  feed_mode: string;
}

interface ResearchReportItem {
  id: string;
  broker_key: string;
  broker_name: string;
  title: string;
  summary: string;
  url: string;
  pdf_url: string | null;
  proxy_download_url: string | null;
  report_type: string;
  stance: string | null;
  target_price: number | null;
  matched_symbols: string[];
  matched_sectors: string[];
  published_at: string;
  is_user_bookmark: boolean;
}

interface ResearchPayload {
  total: number;
  items: ResearchReportItem[];
  brokers: Array<{ broker_key: string; broker_name: string; count: number }>;
  report_types: Array<{ type: string; count: number }>;
  sectors: Array<{ sector: string; count: number }>;
  portals: BrokeragePortal[];
}

function stanceBadgeClass(stance: string | null): string {
  const s = (stance || "").toUpperCase();
  if (s === "BUY" || s === "OVERWEIGHT" || s === "OUTPERFORM") {
    return "bg-emerald-500/15 text-emerald-400 border-emerald-500/30";
  }
  if (s === "SELL" || s === "UNDERWEIGHT" || s === "UNDERPERFORM") {
    return "bg-rose-500/15 text-rose-400 border-rose-500/30";
  }
  return "bg-amber-500/15 text-amber-400 border-amber-500/30";
}

export default function BrokerageResearchPage() {
  const [payload, setPayload] = useState<ResearchPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [brokerFilter, setBrokerFilter] = useState("ALL");
  const [typeFilter, setTypeFilter] = useState("ALL");
  const [sectorFilter, setSectorFilter] = useState("ALL");

  // Custom bookmark form state
  const [showAddForm, setShowAddForm] = useState(false);
  const [bmTitle, setBmTitle] = useState("");
  const [bmUrl, setBmUrl] = useState("");
  const [bmBroker, setBmBroker] = useState("Arif Habib Limited (AHL)");
  const [bmType, setBmType] = useState("COMPANY");
  const [bmStance, setBmStance] = useState("");
  const [bmTp, setBmTp] = useState("");
  const [bmSymbols, setBmSymbols] = useState("");
  const [bmSummary, setBmSummary] = useState("");
  const [savingBm, setSavingBm] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const sp = new URLSearchParams(window.location.search);
      const sym = sp.get("symbol");
      if (sym) setSymbolFilter(sym.toUpperCase());
      const brk = sp.get("broker");
      if (brk) setBrokerFilter(brk.toLowerCase());
    }
  }, []);

  const loadReports = useCallback((forceRefresh = false) => {
    setLoading(true);
    apiFetchCached<ResearchPayload>("/api/v1/market/research?limit=250", 120_000, {
      forceRefresh,
      onUpdate: setPayload,
    })
      .then(setPayload)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadReports(false);
  }, [loadReports]);

  const handleRefreshFeeds = async () => {
    setRefreshing(true);
    setNotice(null);
    try {
      const res = await apiFetch<{ ok: boolean; reports_added: number }>("/api/v1/market/research/refresh", {
        method: "POST",
      });
      invalidateApiCache("/api/v1/market/research");
      loadReports(true);
      setNotice(`Synced brokerage research feeds (${res.reports_added} new report${res.reports_added === 1 ? "" : "s"} added).`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Failed to refresh brokerage research feeds.");
    } finally {
      setRefreshing(false);
    }
  };

  const handleSaveBookmark = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!bmTitle.trim() || !bmUrl.trim()) return;
    setSavingBm(true);
    setNotice(null);
    try {
      const matchedPortal = payload?.portals.find((p) => p.broker_name === bmBroker);
      await apiFetch("/api/v1/market/research/bookmark", {
        method: "POST",
        body: JSON.stringify({
          title: bmTitle.trim(),
          url: bmUrl.trim(),
          broker_name: bmBroker.trim() || "Custom Brokerage",
          broker_key: matchedPortal?.broker_key || "custom",
          report_type: bmType,
          stance: bmStance || null,
          target_price: bmTp ? Number(bmTp) : null,
          symbols: bmSymbols
            .split(/[,\s]+/)
            .map((s) => s.trim().toUpperCase())
            .filter(Boolean),
          summary: bmSummary.trim(),
        }),
      });
      setBmTitle("");
      setBmUrl("");
      setBmStance("");
      setBmTp("");
      setBmSymbols("");
      setBmSummary("");
      setShowAddForm(false);
      invalidateApiCache("/api/v1/market/research");
      loadReports(true);
      setNotice("Saved custom brokerage research report & tagged to stocks.");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Failed to save bookmark.");
    } finally {
      setSavingBm(false);
    }
  };

  const handleDeleteBookmark = async (id: string) => {
    try {
      await apiFetch(`/api/v1/market/research/bookmark/${encodeURIComponent(id)}`, { method: "DELETE" });
      invalidateApiCache("/api/v1/market/research");
      loadReports(true);
    } catch {
      // ignore
    }
  };

  const countByBroker = useMemo(() => {
    const m = new Map<string, number>();
    for (const b of payload?.brokers || []) {
      m.set(b.broker_key.toLowerCase(), b.count);
    }
    return m;
  }, [payload]);

  const filteredItems = useMemo(() => {
    const items = payload?.items || [];
    const qLow = q.trim().toLowerCase();
    const symU = symbolFilter.trim().toUpperCase();
    return items.filter((rep) => {
      if (brokerFilter !== "ALL" && rep.broker_key.toLowerCase() !== brokerFilter.toLowerCase()) return false;
      if (typeFilter !== "ALL" && rep.report_type.toUpperCase() !== typeFilter.toUpperCase()) return false;
      if (sectorFilter !== "ALL" && !rep.matched_sectors.some((s) => s.toLowerCase() === sectorFilter.toLowerCase())) {
        return false;
      }
      if (symU) {
        const hitSym =
          rep.matched_symbols.some((s) => s.toUpperCase() === symU) ||
          rep.title.toUpperCase().includes(symU);
        if (!hitSym) return false;
      }
      if (qLow) {
        const hay = `${rep.title} ${rep.summary} ${rep.broker_name} ${rep.matched_symbols.join(" ")} ${rep.matched_sectors.join(" ")}`.toLowerCase();
        if (!hay.includes(qLow)) return false;
      }
      return true;
    });
  }, [payload, q, symbolFilter, brokerFilter, typeFilter, sectorFilter]);

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <BookOpen className="w-6 h-6 text-emerald-500" />
            Pakistan Brokerage Equity Research Hub
          </h1>
          <p className="text-sm okz-muted">
            Live research reports, corporate briefing notes, valuation matrices &amp; direct portals from AHL, SCS Trade,
            Intermarket Securities (IMS), KASB / KTrade &amp; Chase Securities — auto-tagged to PSX tickers &amp; sectors.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={() => setShowAddForm((v) => !v)}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 text-xs font-semibold"
          >
            <BookmarkPlus className="w-3.5 h-3.5" />
            {showAddForm ? "Close Form" : "Bookmark Report / PDF"}
          </button>
          <button
            type="button"
            onClick={handleRefreshFeeds}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-60 text-white text-xs font-semibold"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "Syncing Desks..." : "Sync Brokerage Feeds"}
          </button>
        </div>
      </div>

      {notice && (
        <div className="px-4 py-2.5 rounded-lg text-xs bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
          {notice}
        </div>
      )}

      {/* 5-Brokerage Institutional Portal Directory */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
        {(payload?.portals || []).map((p) => {
          const cnt = countByBroker.get(p.broker_key.toLowerCase()) || 0;
          const active = brokerFilter === p.broker_key;
          return (
            <div
              key={p.broker_key}
              className={`okz-card p-3.5 flex flex-col justify-between gap-2.5 transition ${
                active ? "border-emerald-500 bg-emerald-500/5" : "hover:border-emerald-500/40"
              }`}
            >
              <div className="space-y-1">
                <div className="flex items-center justify-between gap-1.5">
                  <span className="text-xs font-bold flex items-center gap-1.5">
                    <Building2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                    <span className="truncate">{p.short_name}</span>
                  </span>
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded font-semibold shrink-0 ${
                      cnt > 0
                        ? "bg-emerald-500/15 text-emerald-400"
                        : "bg-sky-500/15 text-sky-400"
                    }`}
                  >
                    {cnt > 0 ? `${cnt} Live` : "Portal"}
                  </span>
                </div>
                <p className="text-[11px] okz-muted leading-snug line-clamp-2">{p.specialty}</p>
              </div>
              <div className="flex items-center justify-between gap-2 pt-1 border-t border-[var(--border)]/70">
                <button
                  type="button"
                  onClick={() => setBrokerFilter((cur) => (cur === p.broker_key ? "ALL" : p.broker_key))}
                  className={`text-[11px] font-semibold ${
                    active ? "text-emerald-400" : "okz-muted hover:text-[var(--text)]"
                  }`}
                >
                  {active ? "Showing Only ✓" : cnt > 0 ? "Filter Feed" : "Show Bookmarks"}
                </button>
                <a
                  href={p.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-[11px] font-semibold text-sky-400 hover:underline"
                >
                  Open Desk <ExternalLink className="w-3 h-3" />
                </a>
              </div>
            </div>
          );
        })}
      </div>

      {/* Collapsible Custom Research Report Bookmark Form */}
      {showAddForm && (
        <form onSubmit={handleSaveBookmark} className="okz-card p-5 space-y-4 border-emerald-500/40">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-bold">Bookmark External Brokerage Report or PDF</h2>
              <p className="text-xs okz-muted">
                Save IMS, AHL, SCS, KASB, Chase, or any analyst PDF link and auto-surface it inside tagged `/stocks/[symbol]` pages.
              </p>
            </div>
            <button type="button" onClick={() => setShowAddForm(false)} className="okz-muted hover:text-[var(--text)]">
              <X className="w-4 h-4" />
            </button>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
            <div className="lg:col-span-2">
              <label className="block okz-muted mb-1">Report Title *</label>
              <input
                required
                value={bmTitle}
                onChange={(e) => setBmTitle(e.target.value)}
                placeholder="e.g. IMS Research — MEBL 3QCY26 Result Review (BUY, TP Rs 320)"
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)]"
              />
            </div>
            <div className="lg:col-span-2">
              <label className="block okz-muted mb-1">Report or PDF URL *</label>
              <input
                required
                type="url"
                value={bmUrl}
                onChange={(e) => setBmUrl(e.target.value)}
                placeholder="https://imsecurities.com.pk/..."
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)]"
              />
            </div>
            <div>
              <label className="block okz-muted mb-1">Brokerage House</label>
              <select
                value={bmBroker}
                onChange={(e) => setBmBroker(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)]"
              >
                <option value="Arif Habib Limited (AHL)">Arif Habib Limited (AHL)</option>
                <option value="Standard Capital Securities (SCS Trade)">Standard Capital (SCS Trade)</option>
                <option value="Intermarket Securities (IMS)">Intermarket Securities (IMS)</option>
                <option value="KASB / KTrade Securities">KASB / KTrade Securities</option>
                <option value="Chase Securities">Chase Securities</option>
                <option value="Custom Brokerage / Analyst">Other / Custom Analyst</option>
              </select>
            </div>
            <div>
              <label className="block okz-muted mb-1">Tag PSX Tickers (comma-separated)</label>
              <input
                value={bmSymbols}
                onChange={(e) => setBmSymbols(e.target.value)}
                placeholder="e.g. MEBL, LUCK, OGDC"
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] font-mono"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block okz-muted mb-1">Stance</label>
                <select
                  value={bmStance}
                  onChange={(e) => setBmStance(e.target.value)}
                  className="w-full px-2 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)]"
                >
                  <option value="">Auto</option>
                  <option value="BUY">BUY</option>
                  <option value="OVERWEIGHT">OVERWEIGHT</option>
                  <option value="HOLD">HOLD</option>
                  <option value="UNDERWEIGHT">UNDERWEIGHT</option>
                  <option value="SELL">SELL</option>
                </select>
              </div>
              <div>
                <label className="block okz-muted mb-1">Target Price (Rs)</label>
                <input
                  type="number"
                  step="0.01"
                  value={bmTp}
                  onChange={(e) => setBmTp(e.target.value)}
                  placeholder="320"
                  className="w-full px-2 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] font-mono"
                />
              </div>
            </div>
            <div>
              <label className="block okz-muted mb-1">Report Type</label>
              <select
                value={bmType}
                onChange={(e) => setBmType(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)]"
              >
                <option value="COMPANY">Company Note</option>
                <option value="CORPORATE_BRIEFING">Corporate Briefing</option>
                <option value="SECTOR">Sector Review</option>
                <option value="STRATEGY">Market Strategy</option>
                <option value="ECONOMY">Macro / Economy</option>
                <option value="COMMODITIES">PMEX / Commodities</option>
              </select>
            </div>
            <div className="lg:col-span-4">
              <label className="block okz-muted mb-1">Key Takeaways / Summary</label>
              <input
                value={bmSummary}
                onChange={(e) => setBmSummary(e.target.value)}
                placeholder="Optional analyst thesis, EPS estimate or catalyst notes..."
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)]"
              />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="px-3 py-1.5 rounded-md border border-[var(--border)] text-xs"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={savingBm}
              className="px-4 py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold"
            >
              {savingBm ? "Saving..." : "Save Report Bookmark"}
            </button>
          </div>
        </form>
      )}

      {/* Instant 0ms Filter Bar */}
      <div className="okz-card p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-3 items-end">
        <div className="lg:col-span-2">
          <label className="block text-xs okz-muted mb-1">Keyword Search</label>
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-2.5 okz-muted pointer-events-none" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search title, thesis, valuation, briefing..."
              className="w-full pl-9 pr-8 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
            />
            {q && (
              <button
                type="button"
                onClick={() => setQ("")}
                className="absolute right-2.5 top-2 okz-muted hover:text-[var(--text)]"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        <div>
          <label className="block text-xs okz-muted mb-1">Filter by Stock Ticker</label>
          <TickerAutocomplete
            value={symbolFilter}
            onChange={(v) => setSymbolFilter(v.toUpperCase())}
            onSelect={(item) => setSymbolFilter(item.symbol)}
            assetClasses={["PSX_EQUITY", "PSX_ETF"]}
            placeholder="e.g. MEBL, LUCK, NPL..."
            inputClassName="py-2"
          />
        </div>

        <div>
          <label className="block text-xs okz-muted mb-1">Brokerage House</label>
          <select
            value={brokerFilter}
            onChange={(e) => setBrokerFilter(e.target.value)}
            className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
          >
            <option value="ALL">All Brokerages ({payload?.items.length || 0})</option>
            {(payload?.brokers || []).map((b) => (
              <option key={b.broker_key} value={b.broker_key}>
                {b.broker_name} ({b.count})
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs okz-muted mb-1">Report Type</label>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
          >
            <option value="ALL">All Types</option>
            {(payload?.report_types || []).map((t) => (
              <option key={t.type} value={t.type}>
                {t.type.replace("_", " ")} ({t.count})
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs okz-muted mb-1">PSX Sector</label>
          <select
            value={sectorFilter}
            onChange={(e) => setSectorFilter(e.target.value)}
            className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
          >
            <option value="ALL">All Sectors</option>
            {(payload?.sectors || []).map((s) => (
              <option key={s.sector} value={s.sector}>
                {s.sector} ({s.count})
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 text-xs okz-muted">
        <span>
          Showing <strong className="text-[var(--text)]">{filteredItems.length}</strong> brokerage research reports
          {symbolFilter ? ` matching ticker ${symbolFilter}` : ""}
        </span>
        {(q || symbolFilter || brokerFilter !== "ALL" || typeFilter !== "ALL" || sectorFilter !== "ALL") && (
          <button
            type="button"
            onClick={() => {
              setQ("");
              setSymbolFilter("");
              setBrokerFilter("ALL");
              setTypeFilter("ALL");
              setSectorFilter("ALL");
            }}
            className="text-rose-400 hover:underline font-semibold"
          >
            Reset Filters
          </button>
        )}
      </div>

      {loading && !payload ? (
        <TableSkeleton rows={8} cols={4} />
      ) : filteredItems.length === 0 ? (
        <div className="okz-card p-8 text-center space-y-2">
          <FileText className="w-8 h-8 text-emerald-500 mx-auto opacity-60" />
          <div className="text-sm font-semibold">No research reports matched your filters</div>
          <p className="text-xs okz-muted">
            Try clearing the ticker or brokerage filter, click &ldquo;Sync Brokerage Feeds&rdquo;, or bookmark an external PDF report above.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filteredItems.map((rep) => (
            <div
              key={rep.id}
              className="okz-card p-4 flex flex-col justify-between gap-3 hover:border-emerald-500/40 transition"
            >
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="px-2 py-0.5 rounded bg-sky-500/15 text-sky-400 text-[10px] font-bold">
                      {rep.broker_name}
                    </span>
                    <span className="px-1.5 py-0.5 rounded bg-[var(--card-muted)] okz-muted text-[10px] font-semibold">
                      {rep.report_type.replace("_", " ")}
                    </span>
                    {rep.stance && (
                      <span
                        className={`px-1.5 py-0.5 rounded border text-[10px] font-bold ${stanceBadgeClass(rep.stance)}`}
                      >
                        {rep.stance}
                      </span>
                    )}
                    {rep.target_price != null && (
                      <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 text-[10px] font-mono font-bold">
                        TP Rs {rep.target_price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                      </span>
                    )}
                    {rep.is_user_bookmark && (
                      <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 text-[10px] font-semibold">
                        Bookmarked
                      </span>
                    )}
                  </div>
                  <span className="text-[10px] okz-muted font-mono">
                    {new Date(rep.published_at).toLocaleDateString()}
                  </span>
                </div>

                <a
                  href={rep.url}
                  target="_blank"
                  rel="noreferrer"
                  className="block text-sm font-bold leading-snug hover:text-emerald-400 transition"
                >
                  {rep.title}
                </a>

                {rep.summary && (
                  <p className="text-xs okz-muted leading-relaxed line-clamp-3">{rep.summary}</p>
                )}

                {(rep.matched_symbols.length > 0 || rep.matched_sectors.length > 0) && (
                  <div className="flex flex-wrap items-center gap-1.5 pt-1">
                    {rep.matched_symbols.map((sym) => (
                      <Link
                        key={sym}
                        href={`/stocks/${encodeURIComponent(sym)}`}
                        className="px-2 py-0.5 rounded bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 font-mono text-[11px] font-bold"
                      >
                        ${sym}
                      </Link>
                    ))}
                    {rep.matched_sectors.slice(0, 3).map((sec) => (
                      <button
                        key={sec}
                        type="button"
                        onClick={() => setSectorFilter(sec)}
                        className="px-2 py-0.5 rounded bg-[var(--card-muted)] hover:text-[var(--text)] okz-muted text-[10px]"
                      >
                        {sec}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between gap-2 pt-2.5 border-t border-[var(--border)]/70 text-xs">
                <div className="flex items-center gap-2">
                  {rep.proxy_download_url && (
                    <a
                      href={rep.proxy_download_url}
                      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-emerald-600/15 hover:bg-emerald-600/25 text-emerald-400 font-semibold"
                    >
                      <Download className="w-3 h-3" /> Download PDF
                    </a>
                  )}
                  <a
                    href={rep.pdf_url || rep.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-[var(--border)] hover:border-sky-500 text-sky-400 font-semibold"
                  >
                    {rep.pdf_url ? "Open PDF" : "Read Report"} <ExternalLink className="w-3 h-3" />
                  </a>
                </div>
                {rep.is_user_bookmark && (
                  <button
                    type="button"
                    onClick={() => handleDeleteBookmark(rep.id)}
                    title="Delete custom bookmark"
                    className="p-1.5 rounded text-rose-400 hover:bg-rose-500/10"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
