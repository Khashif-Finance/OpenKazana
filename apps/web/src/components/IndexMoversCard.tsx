"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { BarChart3, ShieldCheck, TrendingDown, TrendingUp } from "lucide-react";
import { apiFetchCached } from "@/lib/api";

interface MoverItem {
  symbol: string;
  name: string;
  sector: string;
  last_price: number;
  change_pct: number;
  weight_pct: number;
  points: number;
  is_shariah_compliant: boolean;
}

interface IndexMoversPayload {
  index_symbol: string;
  index_value: number;
  index_change_pts: number;
  index_change_pct: number;
  constituents_evaluated: number;
  advancers: number;
  decliners: number;
  unchanged: number;
  top_positive: MoverItem[];
  top_negative: MoverItem[];
  sector_attribution: Array<{
    sector: string;
    points: number;
    weight_pct: number;
    stock_count: number;
  }>;
}

export function IndexMoversCard() {
  const [idxSym, setIdxSym] = useState<"KSE100" | "KMI30">("KSE100");
  const [data, setData] = useState<IndexMoversPayload | null>(null);

  useEffect(() => {
    apiFetchCached<IndexMoversPayload>(`/api/v1/market/index-movers?index_symbol=${idxSym}`, 60_000, {
      onUpdate: setData,
    })
      .then(setData)
      .catch(() => {});
  }, [idxSym]);

  if (!data || data.constituents_evaluated === 0) {
    return null;
  }

  const maxAbsPts = Math.max(
    1,
    ...data.top_positive.map((x) => Math.abs(x.points)),
    ...data.top_negative.map((x) => Math.abs(x.points)),
  );

  return (
    <section className="okz-card p-5 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-bold flex items-center gap-1.5">
            <BarChart3 className="w-4 h-4 text-emerald-500" />
            {data.index_symbol} Index Point Contributors &amp; Sector Attribution
          </h2>
          <p className="text-xs okz-muted">
            Estimated index point impact across {data.constituents_evaluated} constituents &middot;{" "}
            <span className="text-emerald-400 font-semibold">{data.advancers} Adv</span> /{" "}
            <span className="text-rose-400 font-semibold">{data.decliners} Dec</span> /{" "}
            <span>{data.unchanged} Unch</span>
          </p>
        </div>

        <div className="flex items-center gap-2">
          <span
            className={`px-2.5 py-1 rounded-md font-mono text-xs font-bold ${
              data.index_change_pts >= 0
                ? "bg-emerald-500/15 text-emerald-400"
                : "bg-rose-500/15 text-rose-400"
            }`}
          >
            {data.index_value.toLocaleString(undefined, { maximumFractionDigits: 0 })} (
            {data.index_change_pts >= 0 ? "+" : ""}
            {data.index_change_pts.toFixed(0)} pts / {data.index_change_pct >= 0 ? "+" : ""}
            {data.index_change_pct.toFixed(2)}%)
          </span>
          <div className="flex rounded-md border border-[var(--border)] p-0.5 text-xs">
            {(["KSE100", "KMI30"] as const).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setIdxSym(s)}
                className={`px-2.5 py-1 rounded font-semibold transition ${
                  idxSym === s ? "bg-emerald-600 text-white" : "okz-muted hover:text-[var(--text)]"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Positive Contributors */}
        <div className="space-y-2">
          <div className="text-xs font-semibold text-emerald-400 flex items-center gap-1">
            <TrendingUp className="w-3.5 h-3.5" /> Top Positive Point Contributors
          </div>
          {data.top_positive.length === 0 ? (
            <div className="text-xs okz-muted py-3">No positive movers recorded in current snapshot.</div>
          ) : (
            <div className="space-y-1.5">
              {data.top_positive.map((m) => {
                const widthPct = Math.max(6, Math.min(100, (Math.abs(m.points) / maxAbsPts) * 100));
                return (
                  <Link
                    key={m.symbol}
                    href={`/stocks/${encodeURIComponent(m.symbol)}`}
                    className="block p-2 rounded-lg bg-[var(--bg-elevated)]/60 hover:bg-[var(--card-muted)] transition"
                  >
                    <div className="flex items-center justify-between text-xs mb-1">
                      <span className="font-mono font-bold flex items-center gap-1">
                        {m.symbol}
                        {m.is_shariah_compliant && <ShieldCheck className="w-3 h-3 text-emerald-500" />}
                        <span className="font-sans font-normal okz-muted text-[11px] truncate max-w-36">{m.sector}</span>
                      </span>
                      <span className="font-mono font-bold text-emerald-400">
                        +{m.points.toFixed(1)} pts{" "}
                        <span className="text-[10px] opacity-80">(+{m.change_pct.toFixed(2)}%)</span>
                      </span>
                    </div>
                    <div className="h-1.5 rounded bg-[var(--border)] overflow-hidden">
                      <div className="h-full bg-emerald-500 rounded" style={{ width: `${widthPct}%` }} />
                    </div>
                  </Link>
                );
              })}
            </div>
          )}
        </div>

        {/* Negative Drags */}
        <div className="space-y-2">
          <div className="text-xs font-semibold text-rose-400 flex items-center gap-1">
            <TrendingDown className="w-3.5 h-3.5" /> Top Index Drags
          </div>
          {data.top_negative.length === 0 ? (
            <div className="text-xs okz-muted py-3">No negative drags recorded in current snapshot.</div>
          ) : (
            <div className="space-y-1.5">
              {data.top_negative.map((m) => {
                const widthPct = Math.max(6, Math.min(100, (Math.abs(m.points) / maxAbsPts) * 100));
                return (
                  <Link
                    key={m.symbol}
                    href={`/stocks/${encodeURIComponent(m.symbol)}`}
                    className="block p-2 rounded-lg bg-[var(--bg-elevated)]/60 hover:bg-[var(--card-muted)] transition"
                  >
                    <div className="flex items-center justify-between text-xs mb-1">
                      <span className="font-mono font-bold flex items-center gap-1">
                        {m.symbol}
                        {m.is_shariah_compliant && <ShieldCheck className="w-3 h-3 text-emerald-500" />}
                        <span className="font-sans font-normal okz-muted text-[11px] truncate max-w-36">{m.sector}</span>
                      </span>
                      <span className="font-mono font-bold text-rose-400">
                        {m.points.toFixed(1)} pts{" "}
                        <span className="text-[10px] opacity-80">({m.change_pct.toFixed(2)}%)</span>
                      </span>
                    </div>
                    <div className="h-1.5 rounded bg-[var(--border)] overflow-hidden">
                      <div className="h-full bg-rose-500 rounded" style={{ width: `${widthPct}%` }} />
                    </div>
                  </Link>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Sector Point Attribution Pills */}
      {data.sector_attribution.length > 0 && (
        <div className="pt-2 border-t border-[var(--border)] flex flex-wrap items-center gap-1.5 text-[11px]">
          <span className="okz-muted font-semibold mr-1">Sector Impact:</span>
          {data.sector_attribution.slice(0, 8).map((s) => (
            <span
              key={s.sector}
              className={`px-2 py-0.5 rounded font-mono ${
                s.points >= 0
                  ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                  : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
              }`}
            >
              <span className="font-sans font-semibold">{s.sector}</span> {s.points >= 0 ? "+" : ""}
              {s.points.toFixed(0)} pts
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
