"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Grid2X2, ShieldCheck } from "lucide-react";
import { ResponsiveContainer, Tooltip as RechartsTooltip, Treemap } from "recharts";
import { apiFetch } from "@/lib/api";
import { formatNumber, formatPct, pctColorClass } from "@/lib/format";

interface HeatmapStock {
  symbol: string;
  name: string;
  sector: string;
  last_price: number;
  change_pct: number;
  market_cap_mn: number;
  size: number;
  volume: number;
  is_shariah_compliant: boolean;
}

interface HeatmapSector {
  sector: string;
  market_cap_mn: number;
  weight_sum: number;
  change_pct: number;
  stock_count: number;
  advancers: number;
  decliners: number;
  children: HeatmapStock[];
}

interface HeatmapPayload {
  breadth: {
    total: number;
    advancers: number;
    decliners: number;
    unchanged: number;
    advance_decline_ratio: number;
    total_market_cap_bn: number;
    total_volume: number;
  };
  sectors: HeatmapSector[];
}

function tileColor(chg: number): string {
  if (chg >= 3.0) return "#047857";
  if (chg >= 1.2) return "#059669";
  if (chg > 0.1) return "#065f46";
  if (chg <= -3.0) return "#be123c";
  if (chg <= -1.2) return "#e11d48";
  if (chg < -0.1) return "#881337";
  return "#334155";
}

function CustomTreemapCell(props: {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  symbol?: string;
  change_pct?: number;
  last_price?: number;
  onSelect?: (sym: string) => void;
}) {
  const { x = 0, y = 0, width = 0, height = 0, symbol, change_pct = 0, onSelect } = props;
  if (!symbol || width < 14 || height < 14) return null;
  const fill = tileColor(change_pct);
  const showText = width >= 46 && height >= 30;
  const showPct = width >= 46 && height >= 44;

  return (
    <g
      onClick={() => onSelect && onSelect(symbol)}
      style={{ cursor: "pointer" }}
    >
      <rect
        x={x + 1}
        y={y + 1}
        width={Math.max(0, width - 2)}
        height={Math.max(0, height - 2)}
        rx={4}
        fill={fill}
        stroke="rgba(15,23,42,0.75)"
        strokeWidth={1.5}
      />
      {showText && (
        <text
          x={x + width / 2}
          y={y + height / 2 - (showPct ? 4 : -3)}
          textAnchor="middle"
          fill="#ffffff"
          fontSize={width > 90 ? 12 : 10}
          fontWeight={700}
        >
          {symbol}
        </text>
      )}
      {showPct && (
        <text
          x={x + width / 2}
          y={y + height / 2 + 11}
          textAnchor="middle"
          fill="rgba(255,255,255,0.9)"
          fontSize={10}
          fontFamily="monospace"
        >
          {change_pct >= 0 ? "+" : ""}
          {change_pct.toFixed(2)}%
        </text>
      )}
    </g>
  );
}

export function MarketHeatmap() {
  const router = useRouter();
  const [data, setData] = useState<HeatmapPayload | null>(null);
  const [indexFilter, setIndexFilter] = useState<"ALL" | "KSE100" | "KMI30">("KSE100");
  const [shariahOnly, setShariahOnly] = useState(false);

  useEffect(() => {
    const sp = new URLSearchParams();
    sp.set("index_only", indexFilter);
    if (shariahOnly) sp.set("shariah_only", "true");
    apiFetch<HeatmapPayload>(`/api/v1/market/heatmap?${sp.toString()}`)
      .then(setData)
      .catch(() => {});
  }, [indexFilter, shariahOnly]);

  if (!data || !data.sectors || data.sectors.length === 0) {
    return (
      <div className="okz-card p-5 text-xs okz-muted">
        Loading PSX Market Treemap Heatmap...
      </div>
    );
  }

  const { breadth, sectors } = data;
  const advPct = breadth.total > 0 ? (breadth.advancers / breadth.total) * 100 : 0;
  const decPct = breadth.total > 0 ? (breadth.decliners / breadth.total) * 100 : 0;
  const flatTiles: HeatmapStock[] = sectors.flatMap((s) => s.children).slice(0, 42);

  return (
    <div className="okz-card p-5 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-bold flex items-center gap-1.5">
            <Grid2X2 className="w-4 h-4 text-emerald-500" />
            PSX Market Map &amp; Sector Breadth (Market-Cap Weighted)
          </h2>
          <p className="text-[11px] okz-muted">
            Click any stock tile to launch its institutional workbench. Sized by Market Cap, colored by session return.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          {(["KSE100", "KMI30", "ALL"] as const).map((idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => setIndexFilter(idx)}
              className={`px-2.5 py-1 rounded-md font-semibold border transition ${
                indexFilter === idx
                  ? "bg-emerald-600 text-white border-emerald-600"
                  : "border-[var(--border)] okz-muted hover:border-emerald-500"
              }`}
            >
              {idx === "ALL" ? "All PSX" : idx}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setShariahOnly((v) => !v)}
            className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-md font-semibold border transition ${
              shariahOnly
                ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/40"
                : "border-[var(--border)] okz-muted hover:border-emerald-500"
            }`}
          >
            <ShieldCheck className="w-3.5 h-3.5" />
            Shariah
          </button>
        </div>
      </div>

      {/* Market Breadth Bar */}
      <div className="space-y-1.5 text-xs">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-3 font-mono">
            <span className="text-emerald-400 font-bold">Adv: {breadth.advancers}</span>
            <span className="text-rose-400 font-bold">Dec: {breadth.decliners}</span>
            <span className="okz-muted">Unch: {breadth.unchanged}</span>
            <span className="px-1.5 py-0.5 rounded bg-[var(--card-muted)] font-semibold">
              A/D {breadth.advance_decline_ratio.toFixed(2)}x
            </span>
          </div>
          <div className="flex items-center gap-3 okz-muted font-mono text-[11px]">
            <span>MCap: Rs {formatNumber(breadth.total_market_cap_bn, 0)} Bn</span>
            <span>Vol: {formatNumber(breadth.total_volume / 1_000_000, 1)}M</span>
          </div>
        </div>
        <div className="h-2 w-full rounded-full overflow-hidden flex bg-slate-700/50">
          <div className="bg-emerald-500 h-full" style={{ width: `${advPct}%` }} />
          <div className="bg-slate-500 h-full" style={{ width: `${Math.max(0, 100 - advPct - decPct)}%` }} />
          <div className="bg-rose-500 h-full" style={{ width: `${decPct}%` }} />
        </div>
      </div>

      {/* Interactive Recharts Treemap */}
      <div className="h-72 w-full rounded-lg overflow-hidden border border-[var(--border)] bg-[var(--bg-elevated)]/40">
        <ResponsiveContainer width="100%" height="100%">
          <Treemap
            data={flatTiles as unknown as Array<Record<string, unknown>>}
            dataKey="size"
            nameKey="symbol"
            aspectRatio={4 / 3}
            isAnimationActive={false}
            content={<CustomTreemapCell onSelect={(sym) => router.push(`/stocks/${encodeURIComponent(sym)}`)} />}
          >
            <RechartsTooltip
              content={({ active, payload }) => {
                if (!active || !payload || !payload[0]) return null;
                const d = payload[0].payload as HeatmapStock;
                return (
                  <div className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-2.5 text-xs shadow-lg space-y-0.5">
                    <div className="font-bold flex items-center justify-between gap-3">
                      <span>{d.symbol}</span>
                      <span className={pctColorClass(d.change_pct)}>{formatPct(d.change_pct)}</span>
                    </div>
                    <div className="okz-muted text-[11px]">{d.name}</div>
                    <div className="text-[11px] font-mono pt-1">
                      Rs {formatNumber(d.last_price, 2)} &bull; MCap Rs {formatNumber(d.market_cap_mn / 1000, 1)} Bn
                    </div>
                    <div className="text-[10px] okz-muted">{d.sector}</div>
                  </div>
                );
              }}
            />
          </Treemap>
        </ResponsiveContainer>
      </div>

      {/* Sector Performance Pills */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 text-xs">
        {sectors.slice(0, 6).map((sec) => (
          <div
            key={sec.sector}
            className="p-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)]/50 space-y-0.5"
          >
            <div className="text-[11px] font-semibold truncate" title={sec.sector}>
              {sec.sector}
            </div>
            <div className={`text-sm font-bold font-mono ${pctColorClass(sec.change_pct)}`}>
              {formatPct(sec.change_pct)}
            </div>
            <div className="text-[10px] okz-muted font-mono">
              {sec.advancers}&#9650; / {sec.decliners}&#9660; &bull; {sec.stock_count} stocks
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
