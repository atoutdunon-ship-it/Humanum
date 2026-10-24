"use client";
import { useEffect, useState, type FormEvent } from "react";
import MfaSetup, { CodeField, RecoveryCodes } from "@/components/MfaSetup";
import Shell from "@/components/Shell";
import { APP_NAV, ME_NAV } from "@/lib/nav";
import { api, getToken, roleOf, setToken } from "@/lib/api";

type Status = { enabled: boolean; enabled_at: string | null; required: boolean; recovery_codes_remaining: number };

export default function Securite() {
  const [st, setSt] = useState<Status | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [code, setCode] = useState("");
  const [pwd, setPwd] = useState("");
  const [msg, setMsg] = useState<{ ok?: string; err?: string }>({});
  const [role, setRole] = useState<string | null>(null);
  useEffect(() => setRole(roleOf()), []);
  const load = () => api<Status>("/auth/mfa").then(setSt).catch(e => setMsg({ err: (e as Error).message }));
  useEffect(() => { if (!getToken()) { window.location.href = "/login"; return; } load(); }, []);

  const act = (fn: () => Promise<void>) => async (e: FormEvent) => {
    e.preventDefault(); setMsg({});
    try { await fn(); } catch (err) { setMsg({ err: (err as Error).message }); setCode(""); }
  };
  const regenerate = act(async () => {
    const r = await api<{ recovery_codes: string[] }>("/auth/mfa/recovery-codes", { method: "POST", body: JSON.stringify({ code }) });
    setCode(""); setCodes(r.recovery_codes);
  });
  const disable = act(async () => {
    await api("/auth/mfa/disable", { method: "POST", body: JSON.stringify({ password: pwd, code }) });
    setCode(""); setPwd(""); setMsg({ ok: "Double authentification désactivée" }); load();
  });

  return (
    <Shell label="Navigation principale" current="securite" items={role === "coachee" ? ME_NAV.map(i => ({ ...i, href: i.href ?? `/espace#${i.id}` })) : APP_NAV}>
      <h1>Sécurité du compte</h1>
      {st && !st.enabled && st.required && (
        <p className="banner" role="status">Votre rôle donne accès aux données des coachés : la double authentification est obligatoire avant de continuer.</p>
      )}
      <section className="card adm-section">
        <div className="adm-head"><h2>Double authentification</h2>
          {st && <span className={`pill${st.enabled ? " on" : ""}`}>{st.enabled ? "Active" : "Inactive"}</span>}</div>
        {st && !st.enabled && (
          <MfaSetup call={api} base="/auth/mfa" onDone={a => { if (a.access_token) setToken(a.access_token); setMsg({ ok: "Double authentification activée" }); load(); }} />
        )}
        {st?.enabled && !codes && (<>
          <p>Activée le {st.enabled_at ? new Date(st.enabled_at).toLocaleDateString("fr-FR") : "—"} · codes de secours restants : <strong>{st.recovery_codes_remaining}</strong></p>
          <form onSubmit={regenerate} className="mfa">
            <label htmlFor="c1">Code de l'application, pour générer 10 nouveaux codes de secours</label>
            <CodeField id="c1" value={code} onChange={setCode} />
            <p><button type="submit">Générer de nouveaux codes</button></p>
          </form>
          {!st.required && (
            <form onSubmit={disable} className="mfa">
              <h3>Désactiver</h3>
              <label htmlFor="pw">Mot de passe</label>
              <input id="pw" type="password" autoComplete="current-password" required value={pwd} onChange={e => setPwd(e.target.value)} />
              <label htmlFor="c2">Code de l'application ou code de secours</label>
              <CodeField id="c2" value={code} onChange={setCode} recovery />
              <p><button type="submit" className="ghost">Désactiver la double authentification</button></p>
            </form>
          )}
        </>)}
        {codes && <RecoveryCodes codes={codes} label="Terminé" onDone={() => { setCodes(null); load(); }} />}
        {msg.err && <p className="err" role="alert">{msg.err}</p>}
        {msg.ok && <p className="ok" role="status">{msg.ok}</p>}
      </section>
      {st?.enabled && <p><a href={role === "coachee" ? "/espace" : "/contacts"}>{role === "coachee" ? "Retour à mon espace" : "Accéder au CRM"}</a></p>}
    </Shell>
  );
}
