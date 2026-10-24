"use client";
import { useParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { api, setToken, type Token } from "@/lib/api";

type Inv = { organization: string; first_name: string; last_name: string; email: string; existing_account: boolean; expires_at: string };

export default function Invitation() {
  const { token } = useParams<{ token: string }>();
  const [inv, setInv] = useState<Inv | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [pwd, setPwd] = useState({ a: "", b: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    fetch(`/api/v1/public/invitations/${encodeURIComponent(token)}`).then(async r => {
      const b = await r.json(); if (!r.ok) throw new Error(b.detail ?? "Invitation introuvable"); setInv(b);
    }).catch(e => setFatal((e as Error).message));
  }, [token]);

  async function submit(e: FormEvent) {
    e.preventDefault(); setError(null);
    if (!inv!.existing_account && pwd.a !== pwd.b) { setError("Les deux mots de passe ne correspondent pas"); return; }
    setBusy(true);
    try {
      const r = await api<{ email: string; organization_id: string }>(`/public/invitations/${encodeURIComponent(token)}/accept`, { method: "POST", body: JSON.stringify({ password: pwd.a }) });
      const t = await api<Token>("/auth/login", { method: "POST", body: JSON.stringify({ email: r.email, password: pwd.a, organization_id: r.organization_id }) });
      if (t.access_token) { setToken(t.access_token); window.location.href = t.role === "coachee" ? "/espace" : "/contacts"; }
      else window.location.href = "/login";  // double authentification ou choix d'organisme : parcours de connexion habituel
    } catch (err) { setError((err as Error).message); }
    setBusy(false);
  }

  return (
    <div className="login">
      <div className="card">
        <img src="/logo-humanum.png" alt="HUMANUM CONSULTING" />
        {fatal && <><h1 className="center">Invitation indisponible</h1><p className="center">{fatal}</p></>}
        {inv && (
          <form onSubmit={submit}>
            <h1 className="center">Bienvenue {inv.first_name}</h1>
            <p className="center muted">{inv.organization} vous ouvre votre espace : rendez-vous, comptes rendus, actions et documents de votre parcours.</p>
            <label htmlFor="i-mail">Adresse e-mail</label>
            <input id="i-mail" value={inv.email} readOnly autoComplete="username" />
            <label htmlFor="i-p1">{inv.existing_account ? "Votre mot de passe actuel" : "Choisissez un mot de passe (12 caractères, lettres et chiffres)"}</label>
            <input id="i-p1" type="password" required autoComplete={inv.existing_account ? "current-password" : "new-password"} value={pwd.a} onChange={e => setPwd({ ...pwd, a: e.target.value })} />
            {!inv.existing_account && (<>
              <label htmlFor="i-p2">Confirmation</label>
              <input id="i-p2" type="password" required autoComplete="new-password" value={pwd.b} onChange={e => setPwd({ ...pwd, b: e.target.value })} />
            </>)}
            <p><button type="submit" disabled={busy}>{inv.existing_account ? "Rejoindre mon espace" : "Créer mon espace"}</button></p>
            {error && <p className="err" role="alert">{error}</p>}
          </form>
        )}
      </div>
    </div>
  );
}
