"use client";

import React, { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { ShieldCheck } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { formatMoney, formatPct, pctColorClass } from "@/lib/format";
import { useAppContext } from "@/components/CurrencyContext";

interface SharedPayload {
  portfolio_name: string;
  hide_amounts: boolean;
  shariah_compliant_pct: string;
  total_unrealized_pnl_pct: string;
  xirr_pct: string | null;
  twrr_pct: string | null;
  total_market_value_pkr: string | null;
  allocation_by_class: Array<{ asset_class: string; allocation_pct: string; market_value_pkr: string | null }>;
  holdings: Array<{
    symbol: string;
    name: string;
    asset_class: string;
    is_shariah_compliant: boolean;
    allocation_pct: string;
    unrealized_pnl_pct: string;
    market_value_pkr: string | null;
  }>;
}

export default function SharedPortfolioPage() {
  const params = useParams<{ token: string }>();
  const token = String(params.token || "");
  const { currency, usdToPkr } = useAppContext();
  const [data, setData] = useState<SharedPayload | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token) return;
    apiFetch<SharedPayload>(`/api/v1/share/${token}`)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Share link unavailable"));
  }, [token]);

  if (error) return <div className="okz-card p-6 text-sm text-rose-500">{error}</div>;
  if (!data) return <div className="okz-card p-6 text-sm okz-muted">Loading shared portfolio snapshot...</div>;

  return (
    <div className="space-y-6">
      <div className="okz-card p-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <span className="text-xs px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-500 font-semibold">
            Read-Only Shared Snapshot {data.hide_amounts ? "• Amounts Hidden" : ""}
          </span>
          <h1 className="text-2xl font-bold mt-2">{data.portfolio_name}</h1>
        </div>
        <div className="flex gap-6 text-sm">
          <div>
            <div className="text-xs okz-muted">Unrealized Return</div>
            <div className={`font-bold text-lg ${pctColorClass(data.total_unrealized_pnl_pct)}`}>
              {formatPct(data.total_unrealized_pnl_pct)}
            </div>
          </div>
          <div>
            <div className="text-xs okz-muted">XIRR</div>
            <div className="font-bold text-lg text-emerald-500">{data.xirr_pct ? `${data.xirr_pct}%` : "—"}</div>
          </div>
          <div>
            <div className="text-xs okz-muted">TWRR</div>
            <div className="font-bold text-lg text-emerald-500">{data.twrr_pct ? `${data.twrr_pct}%` : "—"}</div>
          </div>
          <div>
            <div className="text-xs okz-muted">Shariah %</div>
            <div className="font-bold text-lg">{data.shariah_compliant_pct}%</div>
          </div>
        </div>
      </div>

      <div className="okz-card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-[var(--border)] text-xs okz-muted text-left">
            <tr>
              <th className="py-3 px-4">Asset</th>
              <th className="py-3 px-4">Class</th>
              <th className="py-3 px-4 text-right">Allocation %</th>
              <th className="py-3 px-4 text-right">Return %</th>
              {!data.hide_amounts && <th className="py-3 px-4 text-right">Value</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)]">
            {data.holdings.map((h) => (
              <tr key={h.symbol}>
                <td className="py-3 px-4 font-bold flex items-center gap-1.5">
                  {h.symbol}
                  {h.is_shariah_compliant && <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />}
                </td>
                <td className="py-3 px-4 text-xs okz-muted">{h.asset_class}</td>
                <td className="py-3 px-4 text-right font-semibold">{h.allocation_pct}%</td>
                <td className={`py-3 px-4 text-right ${pctColorClass(h.unrealized_pnl_pct)}`}>
                  {formatPct(h.unrealized_pnl_pct)}
                </td>
                {!data.hide_amounts && (
                  <td className="py-3 px-4 text-right">{formatMoney(h.market_value_pkr, currency, usdToPkr)}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
