export type Membership = { organization_id: string; organization_name: string; role: string };
export type Token = {
  access_token?: string; organization_id?: string; role?: string; memberships: Membership[];
  mfa_required?: boolean; mfa_token?: string; select_token?: string; mfa_setup_required?: boolean;
};

const KEY = "hc_token";
export const MFA_SETUP = "Activation de la double authentification obligatoire";
export const getToken = () => (typeof window === "undefined" ? null : sessionStorage.getItem(KEY));
export const setToken = (t: string) => sessionStorage.setItem(KEY, t);
export const clearToken = () => sessionStorage.removeItem(KEY);

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const t = getToken();
  const res = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}), ...init.headers },
  });
  const auth = path.startsWith("/auth/") || path.startsWith("/public/");
  if (res.status === 401 && !auth) { clearToken(); window.location.href = "/login"; }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  const detail = (body as { detail?: unknown }).detail;
  if (res.status === 403 && detail === MFA_SETUP && !auth) window.location.href = "/securite";
  if (!res.ok) throw new Error(typeof detail === "string" ? detail : `Erreur ${res.status}`);
  return body as T;
}

/** Rôle porté par le jeton (lecture seule, pour l'aiguillage de l'interface ; l'API reste seule juge). */
export function roleOf(): string | null {
  const t = getToken();
  if (!t) return null;
  try { return JSON.parse(atob(t.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).role ?? null; } catch { return null; }
}
