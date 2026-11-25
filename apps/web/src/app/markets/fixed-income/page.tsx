"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Calculator, Globe2, Landmark, ShieldCheck } from "lucide-react";
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
import { TableSkeleton } from "@/components/Skeletons";
import { apiFetchCached } from "@/lib/api";

interface FixedIncomeInstrument {
  code: string;
  name: string;
  issuer: string;
  category: string;
  tenor: string;
  rate_pct: number;
  is_shariah: boolean;
  wht_exempt: boolean;
  zakat_exempt: boolean;
  payout_frequency?: string;
  eligibility?: string;
  rate_source?: string;
  as_of_date?: string;
}

interface RdaNpcRate {
  code: string;
  name: string;
  currency: string;
  tenor: string;
  rate_pct: number;
  is_shariah: boolean;
  wht_pct: number;
}

interface TopCashFund {
  symbol: string;
  name: string;
  amc_name: string;
  fund_category: string;
  is_shariah: boolean;
  is_daily_dividend: boolean;
  rate_pct: number;
  ter_pct: number;
}

interface FixedIncomePayload {
  policy_rate_pct: number;
  cpi_yoy_pct: number | null;
  kibor_6m_pct: number | null;
  sovereign: FixedIncomeInstrument[];
  sukuk: FixedIncomeInstrument[];
  nss: FixedIncomeInstrument[];
  rda_npc: RdaNpcRate[];
  top_money_market_funds: TopCashFund[];
}

interface UnifiedRow {
  code: string;
  name: string;
  issuer: string;
  category: string;
  tenor: string;
  gross_rate_pct: number;
  wht_rate_pct: number;
  zakat_rate_pct: number;
  net_yield_pct: number;
  real_yield_pct: number | null;
  monthly_net_pkr: number;
  annual_net_pkr: number;
  is_shariah: boolean;
  wht_exempt: boolean;
  zakat_exempt: boolean;
  note?: string;
  href?: string;
}

export default function FixedIncomeHubPage() {
  const [data, setData] = useState<FixedIncomePayload | null>(null);
  const [loading, setLoading] = useState(true);

  // Interactive After-Tax & Zakat Comparator controls
  const [principal, setPrincipal] = useState<number>(1_000_000);
  const [isFiler, setIsFiler] = useState<boolean>(true);
  const [applyZakat, setApplyZakat] = useState<boolean>(false);
  const [shariahOnly, setShariahOnly] = useState<boolean>(false);
  const [catFilter, setCatFilter] = useState<string>("ALL");

  useEffect(() => {
    apiFetchCached<FixedIncomePayload>("/api/v1/market/fixed-income", 300_000, { onUpdate: setData })
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const whtPct = isFiler ? 15.0 : 30.0;
  const cpi = data?.cpi_yoy_pct ?? null;

  const unifiedRows = useMemo<UnifiedRow[]>(() => {
    if (!data) return [];
    const safePrincipal = Math.max(0, principal || 0);
    const all: Array<{
      code: string;
      name: string;
      issuer: string;
      category: string;
      tenor: string;
      rate_pct: number;
      is_shariah: boolean;
      wht_exempt: boolean;
      zakat_exempt: boolean;
      note?: string;
      href?: string;
    }> = [
      ...data.nss.map((s) => ({
        ...s,
        note: s.eligibility || s.payout_frequency,
      })),
      ...data.sukuk.map((s) => ({
        ...s,
        note: s.payout_frequency || "PSX-listed GoP Sovereign Sukuk",
      })),
      ...data.sovereign.map((s) => ({
        ...s,
        note: s.as_of_date ? `SBP PKRV as of ${s.as_of_date}` : "SBP Sovereign Auction",
      })),
      ...data.top_money_market_funds.slice(0, 8).map((f) => ({
        code: f.symbol,
        name: f.name,
        issuer: f.amc_name,
        category: f.is_daily_dividend ? "Daily Div Mutual Fund" : "Money Market Fund",
        tenor: `Liquid (TER ${f.ter_pct}%)`,
        rate_pct: f.rate_pct,
        is_shariah: f.is_shariah,
        wht_exempt: false,
        zakat_exempt: false,
        note: f.fund_category,
        href: `/funds/${encodeURIComponent(f.symbol)}`,
      })),
    ];

    return all
      .filter((item) => {
        if (shariahOnly && !item.is_shariah) return false;
        if (catFilter !== "ALL") {
          if (catFilter === "NSS" && !item.category.includes("NSS")) return false;
          if (catFilter === "SUKUK" && item.category !== "GoP Ijarah Sukuk") return false;
          if (catFilter === "SOVEREIGN" && item.category !== "T-Bill" && item.category !== "PIB Bond") return false;
          if (catFilter === "FUNDS" && !item.category.includes("Fund")) return false;
        }
        return true;
      })
      .map((item) => {
        const effWht = item.wht_exempt ? 0.0 : whtPct;
        const effZakat = applyZakat && !item.zakat_exempt ? 2.5 : 0.0;
        const afterTaxPct = item.rate_pct * (1 - effWht / 100);
        const netYieldPct = Math.max(0, afterTaxPct - effZakat);
        const annualNet = safePrincipal * (netYieldPct / 100);
        const monthlyNet = annualNet / 12;
        const realYield = cpi != null ? netYieldPct - cpi : null;
        return {
          code: item.code,
          name: item.name,
          issuer: item.issuer,
          category: item.category,
          tenor: item.tenor,
          gross_rate_pct: item.rate_pct,
          wht_rate_pct: effWht,
          zakat_rate_pct: effZakat,
          net_yield_pct: Number(netYieldPct.toFixed(2)),
          real_yield_pct: realYield != null ? Number(realYield.toFixed(2)) : null,
          monthly_net_pkr: Math.round(monthlyNet),
          annual_net_pkr: Math.round(annualNet),
          is_shariah: item.is_shariah,
          wht_exempt: item.wht_exempt,
          zakat_exempt: item.zakat_exempt,
          note: item.note,
          href: item.href,
        };
      })
      .sort((a, b) => b.net_yield_pct - a.net_yield_pct);
  }, [data, principal, whtPct, applyZakat, shariahOnly, catFilter, cpi]);

  const chartData = useMemo(() => {
    return unifiedRows.slice(0, 10).map((r) => ({
      name: r.code.replace("NSS_", "").replace("GIS_", "GIS "),
      fullName: r.name,
      Gross: Number(r.gross_rate_pct.toFixed(2)),
      Net: r.net_yield_pct,
    }));
  }, [unifiedRows]);

  if (loading && !data) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">Fixed Income, GoP Ijarah Sukuk &amp; National Savings (NSS)</h1>
        <TableSkeleton rows={10} cols={7} />
      </div>
    );
  }

  const realPolicyRate =
    data?.policy_rate_pct != null && data?.cpi_yoy_pct != null
      ? data.policy_rate_pct - data.cpi_yoy_pct
      : null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Landmark className="w-6 h-6 text-emerald-500" />
          Pakistan Fixed Income, GoP Ijarah Sukuk &amp; National Savings (NSS)
        </h1>
        <p className="text-sm okz-muted">
          Compare live SBP T-Bills &amp; PIBs, PSX-listed GoP Ijarah Sukuk, CDNS National Savings Schemes (Behbood,
          Sarwa Islamic, RIC, DSC), RDA Naya Pakistan Certificates &amp; top daily-dividend mutual funds on an exact{" "}
          <strong>After-Tax &amp; Zakat</strong> basis.
        </p>
      </div>

      {/* Macro Anchor Strip */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="okz-card p-4">
          <div className="text-[11px] okz-muted uppercase tracking-wider">SBP Policy Rate</div>
          <div className="text-2xl font-black font-mono text-emerald-400 mt-1">
            {data?.policy_rate_pct?.toFixed(2)}%
          </div>
          <div className="text-[11px] okz-muted mt-0.5">Monetary Policy benchmark</div>
        </div>
        <div className="okz-card p-4">
          <div className="text-[11px] okz-muted uppercase tracking-wider">6-Month KIBOR</div>
          <div className="text-2xl font-black font-mono mt-1">
            {data?.kibor_6m_pct != null ? `${data.kibor_6m_pct.toFixed(2)}%` : "—"}
          </div>
          <div className="text-[11px] okz-muted mt-0.5">Interbank floating benchmark</div>
        </div>
        <div className="okz-card p-4">
          <div className="text-[11px] okz-muted uppercase tracking-wider">PBS Monthly CPI YoY</div>
          <div className="text-2xl font-black font-mono text-amber-400 mt-1">
            {data?.cpi_yoy_pct != null ? `${data.cpi_yoy_pct.toFixed(2)}%` : "—"}
          </div>
          <div className="text-[11px] okz-muted mt-0.5">Headline inflation hurdle</div>
        </div>
        <div className="okz-card p-4">
          <div className="text-[11px] okz-muted uppercase tracking-wider">Real Interest Rate</div>
          <div
            className={`text-2xl font-black font-mono mt-1 ${
              (realPolicyRate ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {realPolicyRate != null ? `${realPolicyRate >= 0 ? "+" : ""}${realPolicyRate.toFixed(2)}%` : "—"}
          </div>
          <div className="text-[11px] okz-muted mt-0.5">SBP Policy Rate minus PBS CPI</div>
        </div>
      </div>

      {/* Interactive After-Tax Income & Zakat Controls */}
      <div className="okz-card p-5 space-y-4 border-emerald-500/30">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-bold flex items-center gap-1.5">
              <Calculator className="w-4 h-4 text-emerald-500" />
              After-Tax Monthly Income &amp; Zakat Yield Comparator
            </h2>
            <p className="text-xs okz-muted">
              Automatically applies Behbood / Pensioners WHT &amp; Zakat exemptions, RIC/STSC/Sukuk Zakat exemptions, and FBR ATL status.
            </p>
          </div>
          <div className="flex flex-wrap gap-1.5 text-xs">
            {[
              { id: "ALL", label: "All Instruments" },
              { id: "NSS", label: "CDNS NSS" },
              { id: "SUKUK", label: "GoP Ijarah Sukuk" },
              { id: "SOVEREIGN", label: "T-Bills & PIBs" },
              { id: "FUNDS", label: "Top Cash Funds" },
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setCatFilter(tab.id)}
                className={`px-2.5 py-1 rounded font-semibold transition ${
                  catFilter === tab.id
                    ? "bg-emerald-600 text-white"
                    : "bg-[var(--card-muted)] okz-muted hover:text-[var(--text)]"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end text-xs">
          <div>
            <label className="block okz-muted mb-1">Investment Amount (PKR)</label>
            <input
              type="number"
              min={10000}
              step={50000}
              value={principal}
              onChange={(e) => setPrincipal(Number(e.target.value) || 0)}
              className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] font-mono font-bold text-sm"
            />
            <div className="flex flex-wrap gap-1 mt-1.5">
              {[500_000, 1_000_000, 5_000_000, 10_000_000].map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPrincipal(p)}
                  className="px-2 py-0.5 rounded bg-[var(--card-muted)] hover:bg-emerald-500/20 font-mono text-[10px]"
                >
                  Rs {p >= 1_000_000 ? `${p / 1_000_000}M` : `${p / 1_000}K`}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block okz-muted mb-1">FBR Tax Status (Profit on Debt WHT)</label>
            <div className="grid grid-cols-2 gap-1.5">
              <button
                type="button"
                onClick={() => setIsFiler(true)}
                className={`py-2 rounded-md font-semibold border transition ${
                  isFiler
                    ? "bg-emerald-600 text-white border-emerald-600"
                    : "border-[var(--border)] bg-[var(--bg)] okz-muted"
                }`}
              >
                Active Filer (15%)
              </button>
              <button
                type="button"
                onClick={() => setIsFiler(false)}
                className={`py-2 rounded-md font-semibold border transition ${
                  !isFiler
                    ? "bg-rose-600 text-white border-rose-600"
                    : "border-[var(--border)] bg-[var(--bg)] okz-muted"
                }`}
              >
                Non-Filer (30%)
              </button>
            </div>
          </div>

          <div className="space-y-2 pb-1">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={applyZakat} onChange={(e) => setApplyZakat(e.target.checked)} />
              <span>Deduct 2.5% Annual Zakat (respects exemptions)</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer text-emerald-400 font-semibold">
              <input type="checkbox" checked={shariahOnly} onChange={(e) => setShariahOnly(e.target.checked)} />
              <span>Shariah-Compliant Only (Sukuk, Sarwa, Islamic Funds)</span>
            </label>
          </div>

          {unifiedRows[0] && (
            <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30">
              <div className="text-[10px] okz-muted uppercase">Top Net Take-Home ({unifiedRows[0].category})</div>
              <div className="text-sm font-bold truncate mt-0.5">{unifiedRows[0].name}</div>
              <div className="text-lg font-black font-mono text-emerald-400">
                Rs {unifiedRows[0].monthly_net_pkr.toLocaleString()}/mo{" "}
                <span className="text-xs font-normal">({unifiedRows[0].net_yield_pct}% net)</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Gross vs Net After-Tax Yield Comparison Chart */}
      {chartData.length > 0 && (
        <div className="okz-card p-5 space-y-2">
          <h2 className="text-sm font-bold">Top 10 Fixed-Income Yields: Gross % vs Net After-Tax{applyZakat ? " & Zakat" : ""} %</h2>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="name" tick={{ fontSize: 10, fill: "#94a3b8" }} />
                <YAxis unit="%" tick={{ fontSize: 10, fill: "#94a3b8" }} width={42} />
                <RechartsTooltip
                  contentStyle={{
                    backgroundColor: "var(--card)",
                    borderColor: "var(--border)",
                    fontSize: "12px",
                    borderRadius: "8px",
                  }}
                />
                <Legend wrapperStyle={{ fontSize: "11px" }} />
                <Bar dataKey="Gross" name="Gross Rate %" fill="#38bdf8" radius={[4, 4, 0, 0]} />
                <Bar dataKey="Net" name="Net Effective Yield %" fill="#10b981" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Ranked Unified Fixed-Income Table */}
      <div className="okz-card overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="border-b border-[var(--border)] okz-muted text-left">
            <tr>
              <th className="py-3 px-4">Instrument &amp; Issuer</th>
              <th className="py-3 px-3">Category &amp; Tenor</th>
              <th className="py-3 px-3 text-right">Gross %</th>
              <th className="py-3 px-3 text-right">WHT / Zakat</th>
              <th className="py-3 px-3 text-right">Net Yield %</th>
              <th className="py-3 px-3 text-right">Real vs CPI</th>
              <th className="py-3 px-3 text-right">Monthly Net (PKR)</th>
              <th className="py-3 px-4 text-right">Annual Net (PKR)</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)]">
            {unifiedRows.map((r) => (
              <tr key={r.code} className="hover:bg-[var(--card-muted)]/50">
                <td className="py-2.5 px-4">
                  <div className="font-semibold flex items-center gap-1.5 flex-wrap">
                    {r.href ? (
                      <Link href={r.href} className="text-emerald-400 hover:underline">
                        {r.name}
                      </Link>
                    ) : (
                      <span>{r.name}</span>
                    )}
                    {r.is_shariah && (
                      <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 text-[10px] font-bold">
                        <ShieldCheck className="w-3 h-3" /> Shariah
                      </span>
                    )}
                    {r.wht_exempt && (
                      <span className="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-400 text-[10px] font-bold">
                        WHT Exempt
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] okz-muted">
                    {r.issuer}
                    {r.note ? ` · ${r.note}` : ""}
                  </div>
                </td>
                <td className="py-2.5 px-3">
                  <div className="font-medium">{r.category}</div>
                  <div className="text-[11px] okz-muted">{r.tenor}</div>
                </td>
                <td className="py-2.5 px-3 text-right font-mono font-semibold">{r.gross_rate_pct.toFixed(2)}%</td>
                <td className="py-2.5 px-3 text-right font-mono okz-muted">
                  {r.wht_rate_pct}% / {r.zakat_rate_pct}%
                </td>
                <td className="py-2.5 px-3 text-right font-mono font-black text-emerald-400">
                  {r.net_yield_pct.toFixed(2)}%
                </td>
                <td
                  className={`py-2.5 px-3 text-right font-mono font-semibold ${
                    (r.real_yield_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"
                  }`}
                >
                  {r.real_yield_pct != null ? `${r.real_yield_pct >= 0 ? "+" : ""}${r.real_yield_pct.toFixed(2)}%` : "—"}
                </td>
                <td className="py-2.5 px-3 text-right font-mono font-bold">
                  Rs {r.monthly_net_pkr.toLocaleString()}
                </td>
                <td className="py-2.5 px-4 text-right font-mono">Rs {r.annual_net_pkr.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* SBP Roshan Digital Account (RDA) Naya Pakistan Certificates */}
      <div className="okz-card p-5 space-y-3">
        <div>
          <h2 className="text-sm font-bold flex items-center gap-1.5">
            <Globe2 className="w-4 h-4 text-sky-400" />
            SBP Roshan Digital Account (RDA) — Naya Pakistan Certificates (NPC &amp; Islamic NPC)
          </h2>
          <p className="text-xs okz-muted">
            For Overseas Pakistanis investing via RDA — flat 10% Final Tax Regime (FTR) WHT and fully Zakat-exempt.
          </p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {(data?.rda_npc || []).map((npc) => {
            const netPct = npc.rate_pct * (1 - npc.wht_pct / 100);
            return (
              <div key={npc.code} className="p-3.5 rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)]/60 space-y-1.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-bold">{npc.name}</span>
                  {npc.is_shariah && (
                    <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 text-[10px] font-bold">
                      Shariah
                    </span>
                  )}
                </div>
                <div className="flex items-baseline justify-between font-mono">
                  <span className="text-xl font-black text-sky-400">{npc.rate_pct.toFixed(2)}%</span>
                  <span className="text-xs text-emerald-400 font-semibold">Net {netPct.toFixed(2)}% (10% FTR)</span>
                </div>
                <div className="text-[11px] okz-muted">
                  Currency: <strong>{npc.currency}</strong> &middot; Tenor: {npc.tenor}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
