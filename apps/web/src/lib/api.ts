export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export interface AssetIndexItem {
  symbol: string;
  name: string;
  asset_class: string;
  sector_or_category: string | null;
  currency: string;
  last_price: number;
  change_pct: number;
  is_shariah_compliant: boolean;
}

function resolveBaseUrl(): string {
  if (typeof window !== "undefined") {
    return ""; // Proxied via Next.js /api/v1 rewrite for same-origin cookies
  }
  return process.env.INTERNAL_API_BASE_URL || "http://localhost:8000";
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${resolveBaseUrl()}${path}`;
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type") && init?.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(url, {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });

  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const errJson = await res.json();
      detail = errJson.detail || detail;
    } catch {
      // ignore non-json error bodies
    }
    throw new ApiError(detail, res.status);
  }

  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Client-side Stale-While-Revalidate (SWR) Cache + Single-Flight Deduplication
// ---------------------------------------------------------------------------

interface CacheEntry<T> {
  data: T;
  expiresAt: number;
  staleUntil: number;
}

const _swrCache = new Map<string, CacheEntry<unknown>>();
const _inflight = new Map<string, Promise<unknown>>();

export function invalidateApiCache(prefix = ""): void {
  if (!prefix) {
    _swrCache.clear();
    _assetIndexMem = null;
    if (typeof window !== "undefined") {
      try {
        window.sessionStorage.removeItem(ASSET_INDEX_STORAGE_KEY);
      } catch {
        // ignore storage errors
      }
    }
    return;
  }
  for (const key of Array.from(_swrCache.keys())) {
    if (key.startsWith(prefix)) {
      _swrCache.delete(key);
    }
  }
}

export async function apiFetchCached<T>(
  path: string,
  ttlMs = 60_000,
  options?: {
    onUpdate?: (fresh: T) => void;
    forceRefresh?: boolean;
    signal?: AbortSignal;
  },
): Promise<T> {
  const now = Date.now();
  const entry = _swrCache.get(path) as CacheEntry<T> | undefined;

  const fetchAndStore = (): Promise<T> => {
    const existing = _inflight.get(path) as Promise<T> | undefined;
    if (existing && !options?.forceRefresh) {
      return existing;
    }
    const p = apiFetch<T>(path, options?.signal ? { signal: options.signal } : undefined)
      .then((fresh) => {
        const ts = Date.now();
        _swrCache.set(path, {
          data: fresh,
          expiresAt: ts + ttlMs,
          staleUntil: ts + Math.max(ttlMs * 4, 300_000),
        });
        return fresh;
      })
      .finally(() => {
        _inflight.delete(path);
      });
    _inflight.set(path, p);
    return p;
  };

  if (!options?.forceRefresh && entry) {
    if (now < entry.expiresAt) {
      return entry.data;
    }
    if (now < entry.staleUntil) {
      // Stale-while-revalidate: serve immediately and refresh silently in background
      fetchAndStore()
        .then((fresh) => {
          options?.onUpdate?.(fresh);
        })
        .catch(() => {});
      return entry.data;
    }
  }

  return fetchAndStore();
}

// ---------------------------------------------------------------------------
// Compact 874-Asset Search Index (<1ms Global Search & TickerAutocomplete)
// ---------------------------------------------------------------------------

const ASSET_INDEX_STORAGE_KEY = "okz_asset_index_v1";
const ASSET_INDEX_TTL_MS = 10 * 60 * 1000;

let _assetIndexMem: { data: AssetIndexItem[]; expiresAt: number } | null = null;
let _assetIndexPromise: Promise<AssetIndexItem[]> | null = null;

export async function getAssetIndex(forceRefresh = false): Promise<AssetIndexItem[]> {
  const now = Date.now();
  if (!forceRefresh && _assetIndexMem && now < _assetIndexMem.expiresAt) {
    return _assetIndexMem.data;
  }

  if (!forceRefresh && typeof window !== "undefined") {
    try {
      const raw = window.sessionStorage.getItem(ASSET_INDEX_STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as { data: AssetIndexItem[]; expiresAt: number };
        if (parsed && Array.isArray(parsed.data) && parsed.data.length > 0 && now < parsed.expiresAt) {
          _assetIndexMem = parsed;
          return parsed.data;
        }
      }
    } catch {
      // ignore corrupted sessionStorage
    }
  }

  if (_assetIndexPromise && !forceRefresh) {
    return _assetIndexPromise;
  }

  _assetIndexPromise = apiFetch<AssetIndexItem[]>("/api/v1/market/assets/index")
    .then((data) => {
      const expiresAt = Date.now() + ASSET_INDEX_TTL_MS;
      _assetIndexMem = { data, expiresAt };
      if (typeof window !== "undefined") {
        try {
          window.sessionStorage.setItem(ASSET_INDEX_STORAGE_KEY, JSON.stringify({ data, expiresAt }));
        } catch {
          // ignore quota errors
        }
      }
      return data;
    })
    .finally(() => {
      _assetIndexPromise = null;
    });

  return _assetIndexPromise;
}

export function filterAssetIndex(
  index: AssetIndexItem[],
  query: string,
  opts?: { assetClasses?: string[]; limit?: number },
): AssetIndexItem[] {
  const limit = opts?.limit ?? 12;
  const classSet = opts?.assetClasses && opts.assetClasses.length > 0 ? new Set(opts.assetClasses) : null;
  const q = query.trim().toLowerCase();

  if (!q) {
    const base = classSet ? index.filter((a) => classSet.has(a.asset_class)) : index;
    // Prioritize popular PSX leaders & indices when search box is empty
    const featured = ["KSE100", "KMI30", "MEBL", "OGDC", "LUCK", "ENGROH", "FFC", "HUBC", "SYS", "MCB", "UBL", "MIF"];
    const rank = new Map(featured.map((s, i) => [s, i]));
    return [...base]
      .sort((a, b) => {
        const ra = rank.has(a.symbol) ? rank.get(a.symbol)! : 999;
        const rb = rank.has(b.symbol) ? rank.get(b.symbol)! : 999;
        if (ra !== rb) return ra - rb;
        return a.symbol.localeCompare(b.symbol);
      })
      .slice(0, limit);
  }

  const scored: Array<{ score: number; item: AssetIndexItem }> = [];
  for (let i = 0; i < index.length; i++) {
    const item = index[i];
    if (classSet && !classSet.has(item.asset_class)) continue;
    const sym = item.symbol.toLowerCase();
    const name = item.name.toLowerCase();
    const sec = (item.sector_or_category || "").toLowerCase();

    let score = -1;
    if (sym === q) score = 0;
    else if (sym.startsWith(q)) score = 1;
    else if (sym.includes(q)) score = 2;
    else if (name.startsWith(q)) score = 3;
    else if (name.includes(` ${q}`)) score = 4;
    else if (name.includes(q)) score = 5;
    else if (sec.includes(q)) score = 6;

    if (score >= 0) {
      scored.push({ score, item });
    }
  }

  scored.sort((a, b) => a.score - b.score || a.item.symbol.localeCompare(b.item.symbol));
  return scored.slice(0, limit).map((x) => x.item);
}
