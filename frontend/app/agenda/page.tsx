"use client";
import { useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { Notice, Section, Toggle, useAction, useLoad } from "@/components/admin/ui";
import { api, getToken } from "@/lib/api";
import { APP_NAV, fmt } from "@/lib/nav";

type G = { configured: boolean; connected: boolean; email: string | null; status: string | null; last_error: string | null };
type R = { weekday: number; start: string; end: string };
type Sch = { timezone: string; min_notice_minutes: number; max_days_ahead: number; slot_step_minutes: number; rules: R[] };
type T = { id: string; slug: string; title: string; description: string | null; duration_minutes: number; buffer_before_minutes: number;
  buffer_after_minutes: number; is_active: boolean; public_url: string };
type A = { id: string; title: string; attendee_name: string; attendee_email: string; starts_at: string; ends_at: string; timezone: string;
  meet_url: string | null; sync_status: string; sync_error: string | null; source: string };

const DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];
const ZONES = ["America/Martinique", "America/Guadeloupe", "America/Cayenne", "America/Port-au-Prince", "America/Montreal",
  "Indian/Reunion", "Indian/Mayotte", "Pacific/Noumea", "Pacific/Tahiti", "Europe/Paris"];
const NOTICE = [[0, "Aucun"], [60, "1 heure"], [120, "2 heures"], [240, "4 heures"], [720, "12 heures"], [1440, "24 heures"], [2880, "48 heures"]] as const;
const GOOGLE_MSG: Record<string, [string, boolean]> = {
  connecte: ["Agenda Google connecté : vos créneaux tiennent compte de vos occupations.", true],
  refuse: ["Connexion Google annulée.", false], expire: ["Le délai de connexion a expiré : recommencez.", false],
  erreur: ["Google a refusé la connexion : recommencez en acceptant l'accès à l'agenda.", false],
};
const SYNC: Record<string, string> = { synced: "Dans Google Agenda", pending: "Envoi en cours", error: "À resynchroniser", skipped: "Hors Google" };

function GoogleCard() {
  const g = useLoad(() => api<G>("/booking/google"));
  const a = useAction();
  const [flash, setFlash] = useState<[string, boolean] | null>(null);
  useEffect(() => {
    const k = new URLSearchParams(window.location.search).get("google");
    if (k && GOOGLE_MSG[k]) { setFlash(GOOGLE_MSG[k]); window.history.replaceState(null, "", "/agenda"); }
  }, []);
  return (
    <Section title="Agenda Google" actions={g.data && <span className={`pill${g.data.connected ? " on" : ""}`}>{g.data.connected ? "Connecté" : "Non connecté"}</span>}>
      {flash && <Notice ok={flash[1] ? flash[0] : null} error={flash[1] ? null : flash[0]} />}
      <Notice error={a.error ?? g.error} ok={a.ok} />
      {g.data?.status === "revoked" && <p className="banner" role="status">L'accès à votre agenda a été retiré côté Google : la réservation en ligne est fermée jusqu'à la reconnexion.</p>}
      {g.data?.connected ? (<>
        <p>Agenda : <strong>{g.data.email}</strong>. Les rendez-vous y sont créés avec un lien Google Meet et l'invitation est envoyée au coaché.
          Seules vos plages occupées sont lues, jamais le contenu de vos événements.</p>
        <p><button className="ghost" onClick={() => a.run(() => api("/booking/google", { method: "DELETE" }).then(g.reload), "Agenda Google déconnecté")}>Déconnecter</button></p>
      </>) : g.data && (<>
        <p>Connectez votre agenda Google pour ouvrir la réservation en ligne : créneaux calculés sur vos occupations réelles,
          événement et lien Google Meet créés automatiquement, rappels la veille et une heure avant.</p>
        <p><button disabled={!g.data.configured} onClick={() => a.run(async () => {
          window.location.href = (await api<{ url: string }>("/booking/google/connect", { method: "POST" })).url;
        }, "Redirection vers Google…")}>Connecter mon agenda Google</button></p>
        {!g.data.configured && <p className="muted">La connexion Google n'est pas encore configurée sur la plateforme.</p>}
      </>)}
    </Section>
  );
}

function ScheduleCard() {
  const sc = useLoad(() => api<Sch>("/booking/schedule"));
  const a = useAction();
  const [d, setD] = useState<Sch | null>(null);
  useEffect(() => { if (sc.data) setD(sc.data); }, [sc.data]);
  if (!d) return <Section title="Disponibilités"><Notice error={sc.error} /></Section>;
  const rules = (w: number) => d.rules.map((r, i) => [r, i] as const).filter(([r]) => r.weekday === w);
  const set = (i: number, patch: Partial<R>) => setD({ ...d, rules: d.rules.map((r, j) => (j === i ? { ...r, ...patch } : r)) });
  return (
    <Section title="Disponibilités">
      <Notice error={a.error} ok={a.ok} />
      <div className="adm-grid">
        <div><label htmlFor="tz">Fuseau horaire</label>
          <select id="tz" value={d.timezone} onChange={e => setD({ ...d, timezone: e.target.value })}>
            {(ZONES.includes(d.timezone) ? ZONES : [d.timezone, ...ZONES]).map(z => <option key={z} value={z}>{z.replace("_", " ")}</option>)}</select></div>
        <div><label htmlFor="notice">Délai de prévenance</label>
          <select id="notice" value={d.min_notice_minutes} onChange={e => setD({ ...d, min_notice_minutes: Number(e.target.value) })}>
            {NOTICE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
        <div><label htmlFor="horizon">Réservable jusqu'à (jours)</label>
          <input id="horizon" type="number" min={1} max={365} value={d.max_days_ahead} onChange={e => setD({ ...d, max_days_ahead: Number(e.target.value) })} /></div>
        <div><label htmlFor="step">Intervalle entre deux départs</label>
          <select id="step" value={d.slot_step_minutes} onChange={e => setD({ ...d, slot_step_minutes: Number(e.target.value) })}>
            {[15, 20, 30, 45, 60, 90, 120].map(v => <option key={v} value={v}>{v} minutes</option>)}</select></div>
      </div>
      <div className="week">
        {DAYS.map((name, w) => (
          <div key={w} className="week-day">
            <strong>{name}</strong>
            <div className="week-ranges">
              {rules(w).length === 0 && <span className="muted">Indisponible</span>}
              {rules(w).map(([r, i]) => (
                <span key={i} className="range">
                  <input type="time" aria-label={`${name} début`} value={r.start} onChange={e => set(i, { start: e.target.value })} />
                  <span aria-hidden="true">–</span>
                  <input type="time" aria-label={`${name} fin`} value={r.end === "24:00" ? "23:59" : r.end} onChange={e => set(i, { end: e.target.value })} />
                  <button type="button" className="ghost" onClick={() => setD({ ...d, rules: d.rules.filter((_, j) => j !== i) })}>Retirer</button>
                </span>
              ))}
              <button type="button" className="link" onClick={() => setD({ ...d, rules: [...d.rules, { weekday: w, start: "09:00", end: "12:00" }] })}>Ajouter une plage</button>
            </div>
          </div>
        ))}
      </div>
      <p><button onClick={() => a.run(async () => { setD(await api<Sch>("/booking/schedule", { method: "PUT", body: JSON.stringify(d) })); }, "Disponibilités enregistrées")}>
        Enregistrer les disponibilités</button></p>
    </Section>
  );
}

function TypesCard() {
  const t = useLoad(() => api<T[]>("/booking/types"));
  const a = useAction();
  const [n, setN] = useState({ title: "", slug: "", duration_minutes: 60, buffer_after_minutes: 15, description: "" });
  const slugify = (v: string) => v.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80);
  return (
    <Section title="Types de rendez-vous">
      <Notice error={a.error ?? t.error} ok={a.ok} />
      {t.data?.length === 0 && <p className="muted">Aucun type : créez-en un pour obtenir votre lien de réservation.</p>}
      {t.data?.map(x => (
        <div key={x.id} className="adm-user">
          <div className="adm-user-head">
            <div><strong>{x.title}</strong><br /><span className="muted">{x.duration_minutes} min · battement {x.buffer_after_minutes} min</span></div>
            <Toggle label={x.is_active ? "Réservable" : "Fermé"} checked={x.is_active}
              onChange={v => a.run(() => api(`/booking/types/${x.id}`, { method: "PATCH", body: JSON.stringify({ is_active: v }) }).then(t.reload), v ? "Type ouvert" : "Type fermé")} />
          </div>
          <div className="adm-inline">
            <input readOnly aria-label="Lien de réservation" value={x.public_url} onFocus={e => e.currentTarget.select()} />
            <button className="ghost" onClick={() => a.run(() => navigator.clipboard.writeText(x.public_url), "Lien copié")}>Copier le lien</button>
            <a className="btn ghost" href={x.public_url} target="_blank" rel="noopener noreferrer">Ouvrir</a>
          </div>
        </div>
      ))}
      <h3 className="sub-title">Nouveau type</h3>
      <div className="adm-grid">
        <div><label htmlFor="t-title">Intitulé</label>
          <input id="t-title" value={n.title} onChange={e => setN({ ...n, title: e.target.value, slug: slugify(e.target.value) })} placeholder="Séance de coaching individuel" /></div>
        <div><label htmlFor="t-slug">Adresse</label><input id="t-slug" value={n.slug} onChange={e => setN({ ...n, slug: slugify(e.target.value) })} /></div>
        <div><label htmlFor="t-dur">Durée</label>
          <select id="t-dur" value={n.duration_minutes} onChange={e => setN({ ...n, duration_minutes: Number(e.target.value) })}>
            {[15, 30, 45, 60, 90, 120, 180].map(v => <option key={v} value={v}>{v} minutes</option>)}</select></div>
        <div><label htmlFor="t-buf">Battement après</label>
          <select id="t-buf" value={n.buffer_after_minutes} onChange={e => setN({ ...n, buffer_after_minutes: Number(e.target.value) })}>
            {[0, 5, 10, 15, 30, 45, 60].map(v => <option key={v} value={v}>{v} minutes</option>)}</select></div>
      </div>
      <label htmlFor="t-desc">Description affichée au coaché</label>
      <textarea id="t-desc" rows={2} value={n.description} onChange={e => setN({ ...n, description: e.target.value })} />
      <p><button disabled={!n.title || !n.slug} onClick={() => a.run(() => api("/booking/types", { method: "POST", body: JSON.stringify({ ...n, description: n.description || null }) })
        .then(() => { setN({ title: "", slug: "", duration_minutes: 60, buffer_after_minutes: 15, description: "" }); t.reload(); }), "Type créé : partagez son lien")}>Créer</button></p>
    </Section>
  );
}

function AppointmentsCard() {
  const l = useLoad(() => api<A[]>("/booking/appointments"));
  const a = useAction();
  const groups = new Map<string, A[]>();
  for (const x of l.data ?? []) { const k = fmt.day(x.starts_at, x.timezone); groups.set(k, [...(groups.get(k) ?? []), x]); }
  return (
    <Section title="Prochains rendez-vous" actions={<button className="ghost" onClick={l.reload}>Actualiser</button>}>
      <Notice error={a.error ?? l.error} ok={a.ok} />
      {l.data?.length === 0 && <p className="muted">Aucun rendez-vous à venir.</p>}
      {[...groups].map(([day, items]) => (
        <div key={day} className="appt-day">
          <h3>{day}</h3>
          {items.map(x => (
            <div key={x.id} className="appt">
              <div className="appt-time">{fmt.time(x.starts_at, x.timezone)}<span>{fmt.time(x.ends_at, x.timezone)}</span></div>
              <div className="appt-body"><strong>{x.attendee_name}</strong><span className="muted">{x.title.split(" — ")[0]} · {x.attendee_email}</span>
                <span className={`pill${x.sync_status === "synced" ? " on" : ""}`}>{SYNC[x.sync_status] ?? x.sync_status}</span></div>
              <div className="appt-actions">
                {x.meet_url && <a className="btn" href={x.meet_url} target="_blank" rel="noopener noreferrer">Rejoindre Meet</a>}
                <button className="ghost" onClick={() => a.run(() => api(`/booking/appointments/${x.id}/cancel`, { method: "POST", body: "{}" }).then(l.reload),
                  "Rendez-vous annulé : le coaché est prévenu par Google Agenda")}>Annuler</button>
              </div>
            </div>
          ))}
        </div>
      ))}
    </Section>
  );
}

export default function Agenda() {
  const [ok, setOk] = useState(false);
  useEffect(() => { if (!getToken()) window.location.href = "/login"; else setOk(true); }, []);
  return (
    <Shell label="Navigation principale" current="agenda" items={APP_NAV}>
      <h1>Agenda et rendez-vous</h1>
      {ok && (<><GoogleCard /><AppointmentsCard /><TypesCard /><ScheduleCard /></>)}
    </Shell>
  );
}
