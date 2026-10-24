"use client";
import { useEffect, useState } from "react";
import Markdown from "@/components/Markdown";
import Shell from "@/components/Shell";
import { Notice, Section, useAction, useLoad } from "@/components/admin/ui";
import { api, getToken, roleOf } from "@/lib/api";
import { fmt, ME_NAV } from "@/lib/nav";

type Me = { first_name: string; last_name: string; organization: string; coach_name: string | null; open_tasks: number;
  booking_links: { title: string; duration_minutes: number; url: string }[] };
type A = { id: string; title: string; starts_at: string; ends_at: string; timezone: string; status: string; upcoming: boolean; meet_url: string | null; manage_url: string | null };
type S = { id: string; title: string; started_at: string | null; summary_md: string | null; action_items: { description: string; completed: boolean }[] };
type T = { id: string; title: string; notes: string | null; due_on: string | null; done: boolean };
type D = { id: string; title: string; created_at: string; content_md: string };
type Tab = "accueil" | "rendez-vous" | "seances" | "actions" | "documents";
const TABS: Tab[] = ["accueil", "rendez-vous", "seances", "actions", "documents"];

function Appt({ a }: { a: A }) {
  return (
    <div className="appt">
      <div className="appt-time">{fmt.time(a.starts_at, a.timezone)}<span>{fmt.time(a.ends_at, a.timezone)}</span></div>
      <div className="appt-body"><strong>{fmt.day(a.starts_at, a.timezone)}</strong><span className="muted">{a.title}{a.status === "cancelled" ? " · annulé" : ""}</span></div>
      {a.upcoming && <div className="appt-actions">
        {a.meet_url && <a className="btn" href={a.meet_url} target="_blank" rel="noopener noreferrer">Rejoindre Meet</a>}
        {a.manage_url && <a className="btn ghost" href={a.manage_url}>Reporter ou annuler</a>}
      </div>}
    </div>
  );
}

function Accueil({ me, appts, go }: { me: Me; appts: A[]; go: (t: Tab) => void }) {
  const next = appts.filter(a => a.upcoming).sort((x, y) => x.starts_at.localeCompare(y.starts_at))[0];
  return (<>
    <Section title="Votre prochain rendez-vous">
      {next ? <Appt a={next} /> : <p className="muted">Aucun rendez-vous prévu.</p>}
      {me.booking_links.length > 0 && (<>
        <h3 className="sub-title">Prendre rendez-vous</h3>
        <div className="mfa-actions">{me.booking_links.map(l => <a key={l.url} className="btn ghost" href={l.url}>{l.title} · {l.duration_minutes} min</a>)}</div>
      </>)}
    </Section>
    <Section title="Votre parcours">
      <dl className="facts">
        <dt>Organisme</dt><dd>{me.organization}</dd>
        <dt>Coach</dt><dd>{me.coach_name ?? "—"}</dd>
        <dt>Actions en cours</dt><dd>{me.open_tasks} <button className="link" onClick={() => go("actions")}>Voir mes actions</button></dd>
      </dl>
    </Section>
  </>);
}

function Body() {
  const [tab, setTab] = useState<Tab>("accueil");
  const me = useLoad(() => api<Me>("/me"));
  const appts = useLoad(() => api<A[]>("/me/appointments"));
  const sessions = useLoad(() => api<S[]>("/me/sessions"));
  const tasks = useLoad(() => api<T[]>("/me/tasks"));
  const docs = useLoad(() => api<D[]>("/me/documents"));
  const a = useAction();
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => { const h = window.location.hash.slice(1) as Tab; if (TABS.includes(h)) setTab(h); }, []);
  const go = (t: Tab) => { setTab(t); history.replaceState(null, "", `#${t}`); window.scrollTo(0, 0); };
  const label = ME_NAV.find(i => i.id === tab)?.label;
  return (
    <Shell label="Mon espace" current={tab} items={ME_NAV} onNavigate={id => TABS.includes(id as Tab) && go(id as Tab)}>
      <h1>{tab === "accueil" && me.data ? `Bonjour ${me.data.first_name}` : label}</h1>
      <Notice error={me.error ?? a.error} ok={a.ok} />
      {tab === "accueil" && me.data && <Accueil me={me.data} appts={appts.data ?? []} go={go} />}
      {tab === "rendez-vous" && (<Section title="Rendez-vous">
        {appts.data?.length === 0 && <p className="muted">Aucun rendez-vous.</p>}
        {appts.data?.some(x => x.upcoming) && <h3 className="sub-title">À venir</h3>}
        {appts.data?.filter(x => x.upcoming).reverse().map(x => <Appt key={x.id} a={x} />)}
        {appts.data?.some(x => !x.upcoming) && <h3 className="sub-title">Passés et annulés</h3>}
        {appts.data?.filter(x => !x.upcoming).map(x => <Appt key={x.id} a={x} />)}
      </Section>)}
      {tab === "seances" && (<Section title="Comptes rendus partagés par votre coach">
        {sessions.data?.length === 0 && <p className="muted">Votre coach n'a pas encore partagé de compte rendu.</p>}
        {sessions.data?.map(m => (
          <div key={m.id} className="adm-user">
            <div className="adm-user-head">
              <div><strong>{m.started_at ? new Date(m.started_at).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" }) : "—"}</strong><br /><span className="muted">{m.title}</span></div>
              <button className="ghost" aria-expanded={open === m.id} onClick={() => setOpen(open === m.id ? null : m.id)}>{open === m.id ? "Masquer" : "Lire"}</button>
            </div>
            {open === m.id && <div className="meeting-body">{m.summary_md ? <Markdown text={m.summary_md} /> : <p className="muted">Pas de résumé.</p>}</div>}
          </div>
        ))}
      </Section>)}
      {tab === "actions" && (<Section title="Mes actions">
        {tasks.data?.length === 0 && <p className="muted">Aucune action pour le moment.</p>}
        <ul className="tasks">{tasks.data?.map(t => (
          <li key={t.id} className={t.done ? "done" : ""}>
            <label className="check"><input type="checkbox" checked={t.done} onChange={e => a.run(() => api(`/me/tasks/${t.id}`, { method: "PATCH",
              body: JSON.stringify({ done: e.target.checked }) }).then(tasks.reload), e.target.checked ? "Bravo, action réalisée" : "Action rouverte")} />
              <span><strong>{t.title}</strong>{t.due_on && <span className="muted"> · avant le {new Date(t.due_on).toLocaleDateString("fr-FR")}</span>}
                {t.notes && <><br /><span className="muted">{t.notes}</span></>}</span></label>
          </li>))}</ul>
      </Section>)}
      {tab === "documents" && (<Section title="Documents partagés">
        {docs.data?.length === 0 && <p className="muted">Aucun document partagé.</p>}
        {docs.data?.map(d => (
          <div key={d.id} className="adm-user">
            <div className="adm-user-head"><div><strong>{d.title}</strong><br /><span className="muted">{new Date(d.created_at).toLocaleDateString("fr-FR")}</span></div>
              <button className="ghost" aria-expanded={open === d.id} onClick={() => setOpen(open === d.id ? null : d.id)}>{open === d.id ? "Masquer" : "Lire"}</button></div>
            {open === d.id && <div className="doc-inline"><Markdown text={d.content_md} /><p className="no-print"><button onClick={() => window.print()}>Imprimer ou enregistrer en PDF</button></p></div>}
          </div>
        ))}
      </Section>)}
    </Shell>
  );
}

export default function Espace() {
  const [ok, setOk] = useState(false);
  useEffect(() => {
    if (!getToken()) window.location.href = "/login";
    else if (roleOf() !== "coachee") window.location.href = "/contacts";
    else setOk(true);
  }, []);
  return ok ? <Body /> : null;
}
