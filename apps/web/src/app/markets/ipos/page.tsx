"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Download, ExternalLink, FileText, Megaphone, Newspaper, Rocket, ShieldCheck } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { formatNumber, formatPct, pctColorClass } from "@/lib/format";

interface IpoRow {
  id: string;
  company_name: string;
  symbol: string | null;
  sector: string;
  board: string;
  status: string;
  issue_type: string;
  floor_price: number | null;
  strike_price: number | null;
  issue_size_mn: number | null;
  subscription_multiple: number | null;
  book_building_date: string | null;
  listing_date: string | null;
  lead_manager: string | null;
  prospectus_url: string | null;
  source_url: string | null;
  summary: string;
  is_shariah_compliant: boolean;
  live_price: number | null;
  live_change_pct: number | null;
  post_ipo_return_pct: number | null;
  in_database: boolean;
}

interface DevelopmentRow {
  id: string;
  symbol: string;
  company_name: string;
  sector: string;
  title: string;
  category: string;
  doc_type: string;
  published_at: string;
  source_url: string | null;
  summary: string;
}

interface IpoPayload {
  summary: {
    total_tracked: number;
    main_board_count: number;
    gem_board_count: number;
    upcoming_count: number;
    avg_oversubscription_x: number | null;
    avg_post_ipo_return_pct: number | null;
  };
  ipos: IpoRow[];
  developments: DevelopmentRow[];
  ipo_news: Array<{ id: string; title: string; summary: string; url: string; source_name: string; published_at: string }>;
  guides: Array<{ title: string; category: string; url: string }>;
}

export default function IposAndDevelopmentsPage() {
  const [data, setData] = useState<IpoPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"ipos" | "developments" | "guides">("ipos");
  const [boardFilter, setBoardFilter] = useState<"ALL" | "MAIN" | "GEM">("ALL");
  const [devType, setDevType] = useState<string>("ALL");

  useEffect(() => {
    setLoading(true);
    apiFetch<IpoPayload>(`/api/v1/market/ipos?board=${boardFilter}`)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [boardFilter]);

  const filteredDevs = (data?.developments || []).filter((d) => devType === "ALL" || d.doc_type === devType);

  return (
    <div className="space-y-6">
      <div className="okz-card p-6 bg-gradient-to-br from-emerald-500/10 via-transparent to-amber-500/10 space-y-4">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
              <Rocket className="w-6 h-6 text-emerald-500" />
              PSX IPOs &amp; Latest Corporate Developments
            </h1>
            <p className="text-sm okz-muted">
              Track Main &amp; GEM Board IPOs with live Post-IPO Return % vs Strike Price, official PUCARS material disclosures, and PSX listing guides.
            </p>
          </div>
          <Link
            href="/markets/calendar"
            className="px-3.5 py-2 rounded-lg border border-[var(--border)] hover:border-emerald-500 text-xs font-semibold self-start"
          >
            Open Corporate Calendar &rarr;
          </Link>
        </div>

        {data && (
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 text-xs">
            <div className="p-3 rounded-lg bg-[var(--card)] border border-[var(--border)]">
              <div className="okz-muted text-[10px]">Tracked IPOs / Listings</div>
              <div className="text-lg font-bold font-mono">{data.summary.total_tracked}</div>
            </div>
            <div className="p-3 rounded-lg bg-[var(--card)] border border-[var(--border)]">
              <div className="okz-muted text-[10px]">Main / GEM Board</div>
              <div className="text-lg font-bold font-mono">
                {data.summary.main_board_count} / {data.summary.gem_board_count}
              </div>
            </div>
            <div className="p-3 rounded-lg bg-[var(--card)] border border-[var(--border)]">
              <div className="okz-muted text-[10px]">Avg Oversubscription</div>
              <div className="text-lg font-bold font-mono text-sky-400">
                {data.summary.avg_oversubscription_x != null ? `${data.summary.avg_oversubscription_x}x` : "—"}
              </div>
            </div>
            <div className="p-3 rounded-lg bg-[var(--card)] border border-[var(--border)]">
              <div className="okz-muted text-[10px]">Avg Live Post-IPO Return</div>
              <div className={`text-lg font-bold font-mono ${pctColorClass(data.summary.avg_post_ipo_return_pct)}`}>
                {data.summary.avg_post_ipo_return_pct != null ? formatPct(data.summary.avg_post_ipo_return_pct) : "—"}
              </div>
            </div>
            <div className="p-3 rounded-lg bg-[var(--card)] border border-[var(--border)]">
              <div className="okz-muted text-[10px]">PUCARS Developments</div>
              <div className="text-lg font-bold font-mono text-amber-400">{data.developments.length}</div>
            </div>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border)] pb-2 text-xs">
        <div className="flex flex-wrap items-center gap-1.5">
          {(
            [
              ["ipos", `PSX IPOs & New Listings (${data?.ipos.length || 0})`],
              ["developments", `Latest PUCARS Developments (${data?.developments.length || 0})`],
              ["guides", `IPO News & Official PSX Guides (${(data?.ipo_news.length || 0) + (data?.guides.length || 0)})`],
            ] as const
          ).map(([k, lbl]) => (
            <button
              key={k}
              type="button"
              onClick={() => setTab(k)}
              className={`px-3.5 py-2 rounded-lg font-semibold transition ${
                tab === k
                  ? "bg-emerald-600 text-white"
                  : "bg-[var(--card)] border border-[var(--border)] okz-muted hover:text-[var(--text)]"
              }`}
            >
              {lbl}
            </button>
          ))}
        </div>

        {tab === "ipos" && (
          <div className="flex items-center gap-1">
            {(["ALL", "MAIN", "GEM"] as const).map((b) => (
              <button
                key={b}
                type="button"
                onClick={() => setBoardFilter(b)}
                className={`px-2.5 py-1 rounded-md font-semibold ${
                  boardFilter === b ? "bg-sky-500/20 text-sky-400" : "bg-[var(--card-muted)] okz-muted"
                }`}
              >
                {b === "ALL" ? "All Boards" : `${b} Board`}
              </button>
            ))}
          </div>
        )}
      </div>

      {loading && !data ? (
        <div className="okz-card p-8 text-center text-sm okz-muted">Loading PSX IPOs &amp; developments...</div>
      ) : tab === "ipos" ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {(data?.ipos || []).map((ipo) => (
            <div key={ipo.id} className="okz-card p-5 space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      {ipo.symbol && ipo.in_database ? (
                        <Link
                          href={`/stocks/${encodeURIComponent(ipo.symbol)}`}
                          className="font-mono font-bold text-base text-emerald-500 hover:underline"
                        >
                          {ipo.symbol}
                        </Link>
                      ) : (
                        <span className="font-mono font-bold text-base">{ipo.symbol || "IPO"}</span>
                      )}
                      <span
                        className={`text-[10px] px-2 py-0.5 rounded font-bold ${
                          ipo.status === "LISTED"
                            ? "bg-emerald-500/15 text-emerald-400"
                            : "bg-amber-500/20 text-amber-400"
                        }`}
                      >
                        {ipo.status}
                      </span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--card-muted)] okz-muted font-semibold">
                        {ipo.board} BOARD
                      </span>
                      {ipo.is_shariah_compliant && <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />}
                    </div>
                    <h2 className="text-sm font-bold mt-0.5">{ipo.company_name}</h2>
                    <div className="text-[11px] okz-muted">
                      {ipo.sector} &middot; {ipo.issue_type}
                    </div>
                  </div>
                  {ipo.post_ipo_return_pct != null && (
                    <div className="text-right shrink-0">
                      <div className="text-[10px] okz-muted">Post-IPO Return</div>
                      <div className={`text-base font-bold font-mono ${pctColorClass(ipo.post_ipo_return_pct)}`}>
                        {formatPct(ipo.post_ipo_return_pct)}
                      </div>
                    </div>
                  )}
                </div>

                <p className="text-xs okz-muted leading-relaxed">{ipo.summary}</p>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs pt-1">
                  <div className="p-2 rounded bg-[var(--card-muted)]/60">
                    <div className="text-[10px] okz-muted">Strike / Floor</div>
                    <div className="font-mono font-bold">
                      {ipo.strike_price != null ? `Rs ${formatNumber(ipo.strike_price, 2)}` : "—"}
                    </div>
                  </div>
                  <div className="p-2 rounded bg-[var(--card-muted)]/60">
                    <div className="text-[10px] okz-muted">Live PSX Price</div>
                    <div className="font-mono font-bold text-emerald-400">
                      {ipo.live_price != null ? `Rs ${formatNumber(ipo.live_price, 2)}` : "—"}
                    </div>
                  </div>
                  <div className="p-2 rounded bg-[var(--card-muted)]/60">
                    <div className="text-[10px] okz-muted">Issue Size</div>
                    <div className="font-mono font-semibold">
                      {ipo.issue_size_mn != null ? `Rs ${formatNumber(ipo.issue_size_mn, 0)} Mn` : "—"}
                    </div>
                  </div>
                  <div className="p-2 rounded bg-[var(--card-muted)]/60">
                    <div className="text-[10px] okz-muted">Oversubscription</div>
                    <div className="font-mono font-semibold text-sky-400">
                      {ipo.subscription_multiple != null ? `${ipo.subscription_multiple}x` : "—"}
                    </div>
                  </div>
                </div>
              </div>

              <div className="pt-2 border-t border-[var(--border)] flex flex-wrap items-center justify-between gap-2 text-[11px]">
                <span className="okz-muted">
                  {ipo.listing_date ? `Listed: ${ipo.listing_date}` : "Upcoming"}
                  {ipo.lead_manager ? ` · ${ipo.lead_manager}` : ""}
                </span>
                <div className="flex items-center gap-2">
                  {ipo.symbol && ipo.in_database && (
                    <Link
                      href={`/stocks/${encodeURIComponent(ipo.symbol)}`}
                      className="text-emerald-400 hover:underline font-semibold"
                    >
                      Full Analysis &rarr;
                    </Link>
                  )}
                  {ipo.prospectus_url && (
                    <a
                      href={`/api/v1/market/downloads/proxy?url=${encodeURIComponent(ipo.prospectus_url)}&filename=${encodeURIComponent(`${ipo.symbol || ipo.company_name}_Prospectus.pdf`)}`}
                      className="inline-flex items-center gap-1 text-sky-400 hover:underline font-semibold"
                    >
                      <Download className="w-3 h-3" /> Prospectus
                    </a>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : tab === "developments" ? (
        <div className="okz-card p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold flex items-center gap-2">
              <Megaphone className="w-4 h-4 text-amber-500" />
              Latest Official PSX PUCARS Corporate Developments
            </h2>
            <div className="flex flex-wrap gap-1.5 text-xs">
              {[
                ["ALL", "All"],
                ["RESULTS", "Financial Results"],
                ["DIVIDEND", "Dividends & Payouts"],
                ["BOARD_MEETING", "Board Meetings"],
                ["ANNUAL_QUARTERLY", "Reports"],
                ["MATERIAL", "Material Info"],
              ].map(([k, lbl]) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setDevType(k)}
                  className={`px-2.5 py-1 rounded-md font-semibold ${
                    devType === k ? "bg-emerald-600 text-white" : "bg-[var(--card-muted)] okz-muted"
                  }`}
                >
                  {lbl}
                </button>
              ))}
            </div>
          </div>

          <div className="divide-y divide-[var(--border)] text-xs">
            {filteredDevs.map((dev) => {
              const dt = new Date(dev.published_at).toISOString().slice(0, 10);
              const fname = `${dev.symbol}_${dt}_${dev.title.slice(0, 36).replace(/[^A-Za-z0-9]+/g, "_")}.pdf`;
              return (
                <div key={dev.id} className="py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <div className="space-y-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Link
                        href={`/stocks/${encodeURIComponent(dev.symbol)}`}
                        className="font-mono font-bold px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400"
                      >
                        {dev.symbol}
                      </Link>
                      <span className="px-1.5 py-0.5 rounded bg-[var(--card-muted)] okz-muted">{dev.category}</span>
                      <span className="okz-muted font-mono text-[11px]">{dt}</span>
                    </div>
                    <div className="font-semibold text-sm">{dev.title}</div>
                    <div className="text-[11px] okz-muted">
                      {dev.company_name} &middot; {dev.sector}
                    </div>
                  </div>
                  {dev.source_url && (
                    <div className="flex items-center gap-1.5 shrink-0">
                      <a
                        href={`/api/v1/market/downloads/proxy?url=${encodeURIComponent(dev.source_url)}&filename=${encodeURIComponent(fname)}`}
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md bg-emerald-600/15 hover:bg-emerald-600/25 text-emerald-400 font-semibold"
                      >
                        <Download className="w-3.5 h-3.5" /> Download PDF
                      </a>
                      <a
                        href={dev.source_url}
                        target="_blank"
                        rel="noreferrer"
                        className="p-1.5 rounded-md border border-[var(--border)] hover:border-sky-500 text-sky-400"
                      >
                        <ExternalLink className="w-3.5 h-3.5" />
                      </a>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="okz-card p-5 space-y-3">
            <h2 className="text-sm font-semibold flex items-center gap-2">
              <Newspaper className="w-4 h-4 text-sky-400" /> Recent IPO &amp; PSX Market Coverage
            </h2>
            <div className="divide-y divide-[var(--border)]">
              {(data?.ipo_news || []).map((n) => (
                <a key={n.id} href={n.url} target="_blank" rel="noreferrer" className="block py-3 hover:opacity-85 space-y-1">
                  <div className="flex items-center gap-2 text-[10px]">
                    <span className="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-400 font-semibold">{n.source_name}</span>
                    <span className="okz-muted">{new Date(n.published_at).toLocaleDateString()}</span>
                  </div>
                  <div className="text-xs font-semibold">{n.title}</div>
                  {n.summary && <p className="text-[11px] okz-muted line-clamp-2">{n.summary}</p>}
                </a>
              ))}
            </div>
          </div>

          <div className="okz-card p-5 space-y-3">
            <h2 className="text-sm font-semibold flex items-center gap-2">
              <FileText className="w-4 h-4 text-emerald-500" /> Official PSX IPO Manuals &amp; Guides
            </h2>
            <div className="divide-y divide-[var(--border)] text-xs">
              {(data?.guides || []).map((g) => (
                <div key={g.url} className="py-3 flex items-center justify-between gap-3">
                  <div>
                    <div className="font-semibold">{g.title}</div>
                    <div className="text-[11px] okz-muted">{g.category} · Official psx.com.pk PDF</div>
                  </div>
                  <a
                    href={`/api/v1/market/downloads/proxy?url=${encodeURIComponent(g.url)}&filename=${encodeURIComponent(`${g.title.replace(/[^A-Za-z0-9]+/g, "_")}.pdf`)}`}
                    className="inline-flex items-center gap-1 px-3 py-1.5 rounded-md bg-emerald-600/15 hover:bg-emerald-600/25 text-emerald-400 font-semibold shrink-0"
                  >
                    <Download className="w-3.5 h-3.5" /> PDF
                  </a>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
