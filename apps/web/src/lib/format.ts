export type DisplayCurrency = "PKR" | "USD";

export function toNum(val: string | number | null | undefined): number {
  if (val === null || val === undefined || val === "") return 0;
  const n = typeof val === "number" ? val : Number(val);
  return Number.isFinite(n) ? n : 0;
}

export function formatMoney(
  amountPkr: string | number | null | undefined,
  displayCurrency: DisplayCurrency = "PKR",
  usdToPkr: string | number = 278.45,
  compact = false
): string {
  if (amountPkr === null || amountPkr === undefined) return "Hidden";
  const pkr = toNum(amountPkr);
  const fx = Math.max(toNum(usdToPkr), 1);

  if (displayCurrency === "USD") {
    const usd = pkr / fx;
    if (compact && Math.abs(usd) >= 1_000_000) {
      return `$${(usd / 1_000_000).toFixed(2)}M`;
    }
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2,
    }).format(usd);
  }

  if (compact) {
    const abs = Math.abs(pkr);
    const sign = pkr < 0 ? "-" : "";
    if (abs >= 10_000_000) {
      return `${sign}PKR ${(abs / 10_000_000).toFixed(2)} Cr`;
    }
    if (abs >= 100_000) {
      return `${sign}PKR ${(abs / 100_000).toFixed(2)} Lac`;
    }
  }

  return `PKR ${new Intl.NumberFormat("en-PK", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(pkr)}`;
}

export function formatNumber(val: string | number | null | undefined, decimals = 2): string {
  if (val === null || val === undefined || val === "") return "—";
  const n = toNum(val);
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(n);
}

export function formatPct(val: string | number | null | undefined): string {
  if (val === null || val === undefined || val === "") return "—";
  const n = toNum(val);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

export function pctColorClass(val: string | number | null | undefined): string {
  const n = toNum(val);
  if (n > 0) return "text-emerald-500";
  if (n < 0) return "text-rose-500";
  return "text-slate-400";
}
