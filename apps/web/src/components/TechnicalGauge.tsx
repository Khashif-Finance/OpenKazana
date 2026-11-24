"use client";

import React from "react";
import { Gauge } from "lucide-react";

export interface TechnicalGaugePayload {
  available: boolean;
  overall_signal: "STRONG_BUY" | "BUY" | "NEUTRAL" | "SELL" | "STRONG_SELL" | string;
  gauge_score: number; // -100 to +100
  buy_count: number;
  sell_count: number;
  neutral_count: number;
  ma_summary: {
    signal: string;
    buy: number;
    sell: number;
    neutral: number;
    items: Array<{ name: string; value: number; signal: string }>;
  };
  oscillator_summary: {
    signal: string;
    buy: number;
    sell: number;
    neutral: number;
    items: Array<{ name: string; value: number; signal: string }>;
  };
}

function signalMeta(sig: string): { label: string; color: string; pill: string } {
  const s = (sig || "NEUTRAL").toUpperCase();
  if (s === "STRONG_BUY") return { label: "Strong Buy", color: "#10b981", pill: "bg-emerald-500/20 text-emerald-400 border-emerald-500/40" };
  if (s === "BUY") return { label: "Buy", color: "#34d399", pill: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" };
  if (s === "STRONG_SELL") return { label: "Strong Sell", color: "#f43f5e", pill: "bg-rose-500/20 text-rose-400 border-rose-500/40" };
  if (s === "SELL") return { label: "Sell", color: "#fb7185", pill: "bg-rose-500/15 text-rose-400 border-rose-500/30" };
  return { label: "Neutral", color: "#94a3b8", pill: "bg-slate-500/15 text-slate-400 border-slate-500/30" };
}

export function TechnicalGauge({
  gauge,
  showTables = false,
}: {
  gauge: TechnicalGaugePayload | null | undefined;
  showTables?: boolean;
}) {
  if (!gauge || !gauge.available) {
    return (
      <div className="okz-card p-4 text-xs okz-muted">
        Insufficient daily bars to compute Technical Consensus Gauge (need 20+ sessions).
      </div>
    );
  }

  const clamped = Math.max(-100, Math.min(100, gauge.gauge_score || 0));
  // Map -100..+100 -> -90deg..+90deg needle rotation
  const angleDeg = (clamped / 100) * 84;
  const meta = signalMeta(gauge.overall_signal);
  const maMeta = signalMeta(gauge.ma_summary.signal);
  const oscMeta = signalMeta(gauge.oscillator_summary.signal);

  return (
    <div className="okz-card p-5 space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold flex items-center gap-1.5">
            <Gauge className="w-4 h-4 text-emerald-500" />
            Technical Summary Consensus
          </h3>
          <p className="text-[11px] okz-muted">
            Pandas 20-Indicator Engine (12 Moving Averages + 8 Momentum Oscillators)
          </p>
        </div>
        <span className={`px-2.5 py-1 rounded-md border text-xs font-bold ${meta.pill}`}>
          {meta.label}
        </span>
      </div>

      {/* Semi-circular Investing.com style speedometer */}
      <div className="flex flex-col items-center">
        <svg viewBox="0 0 240 132" className="w-60 h-32 overflow-visible">
          {/* 5 colored arc segments: Strong Sell, Sell, Neutral, Buy, Strong Buy */}
          <path d="M 24 116 A 96 96 0 0 1 48 53" fill="none" stroke="#e11d48" strokeWidth="14" strokeLinecap="butt" opacity="0.9" />
          <path d="M 52 49 A 96 96 0 0 1 92 24" fill="none" stroke="#fb7185" strokeWidth="14" strokeLinecap="butt" opacity="0.85" />
          <path d="M 97 23 A 96 96 0 0 1 143 23" fill="none" stroke="#64748b" strokeWidth="14" strokeLinecap="butt" opacity="0.7" />
          <path d="M 148 24 A 96 96 0 0 1 188 49" fill="none" stroke="#34d399" strokeWidth="14" strokeLinecap="butt" opacity="0.85" />
          <path d="M 192 53 A 96 96 0 0 1 216 116" fill="none" stroke="#10b981" strokeWidth="14" strokeLinecap="butt" opacity="0.95" />

          {/* Labels */}
          <text x="14" y="129" fontSize="8" fill="#fb7185" fontWeight="600">Strong Sell</text>
          <text x="106" y="12" fontSize="8" fill="#94a3b8" fontWeight="600">Neutral</text>
          <text x="180" y="129" fontSize="8" fill="#10b981" fontWeight="600">Strong Buy</text>

          {/* Animated Needle */}
          <g transform={`translate(120, 116) rotate(${angleDeg})`}>
            <polygon points="-3.5,0 0,-76 3.5,0" fill={meta.color} />
            <circle cx="0" cy="0" r="7" fill={meta.color} />
            <circle cx="0" cy="0" r="3" fill="var(--card)" />
          </g>
        </svg>

        <div className="flex items-center gap-4 text-xs mt-1">
          <div className="text-center">
            <div className="text-rose-400 font-bold text-sm okz-num">{gauge.sell_count}</div>
            <div className="text-[10px] okz-muted uppercase">Sell</div>
          </div>
          <div className="h-6 w-px bg-[var(--border)]" />
          <div className="text-center">
            <div className="text-slate-400 font-bold text-sm okz-num">{gauge.neutral_count}</div>
            <div className="text-[10px] okz-muted uppercase">Neutral</div>
          </div>
          <div className="h-6 w-px bg-[var(--border)]" />
          <div className="text-center">
            <div className="text-emerald-400 font-bold text-sm okz-num">{gauge.buy_count}</div>
            <div className="text-[10px] okz-muted uppercase">Buy</div>
          </div>
        </div>
      </div>

      {/* Sub-summaries */}
      <div className="grid grid-cols-2 gap-2.5 text-xs">
        <div className="p-2.5 rounded-lg bg-[var(--card-muted)]/70 border border-[var(--border)] flex items-center justify-between">
          <div>
            <div className="text-[10px] okz-muted">Moving Averages</div>
            <div className="font-bold mt-0.5">{maMeta.label}</div>
          </div>
          <div className="text-right font-mono text-[11px]">
            <span className="text-emerald-400">{gauge.ma_summary.buy}B</span> /{" "}
            <span className="text-rose-400">{gauge.ma_summary.sell}S</span>
          </div>
        </div>
        <div className="p-2.5 rounded-lg bg-[var(--card-muted)]/70 border border-[var(--border)] flex items-center justify-between">
          <div>
            <div className="text-[10px] okz-muted">Oscillators</div>
            <div className="font-bold mt-0.5">{oscMeta.label}</div>
          </div>
          <div className="text-right font-mono text-[11px]">
            <span className="text-emerald-400">{gauge.oscillator_summary.buy}B</span> /{" "}
            <span className="text-rose-400">{gauge.oscillator_summary.sell}S</span>
          </div>
        </div>
      </div>

      {showTables && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2 border-t border-[var(--border)]">
          <div className="space-y-1.5">
            <div className="text-[11px] font-bold uppercase tracking-wider okz-muted">
              Moving Averages Breakdown ({gauge.ma_summary.items.length})
            </div>
            <div className="overflow-x-auto border border-[var(--border)] rounded-lg">
              <table className="w-full text-xs">
                <thead className="bg-[var(--bg-elevated)] okz-muted">
                  <tr>
                    <th className="py-1.5 px-2.5 text-left">Indicator</th>
                    <th className="py-1.5 px-2.5 text-right">Value</th>
                    <th className="py-1.5 px-2.5 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border)]">
                  {gauge.ma_summary.items.map((item) => (
                    <tr key={item.name}>
                      <td className="py-1.5 px-2.5 font-medium">{item.name}</td>
                      <td className="py-1.5 px-2.5 text-right font-mono">{item.value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                      <td className="py-1.5 px-2.5 text-right">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            item.signal === "BUY"
                              ? "bg-emerald-500/15 text-emerald-400"
                              : item.signal === "SELL"
                                ? "bg-rose-500/15 text-rose-400"
                                : "bg-slate-500/15 text-slate-400"
                          }`}
                        >
                          {item.signal}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="space-y-1.5">
            <div className="text-[11px] font-bold uppercase tracking-wider okz-muted">
              Momentum Oscillators ({gauge.oscillator_summary.items.length})
            </div>
            <div className="overflow-x-auto border border-[var(--border)] rounded-lg">
              <table className="w-full text-xs">
                <thead className="bg-[var(--bg-elevated)] okz-muted">
                  <tr>
                    <th className="py-1.5 px-2.5 text-left">Oscillator</th>
                    <th className="py-1.5 px-2.5 text-right">Value</th>
                    <th className="py-1.5 px-2.5 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border)]">
                  {gauge.oscillator_summary.items.map((item) => (
                    <tr key={item.name}>
                      <td className="py-1.5 px-2.5 font-medium">{item.name}</td>
                      <td className="py-1.5 px-2.5 text-right font-mono">{item.value.toFixed(2)}</td>
                      <td className="py-1.5 px-2.5 text-right">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            item.signal === "BUY"
                              ? "bg-emerald-500/15 text-emerald-400"
                              : item.signal === "SELL"
                                ? "bg-rose-500/15 text-rose-400"
                                : "bg-slate-500/15 text-slate-400"
                          }`}
                        >
                          {item.signal}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
