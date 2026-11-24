export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
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
