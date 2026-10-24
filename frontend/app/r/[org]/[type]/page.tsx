"use client";
import { useParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import SlotPicker, { type Slots } from "@/components/SlotPicker";
import { fmt } from "@/lib/nav";

type Page = { organization: string; title: string; description: string | null; duration_minutes: number; coach_name: string; timezone: string; max_days_ahead: number };
type Booked = { starts_at: string; timezone: string; meet_url: string | null; manage_url: string; title: string; coach_name: string };

async function pub<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api/v1/public${path}`, { ...init, headers: { "Content-Type": "application/json" } });
  const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof b.detail === "string" ? b.detail : "Service momentanément indisponible");
  return b as T;
}

export default function Reserver() {
  const { org, type } = useParams<{ org: string; type: string }>();
  const base = `/booking/${encodeURIComponent(org)}/${encodeURIComponent(type)}`;
  const [page, setPage] = useState<Page | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [slot, setSlot] = useState<string | null>(null);
  const [f, setF] = useState({ name: "", email: "", phone: "", message: "", consent: false });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Booked | null>(null);
  const [nonce, setNonce] = useState(0);
  useEffect(() => { pub<Page>(base).then(setPage).catch(e => setFatal((e as Error).message)); }, [base]);

  async function submit(e: FormEvent) {
    e.preventDefault(); setError(null); setBusy(true);
    try { setDone(await pub<Booked>(base, { method: "POST", body: JSON.stringify({ ...f, phone: f.phone || null, message: f.message || null, start: slot }) })); }
    catch (err) { setError((err as Error).message); setSlot(null); setNonce(nonce + 1); }
    setBusy(false);
  }

  return (
    <div className="public">
      <header className="public-head"><img src="/logo-humanum.png" alt="HUMANUM CONSULTING" /></header>
      <main className="public-main">
        {fatal && <div className="card"><h1>Réservation indisponible</h1><p>{fatal}</p></div>}
        {page && !done && (<>
          <div className="card public-intro">
            <p className="eyebrow">{page.organization}</p>
            <h1>{page.title}</h1>
            <p className="meta">{page.coach_name} · {page.duration_minutes} minutes · visioconférence Google Meet</p>
            {page.description && <p>{page.description}</p>}
          </div>
          <div className="card">
            <h2>1. Choisissez un créneau</h2>
            <SlotPicker key={nonce} load={(s, d) => pub<Slots>(`${base}/slots?start=${s}&days=${d}`)} selected={slot}
              onPick={s => { setSlot(s); setError(null); setTimeout(() => document.getElementById("coordonnees")?.scrollIntoView({ behavior: "smooth" }), 50); }}
              maxDays={page.max_days_ahead} />
            {error && <p className="err" role="alert">{error}</p>}
          </div>
          {slot && (
            <form className="card" id="coordonnees" onSubmit={submit}>
              <h2>2. Vos coordonnées</h2>
              <p className="banner-light">{fmt.full(slot, page.timezone)} ({page.timezone.replace("_", " ")})</p>
              <label htmlFor="b-name">Nom et prénom</label>
              <input id="b-name" required autoComplete="name" value={f.name} onChange={e => setF({ ...f, name: e.target.value })} />
              <label htmlFor="b-email">Adresse e-mail</label>
              <input id="b-email" type="email" required autoComplete="email" value={f.email} onChange={e => setF({ ...f, email: e.target.value })} />
              <label htmlFor="b-phone">Téléphone (facultatif)</label>
              <input id="b-phone" type="tel" autoComplete="tel" value={f.phone} onChange={e => setF({ ...f, phone: e.target.value })} />
              <label htmlFor="b-msg">Message pour le coach (facultatif)</label>
              <textarea id="b-msg" rows={3} maxLength={2000} value={f.message} onChange={e => setF({ ...f, message: e.target.value })} />
              <label className="check"><input type="checkbox" required checked={f.consent} onChange={e => setF({ ...f, consent: e.target.checked })} />
                <span>J'accepte que mes coordonnées soient utilisées par {page.organization} pour organiser ce rendez-vous.</span></label>
              <p><button type="submit" disabled={busy}>Confirmer le rendez-vous</button></p>
            </form>
          )}
        </>)}
        {done && (
          <div className="card confirm" role="status">
            <p className="eyebrow">Rendez-vous confirmé</p>
            <h1>{fmt.full(done.starts_at, done.timezone)}</h1>
            <p>{done.title} avec {done.coach_name}. Une invitation Google Agenda vient de vous être envoyée par e-mail, avec un rappel la veille.</p>
            {done.meet_url ? <p><a className="btn" href={done.meet_url} target="_blank" rel="noopener noreferrer">Lien Google Meet</a></p>
              : <p className="muted">Le lien Google Meet figurera dans l'invitation reçue par e-mail.</p>}
            <p>Besoin de changer ? <a href={done.manage_url}>Reporter ou annuler ce rendez-vous</a> — conservez ce lien.</p>
          </div>
        )}
      </main>
    </div>
  );
}
