"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Download, ExternalLink, FileSpreadsheet, FileText, Search, X } from "lucide-react";
import { TableSkeleton } from "@/components/Skeletons";
import { TickerAutocomplete } from "@/components/TickerAutocomplete";
import { apiFetchCached } from "@/lib/api";

interface DownloadItem {
  id: string;
  symbol: string;
  company_name: string;
  sector: string;
  doc_type: string;
  title: string;
  published_date: string;
  source_url: string;
  proxy_download_url: string;
  csv_export_url: string | null;
  format: string;
}

interface DownloadsPayload {
  total: number;
  items: DownloadItem[];
}

const DOC_BADGES: Record<string, { label: string; cls: string }> = {
  ANNUAL_QUARTERLY: { label: "Annual / Qtr Report", cls: "bg-emerald-500/15 text-emerald-400" },
  RESULTS: { label: "Financial Results", cls: "bg-sky-500/15 text-sky-400" },
  DIVIDEND: { label: "Dividend / Payout", cls: "bg-amber-500/15 text-amber-400" },
  BOARD_MEETING: { label: "Board Meeting", cls: "bg-purple-500/15 text-purple-400" },
  MATERIAL: { label: "PUCARS Material", cls: "bg-[var(--card-muted)] okz-muted" },
  FMR: { label: "SECP FMR PDF", cls: "bg-indigo-500/15 text-indigo-400" },
};

export default function FilingsDownloadsPage() {
  const [data, setData] = useState<DownloadsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [symbol, setSymbol] = useState("");
  const [category, setCategory] = useState("ALL");
  const [q, setQ] = useState("");
  const [csvTicker, setCsvTicker] = useState("MEBL");

  useEffect(() => {
    if (typeof window !== "undefined") {
      const sp = new URLSearchParams(window.location.search);
      const sym = sp.get("symbol");
      if (sym) {
        setSymbol(sym.toUpperCase());
        setCsvTicker(sym.toUpperCase());
      }
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    const symParam = symbol.trim().toUpperCase();
    const url = symParam
      ? `/api/v1/market/downloads?limit=200&symbol=${encodeURIComponent(symParam)}`
      : "/api/v1/market/downloads?limit=200";
    apiFetchCached<DownloadsPayload>(url, 120_000, { onUpdate: setData })
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [symbol]);

  // Instant 0ms client-side filter for category & free-text search
  const filteredItems = useMemo(() => {
    const qLow = q.trim().toLowerCase();
    return (data?.items || []).filter((item) => {
      if (category !== "ALL" && item.doc_type !== category) return false;
      if (
        qLow &&
        !item.title.toLowerCase().includes(qLow) &&
        !item.symbol.toLowerCase().includes(qLow) &&
        !item.company_name.toLowerCase().includes(qLow)
      ) {
        return false;
      }
      return true;
    });
  }, [data, category, q]);

  return (
    <div className="space-y-6">
      <div className="okz-card p-6 bg-gradient-to-br from-emerald-500/10 via-transparent to-sky-500/10 flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Download className="w-6 h-6 text-emerald-500" />
            Filings, Reports &amp; Financial Downloads Center
          </h1>
          <p className="text-sm okz-muted">
            One-click download hub for official PSX PUCARS Announcement PDFs, Annual &amp; Quarterly Company Reports, SECP Monthly AMC Fund Manager Reports (FMRs), and 3-statement CSV spreadsheets.
          </p>
        </div>

        {/* Instant 3-Statement CSV Export Widget */}
        <div className="p-3 rounded-lg border border-[var(--border)] bg-[var(--card)] flex flex-wrap items-center gap-2 text-xs shrink-0">
          <FileSpreadsheet className="w-4 h-4 text-emerald-500" />
          <span className="font-semibold">Export 3-Statement CSV:</span>
          <TickerAutocomplete
            value={csvTicker}
            onChange={(val) => setCsvTicker(val.toUpperCase())}
            onSelect={(item) => setCsvTicker(item.symbol)}
            assetClasses={["PSX_EQUITY", "INTL_EQUITY"]}
            placeholder="MEBL..."
            className="w-40"
          />
          <a
            href={`/api/v1/market/stocks/${encodeURIComponent(csvTicker.trim() || "MEBL")}/reports/export`}
            download={`${csvTicker.trim() || "MEBL"}_financial_statements.csv`}
            className="px-3 py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white font-semibold inline-flex items-center gap-1"
          >
            <Download className="w-3.5 h-3.5" /> CSV
          </a>
        </div>
      </div>

      {/* Search & Category Filter */}
      <div className="okz-card p-4 space-y-3 text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-56">
            <Search className="w-3.5 h-3.5 okz-muted absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Instant filter document title, company name, or AMC..."
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
            placeholder="Filter stock (e.g. OGDC, Meezan)..."
            className="w-56"
          />
        </div>

        <div className="flex flex-wrap items-center gap-1.5 pt-2 border-t border-[var(--border)]">
          {[
            ["ALL", "All Documents"],
            ["ANNUAL_QUARTERLY", "Annual & Quarterly Reports"],
            ["RESULTS", "Financial Results"],
            ["DIVIDEND", "Dividends & Payouts"],
            ["BOARD_MEETING", "Board Meetings"],
            ["MATERIAL", "Material Notices"],
            ["FMR", "AMC SECP FMRs"],
          ].map(([k, lbl]) => (
            <button
              key={k}
              type="button"
              onClick={() => setCategory(k)}
              className={`px-2.5 py-1.5 rounded-md font-semibold ${
                category === k ? "bg-emerald-600 text-white" : "bg-[var(--card-muted)] okz-muted hover:text-[var(--text)]"
              }`}
            >
              {lbl}
            </button>
          ))}
        </div>
      </div>

      {/* Results Table */}
      <div className="okz-card overflow-hidden">
        <div className="px-5 py-3 border-b border-[var(--border)] flex items-center justify-between text-xs">
          <span className="font-semibold flex items-center gap-1.5">
            <FileText className="w-4 h-4 text-sky-400" />
            Official Downloadable Documents ({filteredItems.length})
          </span>
          <span className="okz-muted">Served directly from official PSX PUCARS &amp; Pakistan AMC archives</span>
        </div>

        {loading && !data ? (
          <TableSkeleton rows={8} cols={4} />
        ) : filteredItems.length === 0 ? (
          <div className="p-8 text-center text-sm okz-muted">No documents matched your search filter.</div>
        ) : (
          <div className="divide-y divide-[var(--border)] text-xs">
            {filteredItems.map((item) => {
              const badge = DOC_BADGES[item.doc_type] || DOC_BADGES.MATERIAL;
              return (
                <div
                  key={item.id}
                  className="px-5 py-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-3 hover:bg-[var(--card-muted)]/30"
                >
                  <div className="space-y-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      {item.doc_type !== "FMR" ? (
                        <Link
                          href={`/stocks/${encodeURIComponent(item.symbol)}`}
                          className="font-mono font-bold px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 hover:underline"
                        >
                          {item.symbol}
                        </Link>
                      ) : (
                        <Link
                          href="/funds?fmr_only=true"
                          className="font-mono font-bold px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-400 hover:underline"
                        >
                          {item.symbol}
                        </Link>
                      )}
                      <span className={`text-[10px] px-2 py-0.5 rounded font-semibold ${badge.cls}`}>{badge.label}</span>
                      <span className="text-[11px] font-mono okz-muted">{item.published_date}</span>
                    </div>
                    <div className="font-semibold text-sm leading-snug">{item.title}</div>
                    <div className="text-[11px] okz-muted">
                      {item.company_name} &middot; {item.sector}
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5 shrink-0">
                    <a
                      href={item.proxy_download_url}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white font-semibold whitespace-nowrap"
                    >
                      <Download className="w-3.5 h-3.5" /> Download PDF
                    </a>
                    {item.csv_export_url && (
                      <a
                        href={item.csv_export_url}
                        download={`${item.symbol}_financials.csv`}
                        title="Download company 3-statement CSV"
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md border border-[var(--border)] hover:border-emerald-500 font-semibold"
                      >
                        <FileSpreadsheet className="w-3.5 h-3.5 text-sky-400" /> CSV
                      </a>
                    )}
                    <a
                      href={item.source_url}
                      target="_blank"
                      rel="noreferrer"
                      title="Open original document URL"
                      className="p-1.5 rounded-md border border-[var(--border)] hover:border-sky-500 text-sky-400"
                    >
                      <ExternalLink className="w-3.5 h-3.5" />
                    </a>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
