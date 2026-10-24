"use client";
import { useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { Notice, Section, useAction, useLoad } from "@/components/admin/ui";
import { api, getToken } from "@/lib/api";
import { APP_NAV } from "@/lib/nav";

type P = { code: string; label: string; monthly_price_cents: number; currency: string; max_coaches: number | null; max_active_coachees: number | null;
  ai_syntheses_per_month: number | null; trial_days: number; available: boolean };
type I = { number: string | null; status: string; currency: string; amount_due: number; amount_paid: number; issued_at: string | null;
  period_start: string | null; period_end: string | null; hosted_invoice_url: string | null; invoice_pdf: string | null };
type B = { vat: { enabled: boolean; rate: number; label: string }; discount_label: string | null; plan: string; status: string; trial_end: string | null; current_period_end: string | null; cancel_at_period_end: boolean; trial_used: boolean;
  has_subscription: boolean; can_manage: boolean; stripe_configured: boolean; usage: { coaches: number; active_coachees: number }; plans: P[]; invoices: I[] };

const eur = (c: number, cur = "EUR") => (c / 100).toLocaleString("fr-FR", { style: "currency", currency: cur, minimumFractionDigits: c % 100 ? 2 : 0 });
const d = (v: string | null) => (v ? new Date(v).toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" }) : "—");
const lim = (n: number | null) => (n == null ? "illimité" : String(n));
const INV: Record<string, string> = { paid: "Payée", open: "À régler", void: "Annulée", uncollectible: "Irrécouvrable", draft: "Brouillon" };

function Banner({ b }: { b: B }) {
  if (b.status === "trialing") return <p className="banner-light">Essai gratuit en cours jusqu'au {d(b.trial_end)}. Aucun prélèvement avant cette date.</p>;
  if (b.status === "past_due") return <p className="banner" role="alert">Le dernier paiement a échoué : mettez à jour votre moyen de paiement pour éviter la suspension.</p>;
  if (["canceled", "unpaid", "incomplete_expired"].includes(b.status))
    return <p className="banner" role="alert">Abonnement suspendu : vos données restent consultables, mais les synthèses et les nouvelles invitations sont désactivées.</p>;
  if (b.cancel_at_period_end) return <p className="banner-light">Résiliation programmée : l'abonnement prendra fin le {d(b.current_period_end)}.</p>;
  return null;
}

function Body() {
  const b = useLoad(() => api<B>("/billing"));
  const a = useAction();
  const [flash, setFlash] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [promo, setPromo] = useState<{ code: string; label: string; description: string } | null>(null);
  const [promoErr, setPromoErr] = useState<string | null>(null);
  const [promoPlan, setPromoPlan] = useState("pro");
  useEffect(() => {
    const k = new URLSearchParams(window.location.search).get("paiement");
    if (k) { setFlash(k === "ok" ? "Paiement enregistré : l'activation de votre formule prend quelques secondes."
      : k === "portail" ? "Mode démonstration : le portail client Stripe (carte, factures, résiliation) s'ouvrira ici en production."
      : "Paiement abandonné : aucune somme n'a été prélevée.");
      window.history.replaceState(null, "", "/facturation"); if (k === "ok") { const t = setInterval(b.reload, 3000); setTimeout(() => clearInterval(t), 20000); } }
  }, [b.reload]);
  if (!b.data) return <Notice error={b.error} />;
  const x = b.data, current = x.plans.find(p => p.code === x.plan);
  const go = (path: string, plan?: string) => a.run(async () => {
    const promo_code = plan && promo && promoPlan === plan ? promo.code : undefined;
    window.location.href = (await api<{ url: string }>(path, { method: "POST", body: JSON.stringify(plan ? { plan, promo_code } : {}) })).url;
  }, "Redirection vers le paiement sécurisé Stripe…");
  const check = async () => {
    setPromoErr(null); setPromo(null);
    try {
      const r = await api<{ valid: boolean; reason?: string; code: string; label: string; description: string }>("/billing/promo-check", { method: "POST", body: JSON.stringify({ code, plan: promoPlan }) });
      if (r.valid) setPromo(r); else setPromoErr(r.reason ?? "Code refusé");
    } catch (e) { setPromoErr((e as Error).message); }
  };
  const ttc = (c: number) => Math.round(c * (1 + x.vat.rate / 100));
  return (<>
    {flash && <Notice ok={flash} />}
    <Banner b={x} />
    <Notice error={a.error} ok={a.ok} />
    <Section title="Votre formule" actions={x.can_manage && x.has_subscription && <button className="ghost" onClick={() => go("/billing/portal")}>Moyen de paiement et factures</button>}>
      <dl className="facts">
        <dt>Formule</dt><dd>{current?.label ?? x.plan}</dd>
        <dt>Coachs</dt><dd>{x.usage.coaches} / {lim(current?.max_coaches ?? null)}</dd>
        <dt>Espaces coachés actifs</dt><dd>{x.usage.active_coachees} / {lim(current?.max_active_coachees ?? null)}</dd>
        {x.current_period_end && <><dt>Prochaine échéance</dt><dd>{d(x.current_period_end)}</dd></>}
        {x.discount_label && <><dt>Remise en cours</dt><dd>{x.discount_label}</dd></>}
      </dl>
      {!x.can_manage && <p className="muted">Seul l'administrateur de l'organisme peut modifier l'abonnement.</p>}
      {!x.stripe_configured && <p className="muted">Le paiement en ligne n'est pas encore ouvert sur la plateforme.</p>}
    </Section>
    {x.can_manage && x.stripe_configured && !x.has_subscription && (
      <Section title="Code promotionnel">
        <div className="adm-inline">
          <input aria-label="Code promotionnel" placeholder="Saisissez votre code" value={code} onChange={e => setCode(e.target.value.toUpperCase())} />
          <select aria-label="Formule visée" value={promoPlan} onChange={e => { setPromoPlan(e.target.value); setPromo(null); }}>
            {x.plans.map(p => <option key={p.code} value={p.code}>{p.label}</option>)}</select>
          <button disabled={!code} onClick={check}>Vérifier le code</button>
        </div>
        {promo && <p className="ok" role="status">{promo.label} : {promo.description}. La remise sera appliquée au paiement de la formule choisie.</p>}
        {promoErr && <p className="err" role="alert">{promoErr}</p>}
      </Section>
    )}
    <div className="plans">
      {x.plans.map(p => {
        const isCurrent = p.code === x.plan && (x.has_subscription || x.status === "none");
        return (
          <article key={p.code} className={`card plan${p.code === x.plan ? " current" : ""}`}>
            <p className="eyebrow">{p.code === x.plan ? "Formule actuelle" : "Formule"}</p>
            <h2>{p.label}</h2>
            <p className="price">{eur(p.monthly_price_cents, p.currency)} <span>HT / mois</span></p>
            {x.vat.enabled && <p className="muted">{x.vat.label} {String(x.vat.rate).replace(".", ",")} % en sus, soit {eur(ttc(p.monthly_price_cents), p.currency)} TTC</p>}
            {promo && promoPlan === p.code && !x.has_subscription && <p className="banner-light">Code {promo.code} : {promo.description}</p>}
            <ul>
              <li>{p.max_coaches == null ? "Coachs illimités" : `Jusqu'à ${p.max_coaches} coach${p.max_coaches > 1 ? "s" : ""}`}</li>
              <li>{p.max_active_coachees == null ? "Espaces coachés illimités" : `${p.max_active_coachees} espaces coachés actifs`}</li>
              <li>{p.ai_syntheses_per_month == null ? "Synthèses illimitées" : `${p.ai_syntheses_per_month} synthèses par mois`}</li>
              <li>Agenda Google, Meet et Fathom inclus</li>
              {p.trial_days > 0 && !x.trial_used && <li><strong>{p.trial_days} jours d'essai gratuit</strong></li>}
            </ul>
            {x.can_manage && x.stripe_configured && (
              x.has_subscription
                ? <button disabled={p.code === x.plan || !p.available} onClick={() => {
                    if (window.confirm(`Passer à la formule ${p.label} ? La différence est calculée au prorata sur la prochaine facture.`))
                      a.run(() => api("/billing/change", { method: "POST", body: JSON.stringify({ plan: p.code }) }).then(b.reload), `Formule ${p.label} activée`);
                  }}>{p.code === x.plan ? "Formule actuelle" : "Passer à cette formule"}</button>
                : <button disabled={!p.available} onClick={() => go("/billing/checkout", p.code)}>{isCurrent ? "Souscrire" : "Choisir"} {p.label}</button>
            )}
          </article>
        );
      })}
    </div>
    <Section title="Factures">
      {x.invoices.length === 0 && <p className="muted">Aucune facture pour l'instant.</p>}
      {x.invoices.length > 0 && (
        <table>
          <thead><tr><th>Date</th><th>Numéro</th><th>Période</th><th>Montant</th><th>Statut</th><th></th></tr></thead>
          <tbody>{x.invoices.map((i, k) => (
            <tr key={k}><td>{d(i.issued_at)}</td><td>{i.number ?? "—"}</td><td>{d(i.period_start)} – {d(i.period_end)}</td>
              <td>{eur(i.amount_due, i.currency)}</td><td>{INV[i.status] ?? i.status}</td>
              <td>{i.invoice_pdf ? <a href={i.invoice_pdf} target="_blank" rel="noopener noreferrer">PDF</a> : i.hosted_invoice_url ? <a href={i.hosted_invoice_url} target="_blank" rel="noopener noreferrer">Voir</a> : "—"}</td></tr>
          ))}</tbody>
        </table>
      )}
    </Section>
  </>);
}

export default function Facturation() {
  const [ok, setOk] = useState(false);
  useEffect(() => { if (!getToken()) window.location.href = "/login"; else setOk(true); }, []);
  return <Shell label="Navigation principale" current="facturation" items={APP_NAV}><h1>Facturation</h1>{ok && <Body />}</Shell>;
}
