"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  Copy,
  Download,
  FileSpreadsheet,
  Globe2,
  Landmark,
  Layers,
  Pencil,
  Plus,
  Share2,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart as RechartsLineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useAppContext } from "@/components/CurrencyContext";
import { CsvImportModal } from "@/components/CsvImportModal";
import { DividendSnowballCard } from "@/components/DividendSnowballCard";
import { BenchmarkCompareChart, BenchmarkPoint, PriceChart } from "@/components/PriceChart";
import { EditableTransaction, TransactionModal } from "@/components/TransactionModal";
import { apiFetch } from "@/lib/api";
import { formatMoney, formatNumber, formatPct, pctColorClass } from "@/lib/format";

interface PortfolioItem {
  id: string;
  name: string;
  base_currency: string;
  description?: string;
  auto_manage_cash: boolean;
  is_default?: boolean;
}

interface HoldingRow {
  asset_id: string;
  symbol: string;
  name: string;
  asset_class: string;
  is_shariah_compliant: boolean;
  quantity: string;
  avg_cost_pkr: string;
  cost_basis_pkr: string;
  current_price_pkr: string;
  market_value_pkr: string;
  unrealized_pnl_pkr: string;
  unrealized_pnl_pct: string;
  realized_pnl_pkr: string;
  dividends_net_pkr: string;
  shariah_purification_pkr: string;
  allocation_pct: string;
  currency?: string;
  avg_cost_native?: string;
  current_price_native?: string;
  native_return_pct?: string;
  fx_return_pct?: string;
  is_daily_dividend_fund?: boolean;
  annualized_yield_pct?: string | null;
  accrued_daily_dividend_pkr?: string;
  est_daily_income_pkr?: string;
  est_monthly_income_pkr?: string;
}

interface PortfolioQuantPayload {
  benchmark_symbol: string;
  risk_free_rate_pct: number;
  metrics: {
    annualized_return_pct: number;
    annualized_volatility_pct: number;
    sharpe_ratio: number;
    sortino_ratio: number;
    calmar_ratio: number;
    max_drawdown_pct: number;
    current_drawdown_pct: number;
    var_95_daily_pct: number;
    cvar_95_daily_pct: number;
    win_rate_pct: number;
    best_day_pct: number;
    worst_day_pct: number;
    beta: number | null;
    alpha_annualized_pct: number | null;
    r_squared: number | null;
    hhi_index?: number;
    effective_holdings_count?: number;
    top_holding_weight_pct?: number;
    diversification_rating?: "HIGH" | "MODERATE" | "CONCENTRATED" | "EMPTY" | string;
  };
  drawdown_series: Array<{ date: string; drawdown_pct: number }>;
  monthly_returns: Array<{ month: string; return_pct: number }>;
  correlation_matrix: {
    symbols: string[];
    matrix: number[][];
  };
  optimization: Array<{
    symbol: string;
    name: string;
    asset_class: string;
    current_weight_pct: number;
    max_sharpe_weight_pct: number;
    min_vol_weight_pct: number;
    hrp_weight_pct?: number;
    expected_annual_return_pct: number;
    annual_volatility_pct: number;
    last_price_pkr?: number;
    last_price_native?: number;
    currency?: string;
    sharpe_delta_weight_pct?: number;
    sharpe_trade_amount_pkr?: number;
    sharpe_estimated_units?: number;
    sharpe_action?: "BUY" | "SELL" | "HOLD" | string;
    min_vol_delta_weight_pct?: number;
    min_vol_trade_amount_pkr?: number;
    min_vol_estimated_units?: number;
    min_vol_action?: "BUY" | "SELL" | "HOLD" | string;
  }>;
  efficient_frontier?: Array<{
    return_pct: number;
    volatility_pct: number;
    sharpe_ratio: number;
  }>;
  monte_carlo?: Array<{
    month: number;
    p10_pkr: number;
    p25_pkr: number;
    p50_pkr: number;
    p75_pkr: number;
    p90_pkr: number;
  }>;
  stress_tests?: Array<{
    scenario: string;
    rationale: string;
    estimated_impact_pkr: number;
    estimated_impact_pct: number;
    projected_value_pkr: number;
  }>;
}

interface CgtByFyRow {
  fiscal_year: string;
  gross_gain_pkr: string;
  gross_loss_pkr: string;
  prior_loss_applied_pkr: string;
  net_taxable_gain_pkr: string;
  estimated_cgt_pkr: string;
  unused_loss_carryforward_pkr: string;
}

interface SummaryPayload {
  portfolio: PortfolioItem;
  usd_to_pkr: string;
  total_market_value_pkr: string;
  total_cost_basis_pkr: string;
  total_unrealized_pnl_pkr: string;
  total_unrealized_pnl_pct: string;
  total_realized_pnl_pkr: string;
  total_net_dividends_pkr: string;
  total_shariah_purification_pkr: string;
  total_fees_pkr: string;
  total_tax_withheld_pkr: string;
  estimated_cgt_pkr: string;
  xirr_pct: string | null;
  twrr_pct: string | null;
  nav_per_unit: string;
  shariah_compliant_pct: string;
  allocation_by_class: Array<{ asset_class: string; market_value_pkr: string; allocation_pct: string }>;
  daily_nav_series: Array<{ date: string; nav_per_unit: number }>;
  holdings: HoldingRow[];
  cgt_by_fy?: CgtByFyRow[];
  warnings?: string[];
  daily_dividend_auto_accrued_pkr?: string;
  daily_dividend_est_monthly_pkr?: string;
}

interface PendingAction {
  corp_action_id: string;
  symbol: string;
  action_type: string;
  ex_date: string;
  eligible_quantity: string;
  estimated_gross_pkr: string;
  estimated_wht_pkr: string;
  estimated_bonus_shares: string;
  deemed_ex_price: string;
  estimated_236z_tax_pkr: string;
  description: string;
}

interface TxRow extends EditableTransaction {
  shariah_purification_pkr: string;
}

interface FbrSummary {
  fiscal_year: string;
  period_start: string;
  period_end: string;
  filer_status: string;
  opening_cost_basis_pkr: string;
  closing_cost_basis_pkr: string;
  closing_market_value_pkr: string;
  total_gross_dividends_pkr: string;
  total_dividend_wht_pkr: string;
  total_236z_bonus_tax_pkr: string;
  total_realized_gain_pkr: string;
  gross_gain_pkr?: string;
  gross_loss_pkr?: string;
  prior_loss_applied_pkr?: string;
  net_taxable_gain_pkr?: string;
  estimated_cgt_pkr: string;
  unused_loss_carryforward_pkr?: string;
  total_shariah_purification_pkr: string;
  warnings?: string[];
}

interface ShareLinkRow {
  id: string;
  token: string;
  hide_amounts: boolean;
  is_active: boolean;
  created_at: string;
}

interface PortfolioXrayPayload {
  portfolio_id: string;
  total_value_pkr: number;
  mutual_fund_value_pkr: number;
  fmr_covered_fund_pct: number;
  funds_with_fmr: Array<{ symbol: string; name: string; report_label: string; value_pkr: number }>;
  funds_missing_fmr: Array<{ symbol: string; name: string; value_pkr: number }>;
  look_through_asset_mix: Array<{ label: string; value_pkr: number; weight_pct: number }>;
  look_through_sectors: Array<{ sector: string; value_pkr: number; weight_pct: number }>;
  stock_overlap: Array<{
    symbol: string;
    name: string;
    sector: string;
    direct_pkr: number;
    via_funds_pkr: number;
    total_pkr: number;
    total_weight_pct: number;
    is_overlap: boolean;
    contributing_funds: Array<{ fund_symbol: string; weight_in_fund_pct: number; exposure_pkr: number }>;
  }>;
}

export default function PortfolioPage() {
  const { user, loadingUser, currency, usdToPkr, setUsdToPkr, refreshUser } = useAppContext();
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [summary, setSummary] = useState<SummaryPayload | null>(null);
  const [pendingActions, setPendingActions] = useState<PendingAction[]>([]);
  const [transactions, setTransactions] = useState<TxRow[]>([]);
  const [showTxModal, setShowTxModal] = useState(false);
  const [editingTx, setEditingTx] = useState<EditableTransaction | null>(null);
  const [showCsvModal, setShowCsvModal] = useState(false);
  const [showFbrPanel, setShowFbrPanel] = useState(false);
  const [fiscalYear, setFiscalYear] = useState("FY2025-26");
  const [fbrSummary, setFbrSummary] = useState<FbrSummary | null>(null);
  const [nssNotice, setNssNotice] = useState("");
  const [tab, setTab] = useState<"holdings" | "quant" | "ledger">("holdings");

  // Benchmark chart overlay
  const [benchmarkSymbol, setBenchmarkSymbol] = useState<"NONE" | "KSE100" | "KMI30" | "SPX">("NONE");
  const [benchmarkSeries, setBenchmarkSeries] = useState<BenchmarkPoint[]>([]);

  // Quant & Risk Lab
  const [quantData, setQuantData] = useState<PortfolioQuantPayload | null>(null);
  const [quantBenchmark, setQuantBenchmark] = useState<"KSE100" | "KMI30" | "SPX">("KSE100");
  const [quantRfRate, setQuantRfRate] = useState<string>("13.5");
  const [loadingQuant, setLoadingQuant] = useState(false);

  // Look-Through Portfolio X-Ray (SECP FMR)
  const [xrayData, setXrayData] = useState<PortfolioXrayPayload | null>(null);
  const [loadingXray, setLoadingXray] = useState(false);

  // Daily Dividend Fund Auto-Accrual Modal
  const [showDailyFundModal, setShowDailyFundModal] = useState(false);
  const [dailyFundMode, setDailyFundMode] = useState<"REINVEST" | "CASH">("REINVEST");
  const [dailyFundRollup, setDailyFundRollup] = useState<"MONTHLY" | "SINGLE" | "DAILY">("MONTHLY");
  const [dailyFundYieldOverride, setDailyFundYieldOverride] = useState<string>("");
  const [dailyFundSymbol, setDailyFundSymbol] = useState<string>("");
  const [accruingDailyFunds, setAccruingDailyFunds] = useState(false);

  // Portfolio Create/Edit Modal
  const [portfolioModalMode, setPortfolioModalMode] = useState<"create" | "edit" | null>(null);
  const [pfName, setPfName] = useState("");
  const [pfCurrency, setPfCurrency] = useState<"PKR" | "USD">("PKR");
  const [pfDesc, setPfDesc] = useState("");
  const [pfAutoCash, setPfAutoCash] = useState(true);
  const [pfError, setPfError] = useState<string | null>(null);

  // Share Links Manager Modal
  const [showShareModal, setShowShareModal] = useState(false);
  const [shareLinks, setShareLinks] = useState<ShareLinkRow[]>([]);
  const [shareHideAmounts, setShareHideAmounts] = useState(true);
  const [copiedToken, setCopiedToken] = useState<string | null>(null);

  const loadPortfolios = useCallback(async () => {
    if (!user) {
      setPortfolios([]);
      setActiveId("");
      setSummary(null);
      return;
    }
    const list = await apiFetch<PortfolioItem[]>("/api/v1/portfolios");
    setPortfolios(list);
    if (list.length > 0) {
      setActiveId((prev) => (list.some((p) => p.id === prev) ? prev : list[0].id));
    } else {
      setActiveId("");
      setSummary(null);
    }
  }, [user]);

  const loadDetail = useCallback(async () => {
    if (!user || !activeId) return;
    const [sum, pend, txs] = await Promise.all([
      apiFetch<SummaryPayload>(`/api/v1/portfolios/${activeId}/summary`),
      apiFetch<PendingAction[]>(`/api/v1/portfolios/${activeId}/pending-actions`),
      apiFetch<TxRow[]>(`/api/v1/portfolios/${activeId}/transactions`),
    ]);
    setSummary(sum);
    setPendingActions(pend);
    setTransactions(txs);
    if (Number(sum.usd_to_pkr) > 0) setUsdToPkr(Number(sum.usd_to_pkr));
  }, [user, activeId, setUsdToPkr]);

  const loadBenchmark = useCallback(async () => {
    if (!user || !activeId || benchmarkSymbol === "NONE") {
      setBenchmarkSeries([]);
      return;
    }
    try {
      const res = await apiFetch<{ benchmark_symbol: string; series: BenchmarkPoint[] }>(
        `/api/v1/portfolios/${activeId}/benchmark?symbol=${benchmarkSymbol}`
      );
      setBenchmarkSeries(res.series);
    } catch {
      setBenchmarkSeries([]);
    }
  }, [user, activeId, benchmarkSymbol]);

  const loadQuant = useCallback(async () => {
    if (!user || !activeId) return;
    setLoadingQuant(true);
    try {
      const rf = Number(quantRfRate) >= 0 ? Number(quantRfRate) : 13.5;
      const data = await apiFetch<PortfolioQuantPayload>(
        `/api/v1/portfolios/${activeId}/quant?benchmark=${quantBenchmark}&risk_free_rate_pct=${rf}`
      );
      setQuantData(data);
    } catch {
      setQuantData(null);
    } finally {
      setLoadingQuant(false);
    }
  }, [user, activeId, quantBenchmark, quantRfRate]);

  const loadXray = useCallback(async () => {
    if (!user || !activeId) return;
    setLoadingXray(true);
    try {
      const data = await apiFetch<PortfolioXrayPayload>(`/api/v1/portfolios/${activeId}/xray`);
      setXrayData(data);
    } catch {
      setXrayData(null);
    } finally {
      setLoadingXray(false);
    }
  }, [user, activeId]);

  const loadFbrSummary = useCallback(
    async (fy: string) => {
      if (!user || !activeId) return;
      const data = await apiFetch<FbrSummary>(
        `/api/v1/portfolios/${activeId}/fbr-summary?fiscal_year=${encodeURIComponent(fy)}`
      );
      setFbrSummary(data);
    },
    [user, activeId]
  );

  const loadShareLinks = useCallback(async () => {
    if (!user || !activeId) return;
    const links = await apiFetch<ShareLinkRow[]>(`/api/v1/portfolios/${activeId}/share-links`);
    setShareLinks(links);
  }, [user, activeId]);

  useEffect(() => {
    loadPortfolios();
  }, [loadPortfolios]);

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

  useEffect(() => {
    loadBenchmark();
  }, [loadBenchmark]);

  useEffect(() => {
    if (tab === "quant") {
      loadQuant();
      loadXray();
    }
  }, [tab, loadQuant, loadXray]);

  useEffect(() => {
    if (showFbrPanel) {
      loadFbrSummary(fiscalYear);
    }
  }, [showFbrPanel, fiscalYear, loadFbrSummary]);

  const openCreatePortfolio = () => {
    setPfError(null);
    setPfName("");
    setPfCurrency("PKR");
    setPfDesc("");
    setPfAutoCash(true);
    setPortfolioModalMode("create");
  };

  const openEditPortfolio = () => {
    const active = portfolios.find((p) => p.id === activeId);
    if (!active) return;
    setPfError(null);
    setPfName(active.name);
    setPfCurrency(active.base_currency === "USD" ? "USD" : "PKR");
    setPfDesc(active.description || "");
    setPfAutoCash(active.auto_manage_cash);
    setPortfolioModalMode("edit");
  };

  const handleSavePortfolio = async (e: React.FormEvent) => {
    e.preventDefault();
    setPfError(null);
    try {
      if (portfolioModalMode === "create") {
        const created = await apiFetch<PortfolioItem>("/api/v1/portfolios", {
          method: "POST",
          body: JSON.stringify({
            name: pfName.trim(),
            base_currency: pfCurrency,
            description: pfDesc.trim(),
            auto_manage_cash: pfAutoCash,
          }),
        });
        await loadPortfolios();
        setActiveId(created.id);
      } else if (portfolioModalMode === "edit" && activeId) {
        await apiFetch(`/api/v1/portfolios/${activeId}`, {
          method: "PATCH",
          body: JSON.stringify({
            name: pfName.trim(),
            base_currency: pfCurrency,
            description: pfDesc.trim(),
            auto_manage_cash: pfAutoCash,
          }),
        });
        await loadPortfolios();
        await loadDetail();
      }
      setPortfolioModalMode(null);
    } catch (err) {
      setPfError(err instanceof Error ? err.message : "Failed to save portfolio");
    }
  };

  const handleDeletePortfolio = async () => {
    if (!activeId || portfolios.length <= 1) return;
    const active = portfolios.find((p) => p.id === activeId);
    if (!window.confirm(`Delete portfolio "${active?.name}" and all its transactions?`)) return;
    await apiFetch(`/api/v1/portfolios/${activeId}`, { method: "DELETE" });
    setActiveId("");
    await loadPortfolios();
  };

  const handleApplyAction = async (caId: string) => {
    await apiFetch(`/api/v1/portfolios/${activeId}/apply-action/${caId}`, { method: "POST" });
    await loadDetail();
  };

  const handleAccrueNss = async () => {
    const res = await apiFetch<{ accrued_count: number; total_gross_pkr: string }>(
      `/api/v1/portfolios/${activeId}/accrue-nss`,
      { method: "POST" }
    );
    setNssNotice(
      res.accrued_count > 0
        ? `Accrued ${res.accrued_count} NSS coupon payouts (${formatMoney(res.total_gross_pkr, currency, usdToPkr)} gross).`
        : "No overdue National Savings coupons waiting to accrue."
    );
    await loadDetail();
  };

  const handleAccrueDailyFunds = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeId) return;
    setAccruingDailyFunds(true);
    try {
      const payload: Record<string, unknown> = {
        mode: dailyFundMode,
        rollup: dailyFundRollup,
      };
      if (dailyFundSymbol.trim()) {
        payload.asset_symbol = dailyFundSymbol.trim().toUpperCase();
      }
      if (dailyFundYieldOverride.trim() && Number(dailyFundYieldOverride) > 0) {
        payload.annual_yield_override_pct = Number(dailyFundYieldOverride);
      }
      const res = await apiFetch<{
        accrued_count: number;
        days_accrued: number;
        mode: string;
        rollup: string;
        total_gross_pkr: string;
        total_wht_pkr: string;
        total_net_pkr: string;
        total_reinvested_units: string;
      }>(`/api/v1/portfolios/${activeId}/accrue-daily-funds`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setShowDailyFundModal(false);
      setNssNotice(
        res.accrued_count > 0
          ? `Accrued ${res.days_accrued} days across ${res.accrued_count} ${res.rollup} ledger entries (${formatMoney(res.total_net_pkr, currency, usdToPkr)} net after WHT${res.mode === "REINVEST" ? `, +${formatNumber(res.total_reinvested_units, 4)} units reinvested at Par NAV` : ""}).`
          : "All Daily Dividend Funds in this portfolio are already accrued up to today."
      );
      await loadDetail();
      if (tab === "quant") await loadQuant();
    } catch (err) {
      setNssNotice(err instanceof Error ? err.message : "Failed to accrue daily dividend funds.");
    } finally {
      setAccruingDailyFunds(false);
    }
  };

  const handleDeleteTx = async (txId: string) => {
    await apiFetch(`/api/v1/portfolios/${activeId}/transactions/${txId}`, { method: "DELETE" });
    await loadDetail();
    if (benchmarkSymbol !== "NONE") await loadBenchmark();
  };

  const handleFilerChange = async (nextStatus: string) => {
    await apiFetch("/api/v1/auth/tax-profile", {
      method: "PATCH",
      body: JSON.stringify({ filer_status: nextStatus }),
    });
    await refreshUser();
    await loadDetail();
    if (showFbrPanel) await loadFbrSummary(fiscalYear);
  };

  const handleOpenShareModal = async () => {
    setShowShareModal(true);
    await loadShareLinks();
  };

  const handleCreateShareLink = async () => {
    await apiFetch(`/api/v1/portfolios/${activeId}/share-links`, {
      method: "POST",
      body: JSON.stringify({ hide_amounts: shareHideAmounts }),
    });
    await loadShareLinks();
  };

  const handleRevokeShareLink = async (linkId: string) => {
    await apiFetch(`/api/v1/portfolios/${activeId}/share-links/${linkId}`, { method: "DELETE" });
    await loadShareLinks();
  };

  const handleCopyShareLink = async (token: string) => {
    const url = `${window.location.origin}/share/${token}`;
    try {
      await navigator.clipboard.writeText(url);
      setCopiedToken(token);
      setTimeout(() => setCopiedToken(null), 2000);
    } catch {
      // Fallback ignored
    }
  };

  if (loadingUser) {
    return <div className="okz-card p-8 text-sm okz-muted">Checking session...</div>;
  }

  if (!user) {
    return (
      <div className="okz-card p-8 max-w-xl mx-auto text-center space-y-4">
        <h1 className="text-2xl font-bold">Track Your Multi-Asset Pakistan Portfolio</h1>
        <p className="text-sm okz-muted">
          Sign in or create an account to track PSX equities, MUFAP daily dividend &amp; NAV funds, US/Global stocks, Gold, FX, Crypto, and NSS with live mark-to-market analytics, FBR Sec 37A/236Z tax calculation, and Markowitz portfolio optimization.
        </p>
        <div className="flex justify-center gap-3 pt-2">
          <Link
            href="/auth"
            className="px-5 py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold"
          >
            Sign In / Create Account
          </Link>
        </div>
      </div>
    );
  }

  if (portfolios.length === 0) {
    return (
      <div className="okz-card p-8 max-w-xl mx-auto text-center space-y-4">
        <h1 className="text-xl font-bold">No Portfolios Found</h1>
        <p className="text-sm okz-muted">Create your first multi-asset ledger to begin tracking PSX equities, MUFAP funds, Gold, FX, Crypto, and NSS.</p>
        <button
          onClick={openCreatePortfolio}
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold"
        >
          <Plus className="w-4 h-4" /> Create Portfolio
        </button>
      </div>
    );
  }

  const normalizedFiler =
    user.tax_profile?.filer_status === "FILER" ? "ACTIVE_FILER" : user.tax_profile?.filer_status || "ACTIVE_FILER";

  const navBars = (summary?.daily_nav_series || []).map((pt) => ({
    bar_time: pt.date,
    open: pt.nav_per_unit,
    high: pt.nav_per_unit,
    low: pt.nav_per_unit,
    close: pt.nav_per_unit,
    volume: 0,
  }));

  return (
    <div className="space-y-6">
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-2xl font-bold">{summary?.portfolio.name || "Portfolio"}</h1>
            {summary?.portfolio.auto_manage_cash && (
              <span className="text-[11px] px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-500 font-semibold">
                Auto-Cash Wallet ON
              </span>
            )}
            <select
              value={activeId}
              onChange={(e) => setActiveId(e.target.value)}
              className="px-2.5 py-1 rounded border border-[var(--border)] bg-[var(--card)] text-xs"
            >
              {portfolios.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.base_currency})
                </option>
              ))}
            </select>
            <button
              onClick={openCreatePortfolio}
              title="Create new portfolio"
              className="px-2 py-1 rounded border border-[var(--border)] hover:border-emerald-500 text-xs font-medium inline-flex items-center gap-1"
            >
              <Plus className="w-3.5 h-3.5" /> New
            </button>
            <button
              onClick={openEditPortfolio}
              title="Edit active portfolio settings"
              className="p-1.5 rounded border border-[var(--border)] hover:border-emerald-500 text-xs"
            >
              <Pencil className="w-3.5 h-3.5" />
            </button>
            {portfolios.length > 1 && (
              <button
                onClick={handleDeletePortfolio}
                title="Delete active portfolio"
                className="p-1.5 rounded border border-[var(--border)] hover:border-rose-500 hover:text-rose-500 text-xs"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs okz-muted mt-1.5">
            <label className="inline-flex items-center gap-1.5">
              Tax Status:
              <select
                value={normalizedFiler}
                onChange={(e) => handleFilerChange(e.target.value)}
                className="px-2 py-0.5 rounded border border-[var(--border)] bg-[var(--card)] font-semibold text-emerald-500"
              >
                <option value="ACTIVE_FILER">ACTIVE FILER</option>
                <option value="LATE_FILER">LATE FILER</option>
                <option value="NON_FILER">NON-FILER</option>
              </select>
            </label>
            <span>&bull;</span>
            <span>Shariah Allocation: {summary?.shariah_compliant_pct ?? "0"}%</span>
            <span>&bull;</span>
            <span>NAV/Unit: {summary?.nav_per_unit ?? "100.0000"}</span>
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => {
              setEditingTx(null);
              setShowTxModal(true);
            }}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold"
          >
            <Plus className="w-4 h-4" /> Add Trade / Opening
          </button>
          <button
            onClick={() => setShowCsvModal(true)}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg border border-[var(--border)] hover:bg-[var(--card-muted)] text-xs font-semibold"
          >
            <Upload className="w-4 h-4" /> Import CSV / Excel
          </button>
          <button
            onClick={handleAccrueNss}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg border border-[var(--border)] hover:bg-[var(--card-muted)] text-xs font-semibold"
          >
            <Landmark className="w-4 h-4" /> Accrue NSS
          </button>
          <button
            onClick={() => {
              setDailyFundSymbol("");
              setShowDailyFundModal(true);
            }}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg border border-sky-500/40 bg-sky-500/10 hover:bg-sky-500/20 text-sky-400 text-xs font-semibold"
          >
            <CalendarClock className="w-4 h-4" /> Accrue Daily Dividends
          </button>
          <button
            onClick={() => setShowFbrPanel((v) => !v)}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg border border-[var(--border)] hover:bg-[var(--card-muted)] text-xs font-semibold"
          >
            <FileSpreadsheet className="w-4 h-4" /> FBR Sec 116 Summary
          </button>
          <button
            onClick={handleOpenShareModal}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg border border-[var(--border)] hover:bg-[var(--card-muted)] text-xs font-semibold"
          >
            <Share2 className="w-4 h-4" /> Share Links
          </button>
        </div>
      </div>

      {summary?.warnings && summary.warnings.length > 0 && (
        <div className="okz-card p-3.5 border-amber-500/40 bg-amber-500/10 text-xs space-y-1">
          <div className="font-bold text-amber-500 flex items-center gap-1.5">
            <AlertTriangle className="w-4 h-4" />
            Ledger Integrity Warnings ({summary.warnings.length})
          </div>
          <ul className="list-disc pl-5 space-y-0.5">
            {summary.warnings.map((w, idx) => (
              <li key={idx}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {nssNotice && (
        <div className="okz-card p-3 border-emerald-500/40 flex items-center justify-between text-xs">
          <span>{nssNotice}</span>
          <button onClick={() => setNssNotice("")} className="okz-muted hover:underline">
            Dismiss
          </button>
        </div>
      )}

      {activeId && summary && summary.holdings.length > 0 && (
        <DividendSnowballCard
          portfolioId={activeId}
          refreshKey={summary.holdings.length + Math.round(Number(summary.total_market_value_pkr || 0) % 1000)}
        />
      )}

      {showFbrPanel && fbrSummary && (
        <div className="okz-card p-5 border-sky-500/40 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-bold">FBR Section 116(2) Wealth Reconciliation &amp; Sec 37A CGT Loss Netting</h2>
              <p className="text-xs okz-muted">
                Pakistan Tax Year: {fbrSummary.period_start} to {fbrSummary.period_end} ({fbrSummary.filer_status})
              </p>
            </div>
            <div className="flex items-center gap-2">
              <select
                value={fiscalYear}
                onChange={(e) => setFiscalYear(e.target.value)}
                className="px-2.5 py-1.5 rounded border border-[var(--border)] bg-[var(--card)] text-xs font-semibold"
              >
                <option value="FY2025-26">FY2025-26</option>
                <option value="FY2024-25">FY2024-25</option>
                <option value="FY2023-24">FY2023-24</option>
                <option value="FY2026-27">FY2026-27</option>
              </select>
              <a
                href={`/api/v1/portfolios/${activeId}/fbr-summary?fiscal_year=${encodeURIComponent(fiscalYear)}&format=csv`}
                download
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold"
              >
                <Download className="w-3.5 h-3.5" /> Export Sec 116 CSV
              </a>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-5 gap-3 text-xs">
            <div className="p-2.5 rounded bg-[var(--card-muted)]/60">
              <div className="okz-muted">Opening Cost</div>
              <div className="font-bold mt-0.5">{formatMoney(fbrSummary.opening_cost_basis_pkr, currency, usdToPkr, true)}</div>
            </div>
            <div className="p-2.5 rounded bg-[var(--card-muted)]/60">
              <div className="okz-muted">June 30 Cost Basis</div>
              <div className="font-bold mt-0.5 text-sky-500">
                {formatMoney(fbrSummary.closing_cost_basis_pkr, currency, usdToPkr, true)}
              </div>
            </div>
            <div className="p-2.5 rounded bg-[var(--card-muted)]/60">
              <div className="okz-muted">June 30 Market Val (Hist. Bar)</div>
              <div className="font-bold mt-0.5">{formatMoney(fbrSummary.closing_market_value_pkr, currency, usdToPkr, true)}</div>
            </div>
            <div className="p-2.5 rounded bg-[var(--card-muted)]/60">
              <div className="okz-muted">Dividend WHT / Sec 236Z</div>
              <div className="font-bold mt-0.5">
                {formatMoney(fbrSummary.total_dividend_wht_pkr, currency, usdToPkr)} / {formatMoney(fbrSummary.total_236z_bonus_tax_pkr, currency, usdToPkr)}
              </div>
            </div>
            <div className="p-2.5 rounded bg-[var(--card-muted)]/60">
              <div className="okz-muted">Charity Purification</div>
              <div className="font-bold mt-0.5 text-amber-500">
                {formatMoney(fbrSummary.total_shariah_purification_pkr, currency, usdToPkr)}
              </div>
            </div>
            <div className="p-2.5 rounded bg-[var(--card-muted)]/60">
              <div className="okz-muted">Gross Gain / Loss</div>
              <div className="font-bold mt-0.5">
                <span className="text-emerald-500">+{formatMoney(fbrSummary.gross_gain_pkr ?? "0", currency, usdToPkr)}</span>
                {" / "}
                <span className="text-rose-500">-{formatMoney(fbrSummary.gross_loss_pkr ?? "0", currency, usdToPkr)}</span>
              </div>
            </div>
            <div className="p-2.5 rounded bg-[var(--card-muted)]/60">
              <div className="okz-muted">3-Yr Loss Applied</div>
              <div className="font-bold mt-0.5 text-sky-500">
                {formatMoney(fbrSummary.prior_loss_applied_pkr ?? "0", currency, usdToPkr)}
              </div>
            </div>
            <div className="p-2.5 rounded bg-[var(--card-muted)]/60">
              <div className="okz-muted">Net Taxable Gain</div>
              <div className="font-bold mt-0.5">
                {formatMoney(fbrSummary.net_taxable_gain_pkr ?? "0", currency, usdToPkr)}
              </div>
            </div>
            <div className="p-2.5 rounded bg-[var(--card-muted)]/60">
              <div className="okz-muted">FBR Est. CGT</div>
              <div className="font-bold mt-0.5 text-emerald-500">
                {formatMoney(fbrSummary.estimated_cgt_pkr, currency, usdToPkr)}
              </div>
            </div>
            <div className="p-2.5 rounded bg-[var(--card-muted)]/60">
              <div className="okz-muted">Unused Loss Carryforward</div>
              <div className="font-bold mt-0.5 text-amber-500">
                {formatMoney(fbrSummary.unused_loss_carryforward_pkr ?? "0", currency, usdToPkr)}
              </div>
            </div>
          </div>

          {summary?.cgt_by_fy && summary.cgt_by_fy.length > 0 && (
            <div className="overflow-x-auto border border-[var(--border)] rounded-md">
              <table className="w-full text-xs">
                <thead className="bg-[var(--bg-elevated)] okz-muted">
                  <tr>
                    <th className="text-left py-2 px-3">Tax Year</th>
                    <th className="text-right py-2 px-3">Gross Gain</th>
                    <th className="text-right py-2 px-3">Gross Loss</th>
                    <th className="text-right py-2 px-3">Prior Loss Used</th>
                    <th className="text-right py-2 px-3">Net Taxable</th>
                    <th className="text-right py-2 px-3">Est. CGT</th>
                    <th className="text-right py-2 px-3">Carryforward</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border)]">
                  {summary.cgt_by_fy.map((fyRow) => (
                    <tr key={fyRow.fiscal_year}>
                      <td className="py-2 px-3 font-semibold">{fyRow.fiscal_year}</td>
                      <td className="py-2 px-3 text-right text-emerald-500">{formatMoney(fyRow.gross_gain_pkr, currency, usdToPkr)}</td>
                      <td className="py-2 px-3 text-right text-rose-500">{formatMoney(fyRow.gross_loss_pkr, currency, usdToPkr)}</td>
                      <td className="py-2 px-3 text-right text-sky-500">{formatMoney(fyRow.prior_loss_applied_pkr, currency, usdToPkr)}</td>
                      <td className="py-2 px-3 text-right font-semibold">{formatMoney(fyRow.net_taxable_gain_pkr, currency, usdToPkr)}</td>
                      <td className="py-2 px-3 text-right font-semibold">{formatMoney(fyRow.estimated_cgt_pkr, currency, usdToPkr)}</td>
                      <td className="py-2 px-3 text-right text-amber-500">{formatMoney(fyRow.unused_loss_carryforward_pkr, currency, usdToPkr)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {pendingActions.length > 0 && (
        <div className="okz-card p-4 border-amber-500/40 bg-amber-500/5 space-y-2.5">
          <div className="text-xs font-bold text-amber-500 uppercase tracking-wider">
            Pending PSX Corporate Actions Eligible on Ex-Date
          </div>
          <div className="space-y-2">
            {pendingActions.map((pa) => (
              <div key={pa.corp_action_id} className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
                <div>
                  <span className="font-bold">{pa.symbol}</span> &mdash; {pa.description} (Ex: {pa.ex_date}) &bull; Eligible Qty:{" "}
                  {formatNumber(pa.eligible_quantity, 0)}
                  {pa.action_type === "CASH_DIVIDEND" && (
                    <span className="okz-muted">
                      {" "}
                      &bull; Gross {formatMoney(pa.estimated_gross_pkr, currency, usdToPkr)} (WHT:{" "}
                      {formatMoney(pa.estimated_wht_pkr, currency, usdToPkr)})
                    </span>
                  )}
                  {pa.action_type === "BONUS_ISSUE" && (
                    <span className="okz-muted">
                      {" "}
                      &bull; +{formatNumber(pa.estimated_bonus_shares, 2)} shares (Sec 236Z Deemed Ex:{" "}
                      {formatNumber(pa.deemed_ex_price, 2)} | Est. Tax:{" "}
                      {formatMoney(pa.estimated_236z_tax_pkr, currency, usdToPkr)})
                    </span>
                  )}
                </div>
                <button
                  onClick={() => handleApplyAction(pa.corp_action_id)}
                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded bg-amber-500 hover:bg-amber-400 text-slate-950 font-semibold whitespace-nowrap"
                >
                  <CheckCircle2 className="w-3.5 h-3.5" /> 1-Click Apply
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {summary && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
            <div className="okz-card p-4">
              <div className="text-xs okz-muted">Net Worth</div>
              <div className="text-xl font-bold mt-1">
                {formatMoney(summary.total_market_value_pkr, currency, usdToPkr, true)}
              </div>
              <div className="text-xs okz-muted mt-0.5">
                Cost Basis: {formatMoney(summary.total_cost_basis_pkr, currency, usdToPkr, true)}
              </div>
            </div>

            <div className="okz-card p-4">
              <div className="text-xs okz-muted">Unrealized P&amp;L</div>
              <div className={`text-xl font-bold mt-1 ${pctColorClass(summary.total_unrealized_pnl_pkr)}`}>
                {formatMoney(summary.total_unrealized_pnl_pkr, currency, usdToPkr, true)}
              </div>
              <div className={`text-xs font-medium mt-0.5 ${pctColorClass(summary.total_unrealized_pnl_pct)}`}>
                {formatPct(summary.total_unrealized_pnl_pct)}
              </div>
            </div>

            <div className="okz-card p-4">
              <div className="text-xs okz-muted">XIRR &amp; Unitized TWRR</div>
              <div className={`text-xl font-bold mt-1 ${pctColorClass(summary.xirr_pct)}`}>
                {summary.xirr_pct ? `${summary.xirr_pct}%` : "—"}
              </div>
              <div className="text-xs okz-muted mt-0.5">
                TWRR: {summary.twrr_pct ? `${summary.twrr_pct}%` : "—"} &bull; NAV {summary.nav_per_unit}
              </div>
            </div>

            <div className="okz-card p-4">
              <div className="text-xs okz-muted">Net Income &amp; Daily Divs</div>
              <div className="text-xl font-bold mt-1 text-emerald-500">
                {formatMoney(
                  Number(summary.total_net_dividends_pkr) + Number(summary.total_realized_pnl_pkr),
                  currency,
                  usdToPkr,
                  true
                )}
              </div>
              <div className="text-xs okz-muted mt-0.5">
                {Number(summary.daily_dividend_est_monthly_pkr || 0) > 0 ? (
                  <span className="text-sky-400 font-semibold">
                    ~{formatMoney(summary.daily_dividend_est_monthly_pkr || "0", currency, usdToPkr, true)}/mo daily-div
                  </span>
                ) : (
                  <>
                    Purify Charity:{" "}
                    <span className="text-amber-500 font-semibold">
                      {formatMoney(summary.total_shariah_purification_pkr, currency, usdToPkr)}
                    </span>
                  </>
                )}
              </div>
            </div>

            <div className="okz-card p-4">
              <div className="text-xs okz-muted">Est. FBR CGT &amp; Taxes</div>
              <div className="text-xl font-bold mt-1">
                {formatMoney(summary.estimated_cgt_pkr, currency, usdToPkr)}
              </div>
              <div className="text-xs okz-muted mt-0.5">
                WHT {formatMoney(summary.total_tax_withheld_pkr, currency, usdToPkr)} &bull; Fees{" "}
                {formatMoney(summary.total_fees_pkr, currency, usdToPkr)}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="okz-card p-5 space-y-3">
              <h2 className="text-sm font-semibold">Asset Class Allocation</h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {summary.allocation_by_class.map((cls) => (
                  <div key={cls.asset_class} className="p-3 rounded-lg bg-[var(--card-muted)]/60 space-y-1.5">
                    <div className="flex justify-between text-xs">
                      <span className="font-semibold">{cls.asset_class.replace("_", " ")}</span>
                      <span className="font-bold">{cls.allocation_pct}%</span>
                    </div>
                    <div className="w-full h-1.5 rounded-full bg-[var(--border)] overflow-hidden">
                      <div
                        className="h-full bg-emerald-500 rounded-full"
                        style={{ width: `${Math.min(Number(cls.allocation_pct), 100)}%` }}
                      />
                    </div>
                    <div className="text-xs okz-muted">{formatMoney(cls.market_value_pkr, currency, usdToPkr, true)}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="okz-card p-5 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="text-sm font-semibold">Unitized Portfolio NAV (Base 100 TWRR)</h2>
                  <span className="text-xs font-semibold text-emerald-500">Current NAV {summary.nav_per_unit}</span>
                </div>
                <div className="flex items-center gap-1 rounded-md border border-[var(--border)] p-0.5 text-[11px]">
                  {(["NONE", "KSE100", "KMI30", "SPX"] as const).map((bm) => (
                    <button
                      key={bm}
                      type="button"
                      onClick={() => setBenchmarkSymbol(bm)}
                      className={`px-2 py-1 rounded ${
                        benchmarkSymbol === bm ? "bg-emerald-600 text-white font-semibold" : "okz-muted hover:text-[var(--text)]"
                      }`}
                    >
                      {bm === "NONE" ? "NAV Only" : `vs ${bm}`}
                    </button>
                  ))}
                </div>
              </div>
              {benchmarkSymbol === "NONE" ? (
                <PriceChart bars={navBars} mode="area" height={200} />
              ) : (
                <BenchmarkCompareChart series={benchmarkSeries} benchmarkSymbol={benchmarkSymbol} height={200} />
              )}
            </div>
          </div>

          <div className="flex flex-wrap gap-2 border-b border-[var(--border)]">
            <button
              onClick={() => setTab("holdings")}
              className={`px-4 py-2 text-sm font-semibold border-b-2 ${
                tab === "holdings" ? "border-emerald-500 text-emerald-500" : "border-transparent okz-muted"
              }`}
            >
              Holdings ({summary.holdings.length})
            </button>
            <button
              onClick={() => setTab("quant")}
              className={`px-4 py-2 text-sm font-semibold border-b-2 inline-flex items-center gap-1.5 ${
                tab === "quant" ? "border-sky-500 text-sky-400" : "border-transparent okz-muted"
              }`}
            >
              <Activity className="w-4 h-4" /> Quant &amp; Risk Lab (Markowitz / VaR)
            </button>
            <button
              onClick={() => setTab("ledger")}
              className={`px-4 py-2 text-sm font-semibold border-b-2 ${
                tab === "ledger" ? "border-emerald-500 text-emerald-500" : "border-transparent okz-muted"
              }`}
            >
              Transaction Ledger ({transactions.length})
            </button>
          </div>

          {tab === "holdings" ? (
            <div className="okz-card overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-[var(--border)] text-xs okz-muted text-left">
                  <tr>
                    <th className="py-3 px-4">Asset</th>
                    <th className="py-3 px-4">Class</th>
                    <th className="py-3 px-4 text-right">Qty / Units</th>
                    <th className="py-3 px-4 text-right">Avg Cost</th>
                    <th className="py-3 px-4 text-right">Price</th>
                    <th className="py-3 px-4 text-right">Market Value</th>
                    <th className="py-3 px-4 text-right">Unrealized P&amp;L</th>
                    <th className="py-3 px-4 text-right">Purification</th>
                    <th className="py-3 px-4 text-right">Weight</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border)]">
                  {summary.holdings.map((h) => {
                    const isStockOrEtf = h.asset_class.startsWith("PSX") || h.asset_class.startsWith("INTL");
                    const isFund = h.asset_class === "MUTUAL_FUND" || h.asset_class === "VPS_PENSION";
                    const isUsd = h.currency === "USD";
                    return (
                      <tr key={h.asset_id} className="hover:bg-[var(--card-muted)]/50">
                        <td className="py-3 px-4">
                          <div className="font-bold flex flex-wrap items-center gap-1.5">
                            {isStockOrEtf ? (
                              <Link href={`/stocks/${encodeURIComponent(h.symbol)}`} className="text-emerald-500 hover:underline">
                                {h.symbol}
                              </Link>
                            ) : isFund ? (
                              <Link href={`/funds/${encodeURIComponent(h.symbol)}`} className="text-sky-400 hover:underline">
                                {h.symbol}
                              </Link>
                            ) : (
                              h.symbol
                            )}
                            {h.is_shariah_compliant && <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />}
                            {isUsd && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/15 text-indigo-400 font-semibold inline-flex items-center gap-0.5">
                                <Globe2 className="w-2.5 h-2.5" /> USD
                              </span>
                            )}
                            {h.is_daily_dividend_fund && (
                              <button
                                type="button"
                                onClick={() => {
                                  setDailyFundSymbol(h.symbol);
                                  setShowDailyFundModal(true);
                                }}
                                title="Click to auto-accrue daily dividends for this fund"
                                className="text-[10px] px-1.5 py-0.5 rounded bg-sky-500/15 hover:bg-sky-500/25 text-sky-400 font-semibold inline-flex items-center gap-0.5"
                              >
                                <Sparkles className="w-2.5 h-2.5" /> Daily Div
                                {h.annualized_yield_pct && Number(h.annualized_yield_pct) > 0
                                  ? ` (${formatNumber(h.annualized_yield_pct, 2)}%)`
                                  : ""}
                              </button>
                            )}
                          </div>
                          <div className="text-xs okz-muted truncate max-w-48">{h.name}</div>
                        </td>
                        <td className="py-3 px-4 text-xs okz-muted">{h.asset_class}</td>
                        <td className="py-3 px-4 text-right">{formatNumber(h.quantity, 4)}</td>
                        <td className="py-3 px-4 text-right">
                          <div>{formatMoney(h.avg_cost_pkr, currency, usdToPkr)}</div>
                          {isUsd && h.avg_cost_native && (
                            <div className="text-[11px] okz-muted">${formatNumber(h.avg_cost_native, 2)} native</div>
                          )}
                        </td>
                        <td className="py-3 px-4 text-right">
                          <div>{formatMoney(h.current_price_pkr, currency, usdToPkr)}</div>
                          {isUsd && h.current_price_native && (
                            <div className="text-[11px] okz-muted">${formatNumber(h.current_price_native, 2)} native</div>
                          )}
                        </td>
                        <td className="py-3 px-4 text-right font-semibold">
                          {formatMoney(h.market_value_pkr, currency, usdToPkr)}
                        </td>
                        <td className={`py-3 px-4 text-right font-semibold ${pctColorClass(h.unrealized_pnl_pkr)}`}>
                          <div>{formatMoney(h.unrealized_pnl_pkr, currency, usdToPkr)}</div>
                          <div className="text-xs">{formatPct(h.unrealized_pnl_pct)}</div>
                          {Number(h.dividends_net_pkr) > 0 && (
                            <div className="text-[10px] text-emerald-400 font-medium mt-0.5">
                              +{formatMoney(h.dividends_net_pkr, currency, usdToPkr, true)} div
                              {Number(h.est_daily_income_pkr || 0) > 0 && (
                                <span className="text-sky-400 ml-1">
                                  ({formatMoney(h.est_daily_income_pkr || "0", currency, usdToPkr)}/d)
                                </span>
                              )}
                            </div>
                          )}
                          {isUsd && h.native_return_pct !== undefined && h.fx_return_pct !== undefined && (
                            <div className="mt-1 flex flex-wrap justify-end gap-1 text-[10px] font-medium">
                              <span
                                className={`px-1.5 py-0.5 rounded bg-[var(--card-muted)] ${pctColorClass(h.native_return_pct)}`}
                                title="Pure underlying asset return in USD"
                              >
                                USD: {formatPct(h.native_return_pct)}
                              </span>
                              <span
                                className={`px-1.5 py-0.5 rounded bg-[var(--card-muted)] ${pctColorClass(h.fx_return_pct)}`}
                                title="PKR currency depreciation / appreciation impact"
                              >
                                FX: {formatPct(h.fx_return_pct)}
                              </span>
                            </div>
                          )}
                        </td>
                        <td className="py-3 px-4 text-right text-xs text-amber-500">
                          {Number(h.shariah_purification_pkr) > 0
                            ? formatMoney(h.shariah_purification_pkr, currency, usdToPkr)
                            : "—"}
                        </td>
                        <td className="py-3 px-4 text-right">{h.allocation_pct}%</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : tab === "quant" ? (
            <div className="space-y-6">
              <div className="okz-card p-5 space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-base font-bold flex items-center gap-2">
                      <Activity className="w-4 h-4 text-sky-400" />
                      Quantitative Risk &amp; Modern Portfolio Theory Lab
                    </h2>
                    <p className="text-xs okz-muted">
                      Powered by NumPy, Pandas &amp; SciPy SLSQP — Sharpe, Sortino, Historical 95% VaR/CVaR, Beta/Alpha,
                      Pairwise Correlation &amp; Markowitz Efficient Frontier Weights.
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <label className="inline-flex items-center gap-1.5">
                      <span className="okz-muted">Benchmark:</span>
                      <select
                        value={quantBenchmark}
                        onChange={(e) => setQuantBenchmark(e.target.value as "KSE100" | "KMI30" | "SPX")}
                        className="px-2.5 py-1 rounded border border-[var(--border)] bg-[var(--bg)] font-semibold"
                      >
                        <option value="KSE100">KSE-100 (PSX)</option>
                        <option value="KMI30">KMI-30 Shariah</option>
                        <option value="SPX">S&amp;P 500 (SPX)</option>
                      </select>
                    </label>
                    <label className="inline-flex items-center gap-1.5">
                      <span className="okz-muted">Risk-Free (%):</span>
                      <input
                        type="number"
                        step="0.5"
                        value={quantRfRate}
                        onChange={(e) => setQuantRfRate(e.target.value)}
                        className="w-16 px-2 py-1 rounded border border-[var(--border)] bg-[var(--bg)] font-semibold text-right"
                      />
                    </label>
                    <button
                      type="button"
                      onClick={loadQuant}
                      disabled={loadingQuant}
                      className="px-3 py-1 rounded bg-sky-600 hover:bg-sky-500 text-white font-semibold"
                    >
                      {loadingQuant ? "Computing..." : "Refresh"}
                    </button>
                  </div>
                </div>

                {loadingQuant && !quantData ? (
                  <div className="py-8 text-center text-xs okz-muted">Running SciPy SLSQP optimization &amp; risk engine...</div>
                ) : quantData ? (
                  <>
                    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 text-xs">
                      <div className="p-3 rounded-lg bg-[var(--card-muted)]/60">
                        <div className="okz-muted">Annualized Return / Vol</div>
                        <div className={`text-base font-bold mt-1 ${pctColorClass(quantData.metrics.annualized_return_pct)}`}>
                          {formatPct(quantData.metrics.annualized_return_pct)}
                        </div>
                        <div className="text-[11px] okz-muted mt-0.5">
                          Vol: {formatNumber(quantData.metrics.annualized_volatility_pct, 2)}% p.a.
                        </div>
                      </div>

                      <div className="p-3 rounded-lg bg-[var(--card-muted)]/60">
                        <div className="okz-muted">Sharpe / Sortino Ratio</div>
                        <div className="text-base font-bold mt-1 text-emerald-400">
                          {formatNumber(quantData.metrics.sharpe_ratio, 2)} / {formatNumber(quantData.metrics.sortino_ratio, 2)}
                        </div>
                        <div className="text-[11px] okz-muted mt-0.5">
                          Rf: {quantData.risk_free_rate_pct}% &bull; Calmar: {formatNumber(quantData.metrics.calmar_ratio, 2)}
                        </div>
                      </div>

                      <div className="p-3 rounded-lg bg-[var(--card-muted)]/60">
                        <div className="okz-muted">Max Drawdown</div>
                        <div className="text-base font-bold mt-1 text-rose-400">
                          {formatPct(quantData.metrics.max_drawdown_pct)}
                        </div>
                        <div className="text-[11px] okz-muted mt-0.5">
                          Current DD: {formatPct(quantData.metrics.current_drawdown_pct)}
                        </div>
                      </div>

                      <div className="p-3 rounded-lg bg-[var(--card-muted)]/60">
                        <div className="okz-muted">95% Daily VaR / CVaR</div>
                        <div className="text-base font-bold mt-1 text-amber-400">
                          {formatPct(quantData.metrics.var_95_daily_pct)}
                        </div>
                        <div className="text-[11px] okz-muted mt-0.5">
                          Expected Shortfall: {formatPct(quantData.metrics.cvar_95_daily_pct)}
                        </div>
                      </div>

                      <div className="p-3 rounded-lg bg-[var(--card-muted)]/60">
                        <div className="okz-muted">Beta / Alpha vs {quantData.benchmark_symbol}</div>
                        <div className="text-base font-bold mt-1 text-sky-400">
                          &beta; {quantData.metrics.beta !== null ? formatNumber(quantData.metrics.beta, 2) : "—"}
                        </div>
                        <div className="text-[11px] okz-muted mt-0.5">
                          &alpha;: {quantData.metrics.alpha_annualized_pct !== null ? `${formatNumber(quantData.metrics.alpha_annualized_pct, 2)}%` : "—"} &bull; R&sup2;:{" "}
                          {quantData.metrics.r_squared !== null ? formatNumber(quantData.metrics.r_squared, 2) : "—"}
                        </div>
                      </div>

                      <div className="p-3 rounded-lg bg-[var(--card-muted)]/60">
                        <div className="okz-muted">Diversification (HHI)</div>
                        <div className="text-base font-bold mt-1 flex items-center gap-1.5">
                          <span>{quantData.metrics.diversification_rating || "MODERATE"}</span>
                        </div>
                        <div className="text-[11px] okz-muted mt-0.5">
                          Eff. N: {formatNumber(quantData.metrics.effective_holdings_count ?? 0, 1)} &bull; Top Wt:{" "}
                          {formatNumber(quantData.metrics.top_holding_weight_pct ?? 0, 1)}% (HHI {formatNumber(quantData.metrics.hhi_index ?? 0, 0)})
                        </div>
                      </div>
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 pt-2">
                      <div className="space-y-2.5">
                        <h3 className="text-xs font-bold uppercase tracking-wider okz-muted">
                          Markowitz Optimizer &amp; Actionable Rebalance Trade Tickets (SciPy SLSQP)
                        </h3>
                        {quantData.optimization.length === 0 ? (
                          <p className="text-xs okz-muted py-4">
                            Need at least 2 non-cash holdings with historical price bars to compute optimal weights.
                          </p>
                        ) : (
                          <div className="overflow-x-auto border border-[var(--border)] rounded-lg">
                            <table className="w-full text-xs">
                              <thead className="bg-[var(--bg-elevated)] okz-muted">
                                <tr>
                                  <th className="py-2 px-2.5 text-left">Asset</th>
                                  <th className="py-2 px-2.5 text-right">Curr Wt</th>
                                  <th className="py-2 px-2.5 text-right text-emerald-400">Max Sharpe Wt</th>
                                  <th className="py-2 px-2.5 text-center">Ticket</th>
                                  <th className="py-2 px-2.5 text-right">Rebalance Trade</th>
                                  <th className="py-2 px-2.5 text-right text-sky-400">Min Vol Wt</th>
                                  <th className="py-2 px-2.5 text-right text-purple-400">HRP Wt</th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-[var(--border)]">
                                {quantData.optimization.map((opt) => {
                                  const deltaSharpe =
                                    opt.sharpe_delta_weight_pct ?? opt.max_sharpe_weight_pct - opt.current_weight_pct;
                                  const action = opt.sharpe_action || (deltaSharpe > 1 ? "BUY" : deltaSharpe < -1 ? "SELL" : "HOLD");
                                  const tradeAmt = opt.sharpe_trade_amount_pkr ?? 0;
                                  const estUnits = opt.sharpe_estimated_units ?? 0;
                                  return (
                                    <tr key={opt.symbol}>
                                      <td className="py-2 px-2.5">
                                        <div className="font-bold">{opt.symbol}</div>
                                        <div className="text-[10px] okz-muted truncate max-w-32">{opt.name}</div>
                                      </td>
                                      <td className="py-2 px-2.5 text-right font-semibold">
                                        {formatNumber(opt.current_weight_pct, 1)}%
                                      </td>
                                      <td className="py-2 px-2.5 text-right font-bold text-emerald-400">
                                        {formatNumber(opt.max_sharpe_weight_pct, 1)}%
                                        <span
                                          className={`ml-1 text-[10px] ${
                                            deltaSharpe >= 0 ? "text-emerald-500" : "text-rose-400"
                                          }`}
                                        >
                                          ({deltaSharpe >= 0 ? "+" : ""}
                                          {formatNumber(deltaSharpe, 1)}%)
                                        </span>
                                      </td>
                                      <td className="py-2 px-2.5 text-center">
                                        <span
                                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                            action === "BUY"
                                              ? "bg-emerald-500/15 text-emerald-400"
                                              : action === "SELL"
                                                ? "bg-rose-500/15 text-rose-400"
                                                : "bg-[var(--card-muted)] okz-muted"
                                          }`}
                                        >
                                          {action}
                                        </span>
                                      </td>
                                      <td className="py-2 px-2.5 text-right">
                                        <div className="font-semibold">
                                          {tradeAmt >= 0 ? "+" : ""}
                                          {formatMoney(tradeAmt, currency, usdToPkr, true)}
                                        </div>
                                        <div className="text-[10px] okz-muted">
                                          {estUnits >= 0 ? "+" : ""}
                                          {formatNumber(estUnits, opt.currency === "USD" ? 4 : 0)} units
                                        </div>
                                      </td>
                                      <td className="py-2 px-2.5 text-right font-semibold text-sky-400">
                                        {formatNumber(opt.min_vol_weight_pct, 1)}%
                                      </td>
                                      <td className="py-2 px-2.5 text-right font-semibold text-purple-400">
                                        {opt.hrp_weight_pct != null ? `${formatNumber(opt.hrp_weight_pct, 1)}%` : "—"}
                                      </td>
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>

                      <div className="space-y-2.5">
                        <h3 className="text-xs font-bold uppercase tracking-wider okz-muted">
                          Pairwise Asset Return Correlation Matrix
                        </h3>
                        {quantData.correlation_matrix.symbols.length < 2 ? (
                          <p className="text-xs okz-muted py-4">
                            Need at least 2 holdings with overlapping daily price bars to compute correlations.
                          </p>
                        ) : (
                          <div className="overflow-x-auto border border-[var(--border)] rounded-lg">
                            <table className="w-full text-xs">
                              <thead className="bg-[var(--bg-elevated)] okz-muted">
                                <tr>
                                  <th className="py-2 px-2.5 text-left">Symbol</th>
                                  {quantData.correlation_matrix.symbols.map((sym) => (
                                    <th key={sym} className="py-2 px-2.5 text-right font-mono">
                                      {sym}
                                    </th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-[var(--border)]">
                                {quantData.correlation_matrix.symbols.map((rowSym, rIdx) => (
                                  <tr key={rowSym}>
                                    <td className="py-2 px-2.5 font-bold font-mono">{rowSym}</td>
                                    {(quantData.correlation_matrix.matrix[rIdx] || []).map((val, cIdx) => {
                                      const bg =
                                        rIdx === cIdx
                                          ? "bg-emerald-500/20 text-emerald-300 font-bold"
                                          : val >= 0.5
                                            ? "bg-amber-500/15 text-amber-300"
                                            : val <= -0.15
                                              ? "bg-sky-500/15 text-sky-300"
                                              : "";
                                      return (
                                        <td key={cIdx} className={`py-2 px-2.5 text-right font-mono ${bg}`}>
                                          {formatNumber(val, 2)}
                                        </td>
                                      );
                                    })}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                    </div>

                    {((quantData.efficient_frontier && quantData.efficient_frontier.length > 0) ||
                      (quantData.monte_carlo && quantData.monte_carlo.length > 0)) && (
                      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 pt-2">
                        {quantData.efficient_frontier && quantData.efficient_frontier.length > 0 && (
                          <div className="space-y-2">
                            <h3 className="text-xs font-bold uppercase tracking-wider okz-muted">
                              PyPortfolioOpt Efficient Frontier (Volatility vs Expected Return %)
                            </h3>
                            <div className="h-56 w-full border border-[var(--border)] rounded-lg p-2">
                              <ResponsiveContainer width="100%" height="100%">
                                <RechartsLineChart
                                  data={quantData.efficient_frontier}
                                  margin={{ top: 8, right: 12, left: 0, bottom: 0 }}
                                >
                                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                                  <XAxis
                                    dataKey="volatility_pct"
                                    unit="%"
                                    tick={{ fontSize: 10, fill: "#94a3b8" }}
                                  />
                                  <YAxis unit="%" tick={{ fontSize: 10, fill: "#94a3b8" }} width={40} />
                                  <RechartsTooltip
                                    contentStyle={{
                                      backgroundColor: "var(--card)",
                                      borderColor: "var(--border)",
                                      fontSize: "11px",
                                      borderRadius: "8px",
                                    }}
                                  />
                                  <Line
                                    type="monotone"
                                    dataKey="return_pct"
                                    name="Expected Return %"
                                    stroke="#10b981"
                                    strokeWidth={2.5}
                                    dot={{ r: 3 }}
                                  />
                                </RechartsLineChart>
                              </ResponsiveContainer>
                            </div>
                          </div>
                        )}

                        {quantData.monte_carlo && quantData.monte_carlo.length > 0 && (
                          <div className="space-y-2">
                            <h3 className="text-xs font-bold uppercase tracking-wider okz-muted">
                              3-Year Portfolio Monte Carlo Wealth Fan (P10 / P50 / P90)
                            </h3>
                            <div className="h-56 w-full border border-[var(--border)] rounded-lg p-2">
                              <ResponsiveContainer width="100%" height="100%">
                                <AreaChart
                                  data={quantData.monte_carlo}
                                  margin={{ top: 8, right: 12, left: 0, bottom: 0 }}
                                >
                                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                                  <XAxis
                                    dataKey="month"
                                    tickFormatter={(m) => `M${m}`}
                                    tick={{ fontSize: 10, fill: "#94a3b8" }}
                                  />
                                  <YAxis
                                    tickFormatter={(v) => `${(Number(v) / 100000).toFixed(1)}L`}
                                    tick={{ fontSize: 10, fill: "#94a3b8" }}
                                    width={44}
                                  />
                                  <RechartsTooltip
                                    contentStyle={{
                                      backgroundColor: "var(--card)",
                                      borderColor: "var(--border)",
                                      fontSize: "11px",
                                      borderRadius: "8px",
                                    }}
                                  />
                                  <Legend wrapperStyle={{ fontSize: "11px" }} />
                                  <Area
                                    type="monotone"
                                    dataKey="p90_pkr"
                                    name="P90 Bull"
                                    stroke="#38bdf8"
                                    fill="rgba(56, 189, 248, 0.14)"
                                  />
                                  <Area
                                    type="monotone"
                                    dataKey="p50_pkr"
                                    name="P50 Median"
                                    stroke="#10b981"
                                    fill="rgba(16, 185, 129, 0.22)"
                                    strokeWidth={2}
                                  />
                                  <Line
                                    type="monotone"
                                    dataKey="p10_pkr"
                                    name="P10 Bear"
                                    stroke="#f43f5e"
                                    strokeWidth={2}
                                    dot={false}
                                  />
                                </AreaChart>
                              </ResponsiveContainer>
                            </div>
                          </div>
                        )}
                      </div>
                    )}

                    {quantData.stress_tests && quantData.stress_tests.length > 0 && (
                      <div className="space-y-2 pt-2">
                        <h3 className="text-xs font-bold uppercase tracking-wider okz-muted">
                          Pakistan &amp; Global Macro Stress Test Matrix (Live SBP {quantData.risk_free_rate_pct.toFixed(1)}% Rf)
                        </h3>
                        <div className="overflow-x-auto border border-[var(--border)] rounded-lg">
                          <table className="w-full text-xs">
                            <thead className="bg-[var(--bg-elevated)] okz-muted text-left">
                              <tr>
                                <th className="py-2 px-3">Scenario</th>
                                <th className="py-2 px-3">Exposure Channel</th>
                                <th className="py-2 px-3 text-right">Impact %</th>
                                <th className="py-2 px-3 text-right">Estimated PnL</th>
                                <th className="py-2 px-3 text-right">Projected Portfolio</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-[var(--border)]">
                              {quantData.stress_tests.map((st) => (
                                <tr key={st.scenario}>
                                  <td className="py-2 px-3 font-semibold">{st.scenario}</td>
                                  <td className="py-2 px-3 okz-muted">{st.rationale}</td>
                                  <td className={`py-2 px-3 text-right font-bold ${pctColorClass(st.estimated_impact_pct)}`}>
                                    {formatPct(st.estimated_impact_pct)}
                                  </td>
                                  <td className={`py-2 px-3 text-right font-mono ${pctColorClass(st.estimated_impact_pkr)}`}>
                                    {st.estimated_impact_pkr >= 0 ? "+" : ""}
                                    {formatMoney(st.estimated_impact_pkr, currency, usdToPkr, true)}
                                  </td>
                                  <td className="py-2 px-3 text-right font-mono font-semibold">
                                    {formatMoney(st.projected_value_pkr, currency, usdToPkr, true)}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="py-6 text-center text-xs okz-muted">Unable to load quantitative analytics.</div>
                )}
              </div>

              {/* SECP FMR Look-Through Portfolio X-Ray */}
              <div className="okz-card p-5 space-y-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-base font-bold flex items-center gap-2">
                      <Layers className="w-4 h-4 text-emerald-500" />
                      Look-Through Portfolio X-Ray (SECP FMR Composition)
                    </h2>
                    <p className="text-xs okz-muted">
                      Explodes your Mutual Fund &amp; VPS holdings through parsed monthly SECP Fund Manager Reports to reveal true
                      asset mix, combined sector concentration, and hidden direct-vs-fund single-stock overlap.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={loadXray}
                    disabled={loadingXray}
                    className="px-3 py-1 rounded border border-[var(--border)] hover:border-emerald-500 text-xs font-semibold"
                  >
                    {loadingXray ? "Scanning FMRs..." : "Refresh X-Ray"}
                  </button>
                </div>

                {loadingXray && !xrayData ? (
                  <div className="py-6 text-center text-xs okz-muted">Computing look-through FMR exposure...</div>
                ) : xrayData && xrayData.total_value_pkr > 0 ? (
                  <div className="space-y-5">
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                      <div className="p-3 rounded-lg bg-[var(--card-muted)]/60">
                        <div className="okz-muted">Total Portfolio Analyzed</div>
                        <div className="text-base font-bold mt-1">
                          {formatMoney(xrayData.total_value_pkr, currency, usdToPkr, true)}
                        </div>
                      </div>
                      <div className="p-3 rounded-lg bg-[var(--card-muted)]/60">
                        <div className="okz-muted">Mutual Funds / VPS Value</div>
                        <div className="text-base font-bold mt-1 text-sky-400">
                          {formatMoney(xrayData.mutual_fund_value_pkr, currency, usdToPkr, true)}
                        </div>
                      </div>
                      <div className="p-3 rounded-lg bg-[var(--card-muted)]/60">
                        <div className="okz-muted">SECP FMR Look-Through Coverage</div>
                        <div className="text-base font-bold mt-1 text-emerald-400">
                          {xrayData.mutual_fund_value_pkr > 0 ? `${xrayData.fmr_covered_fund_pct.toFixed(1)}%` : "Direct Only"}
                        </div>
                        <div className="text-[11px] okz-muted mt-0.5">
                          {xrayData.funds_with_fmr.length} fund{xrayData.funds_with_fmr.length === 1 ? "" : "s"} decomposed
                        </div>
                      </div>
                      <div className="p-3 rounded-lg bg-[var(--card-muted)]/60">
                        <div className="okz-muted">Detected Stock Overlaps</div>
                        <div className="text-base font-bold mt-1 text-amber-400">
                          {xrayData.stock_overlap.filter((s) => s.is_overlap).length}
                        </div>
                        <div className="text-[11px] okz-muted mt-0.5">Held both directly &amp; inside funds</div>
                      </div>
                    </div>

                    {xrayData.funds_missing_fmr.length > 0 && (
                      <div className="p-3 rounded-lg border border-amber-500/30 bg-amber-500/10 text-xs flex flex-wrap items-center justify-between gap-2">
                        <div>
                          <span className="font-semibold text-amber-400">
                            {xrayData.funds_missing_fmr.length} fund{xrayData.funds_missing_fmr.length > 1 ? "s" : ""} missing parsed FMR:
                          </span>{" "}
                          {xrayData.funds_missing_fmr.map((f, idx) => (
                            <React.Fragment key={f.symbol}>
                              {idx > 0 ? ", " : ""}
                              <Link href={`/funds/${encodeURIComponent(f.symbol)}`} className="underline font-mono font-semibold text-amber-300">
                                {f.symbol}
                              </Link>
                            </React.Fragment>
                          ))}
                        </div>
                        <Link href="/funds" className="text-emerald-400 font-semibold hover:underline">
                          Upload FMR PDF &rarr;
                        </Link>
                      </div>
                    )}

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                      <div className="space-y-2.5">
                        <h3 className="text-xs font-bold uppercase tracking-wider okz-muted">
                          True Look-Through Asset Class Mix
                        </h3>
                        <div className="space-y-2 p-3.5 rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)]/40 text-xs">
                          {xrayData.look_through_asset_mix.map((row) => (
                            <div key={row.label} className="space-y-1">
                              <div className="flex justify-between">
                                <span className="font-medium">{row.label}</span>
                                <span className="font-mono font-semibold">
                                  {row.weight_pct.toFixed(1)}%{" "}
                                  <span className="okz-muted">({formatMoney(row.value_pkr, currency, usdToPkr, true)})</span>
                                </span>
                              </div>
                              <div className="h-1.5 w-full rounded bg-[var(--border)] overflow-hidden">
                                <div
                                  className="h-full bg-emerald-500"
                                  style={{ width: `${Math.min(100, row.weight_pct)}%` }}
                                />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="space-y-2.5">
                        <h3 className="text-xs font-bold uppercase tracking-wider okz-muted">
                          Combined Look-Through Sector Concentration
                        </h3>
                        {xrayData.look_through_sectors.length === 0 ? (
                          <p className="text-xs okz-muted py-4">No equity or FMR sector breakdown available yet.</p>
                        ) : (
                          <div className="space-y-2 p-3.5 rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)]/40 text-xs">
                            {xrayData.look_through_sectors.slice(0, 8).map((sec) => (
                              <div key={sec.sector} className="space-y-1">
                                <div className="flex justify-between">
                                  <span className="font-medium truncate max-w-52">{sec.sector}</span>
                                  <span className="font-mono font-semibold">
                                    {sec.weight_pct.toFixed(1)}%{" "}
                                    <span className="okz-muted">({formatMoney(sec.value_pkr, currency, usdToPkr, true)})</span>
                                  </span>
                                </div>
                                <div className="h-1.5 w-full rounded bg-[var(--border)] overflow-hidden">
                                  <div
                                    className="h-full bg-sky-500"
                                    style={{ width: `${Math.min(100, sec.weight_pct)}%` }}
                                  />
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>

                    {xrayData.stock_overlap.length > 0 && (
                      <div className="space-y-2">
                        <h3 className="text-xs font-bold uppercase tracking-wider okz-muted">
                          Combined Single-Stock Exposure &amp; Direct-vs-Fund Overlap Detector
                        </h3>
                        <div className="overflow-x-auto border border-[var(--border)] rounded-lg">
                          <table className="w-full text-xs">
                            <thead className="bg-[var(--bg-elevated)] okz-muted text-left">
                              <tr>
                                <th className="py-2 px-3">Stock</th>
                                <th className="py-2 px-3 text-right">Direct PSX</th>
                                <th className="py-2 px-3 text-right">Via Mutual Funds</th>
                                <th className="py-2 px-3 text-right">Combined Exposure</th>
                                <th className="py-2 px-3">Contributing Funds</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-[var(--border)]">
                              {xrayData.stock_overlap.map((st) => (
                                <tr key={st.symbol} className="hover:bg-[var(--card-muted)]/50">
                                  <td className="py-2 px-3">
                                    <div className="flex items-center gap-1.5">
                                      <Link
                                        href={`/stocks/${encodeURIComponent(st.symbol)}`}
                                        className="font-bold text-emerald-500 hover:underline"
                                      >
                                        {st.symbol}
                                      </Link>
                                      {st.is_overlap && (
                                        <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 text-[10px] font-bold">
                                          OVERLAP
                                        </span>
                                      )}
                                    </div>
                                    <div className="text-[10px] okz-muted truncate max-w-44">{st.name}</div>
                                  </td>
                                  <td className="py-2 px-3 text-right font-mono">
                                    {st.direct_pkr > 0 ? formatMoney(st.direct_pkr, currency, usdToPkr, true) : "—"}
                                  </td>
                                  <td className="py-2 px-3 text-right font-mono text-sky-400">
                                    {st.via_funds_pkr > 0 ? formatMoney(st.via_funds_pkr, currency, usdToPkr, true) : "—"}
                                  </td>
                                  <td className="py-2 px-3 text-right font-mono font-bold">
                                    {st.total_weight_pct.toFixed(2)}%{" "}
                                    <span className="font-normal okz-muted">
                                      ({formatMoney(st.total_pkr, currency, usdToPkr, true)})
                                    </span>
                                  </td>
                                  <td className="py-2 px-3">
                                    {st.contributing_funds.length === 0 ? (
                                      <span className="okz-muted">Direct holding only</span>
                                    ) : (
                                      <div className="flex flex-wrap gap-1">
                                        {st.contributing_funds.map((cf) => (
                                          <Link
                                            key={cf.fund_symbol}
                                            href={`/funds/${encodeURIComponent(cf.fund_symbol)}`}
                                            className="px-1.5 py-0.5 rounded bg-[var(--card-muted)] hover:border-emerald-500 border border-[var(--border)] font-mono text-[10px]"
                                          >
                                            {cf.fund_symbol} ({cf.weight_in_fund_pct.toFixed(1)}%)
                                          </Link>
                                        ))}
                                      </div>
                                    )}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="py-4 text-center text-xs okz-muted">
                    Add PSX stocks or Pakistani mutual funds to your portfolio to unlock look-through X-Ray analysis.
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="okz-card overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-[var(--border)] text-xs okz-muted text-left">
                  <tr>
                    <th className="py-3 px-4">Date</th>
                    <th className="py-3 px-4">Type</th>
                    <th className="py-3 px-4">Symbol</th>
                    <th className="py-3 px-4 text-right">Qty</th>
                    <th className="py-3 px-4 text-right">Price</th>
                    <th className="py-3 px-4 text-right">Fee</th>
                    <th className="py-3 px-4 text-right">WHT</th>
                    <th className="py-3 px-4">Notes</th>
                    <th className="py-3 px-4 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border)]">
                  {transactions.map((tx) => (
                    <tr key={tx.id} className="hover:bg-[var(--card-muted)]/50">
                      <td className="py-2.5 px-4 text-xs">{tx.tx_date}</td>
                      <td className="py-2.5 px-4">
                        <span className="text-xs px-2 py-0.5 rounded bg-[var(--card-muted)] font-semibold">
                          {tx.tx_type}
                        </span>
                      </td>
                      <td className="py-2.5 px-4 font-bold">{tx.asset_symbol}</td>
                      <td className="py-2.5 px-4 text-right">{formatNumber(tx.quantity, 4)}</td>
                      <td className="py-2.5 px-4 text-right">{formatNumber(tx.unit_price, 2)}</td>
                      <td className="py-2.5 px-4 text-right">{formatNumber(tx.fee_amount, 2)}</td>
                      <td className="py-2.5 px-4 text-right">{formatNumber(tx.tax_withheld, 2)}</td>
                      <td className="py-2.5 px-4 text-xs okz-muted truncate max-w-48">{tx.notes}</td>
                      <td className="py-2.5 px-4 text-right whitespace-nowrap">
                        <button
                          onClick={() => {
                            setEditingTx(tx);
                            setShowTxModal(true);
                          }}
                          title="Edit transaction"
                          className="p-1 text-[var(--text-muted)] hover:text-emerald-500 mr-1"
                        >
                          <Pencil className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => handleDeleteTx(tx.id)}
                          title="Delete transaction"
                          className="p-1 text-[var(--text-muted)] hover:text-rose-500"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {showDailyFundModal && activeId && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="okz-card w-full max-w-md p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-bold">Auto-Accrue Daily Dividend Funds</h2>
                <p className="text-xs okz-muted">
                  Constant Par NAV Money Market &amp; Cash Funds (MRAF, NIDDF, MICF, MCF, SGOV)
                </p>
              </div>
              <button onClick={() => setShowDailyFundModal(false)} className="p-1 rounded-md hover:bg-[var(--bg-elevated)]">
                <X className="w-5 h-5" />
              </button>
            </div>
            <form onSubmit={handleAccrueDailyFunds} className="space-y-3 text-xs">
              <div>
                <label className="block font-medium okz-muted mb-1">
                  Target Fund Symbol (leave blank for all Daily Dividend Funds held)
                </label>
                <input
                  type="text"
                  value={dailyFundSymbol}
                  onChange={(e) => setDailyFundSymbol(e.target.value.toUpperCase())}
                  placeholder="e.g., MRAF, NIDDF, MICF, SGOV"
                  className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm font-mono uppercase"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block font-medium okz-muted mb-1">Payout Mode</label>
                  <select
                    value={dailyFundMode}
                    onChange={(e) => setDailyFundMode(e.target.value as "REINVEST" | "CASH")}
                    className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
                  >
                    <option value="REINVEST">Daily DRIP (Reinvest Units)</option>
                    <option value="CASH">Cash Payout to Wallet</option>
                  </select>
                </div>
                <div>
                  <label className="block font-medium okz-muted mb-1">Ledger Rollup</label>
                  <select
                    value={dailyFundRollup}
                    onChange={(e) => setDailyFundRollup(e.target.value as "MONTHLY" | "SINGLE" | "DAILY")}
                    className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
                  >
                    <option value="MONTHLY">Monthly Summary (Clean)</option>
                    <option value="SINGLE">Single Entry To-Date</option>
                    <option value="DAILY">Daily Entries</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="block font-medium okz-muted mb-1">
                  Custom Annualized Yield Override % (optional — defaults to MUFAP live yield)
                </label>
                <input
                  type="number"
                  step="0.01"
                  value={dailyFundYieldOverride}
                  onChange={(e) => setDailyFundYieldOverride(e.target.value)}
                  placeholder="e.g., 17.85"
                  className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowDailyFundModal(false)}
                  className="px-4 py-2 rounded-md border border-[var(--border)] text-sm font-medium"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={accruingDailyFunds}
                  className="px-4 py-2 rounded-md bg-sky-600 hover:bg-sky-500 text-white text-sm font-semibold"
                >
                  {accruingDailyFunds ? "Accruing..." : "Run Auto-Accrual"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {showTxModal && activeId && (
        <TransactionModal
          portfolioId={activeId}
          usdToPkr={usdToPkr}
          initialTx={editingTx}
          onClose={() => {
            setShowTxModal(false);
            setEditingTx(null);
          }}
          onSaved={async () => {
            await loadDetail();
            if (benchmarkSymbol !== "NONE") await loadBenchmark();
          }}
        />
      )}

      {showCsvModal && activeId && (
        <CsvImportModal
          portfolioId={activeId}
          onClose={() => setShowCsvModal(false)}
          onImported={async () => {
            await loadDetail();
            if (benchmarkSymbol !== "NONE") await loadBenchmark();
          }}
        />
      )}

      {portfolioModalMode && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="okz-card w-full max-w-md p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-bold">
                {portfolioModalMode === "create" ? "Create New Portfolio" : "Edit Portfolio Settings"}
              </h2>
              <button onClick={() => setPortfolioModalMode(null)} className="p-1 rounded-md hover:bg-[var(--bg-elevated)]">
                <X className="w-5 h-5" />
              </button>
            </div>
            <form onSubmit={handleSavePortfolio} className="space-y-3">
              <div>
                <label className="block text-xs font-medium okz-muted mb-1">Portfolio Name</label>
                <input
                  type="text"
                  required
                  value={pfName}
                  onChange={(e) => setPfName(e.target.value)}
                  placeholder="e.g., Shariah Retirement Core"
                  className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium okz-muted mb-1">Base Accounting Currency</label>
                <select
                  value={pfCurrency}
                  onChange={(e) => setPfCurrency(e.target.value === "USD" ? "USD" : "PKR")}
                  className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
                >
                  <option value="PKR">PKR</option>
                  <option value="USD">USD</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium okz-muted mb-1">Description</label>
                <input
                  type="text"
                  value={pfDesc}
                  onChange={(e) => setPfDesc(e.target.value)}
                  placeholder="Long-term PSX dividends + money market"
                  className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
                />
              </div>
              <label className="flex items-center gap-2 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  checked={pfAutoCash}
                  onChange={(e) => setPfAutoCash(e.target.checked)}
                />
                <span>Auto-manage PKR Cash settlement wallet from trades &amp; dividends</span>
              </label>
              {pfError && <p className="text-xs text-rose-500">{pfError}</p>}
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setPortfolioModalMode(null)}
                  className="px-4 py-2 rounded-md border border-[var(--border)] text-sm font-medium"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold"
                >
                  {portfolioModalMode === "create" ? "Create" : "Save Changes"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {showShareModal && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="okz-card w-full max-w-lg p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-bold">Manage Public Read-Only Share Links</h2>
              <button onClick={() => setShowShareModal(false)} className="p-1 rounded-md hover:bg-[var(--bg-elevated)]">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex items-center justify-between gap-3 p-3 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border)]">
              <label className="flex items-center gap-2 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  checked={shareHideAmounts}
                  onChange={(e) => setShareHideAmounts(e.target.checked)}
                />
                <span>Hide absolute PKR/USD amounts (weights &amp; returns only)</span>
              </label>
              <button
                type="button"
                onClick={handleCreateShareLink}
                className="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shrink-0"
              >
                + Generate Link
              </button>
            </div>
            <div className="divide-y divide-[var(--border)] max-h-60 overflow-y-auto text-xs">
              {shareLinks.length === 0 ? (
                <p className="py-4 text-center okz-muted">No active share links for this portfolio.</p>
              ) : (
                shareLinks.map((lnk) => (
                  <div key={lnk.id} className="py-2.5 flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <Link
                        href={`/share/${lnk.token}`}
                        target="_blank"
                        className="font-mono text-emerald-500 hover:underline truncate block"
                      >
                        /share/{lnk.token}
                      </Link>
                      <span className="okz-muted text-[11px]">
                        {lnk.hide_amounts ? "Amounts Hidden" : "Amounts Visible"} &bull;{" "}
                        {new Date(lnk.created_at).toLocaleDateString()}
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <button
                        type="button"
                        onClick={() => handleCopyShareLink(lnk.token)}
                        className="px-2 py-1 rounded border border-[var(--border)] hover:border-emerald-500 inline-flex items-center gap-1"
                      >
                        <Copy className="w-3 h-3" />
                        {copiedToken === lnk.token ? "Copied" : "Copy"}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleRevokeShareLink(lnk.id)}
                        className="px-2 py-1 rounded border border-[var(--border)] hover:border-rose-500 text-rose-500"
                      >
                        Revoke
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
