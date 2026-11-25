"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { CalendarDays, Coins } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiFetch } from "@/lib/api";

interface DividendMonthEvent {
  symbol: string;
  name: string;
  source_type: string;
  dps?: number;
  gross_pkr: number;
  net_pkr: number;
}

interface DividendMonthSlot {
  month_num: number;
  month: string;
  equity_gross_pkr: number;
  equity_net_pkr: number;
  fund_gross_pkr: number;
  fund_net_pkr: number;
  wht_pkr: number;
  purification_pkr: number;
  total_gross_pkr: number;
  total_net_pkr: number;
  events: DividendMonthEvent[];
}

interface DividendCalendarPayload {
  portfolio_id: string;
  filer_status: string;
  annual_gross_pkr: number;
  annual_net_pkr: number;
  annual_wht_pkr: number;
  annual_purification_pkr: number;
  monthly_avg_net_pkr: number;
  portfolio_forward_yield_pct: number;
  portfolio_yield_on_cost_pct: number;
  months: DividendMonthSlot[];
}

export function DividendSnowballCard({
  portfolioId,
  refreshKey,
}: {
  portfolioId: string;
  refreshKey?: number;
}) {
  const [data, setData] = useState<DividendCalendarPayload | null>(null);
  const [selectedMonth, setSelectedMonth] = useState<number>(new Date().getMonth() + 1);

  useEffect(() => {
    if (!portfolioId) return;
    apiFetch<DividendCalendarPayload>(`/api/v1/portfolios/${encodeURIComponent(portfolioId)}/dividend-calendar`)
      .then((res) => {
        setData(res);
        // Default to highest-paying month if current month has 0 events
        const curSlot = res.months.find((m) => m.month_num === new Date().getMonth() + 1);
        if (!curSlot || curSlot.events.length === 0) {
          const topMonth = [...res.months].sort((a, b) => b.total_net_pkr - a.total_net_pkr)[0];
          if (topMonth && topMonth.total_net_pkr > 0) setSelectedMonth(topMonth.month_num);
        }
      })
      .catch(() => setData(null));
  }, [portfolioId, refreshKey]);

  if (!data || data.annual_gross_pkr <= 0) {
    return null;
  }

  const activeSlot = data.months.find((m) => m.month_num === selectedMonth) || data.months[0];

  return (
    <section className="okz-card p-5 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-bold flex items-center gap-1.5">
            <CalendarDays className="w-4 h-4 text-emerald-500" />
            12-Month Portfolio Passive Income &amp; Dividend Snowball Calendar
          </h2>
          <p className="text-xs okz-muted">
            Projected Jan&ndash;Dec cashflow from your open PSX stocks, Daily Dividend Mutual Funds &amp; NSS — net of{" "}
            <strong>{data.filer_status.replace("_", " ")}</strong> WHT &amp; Shariah purification.
          </p>
        </div>
        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-emerald-500/15 text-emerald-400 font-mono text-xs font-bold">
          <Coins className="w-3.5 h-3.5" />
          Forward Yield {data.portfolio_forward_yield_pct.toFixed(2)}% (YoC {data.portfolio_yield_on_cost_pct.toFixed(2)}%)
        </span>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
        <div className="p-3 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border)]">
          <div className="okz-muted">Projected Annual Net Income</div>
          <div className="text-lg font-black font-mono text-emerald-400 mt-0.5">
            Rs {data.annual_net_pkr.toLocaleString()}
          </div>
          <div className="text-[10px] okz-muted">Gross: Rs {data.annual_gross_pkr.toLocaleString()}</div>
        </div>
        <div className="p-3 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border)]">
          <div className="okz-muted">Avg Monthly Take-Home</div>
          <div className="text-lg font-black font-mono mt-0.5">
            Rs {data.monthly_avg_net_pkr.toLocaleString()}/mo
          </div>
          <div className="text-[10px] okz-muted">12-month smoothed average</div>
        </div>
        <div className="p-3 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border)]">
          <div className="okz-muted">Est. Annual FBR WHT</div>
          <div className="text-lg font-black font-mono text-amber-400 mt-0.5">
            Rs {data.annual_wht_pkr.toLocaleString()}
          </div>
          <div className="text-[10px] okz-muted">{data.filer_status.replace("_", " ")} tax rate</div>
        </div>
        <div className="p-3 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border)]">
          <div className="okz-muted">Est. Shariah Purification</div>
          <div className="text-lg font-black font-mono text-sky-400 mt-0.5">
            Rs {data.annual_purification_pkr.toLocaleString()}
          </div>
          <div className="text-[10px] okz-muted">Non-compliant income charity portion</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
        <div className="lg:col-span-8 h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data.months} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="month" tick={{ fontSize: 11, fill: "#94a3b8" }} />
              <YAxis
                tick={{ fontSize: 10, fill: "#94a3b8" }}
                tickFormatter={(v) => (Number(v) >= 1000 ? `${(Number(v) / 1000).toFixed(0)}k` : String(v))}
                width={44}
              />
              <RechartsTooltip
                contentStyle={{
                  backgroundColor: "var(--card)",
                  borderColor: "var(--border)",
                  fontSize: "11px",
                  borderRadius: "8px",
                }}
                formatter={(v) => [`Rs ${Number(v).toLocaleString()}`, ""]}
              />
              <Legend wrapperStyle={{ fontSize: "11px" }} />
              <Bar dataKey="equity_net_pkr" stackId="a" name="PSX Equity Dividends (Net)" fill="#10b981" radius={[0, 0, 0, 0]} />
              <Bar dataKey="fund_net_pkr" stackId="a" name="Daily Div Funds & Fixed Income (Net)" fill="#38bdf8" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="lg:col-span-4 p-3.5 rounded-xl bg-[var(--bg-elevated)] border border-[var(--border)] space-y-2.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-bold">{activeSlot.month} Payout Breakdown</span>
            <select
              value={selectedMonth}
              onChange={(e) => setSelectedMonth(Number(e.target.value))}
              className="px-2 py-1 rounded border border-[var(--border)] bg-[var(--card)] text-xs font-semibold"
            >
              {data.months.map((m) => (
                <option key={m.month_num} value={m.month_num}>
                  {m.month} (Rs {m.total_net_pkr.toLocaleString()})
                </option>
              ))}
            </select>
          </div>
          {activeSlot.events.length === 0 ? (
            <p className="text-xs okz-muted py-4">No scheduled dividend payouts projected for {activeSlot.month}.</p>
          ) : (
            <div className="divide-y divide-[var(--border)] max-h-40 overflow-y-auto text-xs">
              {activeSlot.events.map((ev, idx) => (
                <div key={`${ev.symbol}-${idx}`} className="py-1.5 flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <Link
                      href={ev.source_type === "EQUITY_DIVIDEND" ? `/stocks/${encodeURIComponent(ev.symbol)}` : `/funds/${encodeURIComponent(ev.symbol)}`}
                      className="font-mono font-bold text-emerald-400 hover:underline"
                    >
                      {ev.symbol}
                    </Link>
                    <div className="text-[10px] okz-muted truncate">
                      {ev.source_type === "EQUITY_DIVIDEND" && ev.dps ? `Est. Rs ${ev.dps}/sh` : ev.source_type.replace(/_/g, " ")}
                    </div>
                  </div>
                  <div className="text-right font-mono font-semibold shrink-0">
                    Rs {ev.net_pkr.toLocaleString()}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
