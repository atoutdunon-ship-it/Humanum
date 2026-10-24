"use client";
import { useEffect, useState, type FormEvent } from "react";
import MfaSetup, { CodeField } from "@/components/MfaSetup";
import { adminApi, getAdminToken, setAdminToken } from "@/lib/admin";

type Tok = { access_token?: string; must_change_password?: boolean; mfa_required?: boolean; mfa_token?: string; mfa_setup_required?: boolean };
type Step = "password" | "code" | "change" | "enroll";

export default function AdminLogin() {
  const [step, setStep] = useState<Step>("password");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const [mfaToken, setMfaToken] = useState("");
  const [np, setNp] = useState({ a: "", b: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {  // session restreinte déjà ouverte (onglet rechargé) : reprendre à la bonne étape
    if (!getAdminToken()) return;
    adminApi<{ must_change_password: boolean; mfa_enabled: boolean }>("/me").then(async me => {
      if (me.must_change_password) return;  // le mot de passe actuel doit être ressaisi : retour à l'étape 1
      const st = await adminApi<{ required: boolean; enabled: boolean }>("/auth/mfa");
      if (st.required && !st.enabled) setStep("enroll"); else window.location.href = "/admin";
    }).catch(() => {});
  }, []);

  function next(t: Tok) {
    if (t.mfa_required && t.mfa_token) { setMfaToken(t.mfa_token); setCode(""); setStep("code"); return; }
    if (t.access_token) setAdminToken(t.access_token);
    if (t.must_change_password) setStep("change");
    else if (t.mfa_setup_required) setStep("enroll");
    else window.location.href = "/admin";
  }
  const run = (fn: () => Promise<Tok | void>) => async (e: FormEvent) => {
    e.preventDefault(); setError(null); setBusy(true);
    try { const t = await fn(); if (t) next(t); } catch (err) { setError((err as Error).message); setCode(""); }
    setBusy(false);
  };
  const post = (path: string, body: object) => adminApi<Tok>(path, { method: "POST", body: JSON.stringify(body) });

  return (
    <div className="login">
      <div className="card">
        <img src="/logo-humanum.png" alt="HUMANUM CONSULTING" />
        {step === "password" && (
          <form onSubmit={run(() => post("/auth/login", { username, password }))}>
            <h1 className="center">Administration</h1>
            <label htmlFor="u">Identifiant</label>
            <input id="u" autoComplete="username" required value={username} onChange={e => setUsername(e.target.value)} />
            <label htmlFor="p">Mot de passe</label>
            <input id="p" type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} />
            <p><button type="submit" disabled={busy}>Se connecter</button></p>
          </form>
        )}
        {step === "code" && (
          <form onSubmit={run(() => post("/auth/login/mfa", { mfa_token: mfaToken, code }))}>
            <h1 className="center">Vérification</h1>
            <label htmlFor="code">{recovery ? "Code de secours" : "Code de l'application d'authentification"}</label>
            <CodeField id="code" value={code} onChange={setCode} recovery={recovery} />
            <p><button type="submit" disabled={busy}>Valider</button></p>
            <p><button type="button" className="link" onClick={() => { setRecovery(!recovery); setCode(""); }}>
              {recovery ? "Utiliser l'application d'authentification" : "Utiliser un code de secours"}</button></p>
          </form>
        )}
        {step === "change" && (
          <form onSubmit={run(async () => {
            if (np.a !== np.b) throw new Error("Les deux mots de passe ne correspondent pas");
            return post("/auth/change-password", { current_password: password, new_password: np.a });
          })}>
            <h1 className="center">Nouveau mot de passe</h1>
            <p>Première connexion : choisissez un mot de passe personnel d'au moins 12 caractères, avec des lettres et des chiffres.</p>
            <label htmlFor="n1">Nouveau mot de passe</label>
            <input id="n1" type="password" autoComplete="new-password" required value={np.a} onChange={e => setNp({ ...np, a: e.target.value })} />
            <label htmlFor="n2">Confirmation</label>
            <input id="n2" type="password" autoComplete="new-password" required value={np.b} onChange={e => setNp({ ...np, b: e.target.value })} />
            <p><button type="submit" disabled={busy}>Enregistrer et continuer</button></p>
          </form>
        )}
        {step === "enroll" && (<>
          <h1 className="center">Double authentification</h1>
          <p className="banner" role="status">Obligatoire pour la super-administration.</p>
          <MfaSetup call={adminApi} base="/auth/mfa" onDone={a => { if (a.access_token) setAdminToken(a.access_token); window.location.href = "/admin"; }} />
        </>)}
        {error && <p className="err" role="alert">{error}</p>}
      </div>
    </div>
  );
}
