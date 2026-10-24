"use client";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import SlotPicker, { type Slots } from "@/components/SlotPicker";
import { fmt } from "@/lib/nav";

type Appt = { title: string; coach_name: string; starts_at: string; timezone: string; status: string; meet_url: string | null; can_cancel: boolean; can_reschedule: boolean };

async function pub<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api/v1/public${path}`, { ...init, headers: { "Content-Type": "application/json" } });
  const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof b.detail === "string" ? b.detail : "Service momentanément indisponible");
  return b as T;
}

export default function GererRdv() {
  const { token } = useParams<{ token: string }>();
  const base = `/appointments/${encodeURIComponent(token)}`;
  const [a, setA] = useState<Appt | null>(null);
  const [mode, setMode] = useState<"view" | "move" | "cancel">("view");
  const [slot, setSlot] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<{ ok?: string; err?: string }>({});
  useEffect(() => { pub<Appt>(base).then(setA).catch(e => setMsg({ err: (e as Error).message })); }, [base]);

  const act = async (fn: () => Promise<Appt>, ok: string) => {
    setMsg({});
    try { setA(await fn()); setMode("view"); setSlot(null); setMsg({ ok }); } catch (e) { setMsg({ err: (e as Error).message }); }
  };

  return (
    <div className="public">
      <header className="public-head"><img src="/logo-humanum.png" alt="HUMANUM CONSULTING" /></header>
      <main className="public-main">
        {a && (
          <div className="card">
            <p className="eyebrow">{a.status === "cancelled" ? "Rendez-vous annulé" : "Votre rendez-vous"}</p>
            <h1>{fmt.full(a.starts_at, a.timezone)}</h1>
            <p>{a.title} avec {a.coach_name}</p>
            {a.status === "confirmed" && a.meet_url && <p><a className="btn" href={a.meet_url} target="_blank" rel="noopener noreferrer">Rejoindre Google Meet</a></p>}
            {mode === "view" && (a.can_reschedule || a.can_cancel) && (
              <div className="mfa-actions">
                {a.can_reschedule && <button type="button" onClick={() => setMode("move")}>Reporter</button>}
                {a.can_cancel && <button type="button" className="ghost" onClick={() => setMode("cancel")}>Annuler le rendez-vous</button>}
              </div>
            )}
          </div>
        )}
        {a && mode === "move" && (
          <div className="card">
            <h2>Choisir un nouveau créneau</h2>
            <SlotPicker load={(s, d) => pub<Slots>(`${base}/slots?start=${s}&days=${d}`)} selected={slot} onPick={s => setSlot(s)} />
            {slot && <p className="banner-light">Nouveau créneau : {fmt.full(slot, a.timezone)}</p>}
            <div className="mfa-actions">
              <button type="button" disabled={!slot} onClick={() => act(() => pub<Appt>(`${base}/reschedule`, { method: "POST", body: JSON.stringify({ start: slot }) }),
                "Rendez-vous reporté : l'invitation Google Agenda a été mise à jour")}>Confirmer le report</button>
              <button type="button" className="ghost" onClick={() => { setMode("view"); setSlot(null); }}>Retour</button>
            </div>
          </div>
        )}
        {a && mode === "cancel" && (
          <div className="card">
            <h2>Annuler le rendez-vous</h2>
            <label htmlFor="reason">Motif (facultatif, transmis au coach)</label>
            <textarea id="reason" rows={3} maxLength={500} value={reason} onChange={e => setReason(e.target.value)} />
            <div className="mfa-actions">
              <button type="button" onClick={() => act(() => pub<Appt>(`${base}/cancel`, { method: "POST", body: JSON.stringify({ reason: reason || null }) }),
                "Rendez-vous annulé : le coach est prévenu")}>Confirmer l'annulation</button>
              <button type="button" className="ghost" onClick={() => setMode("view")}>Retour</button>
            </div>
          </div>
        )}
        {msg.err && <p className="err" role="alert">{msg.err}</p>}
        {msg.ok && <p className="ok" role="status">{msg.ok}</p>}
      </main>
    </div>
  );
}
