"use client";

import React from "react";
import { CalendarDays } from "lucide-react";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export interface SeasonalityPayload {
  years: Array<{
    year: number;
    months: Record<string, number | null>;
    ytd_pct: number | null;
  }>;
  monthly_avg: Array<{
    month: string;
    avg_return_pct: number | null;
    win_rate_pct: number | null;
  }>;
  rolling_stats?: {
    vol_90d_ann_pct?: number | null;
    return_1y_pct?: number | null;
    beta_vs_benchmark?: number | null;
    corr_vs_benchmark?: number | null;
  };
}

function heatCellClass(val: number | null | undefined): string {
  if (val == null) return "bg-transparent okz-muted";
  if (val >= 8) return "bg-emerald-500/35 text-emerald-300 font-bold";
  if (val >= 3) return "bg-emerald-500/20 text-emerald-400 font-semibold";
  if (val > 0) return "bg-emerald-500/10 text-emerald-400";
  if (val <= -8) return "bg-rose-500/35 text-rose-300 font-bold";
  if (val <= -3) return "bg-rose-500/20 text-rose-400 font-semibold";
  if (val < 0) return "bg-rose-500/10 text-rose-400";
  return "bg-[var(--card-muted)]/40 okz-muted";
}

export function SeasonalityHeatmap({ data }: { data: SeasonalityPayload | null | undefined }) {
  if (!data || !data.years || data.years.length === 0) {
    return (
      <div className="okz-card p-5 text-xs okz-muted">
        Not enough multi-month history yet to render Pandas Monthly Seasonality Heatmap.
      </div>
    );
  }

  const rs = data.rolling_stats || {};

  return (
    <div className="okz-card p-5 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold flex items-center gap-1.5">
            <CalendarDays className="w-4 h-4 text-sky-400" />
            Pandas Monthly Seasonality Return Heatmap (%)
          </h3>
          <p className="text-[11px] okz-muted">
            Month-end compounded return matrix highlighting recurring PSX calendar strength (e.g. Jan rally / post-budget moves).
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-[11px]">
          {rs.vol_90d_ann_pct != null && (
            <span className="px-2 py-0.5 rounded bg-[var(--card-muted)] font-mono">
              90D Vol: {rs.vol_90d_ann_pct.toFixed(1)}%
            </span>
          )}
          {rs.beta_vs_benchmark != null && (
            <span className="px-2 py-0.5 rounded bg-sky-500/15 text-sky-400 font-mono">
              Beta: {rs.beta_vs_benchmark.toFixed(2)}
            </span>
          )}
          {rs.corr_vs_benchmark != null && (
            <span className="px-2 py-0.5 rounded bg-[var(--card-muted)] font-mono">
              Corr: {rs.corr_vs_benchmark.toFixed(2)}
            </span>
          )}
        </div>
      </div>

      <div className="overflow-x-auto border border-[var(--border)] rounded-lg">
        <table className="w-full text-[11px] font-mono">
          <thead className="bg-[var(--bg-elevated)] okz-muted">
            <tr>
              <th className="py-2 px-2.5 text-left">Year</th>
              {MONTHS.map((m) => (
                <th key={m} className="py-2 px-1.5 text-right">{m}</th>
              ))}
              <th className="py-2 px-2.5 text-right text-[var(--text)]">Annual</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)]">
            {data.years.map((yrRow) => (
              <tr key={yrRow.year}>
                <td className="py-2 px-2.5 font-bold bg-[var(--bg-elevated)]/40">{yrRow.year}</td>
                {MONTHS.map((m) => {
                  const v = yrRow.months[m];
                  return (
                    <td key={m} className={`py-2 px-1.5 text-right ${heatCellClass(v)}`}>
                      {v != null ? `${v >= 0 ? "+" : ""}${v.toFixed(1)}` : "—"}
                    </td>
                  );
                })}
                <td className={`py-2 px-2.5 text-right font-bold ${heatCellClass(yrRow.ytd_pct)}`}>
                  {yrRow.ytd_pct != null ? `${yrRow.ytd_pct >= 0 ? "+" : ""}${yrRow.ytd_pct.toFixed(1)}%` : "—"}
                </td>
              </tr>
            ))}
            {data.monthly_avg.length > 0 && (
              <>
                <tr className="border-t-2 border-[var(--border)] bg-[var(--bg-elevated)]/60">
                  <td className="py-2 px-2.5 font-bold font-sans text-[10px] uppercase okz-muted">Avg %</td>
                  {MONTHS.map((m) => {
                    const row = data.monthly_avg.find((x) => x.month === m);
                    const v = row?.avg_return_pct;
                    return (
                      <td key={m} className={`py-2 px-1.5 text-right font-semibold ${v != null ? (v >= 0 ? "text-emerald-400" : "text-rose-400") : "okz-muted"}`}>
                        {v != null ? `${v >= 0 ? "+" : ""}${v.toFixed(1)}` : "—"}
                      </td>
                    );
                  })}
                  <td className="py-2 px-2.5 text-right okz-muted">—</td>
                </tr>
                <tr className="bg-[var(--bg-elevated)]/30">
                  <td className="py-1.5 px-2.5 font-sans text-[10px] uppercase okz-muted">Win %</td>
                  {MONTHS.map((m) => {
                    const row = data.monthly_avg.find((x) => x.month === m);
                    const w = row?.win_rate_pct;
                    return (
                      <td key={m} className="py-1.5 px-1.5 text-right text-[10px] okz-muted">
                        {w != null ? `${w.toFixed(0)}%` : "—"}
                      </td>
                    );
                  })}
                  <td className="py-1.5 px-2.5 text-right okz-muted">—</td>
                </tr>
              </>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
