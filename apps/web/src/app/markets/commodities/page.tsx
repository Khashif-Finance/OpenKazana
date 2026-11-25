"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Calculator, ExternalLink, Flame, Layers, TrendingUp } from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { TableSkeleton } from "@/components/Skeletons";
import { apiFetchCached } from "@/lib/api";
import { formatNumber, formatPct, pctColorClass } from "@/lib/format";

interface CommodityItem {
  code: string;
  yf_symbol: string;
  name: string;
  category: string;
  unit_label: string;
  price_usd: number | null;
  price_pkr_unit: number | null;
  change_1d_pct: number;
  return_1m_pct: number | null;
  return_1y_pct: number | null;
  high_52w: number | null;
  low_52w: number | null;
  pmex_contract: string;
  pmex_lot_desc: string;
  pmex_notional_pkr: number | null;
  pmex_est_margin_pkr: number | null;
  margin_pct: number;
  psx_impact: string;
  psx_tickers: string[];
  series: Array<{ date: string; price: number }>;
}

interface CommoditiesPayload {
  updated_at: string;
  usd_pkr_rate: number;
  gold_pkr_breakdown: {
    gold_24k_1g_pkr: number;
    gold_24k_10g_pkr: number;
    gold_24k_tola_pkr: number;
    gold_22k_tola_pkr: number;
    gold_21k_tola_pkr: number;
    usd_per_oz: number;
  } | null;
  commodities: CommodityItem[];
  pmex_resources: Array<{ label: string; url: string }>;
}

export default function CommoditiesHubPage() {
  const [data, setData] = useState<CommoditiesPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [category, setCategory] = useState("ALL");
  const [selectedCode, setSelectedCode] = useState("GOLD");
  const [lots, setLots] = useState<number>(1);
  const [shockPct, setShockPct] = useState<number>(2);

  useEffect(() => {
    apiFetchCached<CommoditiesPayload>("/api/v1/market/commodities", 300_000, { onUpdate: setData })
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const categories = useMemo(() => {
    const s = new Set<string>(["ALL"]);
    for (const c of data?.commodities || []) s.add(c.category);
    return Array.from(s);
  }, [data]);

  const filtered = useMemo(() => {
    return (data?.commodities || []).filter((c) => category === "ALL" || c.category === category);
  }, [data, category]);

  const activeItem = useMemo(() => {
    return (data?.commodities || []).find((c) => c.code === selectedCode) || data?.commodities?.[0] || null;
  }, [data, selectedCode]);

  const pmexCalc = useMemo(() => {
    if (!activeItem || !activeItem.pmex_notional_pkr || !activeItem.pmex_est_margin_pkr) return null;
    const safeLots = Math.max(1, Math.min(100, lots || 1));
    const notional = activeItem.pmex_notional_pkr * safeLots;
    const margin = activeItem.pmex_est_margin_pkr * safeLots;
    const leverage = activeItem.margin_pct > 0 ? 100 / activeItem.margin_pct : 1;
    const pnl = notional * ((shockPct || 0) / 100);
    const roePct = margin > 0 ? (pnl / margin) * 100 : 0;
    return { notional, margin, leverage, pnl, roePct, safeLots };
  }, [activeItem, lots, shockPct]);

  if (loading && !data) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">PMEX &amp; Global Commodities Hub</h1>
        <TableSkeleton rows={10} cols={7} />
      </div>
    );
  }

  const g = data?.gold_pkr_breakdown;

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Flame className="w-6 h-6 text-amber-400" />
            PMEX &amp; Global Commodities Hub
          </h1>
          <p className="text-sm okz-muted">
            Live COMEX, NYMEX, ICE &amp; CBOT futures mapped to Pakistan Mercantile Exchange (PMEX) contract sizes,
            PKR Gold Tola rates, margin estimates &amp; PSX sector impact links.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          {(data?.pmex_resources || []).map((r) => (
            <a
              key={r.url}
              href={r.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md border border-[var(--border)] hover:border-amber-400 text-xs font-semibold text-amber-400"
            >
              {r.label.replace("PMEX ", "")} <ExternalLink className="w-3 h-3" />
            </a>
          ))}
        </div>
      </div>

      {/* Pakistan Gold Rate Board */}
      {g && (
        <div className="okz-card p-5 border-amber-500/30 bg-amber-500/5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-bold text-amber-400">
              Live Pakistan Bullion &amp; Gold Parity Board (FX Basis: Rs {formatNumber(data?.usd_pkr_rate, 2)}/USD)
            </h2>
            <span className="text-xs font-mono okz-muted">COMEX Spot: ${formatNumber(g.usd_per_oz, 2)} / Troy Oz</span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            {[
              { label: "24K Gold / Tola (11.664g)", val: g.gold_24k_tola_pkr, accent: true },
              { label: "24K Gold / 10 Grams", val: g.gold_24k_10g_pkr, accent: false },
              { label: "24K Gold / 1 Gram", val: g.gold_24k_1g_pkr, accent: false },
              { label: "22K Gold / Tola", val: g.gold_22k_tola_pkr, accent: false },
              { label: "21K Gold / Tola", val: g.gold_21k_tola_pkr, accent: false },
            ].map((card) => (
              <div key={card.label} className="p-3 rounded-lg bg-[var(--card)] border border-[var(--border)]">
                <div className="text-[11px] okz-muted">{card.label}</div>
                <div className={`text-lg font-black font-mono mt-1 ${card.accent ? "text-amber-400" : ""}`}>
                  Rs {card.val.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Interactive 90-Day Chart + PMEX Lot & Margin Calculator */}
      {activeItem && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <div className="lg:col-span-7 okz-card p-5 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-bold flex items-center gap-1.5">
                  <TrendingUp className="w-4 h-4 text-emerald-500" />
                  {activeItem.name} — 90-Day Futures Trajectory
                </h2>
                <p className="text-xs okz-muted font-mono">
                  {activeItem.yf_symbol} &middot; {activeItem.unit_label} &middot; 52W Range:{" "}
                  {activeItem.low_52w != null ? activeItem.low_52w.toFixed(2) : "—"} –{" "}
                  {activeItem.high_52w != null ? activeItem.high_52w.toFixed(2) : "—"}
                </p>
              </div>
              <div className="text-right font-mono">
                <div className="text-lg font-black">
                  {activeItem.price_usd != null ? activeItem.price_usd.toFixed(2) : "—"}
                </div>
                <div className={`text-xs font-semibold ${pctColorClass(activeItem.change_1d_pct)}`}>
                  1D {formatPct(activeItem.change_1d_pct)} &middot; 1Y {formatPct(activeItem.return_1y_pct)}
                </div>
              </div>
            </div>

            {activeItem.series.length > 2 ? (
              <div className="h-60 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={activeItem.series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="pmexAreaGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.35} />
                        <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#94a3b8" }} minTickGap={22} />
                    <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "#94a3b8" }} width={52} />
                    <RechartsTooltip
                      contentStyle={{
                        backgroundColor: "var(--card)",
                        borderColor: "var(--border)",
                        fontSize: "12px",
                        borderRadius: "8px",
                      }}
                    />
                    <Area type="monotone" dataKey="price" stroke="#f59e0b" strokeWidth={2} fill="url(#pmexAreaGrad)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="h-60 flex items-center justify-center text-xs okz-muted">
                No intraday series returned for {activeItem.name}.
              </div>
            )}
          </div>

          <div className="lg:col-span-5 okz-card p-5 space-y-4">
            <div>
              <h2 className="text-sm font-bold flex items-center gap-1.5">
                <Calculator className="w-4 h-4 text-amber-400" />
                PMEX Contract Notional &amp; Margin Calculator
              </h2>
              <p className="text-xs okz-muted">
                Selected Contract: <strong className="text-[var(--text)]">{activeItem.pmex_contract}</strong> (
                {activeItem.pmex_lot_desc})
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <label className="block okz-muted mb-1">PMEX Commodity</label>
                <select
                  value={selectedCode}
                  onChange={(e) => setSelectedCode(e.target.value)}
                  className="w-full px-2.5 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] font-semibold"
                >
                  {(data?.commodities || []).map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.code} — {c.pmex_contract}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block okz-muted mb-1">Number of Lots</label>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={lots}
                  onChange={(e) => setLots(Number(e.target.value) || 1)}
                  className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] font-mono font-bold"
                />
              </div>
              <div className="col-span-2">
                <label className="flex justify-between okz-muted mb-1">
                  <span>Simulated Underlying Move (%)</span>
                  <span className="font-mono font-bold text-[var(--text)]">
                    {shockPct >= 0 ? "+" : ""}
                    {shockPct}%
                  </span>
                </label>
                <input
                  type="range"
                  min={-10}
                  max={10}
                  step={0.5}
                  value={shockPct}
                  onChange={(e) => setShockPct(Number(e.target.value))}
                  className="w-full accent-amber-400"
                />
              </div>
            </div>

            {pmexCalc && (
              <div className="p-3.5 rounded-xl bg-[var(--bg-elevated)] border border-[var(--border)] space-y-2 text-xs">
                <div className="flex justify-between">
                  <span className="okz-muted">Total Contract Value ({pmexCalc.safeLots} lot)</span>
                  <span className="font-mono font-bold">
                    Rs {pmexCalc.notional.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Est. Initial Margin ({activeItem.margin_pct}%)</span>
                  <span className="font-mono font-bold text-amber-400">
                    Rs {pmexCalc.margin.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="okz-muted">Implied Leverage</span>
                  <span className="font-mono font-semibold">{pmexCalc.leverage.toFixed(1)}x</span>
                </div>
                <div className="flex justify-between pt-2 border-t border-[var(--border)]">
                  <span className="okz-muted">Simulated PnL ({shockPct >= 0 ? "+" : ""}{shockPct}% move)</span>
                  <span className={`font-mono font-black ${pmexCalc.pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                    {pmexCalc.pnl >= 0 ? "+" : ""}Rs {pmexCalc.pnl.toLocaleString(undefined, { maximumFractionDigits: 0 })}{" "}
                    ({pmexCalc.roePct >= 0 ? "+" : ""}
                    {pmexCalc.roePct.toFixed(1)}% on margin)
                  </span>
                </div>
              </div>
            )}

            <div className="text-[11px] okz-muted leading-snug">
              <strong className="text-[var(--text)]">PSX Link:</strong> {activeItem.psx_impact}
              <div className="flex flex-wrap gap-1.5 mt-1.5">
                {activeItem.psx_tickers.map((t) => (
                  <Link
                    key={t}
                    href={`/stocks/${encodeURIComponent(t)}`}
                    className="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 font-mono font-bold hover:bg-emerald-500/25"
                  >
                    ${t}
                  </Link>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Category Filter + Full PMEX Futures Table */}
      <div className="okz-card p-5 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-bold">Global Futures &amp; PMEX PKR Contract Board ({filtered.length})</h2>
          <div className="flex flex-wrap gap-1.5">
            {categories.map((cat) => (
              <button
                key={cat}
                type="button"
                onClick={() => setCategory(cat)}
                className={`px-2.5 py-1 rounded text-xs font-semibold transition ${
                  category === cat
                    ? "bg-amber-500 text-slate-950"
                    : "bg-[var(--card-muted)] okz-muted hover:text-[var(--text)]"
                }`}
              >
                {cat === "ALL" ? "All Commodities" : cat}
              </button>
            ))}
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="border-b border-[var(--border)] okz-muted text-left">
              <tr>
                <th className="py-2.5 px-3">Commodity</th>
                <th className="py-2.5 px-3">PMEX Contract &amp; Lot</th>
                <th className="py-2.5 px-3 text-right">Global Quote</th>
                <th className="py-2.5 px-3 text-right">1D / 1M / 1Y</th>
                <th className="py-2.5 px-3 text-right">PMEX Notional (PKR)</th>
                <th className="py-2.5 px-3 text-right">Est. Margin</th>
                <th className="py-2.5 px-3">PSX Beneficiaries / Impacted</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)]">
              {filtered.map((c) => {
                const isSelected = c.code === activeItem?.code;
                return (
                  <tr
                    key={c.code}
                    onClick={() => setSelectedCode(c.code)}
                    className={`cursor-pointer transition ${
                      isSelected ? "bg-amber-500/10" : "hover:bg-[var(--card-muted)]/50"
                    }`}
                  >
                    <td className="py-2.5 px-3">
                      <div className="font-bold">{c.name}</div>
                      <div className="text-[10px] okz-muted">
                        {c.category} &middot; {c.unit_label}
                      </div>
                    </td>
                    <td className="py-2.5 px-3">
                      <div className="font-mono font-semibold text-amber-400">{c.pmex_contract}</div>
                      <div className="text-[10px] okz-muted">{c.pmex_lot_desc}</div>
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono font-bold">
                      {c.price_usd != null ? formatNumber(c.price_usd, 2) : "—"}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono whitespace-nowrap">
                      <span className={pctColorClass(c.change_1d_pct)}>{formatPct(c.change_1d_pct)}</span>
                      <span className="okz-muted"> / </span>
                      <span className={pctColorClass(c.return_1m_pct)}>{formatPct(c.return_1m_pct)}</span>
                      <span className="okz-muted"> / </span>
                      <span className={pctColorClass(c.return_1y_pct)}>{formatPct(c.return_1y_pct)}</span>
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono font-semibold">
                      {c.pmex_notional_pkr != null
                        ? `Rs ${c.pmex_notional_pkr.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                        : "—"}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono text-amber-400">
                      {c.pmex_est_margin_pkr != null
                        ? `Rs ${c.pmex_est_margin_pkr.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                        : "—"}
                    </td>
                    <td className="py-2.5 px-3" onClick={(e) => e.stopPropagation()}>
                      <div className="flex flex-wrap gap-1">
                        {c.psx_tickers.map((t) => (
                          <Link
                            key={t}
                            href={`/stocks/${encodeURIComponent(t)}`}
                            className="px-1.5 py-0.5 rounded bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 font-mono text-[10px] font-bold"
                          >
                            {t}
                          </Link>
                        ))}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* PSX Sector Commodity Transmission Map */}
      <div className="space-y-3">
        <h2 className="text-sm font-bold flex items-center gap-1.5">
          <Layers className="w-4 h-4 text-emerald-500" />
          PSX Sector Commodity Transmission Map
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {(data?.commodities || []).slice(0, 9).map((c) => (
            <div key={c.code} className="okz-card p-3.5 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold">{c.name}</span>
                <span className={`text-xs font-mono font-semibold ${pctColorClass(c.return_1y_pct)}`}>
                  1Y {formatPct(c.return_1y_pct)}
                </span>
              </div>
              <p className="text-[11px] okz-muted leading-snug">{c.psx_impact}</p>
              <div className="flex flex-wrap gap-1 pt-0.5">
                {c.psx_tickers.map((t) => (
                  <Link
                    key={t}
                    href={`/stocks/${encodeURIComponent(t)}`}
                    className="px-1.5 py-0.5 rounded bg-[var(--card-muted)] hover:text-emerald-400 font-mono text-[10px] font-bold"
                  >
                    {t}
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
