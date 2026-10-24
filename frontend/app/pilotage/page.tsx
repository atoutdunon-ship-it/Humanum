"use client";
import { useEffect, useState } from "react";
import { BarList, ColumnChart, Tile } from "@/components/Charts";
import Shell from "@/components/Shell";
import { Notice, Section, useAction, useLoad } from "@/components/admin/ui";
import { api, getToken } from "@/lib/api";
import { APP_NAV, STAGES } from "@/lib/nav";

type D = { period_days: number; scope: string; can_see_all: boolean; quota_left: number | null;
  sessions: { done: number; hours: number; cancelled: number; cancel_rate: number | null; online_share: number | null; upcoming_28d: number };
  coachees: { active: number; new_contacts: number }; pipeline: { stages: { stage: string; count: number }[]; conversion_rate: number | null };
  meetings: { recorded: number; matched_rate: number | null }; syntheses: Record<string, number>;
  tasks: { created: number; done: number; open: number }; weekly: { week: string; sessions: number }[];
  by_coach: { coach: string; sessions: number; hours: number; coachees: number; meetings: number; syntheses: number }[] };

const PERIODS = [[30, "30 jours"], [90, "90 jours"], [365, "12 mois"]] as const;
const pct = (v: number | null) => (v == null ? "—" : `${String(v).replace(".", ",")} %`);
const num = (v: number) => v.toLocaleString("fr-FR");

function Body() {
  const [days, setDays] = useState(90);
  const [scope, setScope] = useState("all");
  const d = useLoad(() => api<D>(`/pilotage?days=${days}&scope=${scope}`), [days, scope]);
  const a = useAction();
  const csv = () => a.run(async () => {
    const r = await fetch(`/api/v1/pilotage/export.csv?days=${days}&scope=${scope}`, { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!r.ok) throw new Error("Export impossible");
    const el = document.createElement("a"); el.href = URL.createObjectURL(await r.blob()); el.download = `pilotage-${days}j.csv`; el.click();
    URL.revokeObjectURL(el.href);
  }, "Export téléchargé (ouvrable dans Excel)");
  const x = d.data;
  return (<>
    <div className="filters" role="group" aria-label="Filtres">
      <div className="seg" role="radiogroup" aria-label="Période">
        {PERIODS.map(([v, l]) => <button key={v} role="radio" aria-checked={days === v} className={days === v ? "on" : ""} onClick={() => setDays(v)}>{l}</button>)}
      </div>
      {x?.can_see_all && <select aria-label="Périmètre" value={scope} onChange={e => setScope(e.target.value)}>
        <option value="all">Tout l'organisme</option><option value="mine">Mon activité</option></select>}
      <button className="ghost" onClick={csv}>Exporter (CSV)</button>
    </div>
    <Notice error={d.error ?? a.error} ok={a.ok} />
    {x && (<>
      <div className="tiles">
        <Tile label="Séances réalisées" value={num(x.sessions.done)} hint={`${String(x.sessions.hours).replace(".", ",")} h d'accompagnement`} />
        <Tile label="Coachés actifs" value={num(x.coachees.active)} hint={`${x.coachees.new_contacts} nouveaux contacts`} />
        <Tile label="Taux d'annulation" value={pct(x.sessions.cancel_rate)} hint={`${x.sessions.cancelled} annulation(s)`} />
        <Tile label="Réservé en ligne" value={pct(x.sessions.online_share)} hint={`${x.sessions.upcoming_28d} rendez-vous à 4 semaines`} />
      </div>
      <Section title="Activité">
        <ColumnChart title="Séances réalisées par semaine (12 dernières semaines)" unit="séances"
          data={x.weekly.map(w => { const dt = new Date(`${w.week}T12:00:00`); return {
            label: dt.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" }), value: w.sessions,
            tip: `Semaine du ${dt.toLocaleDateString("fr-FR", { day: "numeric", month: "long" })}` }; })} />
      </Section>
      <div className="two">
        <Section title="Parcours">
          <BarList title={`Coachés par étape · conversion ${pct(x.pipeline.conversion_rate)}`}
            data={x.pipeline.stages.map(s => ({ label: STAGES[s.stage] ?? s.stage, value: s.count }))} />
        </Section>
        <Section title="Production">
          <dl className="facts">
            <dt>Séances enregistrées (Fathom)</dt><dd>{x.meetings.recorded} · rattachées {pct(x.meetings.matched_rate)}</dd>
            <dt>Synthèses de parcours</dt><dd>{x.syntheses.parcours}</dd>
            <dt>Synthèses de groupe</dt><dd>{x.syntheses.groupe}</dd>
            <dt>Rapports financeurs</dt><dd>{x.syntheses.financeur}</dd>
            <dt>Actions de parcours</dt><dd>{x.tasks.done} réalisées · {x.tasks.open} en cours</dd>
            {x.quota_left != null && <><dt>Synthèses restantes ce mois</dt><dd>{x.quota_left}</dd></>}
          </dl>
        </Section>
      </div>
      {x.by_coach.length > 0 && (
        <Section title="Par coach">
          <table><thead><tr><th>Coach</th><th>Séances</th><th>Heures</th><th>Coachés</th><th>Enregistrées</th><th>Synthèses</th></tr></thead>
            <tbody>{x.by_coach.map(c => <tr key={c.coach}><td>{c.coach}</td><td>{c.sessions}</td><td>{String(c.hours).replace(".", ",")}</td>
              <td>{c.coachees}</td><td>{c.meetings}</td><td>{c.syntheses}</td></tr>)}</tbody></table>
        </Section>
      )}
    </>)}
  </>);
}

export default function Pilotage() {
  const [ok, setOk] = useState(false);
  useEffect(() => { if (!getToken()) window.location.href = "/login"; else setOk(true); }, []);
  return <Shell label="Navigation principale" current="pilotage" items={APP_NAV}><h1>Pilotage</h1>{ok && <Body />}</Shell>;
}
