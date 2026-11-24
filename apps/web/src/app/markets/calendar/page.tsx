"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { BookmarkCheck, CalendarDays, Download, ExternalLink, ShieldCheck } from "lucide-react";
import { apiFetch } from "@/lib/api";

interface CalendarEvent {
  id: string;
  event_date: string;
  is_upcoming: boolean;
  event_type: string;
  symbol: string;
  company_name: string;
  sector: string;
  title: string;
  badge: string;
  source_url: string | null;
  in_my_stocks: boolean;
  is_shariah_compliant: boolean;
}

interface CalendarPayload {
  today: string;
  total: number;
  upcoming_count: number;
  recent_count: number;
  my_symbols_count: number;
  events: CalendarEvent[];
}

const EVENT_COLORS: Record<string, string> = {
  DIVIDEND: "bg-emerald-500/15 text-emerald-400",
  BONUS_RIGHTS: "bg-purple-500/15 text-purple-400",
  BOARD_MEETING: "bg-amber-500/15 text-amber-400",
  RESULTS: "bg-sky-500/15 text-sky-400",
};

export default function CorporateCalendarPage() {
  const [data, setData] = useState<CalendarPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [eventType, setEventType] = useState("ALL");
  const [symbol, setSymbol] = useState("");
  const [myOnly, setMyOnly] = useState(false);

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams({ event_type: eventType });
    if (symbol.trim()) params.set("symbol", symbol.trim().toUpperCase());
    if (myOnly) params.set("my_stocks_only", "true");
    apiFetch<CalendarPayload>(`/api/v1/market/calendar?${params.toString()}`)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [eventType, symbol, myOnly]);

  // Group events by YYYY-MM month header
  const grouped: Array<{ monthLabel: string; events: CalendarEvent[] }> = [];
  const monthMap = new Map<string, CalendarEvent[]>();
  for (const ev of data?.events || []) {
    const key = ev.event_date.slice(0, 7);
    if (!monthMap.has(key)) {
      const arr: CalendarEvent[] = [];
      monthMap.set(key, arr);
      const dt = new Date(`${key}-01T00:00:00Z`);
      const label = dt.toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
      grouped.push({ monthLabel: label, events: arr });
    }
    monthMap.get(key)!.push(ev);
  }

  return (
    <div className="space-y-6">
      <div className="okz-card p-6 bg-gradient-to-br from-amber-500/10 via-transparent to-emerald-500/10 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <CalendarDays className="w-6 h-6 text-amber-400" />
            Pakistan Corporate &amp; Dividend Calendar
          </h1>
          <p className="text-sm okz-muted">
            Track PSX Ex-Dividend dates, Bonus &amp; Right Issues, Board Meetings, and Financial Results disclosures — filterable directly to your Portfolio &amp; Watchlist.
          </p>
        </div>
        {data && (
          <div className="flex items-center gap-3 text-xs font-mono">
            <div className="px-3 py-2 rounded-lg bg-[var(--card)] border border-[var(--border)]">
              <div className="text-[10px] font-sans okz-muted">Upcoming</div>
              <div className="font-bold text-emerald-400 text-base">{data.upcoming_count}</div>
            </div>
            <div className="px-3 py-2 rounded-lg bg-[var(--card)] border border-[var(--border)]">
              <div className="text-[10px] font-sans okz-muted">Recent 120D</div>
              <div className="font-bold text-sky-400 text-base">{data.recent_count}</div>
            </div>
          </div>
        )}
      </div>

      {/* Filter Bar */}
      <div className="okz-card p-4 flex flex-wrap items-center justify-between gap-3 text-xs">
        <div className="flex flex-wrap items-center gap-1.5">
          {[
            ["ALL", "All Events"],
            ["DIVIDEND", "Cash Dividends"],
            ["BONUS_RIGHTS", "Bonus & Rights"],
            ["RESULTS", "Financial Results"],
            ["BOARD_MEETING", "Board Meetings"],
          ].map(([k, lbl]) => (
            <button
              key={k}
              type="button"
              onClick={() => setEventType(k)}
              className={`px-3 py-1.5 rounded-lg font-semibold ${
                eventType === k ? "bg-emerald-600 text-white" : "bg-[var(--card-muted)] okz-muted hover:text-[var(--text)]"
              }`}
            >
              {lbl}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="Filter Ticker (MEBL)"
            className="w-36 px-3 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] font-mono uppercase text-xs"
          />
          <label className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-[var(--border)] cursor-pointer select-none">
            <input type="checkbox" checked={myOnly} onChange={(e) => setMyOnly(e.target.checked)} />
            <BookmarkCheck className="w-3.5 h-3.5 text-emerald-500" />
            <span className="font-semibold">My Holdings &amp; Watchlist ({data?.my_symbols_count || 0})</span>
          </label>
        </div>
      </div>

      {loading && !data ? (
        <div className="okz-card p-8 text-center text-sm okz-muted">Loading corporate calendar...</div>
      ) : grouped.length === 0 ? (
        <div className="okz-card p-8 text-center text-sm okz-muted">
          No calendar events matched your current filter. Try disabling &ldquo;My Holdings &amp; Watchlist&rdquo; or clearing the ticker filter.
        </div>
      ) : (
        <div className="space-y-5">
          {grouped.map((grp) => (
            <div key={grp.monthLabel} className="okz-card overflow-hidden">
              <div className="px-5 py-3 bg-[var(--bg-elevated)] border-b border-[var(--border)] flex items-center justify-between">
                <h2 className="text-sm font-bold">{grp.monthLabel}</h2>
                <span className="text-xs okz-muted font-mono">{grp.events.length} events</span>
              </div>
              <div className="divide-y divide-[var(--border)] text-xs">
                {grp.events.map((ev) => {
                  const isPdf = Boolean(ev.source_url && ev.source_url.toLowerCase().includes(".pdf"));
                  return (
                    <div key={ev.id} className="px-5 py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-2 hover:bg-[var(--card-muted)]/30">
                      <div className="flex items-start gap-3 min-w-0">
                        <div className="font-mono text-center px-2.5 py-1 rounded bg-[var(--card-muted)] shrink-0">
                          <div className="text-[10px] okz-muted">{ev.event_date.slice(5, 7)}/{ev.event_date.slice(0, 4)}</div>
                          <div className="text-sm font-bold">{ev.event_date.slice(8, 10)}</div>
                        </div>
                        <div className="min-w-0 space-y-1">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <Link
                              href={`/stocks/${encodeURIComponent(ev.symbol)}`}
                              className="font-mono font-bold text-emerald-500 hover:underline inline-flex items-center gap-1"
                            >
                              {ev.symbol}
                              {ev.is_shariah_compliant && <ShieldCheck className="w-3 h-3 text-emerald-500" />}
                            </Link>
                            <span className={`text-[10px] px-2 py-0.5 rounded font-bold ${EVENT_COLORS[ev.event_type] || "bg-[var(--card-muted)]"}`}>
                              {ev.badge}
                            </span>
                            {ev.is_upcoming && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-600 text-white font-bold">
                                UPCOMING
                              </span>
                            )}
                            {ev.in_my_stocks && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 font-semibold">
                                In Watchlist/Portfolio
                              </span>
                            )}
                          </div>
                          <div className="font-medium leading-snug">{ev.title}</div>
                          <div className="text-[11px] okz-muted">
                            {ev.company_name} &middot; {ev.sector}
                          </div>
                        </div>
                      </div>

                      {ev.source_url && (
                        <div className="flex items-center gap-1.5 shrink-0">
                          {isPdf ? (
                            <a
                              href={`/api/v1/market/downloads/proxy?url=${encodeURIComponent(ev.source_url)}&filename=${encodeURIComponent(`${ev.symbol}_${ev.event_date}_${ev.event_type}.pdf`)}`}
                              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-emerald-600/15 text-emerald-400 font-semibold hover:bg-emerald-600/25"
                            >
                              <Download className="w-3 h-3" /> PDF
                            </a>
                          ) : (
                            <a
                              href={ev.source_url}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-[var(--border)] text-sky-400 hover:border-sky-500 font-semibold"
                            >
                              PSX <ExternalLink className="w-3 h-3" />
                            </a>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
