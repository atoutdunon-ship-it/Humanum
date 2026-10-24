/** Client de l'API d'administration : jeton distinct de celui des organismes, conservé pour l'onglet seulement. */
const KEY = "hc_admin_token";
export const getAdminToken = () => (typeof window === "undefined" ? null : sessionStorage.getItem(KEY));
export const setAdminToken = (t: string) => sessionStorage.setItem(KEY, t);
export const clearAdminToken = () => sessionStorage.removeItem(KEY);

export async function adminApi<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const t = getAdminToken();
  const res = await fetch(`/api/v1/admin${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}), ...init.headers },
  });
  if (res.status === 401 && !path.startsWith("/auth/")) { clearAdminToken(); window.location.href = "/admin/login"; }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = (body as { detail?: unknown }).detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x: { msg?: string }) => x.msg).join(" · ") : `Erreur ${res.status}`);
  }
  return body as T;
}

export const euros = (cents: number) => (cents / 100).toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const ROLES: Record<string, string> = { org_admin: "Administrateur", coach: "Coach", coordinator: "Coordinateur", coachee: "Coaché", funder: "Financeur" };
