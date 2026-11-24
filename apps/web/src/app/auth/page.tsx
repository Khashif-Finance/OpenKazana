"use client";

import React, { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { KeyRound, ShieldCheck, UserPlus } from "lucide-react";
import { useAppContext } from "@/components/CurrencyContext";
import { apiFetch } from "@/lib/api";

interface GoogleAccountsId {
  initialize: (cfg: { client_id: string; callback: (resp: { credential?: string }) => void }) => void;
  renderButton: (el: HTMLElement, opts: Record<string, unknown>) => void;
}

interface GoogleWindow {
  google?: {
    accounts?: {
      id?: GoogleAccountsId;
    };
  };
}

interface AuthConfig {
  google_client_id: string | null;
  demo_ttl_hours: number;
}

export default function AuthPage() {
  const router = useRouter();
  const { refreshUser } = useAppContext();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [filerStatus, setFilerStatus] = useState("ACTIVE_FILER");
  const [authConfig, setAuthConfig] = useState<AuthConfig>({ google_client_id: null, demo_ttl_hours: 24 });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const googleBtnRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    apiFetch<AuthConfig>("/api/v1/auth/config")
      .then((cfg) => setAuthConfig(cfg))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const clientId = authConfig.google_client_id;
    if (!clientId || !googleBtnRef.current) return;

    let cancelled = false;
    const renderGoogleButton = () => {
      if (cancelled || !googleBtnRef.current) return;
      const gId = (window as unknown as GoogleWindow).google?.accounts?.id;
      if (!gId) return;
      googleBtnRef.current.innerHTML = "";
      gId.initialize({
        client_id: clientId,
        callback: async (resp: { credential?: string }) => {
          if (!resp.credential) return;
          setError(null);
          setSubmitting(true);
          try {
            await apiFetch("/api/v1/auth/google", {
              method: "POST",
              body: JSON.stringify({ id_token: resp.credential }),
            });
            await refreshUser();
            router.push("/portfolio");
          } catch (err) {
            setError(err instanceof Error ? err.message : "Google Sign-In failed");
          } finally {
            setSubmitting(false);
          }
        },
      });
      gId.renderButton(googleBtnRef.current, {
        theme: "outline",
        size: "large",
        width: 360,
        text: "continue_with",
      });
    };

    const existing = document.getElementById("google-gsi-script");
    if (existing) {
      renderGoogleButton();
    } else {
      const script = document.createElement("script");
      script.id = "google-gsi-script";
      script.src = "https://accounts.google.com/gsi/client";
      script.async = true;
      script.defer = true;
      script.onload = renderGoogleButton;
      document.head.appendChild(script);
    }

    return () => {
      cancelled = true;
    };
  }, [authConfig.google_client_id, refreshUser, router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "login") {
        await apiFetch("/api/v1/auth/login", {
          method: "POST",
          body: JSON.stringify({ email, password }),
        });
      } else {
        await apiFetch("/api/v1/auth/register", {
          method: "POST",
          body: JSON.stringify({
            email,
            password,
            full_name: fullName,
            filer_status: filerStatus,
          }),
        });
      }
      await refreshUser();
      router.push("/portfolio");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-md mx-auto py-8">
      <div className="okz-card p-6 space-y-5">
        <div className="text-center space-y-1">
          <h1 className="text-2xl font-bold">OpenKhazana Account</h1>
          <p className="text-xs okz-muted">
            Secure PostgreSQL RLS multi-user workspace with Pakistan tax profile &amp; broker ledgers.
          </p>
        </div>

        <div className="grid grid-cols-2 rounded-lg p-1 bg-[var(--bg-elevated)] border border-[var(--border)]">
          <button
            type="button"
            onClick={() => setMode("login")}
            className={`py-2 rounded-md text-xs font-semibold flex items-center justify-center gap-1.5 ${
              mode === "login" ? "bg-[var(--card)] shadow text-emerald-500" : "okz-muted"
            }`}
          >
            <KeyRound className="w-3.5 h-3.5" />
            Sign In
          </button>
          <button
            type="button"
            onClick={() => setMode("register")}
            className={`py-2 rounded-md text-xs font-semibold flex items-center justify-center gap-1.5 ${
              mode === "register" ? "bg-[var(--card)] shadow text-emerald-500" : "okz-muted"
            }`}
          >
            <UserPlus className="w-3.5 h-3.5" />
            Create Account
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          {mode === "register" && (
            <>
              <div>
                <label className="block text-xs font-medium okz-muted mb-1">Full Name</label>
                <input
                  type="text"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  required
                  placeholder="Ayesha Khan"
                  className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium okz-muted mb-1">FBR Filer Status (ATL)</label>
                <select
                  value={filerStatus}
                  onChange={(e) => setFilerStatus(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
                >
                  <option value="ACTIVE_FILER">Active Filer (15% CGT / 15% Div WHT)</option>
                  <option value="NON_FILER">Non-Filer (30% Div WHT / Doubled Tier)</option>
                </select>
              </div>
            </>
          )}

          <div>
            <label className="block text-xs font-medium okz-muted mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              placeholder="you@example.com"
              className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            />
          </div>

          <div>
            <label className="block text-xs font-medium okz-muted mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              placeholder="Minimum 8 characters"
              className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            />
          </div>

          {error && <p className="text-xs text-rose-500">{error}</p>}

          <button
            type="submit"
            disabled={submitting}
            className="w-full py-2.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold disabled:opacity-60"
          >
            {submitting ? "Authenticating..." : mode === "login" ? "Sign In" : "Create Account"}
          </button>
        </form>

        {authConfig.google_client_id && (
          <div className="border-t border-[var(--border)] pt-4 space-y-2">
            <div className="text-[11px] uppercase tracking-wider text-center okz-muted">Or Continue With Google</div>
            <div ref={googleBtnRef} className="flex justify-center" />
          </div>
        )}

        <div className="border-t border-[var(--border)] pt-4">
          <p className="text-[11px] okz-muted text-center flex items-center justify-center gap-1.5">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
            <span>
              Real-world data workspace — the first registered user is automatically granted Admin privileges for live market syncs.
            </span>
          </p>
        </div>
      </div>
    </div>
  );
}
