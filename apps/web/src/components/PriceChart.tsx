"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickData,
  ColorType,
  HistogramData,
  LineData,
  Time,
  createChart,
} from "lightweight-charts";
import { BollingerBands, EMA, MACD, RSI, SMA } from "technicalindicators";
import {
  Bar,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useAppContext } from "./CurrencyContext";

export interface BarPoint {
  bar_time: string;
  open: string | number;
  high: string | number;
  low: string | number;
  close: string | number;
  volume: string | number;
}

export function PriceChart({
  bars,
  mode = "candlestick",
  intraday = false,
  showVolume = false,
  showIndicatorControls = true,
  height = 340,
}: {
  bars: BarPoint[];
  mode?: "candlestick" | "area";
  intraday?: boolean;
  showVolume?: boolean;
  showIndicatorControls?: boolean;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const { theme } = useAppContext();
  const [showSma20, setShowSma20] = useState(true);
  const [showEma50, setShowEma50] = useState(false);
  const [showSma200, setShowSma200] = useState(false);
  const [showBollinger, setShowBollinger] = useState(false);
  const [showOscillators, setShowOscillators] = useState(false);

  const clientMomentum = useMemo(() => {
    if (bars.length < 15) {
      return {
        rsi14: null as number | null,
        macdHist: null as number | null,
        oscSeries: [] as Array<{ date: string; rsi: number | null; macdHist: number | null }>,
      };
    }
    const sorted = [...bars].sort((a, b) => new Date(a.bar_time).getTime() - new Date(b.bar_time).getTime());
    const closes = sorted.map((b) => Number(b.close));
    const rsiArr = RSI.calculate({ values: closes, period: 14 });
    const macdArr =
      closes.length >= 35
        ? MACD.calculate({
            values: closes,
            fastPeriod: 12,
            slowPeriod: 26,
            signalPeriod: 9,
            SimpleMAOscillator: false,
            SimpleMASignal: false,
          })
        : [];
    const rsiOffset = closes.length - rsiArr.length;
    const macdOffset = closes.length - macdArr.length;
    const oscSeries = sorted
      .map((b, i) => ({
        date: b.bar_time.slice(0, 10),
        rsi: i >= rsiOffset ? Number(rsiArr[i - rsiOffset]?.toFixed(2)) : null,
        macdHist:
          i >= macdOffset && macdArr[i - macdOffset]?.histogram != null
            ? Number(macdArr[i - macdOffset].histogram!.toFixed(3))
            : null,
      }))
      .slice(-90);
    const lastRsi = rsiArr.length > 0 ? rsiArr[rsiArr.length - 1] : null;
    const lastMacd = macdArr.length > 0 ? macdArr[macdArr.length - 1]?.histogram ?? null : null;
    return { rsi14: lastRsi, macdHist: lastMacd, oscSeries };
  }, [bars]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || bars.length === 0) return;

    const isDark = theme === "dark";
    const hasDuplicateDays =
      intraday || new Set(bars.map((b) => b.bar_time.slice(0, 10))).size < bars.length;

    const chart = createChart(el, {
      width: el.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: isDark ? "#0f172a" : "#ffffff" },
        textColor: isDark ? "#94a3b8" : "#475569",
      },
      grid: {
        vertLines: { color: isDark ? "#1e293b" : "#f1f5f9" },
        horzLines: { color: isDark ? "#1e293b" : "#f1f5f9" },
      },
      timeScale: {
        borderColor: isDark ? "#1e293b" : "#e2e8f0",
        timeVisible: hasDuplicateDays,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: isDark ? "#1e293b" : "#e2e8f0",
        scaleMargins: showVolume ? { top: 0.08, bottom: 0.24 } : { top: 0.1, bottom: 0.1 },
      },
    });

    const normalized = bars
      .map((b) => ({
        bar: b,
        key: hasDuplicateDays ? Math.floor(new Date(b.bar_time).getTime() / 1000) : b.bar_time.slice(0, 10),
        sortMs: new Date(b.bar_time).getTime(),
      }))
      .sort((a, b) => a.sortMs - b.sortMs);

    const seenKeys = new Set<string | number>();
    const deduped = normalized.filter((item) => {
      if (seenKeys.has(item.key)) return false;
      seenKeys.add(item.key);
      return true;
    });

    if (mode === "area") {
      const series = chart.addAreaSeries({
        lineColor: "#10b981",
        topColor: "rgba(16, 185, 129, 0.35)",
        bottomColor: "rgba(16, 185, 129, 0.02)",
      });
      const data: LineData<Time>[] = deduped.map((item) => ({
        time: item.key as Time,
        value: Number(item.bar.close),
      }));
      series.setData(data);
    } else {
      const series = chart.addCandlestickSeries({
        upColor: "#10b981",
        downColor: "#f43f5e",
        borderVisible: false,
        wickUpColor: "#10b981",
        wickDownColor: "#f43f5e",
      });
      const data: CandlestickData<Time>[] = deduped.map((item) => ({
        time: item.key as Time,
        open: Number(item.bar.open),
        high: Number(item.bar.high),
        low: Number(item.bar.low),
        close: Number(item.bar.close),
      }));
      series.setData(data);
    }

    const closes = deduped.map((d) => Number(d.bar.close));

    if (showSma20 && closes.length >= 20) {
      const smaVals = SMA.calculate({ period: 20, values: closes });
      const offset = closes.length - smaVals.length;
      const smaSeries = chart.addLineSeries({
        color: "#f59e0b",
        lineWidth: 2,
        title: "SMA 20",
      });
      smaSeries.setData(
        smaVals.map((val, idx) => ({
          time: deduped[idx + offset].key as Time,
          value: val,
        })),
      );
    }

    if (showEma50 && closes.length >= 50) {
      const emaVals = EMA.calculate({ period: 50, values: closes });
      const offset = closes.length - emaVals.length;
      const emaSeries = chart.addLineSeries({
        color: "#38bdf8",
        lineWidth: 2,
        title: "EMA 50",
      });
      emaSeries.setData(
        emaVals.map((val, idx) => ({
          time: deduped[idx + offset].key as Time,
          value: val,
        })),
      );
    }

    if (showSma200 && closes.length >= 200) {
      const sma200Vals = SMA.calculate({ period: 200, values: closes });
      const offset = closes.length - sma200Vals.length;
      const sma200Series = chart.addLineSeries({
        color: "#f97316",
        lineWidth: 2,
        title: "SMA 200",
      });
      sma200Series.setData(
        sma200Vals.map((val, idx) => ({
          time: deduped[idx + offset].key as Time,
          value: val,
        })),
      );
    }

    if (showBollinger && closes.length >= 20) {
      const bbVals = BollingerBands.calculate({ period: 20, stdDev: 2, values: closes });
      const offset = closes.length - bbVals.length;
      const bbUpper = chart.addLineSeries({
        color: "rgba(168, 85, 247, 0.75)",
        lineWidth: 1,
        title: "BB Upper",
      });
      const bbLower = chart.addLineSeries({
        color: "rgba(168, 85, 247, 0.75)",
        lineWidth: 1,
        title: "BB Lower",
      });
      bbUpper.setData(
        bbVals.map((b, idx) => ({
          time: deduped[idx + offset].key as Time,
          value: b.upper,
        })),
      );
      bbLower.setData(
        bbVals.map((b, idx) => ({
          time: deduped[idx + offset].key as Time,
          value: b.lower,
        })),
      );
    }

    if (showVolume && deduped.some((d) => Number(d.bar.volume) > 0)) {
      const volumeSeries = chart.addHistogramSeries({
        priceFormat: { type: "volume" },
        priceScaleId: "",
      });
      volumeSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.78, bottom: 0 },
      });
      const volData: HistogramData<Time>[] = deduped.map((item) => {
        const up = Number(item.bar.close) >= Number(item.bar.open);
        return {
          time: item.key as Time,
          value: Number(item.bar.volume),
          color: up ? "rgba(16, 185, 129, 0.45)" : "rgba(244, 63, 94, 0.45)",
        };
      });
      volumeSeries.setData(volData);
    }

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [bars, mode, intraday, showVolume, showSma20, showEma50, showSma200, showBollinger, height, theme]);

  if (bars.length === 0) {
    return (
      <div className="h-64 flex items-center justify-center text-sm okz-muted border border-dashed border-[var(--border)] rounded-lg">
        No historical bars available yet.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {showIndicatorControls && bars.length >= 20 && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="okz-muted mr-1 font-medium">Overlays:</span>
            <button
              type="button"
              onClick={() => setShowSma20((v) => !v)}
              className={`px-2 py-0.5 rounded border transition-colors ${
                showSma20
                  ? "bg-amber-500/15 text-amber-400 border-amber-500/40 font-semibold"
                  : "border-[var(--border)] okz-muted"
              }`}
            >
              SMA 20
            </button>
            <button
              type="button"
              onClick={() => setShowEma50((v) => !v)}
              className={`px-2 py-0.5 rounded border transition-colors ${
                showEma50
                  ? "bg-sky-500/15 text-sky-400 border-sky-500/40 font-semibold"
                  : "border-[var(--border)] okz-muted"
              }`}
            >
              EMA 50
            </button>
            <button
              type="button"
              onClick={() => setShowSma200((v) => !v)}
              disabled={bars.length < 200}
              title={bars.length < 200 ? "Requires 200+ daily bars" : "Toggle 200-Day SMA"}
              className={`px-2 py-0.5 rounded border transition-colors ${
                bars.length < 200
                  ? "border-[var(--border)] okz-muted opacity-45 cursor-not-allowed"
                  : showSma200
                    ? "bg-orange-500/15 text-orange-400 border-orange-500/40 font-semibold"
                    : "border-[var(--border)] okz-muted"
              }`}
            >
              SMA 200
            </button>
            <button
              type="button"
              onClick={() => setShowBollinger((v) => !v)}
              className={`px-2 py-0.5 rounded border transition-colors ${
                showBollinger
                  ? "bg-purple-500/15 text-purple-400 border-purple-500/40 font-semibold"
                  : "border-[var(--border)] okz-muted"
              }`}
            >
              Bollinger Bands (20,2)
            </button>
            <button
              type="button"
              onClick={() => setShowOscillators((v) => !v)}
              className={`px-2 py-0.5 rounded border transition-colors ${
                showOscillators
                  ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/40 font-semibold"
                  : "border-[var(--border)] okz-muted"
              }`}
            >
              RSI / MACD Pane
            </button>
          </div>
          <div className="flex items-center gap-2">
            {clientMomentum.rsi14 !== null && (
              <span
                className={`px-2 py-0.5 rounded font-mono ${
                  clientMomentum.rsi14 >= 70
                    ? "bg-rose-500/15 text-rose-400"
                    : clientMomentum.rsi14 <= 30
                      ? "bg-emerald-500/15 text-emerald-400"
                      : "bg-slate-800/60 text-slate-300"
                }`}
              >
                RSI(14): {clientMomentum.rsi14.toFixed(1)}
              </span>
            )}
            {clientMomentum.macdHist !== null && (
              <span
                className={`px-2 py-0.5 rounded font-mono ${
                  clientMomentum.macdHist >= 0
                    ? "bg-emerald-500/15 text-emerald-400"
                    : "bg-rose-500/15 text-rose-400"
                }`}
              >
                MACD Hist: {clientMomentum.macdHist >= 0 ? "+" : ""}
                {clientMomentum.macdHist.toFixed(2)}
              </span>
            )}
          </div>
        </div>
      )}
      <div ref={containerRef} className="w-full" />
      {showOscillators && clientMomentum.oscSeries.length > 0 && (
        <div className="pt-2 border-t border-[var(--border)]">
          <div className="flex items-center justify-between text-[11px] okz-muted mb-1">
            <span>Oscillators: RSI (14, purple, 30/70 bounds) &amp; MACD Histogram (emerald)</span>
            <span>Last {clientMomentum.oscSeries.length} bars</span>
          </div>
          <div className="h-36 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={clientMomentum.oscSeries} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#94a3b8" }} minTickGap={24} />
                <YAxis yAxisId="rsi" domain={[0, 100]} tick={{ fontSize: 10, fill: "#94a3b8" }} width={28} />
                <YAxis yAxisId="macd" orientation="right" tick={{ fontSize: 10, fill: "#94a3b8" }} width={34} />
                <RechartsTooltip
                  contentStyle={{
                    backgroundColor: "var(--card)",
                    borderColor: "var(--border)",
                    fontSize: "11px",
                    borderRadius: "8px",
                  }}
                />
                <ReferenceLine yAxisId="rsi" y={70} stroke="#f43f5e" strokeDasharray="3 3" />
                <ReferenceLine yAxisId="rsi" y={30} stroke="#10b981" strokeDasharray="3 3" />
                <Bar yAxisId="macd" dataKey="macdHist" name="MACD Hist" fill="#10b981" opacity={0.45} />
                <Line
                  yAxisId="rsi"
                  type="monotone"
                  dataKey="rsi"
                  name="RSI(14)"
                  stroke="#a855f7"
                  strokeWidth={2}
                  dot={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}

export interface BenchmarkPoint {
  date: string;
  portfolio_index: number;
  benchmark_index: number;
}

export function BenchmarkCompareChart({
  series,
  benchmarkSymbol,
  height = 240,
}: {
  series: BenchmarkPoint[];
  benchmarkSymbol: string;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const { theme } = useAppContext();

  useEffect(() => {
    const el = containerRef.current;
    if (!el || series.length === 0) return;

    const isDark = theme === "dark";
    const chart = createChart(el, {
      width: el.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: isDark ? "#0f172a" : "#ffffff" },
        textColor: isDark ? "#94a3b8" : "#475569",
      },
      grid: {
        vertLines: { color: isDark ? "#1e293b" : "#f1f5f9" },
        horzLines: { color: isDark ? "#1e293b" : "#f1f5f9" },
      },
      timeScale: { borderColor: isDark ? "#1e293b" : "#e2e8f0" },
      rightPriceScale: { borderColor: isDark ? "#1e293b" : "#e2e8f0" },
    });

    const portLine = chart.addLineSeries({
      color: "#10b981",
      lineWidth: 2,
      title: "Portfolio NAV",
    });
    const benchLine = chart.addLineSeries({
      color: "#38bdf8",
      lineWidth: 2,
      title: benchmarkSymbol,
    });

    portLine.setData(series.map((s) => ({ time: s.date as Time, value: s.portfolio_index })));
    benchLine.setData(series.map((s) => ({ time: s.date as Time, value: s.benchmark_index })));

    chart.timeScale().fitContent();
    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [series, benchmarkSymbol, height, theme]);

  if (series.length === 0) {
    return (
      <div className="h-48 flex items-center justify-center text-sm okz-muted border border-dashed border-[var(--border)] rounded-lg">
        No overlapping benchmark history yet.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-4 text-xs">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
          Portfolio TWRR (Base 100)
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-sky-400" />
          {benchmarkSymbol} (Base 100)
        </span>
      </div>
      <div ref={containerRef} className="w-full" />
    </div>
  );
}
