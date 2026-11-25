"use client";

import React, { useEffect, useMemo, useState } from "react";
import { ArrowLeftRight, Globe2, Search, TrendingUp } from "lucide-react";
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

interface FxCorridor {
  code: string;
  pair: string;
  name: string;
  region: string;
  interbank_pkr: number;
  open_market_buy_pkr: number;
  open_market_sell_pkr: number;
  usd_cross: number;
  change_pct: number;
  history_key: string | null;
}

interface GlobalMajorPair {
  pair: string;
  base: string;
  quote: string;
  name: string;
  rate: number;
}

interface ForexHubPayload {
  updated_at: string;
  usd_pkr_interbank: number;
  usd_pkr_open_sell: number;
  dxy: {
    symbol: string;
    name: string;
    price: number | null;
    change_1d_pct: number | null;
    return_1m_pct: number | null;
    return_1y_pct: number | null;
  };
  corridors: FxCorridor[];
  global_majors: GlobalMajorPair[];
  rates_per_usd: Record<string, number>;
  history: Record<string, Array<{ date: string; rate: number }>>;
}

const HISTORY_TABS: Array<{ key: string; label: string }> = [
  { key: "USD_PKR_INTERBANK", label: "USD / PKR" },
  { key: "EURPKR", label: "EUR / PKR" },
  { key: "GBPPKR", label: "GBP / PKR" },
  { key: "AEDPKR", label: "AED / PKR" },
  { key: "SARPKR", label: "SAR / PKR" },
];

export default function ForexHubPage() {
  const [data, setData] = useState<ForexHubPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [activeHistKey, setActiveHistKey] = useState("USD_PKR_INTERBANK");

  // Multi-currency converter state
  const [amount, setAmount] = useState<string>("1000");
  const [fromCode, setFromCode] = useState<string>("USD");
  const [toCode, setToCode] = useState<string>("PKR");

  useEffect(() => {
    apiFetchCached<ForexHubPayload>("/api/v1/market/forex", 180_000, { onUpdate: setData })
      .then((res) => {
        setData(res);
        const avail = Object.keys(res.history || {});
        if (avail.length > 0 && !avail.includes("USD_PKR_INTERBANK")) {
          setActiveHistKey(avail[0]);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const currencyChoices = useMemo(() => {
    const base = [{ code: "PKR", name: "Pakistani Rupee" }];
    for (const c of data?.corridors || []) {
      base.push({ code: c.code, name: c.name });
    }
    return base;
  }, [data]);

  const conversion = useMemo(() => {
    if (!data?.rates_per_usd) return null;
    const amt = Math.max(0, Number(amount) || 0);
    const rFrom = data.rates_per_usd[fromCode];
    const rTo = data.rates_per_usd[toCode];
    if (!rFrom || !rTo) return null;

    const unitRate = rTo / rFrom;
    const interbankVal = amt * unitRate;

    // Open market buy/sell spread estimate when PKR is one leg
    const involvesPkr = fromCode === "PKR" || toCode === "PKR";
    const openBuyVal = involvesPkr
      ? toCode === "PKR"
        ? interbankVal * 1.005
        : interbankVal / 1.011
      : null;
    const openSellVal = involvesPkr
      ? toCode === "PKR"
        ? interbankVal * 1.011
        : interbankVal / 1.005
      : null;

    return { unitRate, interbankVal, openBuyVal, openSellVal, involvesPkr };
  }, [data, amount, fromCode, toCode]);

  const filteredCorridors = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (data?.corridors || []).filter(
      (c) =>
        !q ||
        c.code.toLowerCase().includes(q) ||
        c.name.toLowerCase().includes(q) ||
        c.region.toLowerCase().includes(q),
    );
  }, [data, search]);

  const histSeries = data?.history?.[activeHistKey] || [];
  const histStats = useMemo(() => {
    if (histSeries.length < 2) return null;
    const first = histSeries[0].rate;
    const last = histSeries[histSeries.length - 1].rate;
    const min = Math.min(...histSeries.map((p) => p.rate));
    const max = Math.max(...histSeries.map((p) => p.rate));
    const chgPct = first > 0 ? ((last / first) - 1) * 100 : 0;
    return { first, last, min, max, chgPct };
  }, [histSeries]);

  const eurC = data?.corridors.find((c) => c.code === "EUR");
  const gbpC = data?.corridors.find((c) => c.code === "GBP");
  const sarC = data?.corridors.find((c) => c.code === "SAR");
  const aedC = data?.corridors.find((c) => c.code === "AED");

  if (loading && !data) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">Forex &amp; Remittance Hub</h1>
        <TableSkeleton rows={10} cols={6} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <ArrowLeftRight className="w-6 h-6 text-emerald-500" />
            Pakistan Forex &amp; Global FX Hub
          </h1>
          <p className="text-sm okz-muted">
            Live PKR &harr; 14 FCY corridors (SBP Interbank vs Open Market spreads), global major FX pairs, ICE US
            Dollar Index (DXY), and 5-year historical PKR trajectories.
          </p>
        </div>
        {data?.updated_at && (
          <span className="text-xs okz-muted font-mono">
            Updated: {new Date(data.updated_at).toLocaleString()}
          </span>
        )}
      </div>

      {/* Top FX KPI Strip */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <div className="okz-card p-4">
          <div className="text-[11px] okz-muted uppercase tracking-wider">USD / PKR Interbank</div>
          <div className="text-2xl font-black font-mono text-emerald-400 mt-1">
            Rs {formatNumber(data?.usd_pkr_interbank, 2)}
          </div>
          <div className="text-[11px] okz-muted mt-0.5">SBP benchmark mid-rate</div>
        </div>
        <div className="okz-card p-4">
          <div className="text-[11px] okz-muted uppercase tracking-wider">USD / PKR Open Market</div>
          <div className="text-2xl font-black font-mono mt-1">
            Rs {formatNumber(data?.usd_pkr_open_sell, 2)}
          </div>
          <div className="text-[11px] text-amber-400 mt-0.5">
            Spread: +Rs {formatNumber((data?.usd_pkr_open_sell || 0) - (data?.usd_pkr_interbank || 0), 2)}
          </div>
        </div>
        <div className="okz-card p-4">
          <div className="text-[11px] okz-muted uppercase tracking-wider">US Dollar Index (DXY)</div>
          <div className="text-2xl font-black font-mono mt-1">
            {data?.dxy?.price != null ? data.dxy.price.toFixed(2) : "—"}
          </div>
          <div className={`text-[11px] font-mono mt-0.5 ${pctColorClass(data?.dxy?.change_1d_pct)}`}>
            1D {formatPct(data?.dxy?.change_1d_pct)} &middot; 1Y {formatPct(data?.dxy?.return_1y_pct)}
          </div>
        </div>
        <div className="okz-card p-4">
          <div className="text-[11px] okz-muted uppercase tracking-wider">EUR &amp; GBP / PKR</div>
          <div className="text-sm font-bold font-mono mt-1.5 space-y-0.5">
            <div className="flex justify-between">
              <span className="okz-muted">EUR</span>
              <span>Rs {formatNumber(eurC?.interbank_pkr, 2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="okz-muted">GBP</span>
              <span>Rs {formatNumber(gbpC?.interbank_pkr, 2)}</span>
            </div>
          </div>
        </div>
        <div className="okz-card p-4">
          <div className="text-[11px] okz-muted uppercase tracking-wider">Gulf Remittance (SAR / AED)</div>
          <div className="text-sm font-bold font-mono mt-1.5 space-y-0.5">
            <div className="flex justify-between">
              <span className="okz-muted">SAR</span>
              <span>Rs {formatNumber(sarC?.interbank_pkr, 2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="okz-muted">AED</span>
              <span>Rs {formatNumber(aedC?.interbank_pkr, 2)}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Converter + 5Y PKR History Chart */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="lg:col-span-5 okz-card p-5 space-y-4">
          <div>
            <h2 className="text-sm font-bold flex items-center gap-1.5">
              <ArrowLeftRight className="w-4 h-4 text-emerald-500" />
              Interactive Multi-Currency &amp; Remittance Converter
            </h2>
            <p className="text-xs okz-muted">
              Convert between PKR and 14 global currencies with Interbank vs Open Market spread preview.
            </p>
          </div>

          <div className="space-y-3">
            <div>
              <label className="block text-xs okz-muted mb-1">Amount</label>
              <input
                type="number"
                min="0"
                step="any"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] font-mono text-base font-bold"
              />
              <div className="flex flex-wrap gap-1.5 mt-1.5">
                {["100", "500", "1000", "5000", "100000"].map((preset) => (
                  <button
                    key={preset}
                    type="button"
                    onClick={() => setAmount(preset)}
                    className="px-2 py-0.5 rounded bg-[var(--card-muted)] hover:bg-emerald-500/20 text-[11px] font-mono"
                  >
                    {Number(preset).toLocaleString()}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-[1fr_auto_1fr] gap-2 items-end">
              <div>
                <label className="block text-xs okz-muted mb-1">From</label>
                <select
                  value={fromCode}
                  onChange={(e) => setFromCode(e.target.value)}
                  className="w-full px-2.5 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs font-semibold"
                >
                  {currencyChoices.map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.code} — {c.name}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="button"
                onClick={() => {
                  setFromCode(toCode);
                  setToCode(fromCode);
                }}
                title="Swap currencies"
                className="p-2 rounded-md border border-[var(--border)] hover:border-emerald-500 hover:bg-[var(--card-muted)]"
              >
                <ArrowLeftRight className="w-4 h-4 text-emerald-500" />
              </button>
              <div>
                <label className="block text-xs okz-muted mb-1">To</label>
                <select
                  value={toCode}
                  onChange={(e) => setToCode(e.target.value)}
                  className="w-full px-2.5 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs font-semibold"
                >
                  {currencyChoices.map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.code} — {c.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {conversion && (
              <div className="p-4 rounded-xl bg-[var(--bg-elevated)] border border-emerald-500/30 space-y-2">
                <div className="text-[11px] okz-muted">
                  1 {fromCode} ={" "}
                  <span className="font-mono font-bold text-[var(--text)]">
                    {conversion.unitRate >= 10
                      ? conversion.unitRate.toFixed(2)
                      : conversion.unitRate.toFixed(4)}{" "}
                    {toCode}
                  </span>{" "}
                  (Interbank / Mid)
                </div>
                <div className="text-2xl font-black font-mono text-emerald-400">
                  {toCode}{" "}
                  {conversion.interbankVal.toLocaleString(undefined, {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </div>
                {conversion.involvesPkr && conversion.openBuyVal != null && conversion.openSellVal != null && (
                  <div className="grid grid-cols-2 gap-2 pt-2 border-t border-[var(--border)] text-xs">
                    <div>
                      <div className="okz-muted text-[10px]">Est. Open Market Buy</div>
                      <div className="font-mono font-semibold">
                        {toCode} {conversion.openBuyVal.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                      </div>
                    </div>
                    <div>
                      <div className="okz-muted text-[10px]">Est. Open Market Sell</div>
                      <div className="font-mono font-semibold">
                        {toCode} {conversion.openSellVal.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="lg:col-span-7 okz-card p-5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-bold flex items-center gap-1.5">
                <TrendingUp className="w-4 h-4 text-sky-400" />
                5-Year Historical PKR Exchange Rate Trend
              </h2>
              {histStats && (
                <p className="text-xs okz-muted font-mono">
                  Range: Rs {histStats.min.toFixed(2)} – Rs {histStats.max.toFixed(2)} &middot; Period Change:{" "}
                  <span className={histStats.chgPct >= 0 ? "text-amber-400 font-semibold" : "text-emerald-400 font-semibold"}>
                    {histStats.chgPct >= 0 ? "+" : ""}
                    {histStats.chgPct.toFixed(1)}%
                  </span>
                </p>
              )}
            </div>
            <div className="flex flex-wrap gap-1">
              {HISTORY_TABS.filter((t) => (data?.history?.[t.key] || []).length > 1).map((t) => (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => setActiveHistKey(t.key)}
                  className={`px-2.5 py-1 rounded text-xs font-semibold transition ${
                    activeHistKey === t.key
                      ? "bg-emerald-600 text-white"
                      : "bg-[var(--card-muted)] okz-muted hover:text-[var(--text)]"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          {histSeries.length > 1 ? (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={histSeries} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="fxAreaGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#10b981" stopOpacity={0.35} />
                      <stop offset="95%" stopColor="#10b981" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#94a3b8" }} minTickGap={30} />
                  <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "#94a3b8" }} width={52} />
                  <RechartsTooltip
                    contentStyle={{
                      backgroundColor: "var(--card)",
                      borderColor: "var(--border)",
                      fontSize: "12px",
                      borderRadius: "8px",
                    }}
                    formatter={(val) => [`Rs ${Number(val).toFixed(2)}`, "PKR Rate"]}
                  />
                  <Area type="monotone" dataKey="rate" stroke="#10b981" strokeWidth={2} fill="url(#fxAreaGrad)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="h-64 flex items-center justify-center text-xs okz-muted">
              Historical FX series will populate after running FX sync in Data Pipeline Status.
            </div>
          )}
        </div>
      </div>

      {/* PKR <-> 14 FCY Corridors Table */}
      <div className="okz-card p-5 space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-bold">PKR &harr; Major World &amp; Gulf Remittance Corridors (14 Currencies)</h2>
            <p className="text-xs okz-muted">
              Click any currency row to load it directly into the converter or plot its 5Y PKR chart.
            </p>
          </div>
          <div className="relative w-full sm:w-64">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 okz-muted" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter USD, AED, SAR, GBP..."
              className="w-full pl-8 pr-3 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
            />
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="border-b border-[var(--border)] okz-muted text-left">
              <tr>
                <th className="py-2.5 px-3">Pair</th>
                <th className="py-2.5 px-3">Currency &amp; Region</th>
                <th className="py-2.5 px-3 text-right">SBP Interbank (PKR)</th>
                <th className="py-2.5 px-3 text-right">Open Mkt Buy</th>
                <th className="py-2.5 px-3 text-right">Open Mkt Sell</th>
                <th className="py-2.5 px-3 text-right">Units per 1 USD</th>
                <th className="py-2.5 px-3 text-right">1D Chg</th>
                <th className="py-2.5 px-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)]">
              {filteredCorridors.map((c) => (
                <tr key={c.code} className="hover:bg-[var(--card-muted)]/50">
                  <td className="py-2.5 px-3 font-mono font-bold text-emerald-400">{c.pair}</td>
                  <td className="py-2.5 px-3">
                    <div className="font-semibold">{c.name}</div>
                    <div className="text-[11px] okz-muted">{c.region}</div>
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono font-bold">
                    Rs {formatNumber(c.interbank_pkr, c.interbank_pkr >= 10 ? 2 : 4)}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono">
                    Rs {formatNumber(c.open_market_buy_pkr, c.interbank_pkr >= 10 ? 2 : 4)}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono text-amber-400">
                    Rs {formatNumber(c.open_market_sell_pkr, c.interbank_pkr >= 10 ? 2 : 4)}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono okz-muted">{c.usd_cross.toFixed(4)}</td>
                  <td className={`py-2.5 px-3 text-right font-mono ${pctColorClass(c.change_pct)}`}>
                    {formatPct(c.change_pct)}
                  </td>
                  <td className="py-2.5 px-3 text-right space-x-2 whitespace-nowrap">
                    <button
                      type="button"
                      onClick={() => {
                        setFromCode(c.code);
                        setToCode("PKR");
                      }}
                      className="text-sky-400 hover:underline font-semibold"
                    >
                      Convert
                    </button>
                    {c.history_key && (
                      <button
                        type="button"
                        onClick={() => setActiveHistKey(c.history_key!)}
                        className="text-emerald-400 hover:underline font-semibold"
                      >
                        5Y Chart
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 9 Global Major FX Pairs Board */}
      <div className="space-y-3">
        <h2 className="text-sm font-bold flex items-center gap-1.5">
          <Globe2 className="w-4 h-4 text-sky-400" />
          Global Major Forex Crosses
        </h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          {(data?.global_majors || []).map((m) => (
            <button
              key={m.pair}
              type="button"
              onClick={() => {
                setFromCode(m.base);
                setToCode(m.quote);
              }}
              className="okz-card p-3.5 text-left hover:border-sky-500/50 transition space-y-1"
            >
              <div className="text-xs font-mono font-bold text-sky-400">{m.pair}</div>
              <div className="text-lg font-black font-mono">{m.rate.toFixed(m.rate < 20 ? 4 : 2)}</div>
              <div className="text-[11px] okz-muted truncate">{m.name}</div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
