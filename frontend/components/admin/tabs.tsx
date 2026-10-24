"use client";
import { Fragment, useState } from "react";
import { BarList, Tile } from "@/components/Charts";
import MfaSetup, { CodeField, RecoveryCodes } from "@/components/MfaSetup";
import { adminApi, ROLES, setAdminToken } from "@/lib/admin";
import { Notice, Section, Toggle, useAction, useLoad } from "./ui";

type Plan = { code: string; label: string; monthly_price_cents: number; currency: string; max_coaches: number | null;
  max_active_coachees: number | null; ai_syntheses_per_month: number | null; exports: string[]; trial_days: number;
  is_public: boolean; sort_order: number; stripe_synced?: boolean };
type Org = { id: string; name: string; slug: string; plan: string; is_active: boolean; ai_enabled: boolean; members: number; billing_status?: string };
const BILLING: Record<string, string> = { none: "Hors Stripe", trialing: "Essai", active: "Payé", past_due: "Impayé", canceled: "Résilié", unpaid: "Suspendu", incomplete: "En attente", incomplete_expired: "Expiré" };
type Flag = { key: string; label: string; description: string | null; enabled_by_default: boolean };
type OrgFlag = Flag & { override: boolean | null; effective: boolean };
type U = { id: string; email: string; full_name: string; is_active: boolean; mfa_enabled: boolean;
  memberships: { organization_id: string; organization_name: string; role: string }[] };
type Txt = { key: string; locale: string; value: string; description: string | null; updated_at: string };
type Adm = { id: string; username: string; is_active: boolean; must_change_password: boolean; last_login_at: string | null; mfa_enabled: boolean };
type Log = { at: string; admin: string | null; action: string; target: string | null; ip: string | null; details: Record<string, unknown> };

const num = (v: string) => (v.trim() === "" ? null : Number(v));
const body = (o: unknown) => JSON.stringify(o);

/* ------------------------------------------------------------------ Organismes */
export function OrgsTab() {
  const orgs = useLoad(() => adminApi<Org[]>("/organizations"));
  const plans = useLoad(() => adminApi<Plan[]>("/plans"));
  const a = useAction();
  const [open, setOpen] = useState<string | null>(null);
  const [n, setN] = useState({ name: "", slug: "", plan: "starter", admin_email: "", admin_full_name: "", admin_password: "" });
  const save = (o: Org) => a.run(() => adminApi(`/organizations/${o.id}`, { method: "PATCH",
    body: body({ name: o.name, plan: o.plan, is_active: o.is_active, ai_enabled: o.ai_enabled }) }).then(orgs.reload), `« ${o.name} » enregistré`);
  const edit = (id: string, patch: Partial<Org>) => orgs.setData(d => d?.map(o => (o.id === id ? { ...o, ...patch } : o)) ?? d);
  return (<>
    <Section title="Organismes">
      <Notice error={a.error ?? orgs.error} ok={a.ok} />
      <div className="adm-scroll"><table>
        <thead><tr><th>Nom</th><th>Identifiant</th><th>Formule</th><th>Abonnement</th><th>Actif</th><th>IA</th><th>Membres</th><th></th></tr></thead>
        <tbody>{orgs.data?.map(o => (<Fragment key={o.id}>
          <tr>
            <td><input aria-label="Nom" value={o.name} onChange={e => edit(o.id, { name: e.target.value })} /></td>
            <td>{o.slug}</td>
            <td><select aria-label="Formule" value={o.plan} onChange={e => edit(o.id, { plan: e.target.value })}>
              {plans.data?.map(p => <option key={p.code} value={p.code}>{p.label}</option>)}</select></td>
            <td>{BILLING[o.billing_status ?? "none"] ?? o.billing_status}</td>
            <td><Toggle label="" checked={o.is_active} onChange={v => edit(o.id, { is_active: v })} /></td>
            <td><Toggle label="" checked={o.ai_enabled} onChange={v => edit(o.id, { ai_enabled: v })} /></td>
            <td>{o.members}</td>
            <td className="adm-actions"><button onClick={() => save(o)}>Enregistrer</button>
              <button className="ghost" onClick={() => setOpen(open === o.id ? null : o.id)}>Options</button></td>
          </tr>
          {open === o.id && <tr><td colSpan={7}><OrgFeatures orgId={o.id} /></td></tr>}
        </Fragment>))}</tbody>
      </table></div>
    </Section>
    <Section title="Créer un organisme">
      <div className="adm-grid">
        {([["name", "Nom"], ["slug", "Identifiant (minuscules, tirets)"], ["admin_full_name", "Administrateur : nom complet"],
          ["admin_email", "Administrateur : e-mail"], ["admin_password", "Mot de passe initial (12 caractères min.)"]] as const).map(([k, l]) => (
          <div key={k}><label htmlFor={`n-${k}`}>{l}</label>
            <input id={`n-${k}`} type={k === "admin_password" ? "password" : "text"} value={n[k]} onChange={e => setN({ ...n, [k]: e.target.value })} /></div>))}
        <div><label htmlFor="n-plan">Formule</label>
          <select id="n-plan" value={n.plan} onChange={e => setN({ ...n, plan: e.target.value })}>
            {plans.data?.map(p => <option key={p.code} value={p.code}>{p.label}</option>)}</select></div>
      </div>
      <p><button onClick={() => a.run(() => adminApi("/organizations", { method: "POST", body: body(n) }).then(orgs.reload), "Organisme créé")}>Créer</button></p>
    </Section>
  </>);
}

function OrgFeatures({ orgId }: { orgId: string }) {
  const f = useLoad(() => adminApi<OrgFlag[]>(`/organizations/${orgId}/features`), [orgId]);
  const a = useAction();
  const set = (key: string, v: string) => a.run(() => adminApi(`/organizations/${orgId}/features/${key}`, { method: "PUT",
    body: body({ enabled: v === "default" ? null : v === "on" }) }).then(f.reload), "Option mise à jour");
  return (<div className="adm-sub">
    <Notice error={a.error ?? f.error} ok={a.ok} />
    <table><thead><tr><th>Option</th><th>Par défaut</th><th>Pour cet organisme</th><th>Effectif</th></tr></thead>
      <tbody>{f.data?.map(x => (<tr key={x.key}><td>{x.label}</td><td>{x.enabled_by_default ? "Activée" : "Désactivée"}</td>
        <td><select aria-label={`Réglage ${x.label}`} value={x.override === null ? "default" : x.override ? "on" : "off"} onChange={e => set(x.key, e.target.value)}>
          <option value="default">Valeur par défaut</option><option value="on">Forcer : activée</option><option value="off">Forcer : désactivée</option></select></td>
        <td><strong>{x.effective ? "Activée" : "Désactivée"}</strong></td></tr>))}</tbody></table>
  </div>);
}

/* ------------------------------------------------------------------ Accès */
export function AccessTab() {
  const [q, setQ] = useState("");
  const users = useLoad(() => adminApi<U[]>(`/users${q ? `?q=${encodeURIComponent(q)}` : ""}`), [q]);
  const orgs = useLoad(() => adminApi<Org[]>("/organizations"));
  const a = useAction();
  const [pwd, setPwd] = useState<Record<string, string>>({});
  const [add, setAdd] = useState<Record<string, { org: string; role: string }>>({});
  return (<Section title="Comptes et accès" actions={<input className="adm-search" placeholder="Rechercher un nom ou un e-mail" aria-label="Rechercher" value={q} onChange={e => setQ(e.target.value)} />}>
    <Notice error={a.error ?? users.error} ok={a.ok} />
    {users.data?.map(u => (
      <div key={u.id} className="adm-user">
        <div className="adm-user-head">
          <div><strong>{u.full_name}</strong><br /><span className="muted">{u.email}</span><br />
            <span className={`pill${u.mfa_enabled ? " on" : ""}`}>{u.mfa_enabled ? "Double authentification active" : "Sans double authentification"}</span></div>
          <Toggle label={u.is_active ? "Compte actif" : "Compte suspendu"} checked={u.is_active}
            onChange={v => a.run(() => adminApi(`/users/${u.id}`, { method: "PATCH", body: body({ is_active: v }) }).then(users.reload), v ? "Compte réactivé" : "Compte suspendu")} />
        </div>
        <table><thead><tr><th>Organisme</th><th>Rôle</th><th></th></tr></thead><tbody>
          {u.memberships.map(m => (<tr key={m.organization_id}><td>{m.organization_name}</td>
            <td><select aria-label="Rôle" value={m.role} onChange={e => a.run(() => adminApi(`/organizations/${m.organization_id}/members/${u.id}`,
              { method: "PUT", body: body({ role: e.target.value }) }).then(users.reload), "Rôle modifié")}>
              {Object.entries(ROLES).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
            <td><button className="ghost" onClick={() => a.run(() => adminApi(`/organizations/${m.organization_id}/members/${u.id}`, { method: "DELETE" }).then(users.reload), "Accès retiré")}>Retirer l'accès</button></td></tr>))}
          <tr><td><select aria-label="Ajouter à un organisme" value={add[u.id]?.org ?? ""} onChange={e => setAdd({ ...add, [u.id]: { org: e.target.value, role: add[u.id]?.role ?? "coach" } })}>
              <option value="">Ajouter à un organisme…</option>{orgs.data?.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}</select></td>
            <td><select aria-label="Rôle à attribuer" value={add[u.id]?.role ?? "coach"} onChange={e => setAdd({ ...add, [u.id]: { org: add[u.id]?.org ?? "", role: e.target.value } })}>
              {Object.entries(ROLES).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
            <td><button disabled={!add[u.id]?.org} onClick={() => a.run(() => adminApi(`/organizations/${add[u.id].org}/members/${u.id}`,
              { method: "PUT", body: body({ role: add[u.id].role }) }).then(users.reload), "Accès ajouté")}>Ajouter</button></td></tr>
        </tbody></table>
        <div className="adm-inline">
          <input type="password" aria-label="Nouveau mot de passe" placeholder="Nouveau mot de passe (12 caractères min.)" value={pwd[u.id] ?? ""} onChange={e => setPwd({ ...pwd, [u.id]: e.target.value })} />
          <button className="ghost" onClick={() => a.run(() => adminApi(`/users/${u.id}/reset-password`, { method: "POST", body: body({ new_password: pwd[u.id] ?? "" }) }), "Mot de passe réinitialisé")}>Réinitialiser le mot de passe</button>
          {u.mfa_enabled && <button className="ghost" onClick={() => a.run(() => adminApi(`/users/${u.id}/reset-mfa`, { method: "POST" }).then(users.reload),
            "Double authentification réinitialisée : nouvel enrôlement à la prochaine connexion")}>Réinitialiser la double authentification</button>}
        </div>
      </div>))}
  </Section>);
}

/* ------------------------------------------------------------------ Tarifs */
const EMPTY_PLAN: Plan = { code: "", label: "", monthly_price_cents: 0, currency: "EUR", max_coaches: null, max_active_coachees: null,
  ai_syntheses_per_month: null, exports: ["pdf"], trial_days: 0, is_public: true, sort_order: 9 };

export function PlansTab() {
  const plans = useLoad(() => adminApi<Plan[]>("/plans"));
  const a = useAction();
  const [n, setN] = useState<Plan>(EMPTY_PLAN);
  const edit = (code: string, p: Partial<Plan>) => plans.setData(d => d?.map(x => (x.code === code ? { ...x, ...p } : x)) ?? d);
  const row = (p: Plan, onChange: (x: Partial<Plan>) => void, isNew = false) => (<>
    <td>{isNew ? <input aria-label="Code" value={p.code} onChange={e => onChange({ code: e.target.value })} /> : <code>{p.code}</code>}</td>
    <td><input aria-label="Libellé" className="w-label" value={p.label} onChange={e => onChange({ label: e.target.value })} /></td>
    <td><input aria-label="Prix mensuel en euros" type="number" min={0} step={0.01} className="w-price" value={p.monthly_price_cents / 100}
      onChange={e => onChange({ monthly_price_cents: Math.round(Number(e.target.value) * 100) || 0 })} /></td>
    {(["max_coaches", "max_active_coachees", "ai_syntheses_per_month"] as const).map(k => (
      <td key={k}><input aria-label={k} inputMode="numeric" placeholder="Illimité" value={p[k] ?? ""} onChange={e => onChange({ [k]: num(e.target.value) })} /></td>))}
    <td><input aria-label="Exports" className="w-exports" value={p.exports.join(", ")} onChange={e => onChange({ exports: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })} /></td>
    <td><input aria-label="Jours d'essai" inputMode="numeric" value={p.trial_days} onChange={e => onChange({ trial_days: Number(e.target.value) || 0 })} /></td>
    <td><Toggle label="" checked={p.is_public} onChange={v => onChange({ is_public: v })} /></td>
  </>);
  return (<Section title="Tarifs et quotas" actions={<button onClick={() => a.run(() => adminApi("/billing/sync-plans", { method: "POST" }).then(plans.reload),
      "Formules publiées dans Stripe")}>Publier dans Stripe</button>}>
    <Notice error={a.error ?? plans.error} ok={a.ok} />
    <p className="muted">Prix mensuel hors taxes par organisme, en euros. Un quota vide signifie « illimité ». Après un changement de prix, publiez dans Stripe :
      un nouveau prix est créé pour les nouveaux abonnements, les abonnements en cours gardent le leur jusqu'à leur prochain changement de formule.</p>
    <div className="adm-scroll"><table>
      <thead><tr><th>Code</th><th>Libellé</th><th>Prix / mois (€)</th><th>Coachs max.</th><th>Coachés actifs</th><th>Synthèses IA / mois</th><th>Exports</th><th>Essai (j)</th><th>Public</th><th>Stripe</th><th></th></tr></thead>
      <tbody>
        {plans.data?.map(p => (<tr key={p.code}>{row(p, x => edit(p.code, x))}
          <td>{p.stripe_synced ? "Publié" : "À publier"}</td>
          <td className="adm-actions"><button onClick={() => { const { code, ...rest } = p; a.run(() => adminApi(`/plans/${code}`, { method: "PATCH", body: body(rest) }).then(plans.reload), `Formule « ${p.label} » enregistrée`); }}>Enregistrer</button>
            <button className="ghost" onClick={() => a.run(() => adminApi(`/plans/${p.code}`, { method: "DELETE" }).then(plans.reload), "Formule supprimée")}>Supprimer</button></td></tr>))}
        <tr className="adm-new">{row(n, x => setN({ ...n, ...x }), true)}
          <td>—</td>
          <td><button onClick={() => a.run(() => adminApi("/plans", { method: "POST", body: body(n) }).then(() => { setN(EMPTY_PLAN); plans.reload(); }), "Formule créée")}>Ajouter</button></td></tr>
      </tbody></table></div>
  </Section>);
}

/* ------------------------------------------------------------------ Options */
export function FeaturesTab() {
  const f = useLoad(() => adminApi<Flag[]>("/features"));
  const a = useAction();
  const [n, setN] = useState<Flag>({ key: "", label: "", description: "", enabled_by_default: false });
  const edit = (key: string, p: Partial<Flag>) => f.setData(d => d?.map(x => (x.key === key ? { ...x, ...p } : x)) ?? d);
  return (<Section title="Options de la plateforme">
    <Notice error={a.error ?? f.error} ok={a.ok} />
    <p className="muted">Valeur par défaut pour tous les organismes ; chaque organisme peut être réglé à part depuis l'onglet Organismes.</p>
    <table><thead><tr><th>Clé</th><th>Libellé</th><th>Description</th><th>Activée par défaut</th><th></th></tr></thead><tbody>
      {f.data?.map(x => (<tr key={x.key}><td><code>{x.key}</code></td>
        <td><input aria-label="Libellé" value={x.label} onChange={e => edit(x.key, { label: e.target.value })} /></td>
        <td><input aria-label="Description" value={x.description ?? ""} onChange={e => edit(x.key, { description: e.target.value })} /></td>
        <td><Toggle label="" checked={x.enabled_by_default} onChange={v => edit(x.key, { enabled_by_default: v })} /></td>
        <td className="adm-actions"><button onClick={() => a.run(() => adminApi(`/features/${x.key}`, { method: "PATCH",
          body: body({ label: x.label, description: x.description, enabled_by_default: x.enabled_by_default }) }).then(f.reload), "Option enregistrée")}>Enregistrer</button>
          <button className="ghost" onClick={() => a.run(() => adminApi(`/features/${x.key}`, { method: "DELETE" }).then(f.reload), "Option supprimée")}>Supprimer</button></td></tr>))}
      <tr className="adm-new"><td><input aria-label="Nouvelle clé" placeholder="cle_option" value={n.key} onChange={e => setN({ ...n, key: e.target.value })} /></td>
        <td><input aria-label="Libellé" value={n.label} onChange={e => setN({ ...n, label: e.target.value })} /></td>
        <td><input aria-label="Description" value={n.description ?? ""} onChange={e => setN({ ...n, description: e.target.value })} /></td>
        <td><Toggle label="" checked={n.enabled_by_default} onChange={v => setN({ ...n, enabled_by_default: v })} /></td>
        <td><button onClick={() => a.run(() => adminApi("/features", { method: "POST", body: body(n) }).then(f.reload), "Option créée")}>Ajouter</button></td></tr>
    </tbody></table>
  </Section>);
}

/* ------------------------------------------------------------------ Textes */
export function TextsTab() {
  const [locale, setLocale] = useState("fr");
  const [q, setQ] = useState("");
  const t = useLoad(() => adminApi<Txt[]>(`/texts?locale=${locale}`), [locale]);
  const a = useAction();
  const [n, setN] = useState({ key: "", value: "", description: "" });
  const edit = (key: string, value: string) => t.setData(d => d?.map(x => (x.key === key ? { ...x, value } : x)) ?? d);
  const rows = t.data?.filter(x => !q || x.key.includes(q) || x.value.toLowerCase().includes(q.toLowerCase()));
  return (<Section title="Textes de la plateforme" actions={<div className="adm-inline">
      <select aria-label="Langue" value={locale} onChange={e => setLocale(e.target.value)}><option value="fr">Français</option><option value="en">English</option><option value="es">Español</option></select>
      <input className="adm-search" placeholder="Filtrer" aria-label="Filtrer les textes" value={q} onChange={e => setQ(e.target.value)} /></div>}>
    <Notice error={a.error ?? t.error} ok={a.ok} />
    {rows?.map(x => (<div key={x.key} className="adm-text">
      <label htmlFor={`t-${x.key}`}><code>{x.key}</code>{x.description && <span className="muted"> — {x.description}</span>}</label>
      <textarea id={`t-${x.key}`} rows={Math.min(8, Math.max(2, Math.ceil(x.value.length / 90)))} value={x.value} onChange={e => edit(x.key, e.target.value)} />
      <div className="adm-actions"><button onClick={() => a.run(() => adminApi("/texts", { method: "PUT", body: body({ key: x.key, locale, value: x.value }) }), `Texte « ${x.key} » publié`)}>Publier</button>
        <button className="ghost" onClick={() => a.run(() => adminApi(`/texts/${locale}/${x.key}`, { method: "DELETE" }).then(t.reload), "Texte supprimé")}>Supprimer</button></div>
    </div>))}
    <div className="adm-text adm-new">
      <label htmlFor="t-new-key">Nouveau texte</label>
      <input id="t-new-key" placeholder="cle.du.texte" value={n.key} onChange={e => setN({ ...n, key: e.target.value })} />
      <input aria-label="Description" placeholder="Où ce texte apparaît" value={n.description} onChange={e => setN({ ...n, description: e.target.value })} />
      <textarea aria-label="Contenu" rows={3} value={n.value} onChange={e => setN({ ...n, value: e.target.value })} />
      <button onClick={() => a.run(() => adminApi("/texts", { method: "PUT", body: body({ ...n, locale }) }).then(() => { setN({ key: "", value: "", description: "" }); t.reload(); }), "Texte ajouté")}>Ajouter</button>
    </div>
  </Section>);
}

/* ------------------------------------------------------------------ Administrateurs */
export function AdminsTab() {
  const l = useLoad(() => adminApi<Adm[]>("/admins"));
  const a = useAction();
  const [n, setN] = useState({ username: "", initial_password: "" });
  return (<Section title="Super-administrateurs">
    <Notice error={a.error ?? l.error} ok={a.ok} />
    <table><thead><tr><th>Identifiant</th><th>Statut</th><th>Double authentification</th><th>Dernière connexion</th><th></th></tr></thead><tbody>
      {l.data?.map(x => (<tr key={x.id}><td><strong>{x.username}</strong>{x.must_change_password && <span className="muted"> — mot de passe initial</span>}</td>
        <td>{x.is_active ? "Actif" : "Désactivé"}</td><td>{x.mfa_enabled ? "Active" : "Non configurée"}</td>
        <td>{x.last_login_at ? new Date(x.last_login_at).toLocaleString("fr-FR") : "—"}</td>
        <td><div className="adm-actions">
          <button className="ghost" onClick={() => a.run(() => adminApi(`/admins/${x.id}?is_active=${!x.is_active}`, { method: "PATCH" }).then(l.reload), "Statut modifié")}>{x.is_active ? "Désactiver" : "Réactiver"}</button>
          {x.mfa_enabled && <button className="ghost" onClick={() => a.run(() => adminApi(`/admins/${x.id}/reset-mfa`, { method: "POST" }).then(l.reload), "Double authentification réinitialisée")}>Réinitialiser la 2FA</button>}
        </div></td></tr>))}
    </tbody></table>
    <div className="adm-inline">
      <input aria-label="Identifiant" placeholder="Identifiant" value={n.username} onChange={e => setN({ ...n, username: e.target.value })} />
      <input aria-label="Mot de passe initial" type="password" placeholder="Mot de passe initial (12 caractères min.)" value={n.initial_password} onChange={e => setN({ ...n, initial_password: e.target.value })} />
      <button onClick={() => a.run(() => adminApi("/admins", { method: "POST", body: body(n) }).then(l.reload), "Compte créé : changement du mot de passe à la première connexion")}>Créer</button>
    </div>
  </Section>);
}

/* ------------------------------------------------------------------ Ma sécurité */
export function SecurityTab() {
  const st = useLoad(() => adminApi<{ enabled: boolean; enabled_at: string | null; required: boolean; recovery_codes_remaining: number }>("/auth/mfa"));
  const a = useAction();
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  return (<Section title="Double authentification">
    <Notice error={a.error ?? st.error} ok={a.ok} />
    {st.data && !st.data.enabled && <MfaSetup call={adminApi} base="/auth/mfa" onDone={x => { if (x.access_token) setAdminToken(x.access_token); st.reload(); }} />}
    {st.data?.enabled && !codes && (<>
      <p>Activée le {st.data.enabled_at ? new Date(st.data.enabled_at).toLocaleDateString("fr-FR") : "—"} · codes de secours restants : <strong>{st.data.recovery_codes_remaining}</strong></p>
      <label htmlFor="sec-code">Code de l'application, pour générer 10 nouveaux codes de secours</label>
      <CodeField id="sec-code" value={code} onChange={setCode} />
      <p><button onClick={() => a.run(async () => {
        const r = await adminApi<{ recovery_codes: string[] }>("/auth/mfa/recovery-codes", { method: "POST", body: body({ code }) });
        setCode(""); setCodes(r.recovery_codes);
      }, "Nouveaux codes générés : les anciens ne fonctionnent plus")}>Générer de nouveaux codes</button></p>
    </>)}
    {codes && <RecoveryCodes codes={codes} label="Terminé" onDone={() => { setCodes(null); st.reload(); }} />}
  </Section>);
}

/* ------------------------------------------------------------------ Journal */
export function AuditTab() {
  const l = useLoad(() => adminApi<Log[]>("/audit?limit=300"));
  return (<Section title="Journal des actions">
    <Notice error={l.error} />
    <div className="adm-scroll"><table><thead><tr><th>Date</th><th>Administrateur</th><th>Action</th><th>Cible</th><th>Adresse IP</th></tr></thead><tbody>
      {l.data?.map((x, i) => (<tr key={i}><td>{new Date(x.at).toLocaleString("fr-FR")}</td><td>{x.admin ?? "—"}</td><td><code>{x.action}</code></td><td>{x.target ?? "—"}</td><td>{x.ip ?? "—"}</td></tr>))}
    </tbody></table></div>
  </Section>);
}

/* ------------------------------------------------------------------ Promotions et TVA */
type Promo = { id: string; code: string; label: string; description: string; plans: string[]; max_redemptions: number | null;
  expires_at: string | null; first_time_only: boolean; is_active: boolean; times_redeemed: number; usable: boolean;
  percent_off: number | null; amount_off_cents: number | null; duration: string; duration_in_months: number | null; revision: number };
type Terms = typeof EMPTY_PROMO;
const termsOf = (p: Promo): Terms => ({ code: p.code, label: p.label, kind: p.percent_off != null ? "percent" : "amount",
  value: p.percent_off != null ? String(p.percent_off) : String((p.amount_off_cents ?? 0) / 100), duration: p.duration,
  months: String(p.duration_in_months ?? 3), plans: p.plans, max: p.max_redemptions ? String(p.max_redemptions) : "",
  expires: p.expires_at ? p.expires_at.slice(0, 10) : "", first: p.first_time_only });
const termsBody = (t: Terms) => {
  const v = Number(t.value.replace(",", "."));
  return { duration: t.duration, duration_in_months: t.duration === "repeating" ? Number(t.months) : null,
    percent_off: t.kind === "percent" ? v : null, amount_off_cents: t.kind === "amount" ? Math.round(v * 100) : null,
    plans: t.plans, max_redemptions: t.max ? Number(t.max) : null, first_time_only: t.first,
    expires_at: t.expires ? new Date(`${t.expires}T23:59:00`).toISOString() : null };
};

function TermsFields({ id, t, set, plans }: { id: string; t: Terms; set: (t: Terms) => void; plans: Plan[] }) {
  return (<>
    <div className="adm-grid">
      <div><label htmlFor={`${id}-kind`}>Type de remise</label>
        <select id={`${id}-kind`} value={t.kind} onChange={e => set({ ...t, kind: e.target.value })}><option value="percent">Pourcentage</option><option value="amount">Montant en euros</option></select></div>
      <div><label htmlFor={`${id}-val`}>{t.kind === "percent" ? "Pourcentage" : "Montant (€)"}</label><input id={`${id}-val`} inputMode="decimal" value={t.value} onChange={e => set({ ...t, value: e.target.value })} /></div>
      <div><label htmlFor={`${id}-dur`}>Durée</label>
        <select id={`${id}-dur`} value={t.duration} onChange={e => set({ ...t, duration: e.target.value })}>
          <option value="once">Première facture</option><option value="repeating">Plusieurs mois</option><option value="forever">Sans limite</option></select></div>
      {t.duration === "repeating" && <div><label htmlFor={`${id}-m`}>Nombre de mois</label><input id={`${id}-m`} inputMode="numeric" value={t.months} onChange={e => set({ ...t, months: e.target.value })} /></div>}
      <div><label htmlFor={`${id}-max`}>Utilisations maximales (vide : illimité)</label><input id={`${id}-max`} inputMode="numeric" value={t.max} onChange={e => set({ ...t, max: e.target.value })} /></div>
      <div><label htmlFor={`${id}-exp`}>Date de fin (facultatif)</label><input id={`${id}-exp`} type="date" value={t.expires} onChange={e => set({ ...t, expires: e.target.value })} /></div>
    </div>
    <fieldset className="plans-pick"><legend>Formules concernées (aucune cochée : toutes)</legend>
      {plans.map(p => <Toggle key={p.code} label={p.label} checked={t.plans.includes(p.code)}
        onChange={v => set({ ...t, plans: v ? [...t.plans, p.code] : t.plans.filter(c => c !== p.code) })} />)}
      <Toggle label="Réservé aux nouveaux clients" checked={t.first} onChange={v => set({ ...t, first: v })} />
    </fieldset>
  </>);
}
type Vat = { enabled: boolean; rate: number; label: string; jurisdiction: string };
const EMPTY_PROMO = { code: "", label: "", kind: "percent", value: "", duration: "once", months: "3", plans: [] as string[], max: "", expires: "", first: false };

export function PromotionsTab() {
  const vat = useLoad(() => adminApi<Vat>("/billing/vat"));
  const promos = useLoad(() => adminApi<Promo[]>("/promotions"));
  const plans = useLoad(() => adminApi<Plan[]>("/plans"));
  const orgs = useLoad(() => adminApi<Org[]>("/organizations"));
  const a = useAction();
  const av = useAction();  // messages de la TVA, distincts de ceux des promotions
  const rate = (r?: number) => (r ?? 8.5).toLocaleString("fr-FR");
  const [n, setN] = useState(EMPTY_PROMO);
  const [apply, setApply] = useState<Record<string, string>>({});
  const [edit, setEdit] = useState<Record<string, Terms>>({});
  const create = () => a.run(async () => {
    const v = Number(n.value.replace(",", "."));
    await adminApi("/promotions", { method: "POST", body: body({
      code: n.code.trim().toUpperCase(), label: n.label, duration: n.duration,
      duration_in_months: n.duration === "repeating" ? Number(n.months) : null,
      percent_off: n.kind === "percent" ? v : null, amount_off_cents: n.kind === "amount" ? Math.round(v * 100) : null,
      plans: n.plans, max_redemptions: n.max ? Number(n.max) : null, first_time_only: n.first,
      expires_at: n.expires ? new Date(`${n.expires}T23:59:00`).toISOString() : null }) });
    setN(EMPTY_PROMO); promos.reload();
  }, "Promotion créée et publiée dans Stripe");
  return (<>
    <Section title="TVA" actions={vat.data && <span className={`pill${vat.data.enabled ? " on" : ""}`}>{vat.data.enabled ? `${vat.data.label} ${rate(vat.data.rate)} % activée` : "Hors TVA"}</span>}>
      <Notice error={av.error ?? vat.error} ok={av.ok} />
      <p className="muted">Les tarifs sont exprimés hors taxes. Activée, la TVA de {rate(vat.data?.rate)} % ({vat.data?.jurisdiction ?? "Martinique"}) s'ajoute aux
        nouveaux abonnements ; les abonnements en cours ne changent qu'après « Appliquer aux abonnements en cours ».</p>
      {vat.data && <div className="mfa-actions">
        <Toggle label={`Appliquer la ${vat.data.label} à ${rate(vat.data.rate)} %`} checked={vat.data.enabled}
          onChange={v => av.run(() => adminApi("/billing/vat", { method: "PUT", body: body({ enabled: v }) }).then(vat.reload), v ? "TVA activée pour les nouveaux abonnements" : "TVA désactivée pour les nouveaux abonnements")} />
        <button className="ghost" onClick={() => av.run(async () => {
          const r = await adminApi<{ subscriptions: number; failed: string[] }>("/billing/vat/apply-existing", { method: "POST" });
          if (r.failed.length) throw new Error(`${r.subscriptions} abonnement(s) mis à jour ; échec pour : ${r.failed.join(", ")}`);
        }, "Abonnements en cours alignés sur le réglage")}>Appliquer aux abonnements en cours</button>
      </div>}
    </Section>
    <Section title="Promotions" actions={<button className="ghost" onClick={() => a.run(() => adminApi("/promotions/refresh", { method: "POST" }).then(promos.reload), "Utilisations relues dans Stripe")}>Actualiser les utilisations</button>}>
      <Notice error={a.error ?? promos.error} ok={a.ok} />
      {promos.data?.length === 0 && <p className="muted">Aucune promotion.</p>}
      {promos.data?.map(p => (
        <div key={p.id} className="adm-user">
          <div className="adm-user-head">
            <div><code>{p.code}</code> — <strong>{p.label}</strong><br /><span className="muted">{p.description}
              {p.plans.length ? ` · formules : ${p.plans.join(", ")}` : " · toutes formules"}
              {p.max_redemptions ? ` · ${p.times_redeemed} / ${p.max_redemptions} utilisations` : ` · ${p.times_redeemed} utilisation(s)`}
              {p.expires_at ? ` · expire le ${new Date(p.expires_at).toLocaleDateString("fr-FR")}` : ""}{p.first_time_only ? " · nouveaux clients" : ""}
              {p.revision > 1 ? ` · version ${p.revision}` : ""}</span></div>
            <Toggle label={p.is_active ? "Active" : "Inactive"} checked={p.is_active}
              onChange={v => a.run(() => adminApi(`/promotions/${p.id}`, { method: "PATCH", body: body({ is_active: v }) }).then(promos.reload), v ? "Promotion réactivée" : "Promotion désactivée")} />
          </div>
          <div className="adm-inline">
            <select aria-label="Appliquer à un organisme" value={apply[p.id] ?? ""} onChange={e => setApply({ ...apply, [p.id]: e.target.value })}>
              <option value="">Geste commercial : appliquer à un organisme abonné…</option>
              {orgs.data?.filter(o => o.billing_status && o.billing_status !== "none").map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
            <button disabled={!apply[p.id] || !p.usable} onClick={() => a.run(() => adminApi(`/organizations/${apply[p.id]}/discount`, { method: "POST", body: body({ promotion_id: p.id }) }),
              "Remise appliquée à l'abonnement")}>Appliquer</button>
            <button className="ghost" aria-expanded={!!edit[p.id]} onClick={() => setEdit(e => { const n2 = { ...e }; if (n2[p.id]) delete n2[p.id]; else n2[p.id] = termsOf(p); return n2; })}>
              {edit[p.id] ? "Fermer" : "Ajuster les conditions"}</button>
          </div>
          {edit[p.id] && (<div className="adm-sub">
            <TermsFields id={`ed-${p.id}`} t={edit[p.id]} set={t => setEdit({ ...edit, [p.id]: t })} plans={plans.data ?? []} />
            <p className="muted">Le code {p.code} reste le même pour vos clients. Les nouvelles conditions valent pour les prochaines souscriptions ;
              les abonnements déjà remisés conservent leur remise. Les utilisations passées restent comptées dans le plafond.</p>
            <p><button onClick={() => a.run(async () => {
              await adminApi(`/promotions/${p.id}/terms`, { method: "PUT", body: body(termsBody(edit[p.id])) });
              setEdit(e => { const n2 = { ...e }; delete n2[p.id]; return n2; }); promos.reload();
            }, `Conditions de ${p.code} ajustées et publiées dans Stripe`)}>Publier les nouvelles conditions</button></p>
          </div>)}
        </div>
      ))}
      <h3 className="sub-title">Nouvelle promotion</h3>
      <div className="adm-grid">
        <div><label htmlFor="pr-code">Code saisi par le client</label><input id="pr-code" value={n.code} placeholder="LANCEMENT2026" onChange={e => setN({ ...n, code: e.target.value.toUpperCase().replace(/[^A-Z0-9_-]/g, "") })} /></div>
        <div><label htmlFor="pr-label">Libellé</label><input id="pr-label" value={n.label} placeholder="Offre de lancement" onChange={e => setN({ ...n, label: e.target.value })} /></div>
        <div><label htmlFor="pr-kind">Type de remise</label>
          <select id="pr-kind" value={n.kind} onChange={e => setN({ ...n, kind: e.target.value })}><option value="percent">Pourcentage</option><option value="amount">Montant en euros</option></select></div>
        <div><label htmlFor="pr-val">{n.kind === "percent" ? "Pourcentage" : "Montant (€)"}</label><input id="pr-val" inputMode="decimal" value={n.value} onChange={e => setN({ ...n, value: e.target.value })} /></div>
        <div><label htmlFor="pr-dur">Durée</label>
          <select id="pr-dur" value={n.duration} onChange={e => setN({ ...n, duration: e.target.value })}>
            <option value="once">Première facture</option><option value="repeating">Plusieurs mois</option><option value="forever">Sans limite</option></select></div>
        {n.duration === "repeating" && <div><label htmlFor="pr-m">Nombre de mois</label><input id="pr-m" inputMode="numeric" value={n.months} onChange={e => setN({ ...n, months: e.target.value })} /></div>}
        <div><label htmlFor="pr-max">Utilisations maximales (vide : illimité)</label><input id="pr-max" inputMode="numeric" value={n.max} onChange={e => setN({ ...n, max: e.target.value })} /></div>
        <div><label htmlFor="pr-exp">Date de fin (facultatif)</label><input id="pr-exp" type="date" value={n.expires} onChange={e => setN({ ...n, expires: e.target.value })} /></div>
      </div>
      <fieldset className="plans-pick"><legend>Formules concernées (aucune cochée : toutes)</legend>
        {plans.data?.map(p => <Toggle key={p.code} label={p.label} checked={n.plans.includes(p.code)}
          onChange={v => setN({ ...n, plans: v ? [...n.plans, p.code] : n.plans.filter(c => c !== p.code) })} />)}
        <Toggle label="Réservé aux nouveaux clients" checked={n.first} onChange={v => setN({ ...n, first: v })} />
      </fieldset>
      <p><button disabled={n.code.length < 3 || n.label.length < 2 || !n.value} onClick={create}>Créer la promotion</button></p>
    </Section>
  </>);
}

/* ------------------------------------------------------------------ Pilotage de la plateforme */
type PB = { period_days: number; organizations: number; mrr_cents: number; arr_cents: number; trial_mrr_cents: number; paying: number;
  trialing: number; past_due: number; canceled: number; by_plan: Record<string, number>;
  usage: { organization: string; plan: string; billing_status: string; coaches: number; coachee_spaces: number; contacts: number; sessions: number;
    meetings: number; syntheses: number; input_tokens: number; output_tokens: number }[] };

export function PlatformBoardTab() {
  const [days, setDays] = useState(30);
  const d = useLoad(() => adminApi<PB>(`/pilotage?days=${days}`), [days]);
  const eur = (c: number) => (c / 100).toLocaleString("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
  const x = d.data;
  return (<>
    <div className="filters"><div className="seg" role="radiogroup" aria-label="Période">
      {[[30, "30 jours"], [90, "90 jours"], [365, "12 mois"]].map(([v, l]) =>
        <button key={v} role="radio" aria-checked={days === v} className={days === v ? "on" : ""} onClick={() => setDays(Number(v))}>{l}</button>)}
    </div></div>
    <Notice error={d.error} />
    {x && (<>
      <div className="tiles">
        <Tile label="Revenu mensuel récurrent" value={eur(x.mrr_cents)} hint={`${eur(x.arr_cents)} par an · HT`} />
        <Tile label="Organismes payants" value={String(x.paying)} hint={`${x.organizations} organismes au total`} />
        <Tile label="En essai" value={String(x.trialing)} hint={`${eur(x.trial_mrr_cents)} / mois potentiels`} />
        <Tile label="Impayés · résiliés" value={`${x.past_due} · ${x.canceled}`} />
      </div>
      <Section title="Abonnements payants par formule">
        <BarList title="Organismes" data={Object.entries(x.by_plan).map(([k, v]) => ({ label: k, value: v }))} />
      </Section>
      <Section title={`Usage par organisme (${x.period_days} jours)`}>
        <p className="muted">Comptages uniquement : la super-administration n'accède à aucun contenu de coaching.</p>
        <div className="adm-scroll"><table><thead><tr><th>Organisme</th><th>Formule</th><th>Abonnement</th><th>Coachs</th><th>Espaces coachés</th>
          <th>Séances</th><th>Enregistrées</th><th>Synthèses</th><th>Jetons IA (entrée / sortie)</th></tr></thead>
          <tbody>{x.usage.map(u => <tr key={u.organization}><td>{u.organization}</td><td>{u.plan}</td><td>{BILLING[u.billing_status] ?? u.billing_status}</td>
            <td>{u.coaches}</td><td>{u.coachee_spaces}</td><td>{u.sessions}</td><td>{u.meetings}</td><td>{u.syntheses}</td>
            <td>{u.input_tokens.toLocaleString("fr-FR")} / {u.output_tokens.toLocaleString("fr-FR")}</td></tr>)}</tbody></table></div>
      </Section>
    </>)}
  </>);
}
