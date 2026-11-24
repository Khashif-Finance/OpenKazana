"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Download, ExternalLink, FileSpreadsheet, FileText, Search } from "lucide-react";
import { apiFetch } from "@/lib/api";

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
    const params = new URLSearchParams({ category, limit: "100" });
    if (symbol.trim()) params.set("symbol", symbol.trim().toUpperCase());
    if (q.trim()) params.set("q", q.trim());
    apiFetch<DownloadsPayload>(`/api/v1/market/downloads?${params.toString()}`)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [symbol, category, q]);

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
          <input
            value={csvTicker}
            onChange={(e) => setCsvTicker(e.target.value.toUpperCase())}
            placeholder="MEBL"
            className="w-24 px-2 py-1 rounded border border-[var(--border)] bg-[var(--bg)] font-mono uppercase"
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
            <Search className="w-3.5 h-3.5 okz-muted absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search document title, company name, or AMC..."
              className="w-full pl-8 pr-3 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
            />
          </div>
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="Stock Ticker (e.g. OGDC)"
            className="w-40 px-3 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] font-mono uppercase text-xs"
          />
          {symbol && (
            <button
              type="button"
              onClick={() => setSymbol("")}
              className="px-2.5 py-1.5 rounded-md border border-[var(--border)] okz-muted hover:text-[var(--text)]"
            >
              Clear
            </button>
          )}
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
            Official Downloadable Documents ({data?.total || 0})
          </span>
          <span className="okz-muted">Served directly from official PSX PUCARS &amp; Pakistan AMC archives</span>
        </div>

        {loading && !data ? (
          <div className="p-8 text-center text-sm okz-muted">Loading filings &amp; reports catalog...</div>
        ) : !data || data.items.length === 0 ? (
          <div className="p-8 text-center text-sm okz-muted">No documents matched your search filter.</div>
        ) : (
          <div className="divide-y divide-[var(--border)] text-xs">
            {data.items.map((item) => {
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
