"use client";

import React, { useEffect, useState } from "react";
import { Plus, Search, X } from "lucide-react";
import { apiFetch } from "@/lib/api";

interface AssetOption {
  id: string;
  symbol: string;
  name: string;
  asset_class: string;
  currency: string;
  last_price: string;
  is_custom?: boolean;
}

export interface EditableTransaction {
  id: string;
  asset_symbol: string;
  tx_type: string;
  tx_date: string;
  quantity: string;
  unit_price: string;
  gross_amount?: string;
  fee_amount: string;
  tax_withheld: string;
  deemed_ex_price: string;
  fx_rate_to_pkr?: string;
  notes: string;
}

interface TransactionModalProps {
  portfolioId: string;
  usdToPkr: number;
  onClose: () => void;
  onSaved: () => void;
  initialTx?: EditableTransaction | null;
  defaultSymbol?: string;
}

const TX_TYPES = [
  { value: "BUY", label: "BUY (Purchase)" },
  { value: "SELL", label: "SELL (Disposal)" },
  { value: "OPENING_BALANCE", label: "OPENING_BALANCE (Initial Holding)" },
  { value: "DIVIDEND", label: "DIVIDEND (Cash Payout)" },
  { value: "REINVEST_DIVIDEND", label: "REINVEST_DIVIDEND (DRIP)" },
  { value: "BONUS", label: "BONUS (Stock Dividend)" },
  { value: "RIGHTS", label: "RIGHTS (Rights Issue)" },
  { value: "SPLIT", label: "SPLIT (Stock Split Ratio)" },
  { value: "DEPOSIT", label: "DEPOSIT (Cash In)" },
  { value: "WITHDRAWAL", label: "WITHDRAWAL (Cash Out)" },
  { value: "FEE", label: "FEE (CDC / Broker Charge)" },
  { value: "ZAKAT_PAID", label: "ZAKAT_PAID" },
];

export function TransactionModal({
  portfolioId,
  usdToPkr,
  onClose,
  onSaved,
  initialTx = null,
  defaultSymbol = "MEBL",
}: TransactionModalProps) {
  const isEditing = Boolean(initialTx);
  const todayStr = new Date().toISOString().slice(0, 10);

  const [assetQuery, setAssetQuery] = useState(initialTx?.asset_symbol || defaultSymbol);
  const [assets, setAssets] = useState<AssetOption[]>([]);
  const [searchingAssets, setSearchingAssets] = useState(false);
  const [assetSymbol, setAssetSymbol] = useState(initialTx?.asset_symbol || defaultSymbol);

  const [txType, setTxType] = useState(initialTx?.tx_type || "BUY");
  const [txDate, setTxDate] = useState(initialTx?.tx_date || todayStr);
  const [quantity, setQuantity] = useState(initialTx?.quantity || "100");
  const [unitPrice, setUnitPrice] = useState(initialTx?.unit_price || "0");
  const [grossAmount, setGrossAmount] = useState(initialTx?.gross_amount || "0");
  const [feeAmount, setFeeAmount] = useState(initialTx?.fee_amount || "0");
  const [taxWithheld, setTaxWithheld] = useState(initialTx?.tax_withheld || "0");
  const [deemedExPrice, setDeemedExPrice] = useState(initialTx?.deemed_ex_price || "0");
  const [fxRate, setFxRate] = useState(initialTx?.fx_rate_to_pkr || "1");
  const [notes, setNotes] = useState(initialTx?.notes || "");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Inline custom asset creator state
  const [showCustomForm, setShowCustomForm] = useState(false);
  const [customSymbol, setCustomSymbol] = useState("");
  const [customName, setCustomName] = useState("");
  const [customClass, setCustomClass] = useState("CUSTOM");
  const [customCurrency, setCustomCurrency] = useState<"PKR" | "USD">("PKR");
  const [customSector, setCustomSector] = useState("Custom Holdings");
  const [customPrice, setCustomPrice] = useState("100");
  const [customShariah, setCustomShariah] = useState(true);
  const [creatingAsset, setCreatingAsset] = useState(false);

  useEffect(() => {
    if (isEditing) return;
    let active = true;
    const timer = setTimeout(async () => {
      setSearchingAssets(true);
      try {
        const res = await apiFetch<AssetOption[]>(
          `/api/v1/market/assets?q=${encodeURIComponent(assetQuery.trim())}&limit=40`
        );
        if (!active) return;
        setAssets(res);
        // Auto-select exact symbol match if current selection isn't in filtered list
        const exact = res.find((a) => a.symbol === assetSymbol) || res[0];
        if (exact && !res.some((a) => a.symbol === assetSymbol)) {
          setAssetSymbol(exact.symbol);
          setUnitPrice(String(Number(exact.last_price)));
          setFxRate(exact.currency === "USD" ? String(usdToPkr) : "1");
        }
      } catch {
        // Keep current options on transient search failure
      } finally {
        if (active) setSearchingAssets(false);
      }
    }, 220);

    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [assetQuery, isEditing, assetSymbol, usdToPkr]);

  const handleSelectAsset = (symbol: string) => {
    setAssetSymbol(symbol);
    const chosen = assets.find((a) => a.symbol === symbol);
    if (chosen) {
      setUnitPrice(String(Number(chosen.last_price)));
      setFxRate(chosen.currency === "USD" ? String(usdToPkr) : "1");
    }
  };

  const handleCreateCustomAsset = async () => {
    setError(null);
    if (!customSymbol.trim() || !customName.trim()) {
      setError("Custom asset requires both symbol and name.");
      return;
    }
    setCreatingAsset(true);
    try {
      const created = await apiFetch<AssetOption>("/api/v1/market/assets/custom", {
        method: "POST",
        body: JSON.stringify({
          symbol: customSymbol.trim().toUpperCase(),
          name: customName.trim(),
          asset_class: customClass,
          currency: customCurrency,
          sector_or_category: customSector.trim() || "Custom Holdings",
          is_shariah_compliant: customShariah,
          last_price: Number(customPrice) || 1,
        }),
      });
      setAssets((prev) => [created, ...prev.filter((a) => a.symbol !== created.symbol)]);
      setAssetSymbol(created.symbol);
      setAssetQuery(created.symbol);
      setUnitPrice(String(Number(created.last_price)));
      setFxRate(created.currency === "USD" ? String(usdToPkr) : "1");
      setShowCustomForm(false);
      setCustomSymbol("");
      setCustomName("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create custom asset");
    } finally {
      setCreatingAsset(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (isEditing && initialTx) {
        await apiFetch(`/api/v1/portfolios/${portfolioId}/transactions/${initialTx.id}`, {
          method: "PATCH",
          body: JSON.stringify({
            tx_type: txType,
            tx_date: txDate,
            quantity: Number(quantity) || 0,
            unit_price: Number(unitPrice) || 0,
            gross_amount: Number(grossAmount) || 0,
            fee_amount: Number(feeAmount) || 0,
            tax_withheld: Number(taxWithheld) || 0,
            deemed_ex_price: Number(deemedExPrice) || 0,
            fx_rate_to_pkr: Number(fxRate) || 1,
            notes,
          }),
        });
      } else {
        await apiFetch(`/api/v1/portfolios/${portfolioId}/transactions`, {
          method: "POST",
          body: JSON.stringify({
            asset_symbol: assetSymbol,
            tx_type: txType,
            tx_date: txDate,
            quantity: Number(quantity) || 0,
            unit_price: Number(unitPrice) || 0,
            gross_amount: Number(grossAmount) || 0,
            fee_amount: Number(feeAmount) || 0,
            tax_withheld: Number(taxWithheld) || 0,
            deemed_ex_price: Number(deemedExPrice) || 0,
            fx_rate_to_pkr: Number(fxRate) || 1,
            notes,
          }),
        });
      }
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save transaction");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4">
      <div className="okz-card w-full max-w-xl p-6 shadow-2xl max-h-[92vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-bold">
            {isEditing ? `Edit Transaction (${initialTx?.asset_symbol})` : "Add Portfolio Transaction"}
          </h2>
          <button onClick={onClose} className="p-1 rounded-md hover:bg-[var(--bg-elevated)]" aria-label="Close modal">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="sm:col-span-2">
              <div className="flex items-center justify-between mb-1">
                <label className="block text-xs font-medium okz-muted">
                  Asset {isEditing ? "(Locked on edit)" : "(Async Search across PSX, MUFAP, FX, Crypto & Custom)"}
                </label>
                {!isEditing && (
                  <button
                    type="button"
                    onClick={() => setShowCustomForm((v) => !v)}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-500 hover:underline"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    {showCustomForm ? "Cancel Custom Asset" : "Create Custom Asset"}
                  </button>
                )}
              </div>

              {!isEditing ? (
                <div className="space-y-2">
                  <div className="relative">
                    <Search className="w-4 h-4 okz-muted absolute left-2.5 top-2.5" />
                    <input
                      type="text"
                      value={assetQuery}
                      onChange={(e) => setAssetQuery(e.target.value)}
                      placeholder="Search symbol or name (e.g., MEBL, SYS, GOLD, MCF, BTC)..."
                      className="w-full pl-8 pr-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
                    />
                  </div>
                  <select
                    value={assetSymbol}
                    onChange={(e) => handleSelectAsset(e.target.value)}
                    className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
                  >
                    {assets.length === 0 ? (
                      <option value={assetSymbol}>
                        {searchingAssets ? "Searching..." : assetSymbol}
                      </option>
                    ) : (
                      assets.map((a) => (
                        <option key={a.id} value={a.symbol}>
                          {a.symbol} — {a.name} ({a.currency}) {a.is_custom ? "[Custom]" : ""}
                        </option>
                      ))
                    )}
                  </select>
                </div>
              ) : (
                <input
                  type="text"
                  disabled
                  value={assetSymbol}
                  className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg-elevated)] text-sm font-semibold opacity-80"
                />
              )}
            </div>

            {showCustomForm && !isEditing && (
              <div className="sm:col-span-2 p-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5 space-y-2.5">
                <div className="text-xs font-semibold text-emerald-500">New Private Custom Asset (Plot, Sukuk, Gold Jewellery, Private Equity)</div>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                  <input
                    type="text"
                    value={customSymbol}
                    onChange={(e) => setCustomSymbol(e.target.value)}
                    placeholder="Symbol (e.g. DHA_PLOT)"
                    className="px-2.5 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
                  />
                  <input
                    type="text"
                    value={customName}
                    onChange={(e) => setCustomName(e.target.value)}
                    placeholder="Full Name"
                    className="px-2.5 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs sm:col-span-2"
                  />
                  <select
                    value={customClass}
                    onChange={(e) => setCustomClass(e.target.value)}
                    className="px-2.5 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
                  >
                    <option value="CUSTOM">CUSTOM (Real Estate / Other)</option>
                    <option value="NSS">NSS / Sukuk</option>
                    <option value="GOLD">GOLD Jewellery / Bar</option>
                    <option value="PSX_EQUITY">PSX_EQUITY</option>
                    <option value="MUTUAL_FUND">MUTUAL_FUND</option>
                    <option value="CRYPTO">CRYPTO</option>
                    <option value="CASH">CASH</option>
                  </select>
                  <select
                    value={customCurrency}
                    onChange={(e) => setCustomCurrency(e.target.value as "PKR" | "USD")}
                    className="px-2.5 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
                  >
                    <option value="PKR">PKR</option>
                    <option value="USD">USD</option>
                  </select>
                  <input
                    type="number"
                    step="any"
                    min="0.0001"
                    value={customPrice}
                    onChange={(e) => setCustomPrice(e.target.value)}
                    placeholder="Current Price"
                    className="px-2.5 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
                  />
                  <input
                    type="text"
                    value={customSector}
                    onChange={(e) => setCustomSector(e.target.value)}
                    placeholder="Sector / Category"
                    className="px-2.5 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs sm:col-span-2"
                  />
                  <label className="flex items-center gap-1.5 text-xs">
                    <input
                      type="checkbox"
                      checked={customShariah}
                      onChange={(e) => setCustomShariah(e.target.checked)}
                    />
                    Shariah Compliant
                  </label>
                </div>
                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={handleCreateCustomAsset}
                    disabled={creatingAsset}
                    className="px-3 py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold disabled:opacity-60"
                  >
                    {creatingAsset ? "Creating..." : "Create & Select Asset"}
                  </button>
                </div>
              </div>
            )}

            <div>
              <label className="block text-xs font-medium okz-muted mb-1">Transaction Type</label>
              <select
                value={txType}
                onChange={(e) => setTxType(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              >
                {TX_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium okz-muted mb-1">Transaction Date</label>
              <input
                type="date"
                value={txDate}
                onChange={(e) => setTxDate(e.target.value)}
                required
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              />
            </div>

            <div>
              <label className="block text-xs font-medium okz-muted mb-1">
                {txType === "SPLIT" ? "Split Ratio (e.g. 2 for 2-for-1)" : "Quantity / Units"}
              </label>
              <input
                type="number"
                step="any"
                min="0"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              />
            </div>

            <div>
              <label className="block text-xs font-medium okz-muted mb-1">Unit Price (Asset Currency)</label>
              <input
                type="number"
                step="any"
                min="0"
                value={unitPrice}
                onChange={(e) => setUnitPrice(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              />
            </div>

            <div>
              <label className="block text-xs font-medium okz-muted mb-1">Brokerage + CDC/NCCPL Fee (0=Auto)</label>
              <input
                type="number"
                step="any"
                min="0"
                value={feeAmount}
                onChange={(e) => setFeeAmount(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              />
            </div>

            <div>
              <label className="block text-xs font-medium okz-muted mb-1">
                {txType === "BONUS" ? "Sec 236Z Tax (0=Auto)" : "Tax WHT (0=Auto)"}
              </label>
              <input
                type="number"
                step="any"
                min="0"
                value={taxWithheld}
                onChange={(e) => setTaxWithheld(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              />
            </div>

            <div>
              <label className="block text-xs font-medium okz-muted mb-1">FX Rate to PKR</label>
              <input
                type="number"
                step="any"
                min="0.0001"
                value={fxRate}
                onChange={(e) => setFxRate(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              />
            </div>
          </div>

          {(txType === "DIVIDEND" || txType === "REINVEST_DIVIDEND") && (
            <div>
              <label className="block text-xs font-medium okz-muted mb-1">Gross Dividend / DRIP Payout (PKR)</label>
              <input
                type="number"
                step="any"
                min="0"
                value={grossAmount}
                onChange={(e) => setGrossAmount(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              />
            </div>
          )}

          {txType === "BONUS" && (
            <div>
              <label className="block text-xs font-medium okz-muted mb-1">
                Section 236Z Deemed Ex-Price (PKR — 0 uses current market price)
              </label>
              <input
                type="number"
                step="any"
                min="0"
                value={deemedExPrice}
                onChange={(e) => setDeemedExPrice(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              />
            </div>
          )}

          <div>
            <label className="block text-xs font-medium okz-muted mb-1">Notes</label>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g., KTrade execution, CDC bonus lot, or MCF DRIP"
              className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            />
          </div>

          {error && <p className="text-xs text-rose-500">{error}</p>}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-md border border-[var(--border)] text-sm font-medium"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold disabled:opacity-60"
            >
              {submitting ? "Saving..." : isEditing ? "Update Transaction" : "Save Transaction"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
