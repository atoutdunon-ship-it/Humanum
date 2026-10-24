"use client";
import { useEffect, useState, type FormEvent } from "react";
import { CodeField } from "@/components/MfaSetup";
import { api, setToken, type Membership, type Token } from "@/lib/api";

type Step = "password" | "code" | "org";

export default function Login() {
  const [step, setStep] = useState<Step>("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const [mfaToken, setMfaToken] = useState("");
  const [selectToken, setSelectToken] = useState("");
  const [orgs, setOrgs] = useState<Membership[]>([]);
  const [org, setOrg] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [t, setT] = useState<Record<string, string>>({});
  useEffect(() => { fetch("/api/v1/public/texts?locale=fr").then(r => r.json()).then(setT).catch(() => {}); }, []);

  function next(r: Token) {
    if (r.mfa_required && r.mfa_token) { setMfaToken(r.mfa_token); setCode(""); setStep("code"); return; }
    if (!r.access_token && r.select_token) {
      setSelectToken(r.select_token); setOrgs(r.memberships); setOrg(r.memberships[0]?.organization_id ?? ""); setStep("org"); return;
    }
    if (r.access_token) { setToken(r.access_token); window.location.href = r.mfa_setup_required ? "/securite" : r.role === "coachee" ? "/espace" : "/contacts"; }
  }
  const run = (fn: () => Promise<Token>) => async (e: FormEvent) => {
    e.preventDefault(); setError(null); setBusy(true);
    try { next(await fn()); } catch (err) { setError((err as Error).message); if (step === "code") setCode(""); }
    setBusy(false);
  };
  const post = (path: string, body: object) => api<Token>(path, { method: "POST", body: JSON.stringify(body) });

  return (
    <div className="login">
      <div className="card">
        <img src="/logo-humanum.png" alt={t["brand.name"] ?? "HUMANUM CONSULTING"} />
        {step === "password" && (
          <form onSubmit={run(() => post("/auth/login", { email, password }))}>
            <h1 className="center">{t["login.title"] ?? "Connexion"}</h1>
            {t["login.subtitle"] && <p className="center muted">{t["login.subtitle"]}</p>}
            <label htmlFor="email">Adresse e-mail</label>
            <input id="email" type="email" autoComplete="username" required value={email} onChange={e => setEmail(e.target.value)} />
            <label htmlFor="pwd">Mot de passe</label>
            <input id="pwd" type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} />
            <p><button type="submit" disabled={busy}>{t["login.button"] ?? "Se connecter"}</button></p>
          </form>
        )}
        {step === "code" && (
          <form onSubmit={run(() => post("/auth/login/mfa", { mfa_token: mfaToken, code }))}>
            <h1 className="center">Vérification</h1>
            <p className="center muted">{recovery ? "Saisissez l'un de vos codes de secours." : "Saisissez le code à 6 chiffres de votre application d'authentification."}</p>
            <label htmlFor="code">{recovery ? "Code de secours" : "Code de vérification"}</label>
            <CodeField id="code" value={code} onChange={setCode} recovery={recovery} />
            <p><button type="submit" disabled={busy}>Valider</button></p>
            <p><button type="button" className="link" onClick={() => { setRecovery(!recovery); setCode(""); setError(null); }}>
              {recovery ? "Utiliser l'application d'authentification" : "Téléphone indisponible ? Utiliser un code de secours"}</button></p>
          </form>
        )}
        {step === "org" && (
          <form onSubmit={run(() => post("/auth/select-org", { select_token: selectToken, organization_id: org }))}>
            <h1 className="center">Choisir l'organisme</h1>
            <label htmlFor="org">Organisme</label>
            <select id="org" value={org} onChange={e => setOrg(e.target.value)}>
              {orgs.map(m => <option key={m.organization_id} value={m.organization_id}>{m.organization_name} — {m.role}</option>)}
            </select>
            <p><button type="submit" disabled={busy}>Entrer dans l'organisme</button></p>
          </form>
        )}
        {step !== "password" && <p><button type="button" className="link" onClick={() => { setStep("password"); setError(null); }}>Recommencer la connexion</button></p>}
        {error && <p className="err" role="alert">{error}</p>}
      </div>
    </div>
  );
}
