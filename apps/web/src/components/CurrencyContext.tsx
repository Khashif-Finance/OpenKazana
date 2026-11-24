"use client";

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { DisplayCurrency } from "@/lib/format";

export interface UserPreferences {
  shariah_only_default: boolean;
  default_market_view: string;
  compact_tables: boolean;
  show_news_ticker: boolean;
  preferred_news_sources: string;
}

export interface UserSession {
  id: string;
  email: string;
  full_name: string;
  is_demo: boolean;
  is_admin?: boolean;
  demo_expires_at?: string | null;
  preferred_currency: string;
  telegram_chat_id?: string | null;
  tax_profile?: {
    filer_status: string;
    fiscal_year: string;
    default_broker_rate_bps: number;
    zakat_exempt: boolean;
    us_w8ben_active?: boolean;
    foreign_cgt_rate_pct?: string | number;
  } | null;
  preferences?: UserPreferences | null;
}

interface NotificationBrief {
  id: string;
  is_read: boolean;
}

interface AuthConfigResp {
  auth_mode: string;
  local_mode: boolean;
  allow_registration: boolean;
  google_client_id?: string | null;
  demo_ttl_hours: number;
}

interface AppContextValue {
  currency: DisplayCurrency;
  setCurrency: (c: DisplayCurrency) => void;
  toggleCurrency: () => void;
  theme: "dark" | "light";
  toggleTheme: () => void;
  usdToPkr: number;
  setUsdToPkr: (rate: number) => void;
  user: UserSession | null;
  setUserSession: (u: UserSession | null) => void;
  authMode: "local" | "multiuser";
  localMode: boolean;
  shariahOnlyDefault: boolean;
  loadingUser: boolean;
  unreadNotifications: number;
  refreshNotifications: () => Promise<void>;
  refreshUser: () => Promise<void>;
  logout: () => Promise<void>;
}

const AppContext = createContext<AppContextValue | undefined>(undefined);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [currency, setCurrency] = useState<DisplayCurrency>("PKR");
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [usdToPkr, setUsdToPkr] = useState<number>(280.4);
  const [user, setUser] = useState<UserSession | null>(null);
  const [authMode, setAuthMode] = useState<"local" | "multiuser">("local");
  const [localMode, setLocalMode] = useState<boolean>(true);
  const [loadingUser, setLoadingUser] = useState(true);
  const [unreadNotifications, setUnreadNotifications] = useState(0);

  const refreshNotifications = useCallback(async () => {
    try {
      const list = await apiFetch<NotificationBrief[]>("/api/v1/notifications");
      setUnreadNotifications(list.filter((n) => !n.is_read).length);
    } catch {
      setUnreadNotifications(0);
    }
  }, []);

  const refreshUser = useCallback(async () => {
    setLoadingUser(true);
    try {
      const cfg = await apiFetch<AuthConfigResp>("/api/v1/auth/config").catch(() => null);
      if (cfg) {
        const isLoc = Boolean(cfg.local_mode);
        setLocalMode(isLoc);
        setAuthMode(isLoc ? "local" : "multiuser");
      }
      const me = await apiFetch<UserSession>("/api/v1/auth/me");
      setUser(me);
      if (me.preferred_currency === "USD" || me.preferred_currency === "PKR") {
        setCurrency(me.preferred_currency);
      }
      await refreshNotifications();
    } catch {
      setUser(null);
      setUnreadNotifications(0);
    } finally {
      setLoadingUser(false);
    }
  }, [refreshNotifications]);

  useEffect(() => {
    const savedTheme = (localStorage.getItem("okz_theme") as "dark" | "light" | null) || "dark";
    setTheme(savedTheme);
    document.documentElement.classList.toggle("dark", savedTheme === "dark");

    const savedCcy = (localStorage.getItem("okz_currency") as DisplayCurrency | null) || "PKR";
    setCurrency(savedCcy);

    refreshUser();
  }, [refreshUser]);

  useEffect(() => {
    if (!user) return;
    const id = setInterval(() => {
      refreshNotifications();
    }, 60000);
    return () => clearInterval(id);
  }, [user, refreshNotifications]);

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("okz_theme", next);
    document.documentElement.classList.toggle("dark", next === "dark");
  };

  const handleSetCurrency = (c: DisplayCurrency) => {
    setCurrency(c);
    localStorage.setItem("okz_currency", c);
  };

  const toggleCurrency = () => handleSetCurrency(currency === "PKR" ? "USD" : "PKR");

  const logout = async () => {
    if (localMode) return;
    await apiFetch("/api/v1/auth/logout", { method: "POST" });
    setUser(null);
    setUnreadNotifications(0);
  };

  return (
    <AppContext.Provider
      value={{
        currency,
        setCurrency: handleSetCurrency,
        toggleCurrency,
        theme,
        toggleTheme,
        usdToPkr,
        setUsdToPkr,
        user,
        setUserSession: setUser,
        authMode,
        localMode,
        shariahOnlyDefault: Boolean(user?.preferences?.shariah_only_default),
        loadingUser,
        unreadNotifications,
        refreshNotifications,
        refreshUser,
        logout,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useAppContext() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useAppContext must be used inside AppProvider");
  return ctx;
}
