"use client";
import { useParams } from "next/navigation";
import { useState } from "react";
import Shell from "@/components/Shell";
import Syntheses from "@/components/Syntheses";
import { Notice, Section, useAction, useLoad } from "@/components/admin/ui";
import { api } from "@/lib/api";
import { APP_NAV } from "@/lib/nav";

type P = { id: string; first_name: string; last_name: string; email: string | null; status: string | null };
type G = { id: string; name: string; program: string | null; funder: string | null; members: number; contacts: P[] };

export default function Groupe() {
  const { id } = useParams<{ id: string }>();
  const g = useLoad(() => api<G>(`/cohorts/${id}`), [id]);
  const all = useLoad(() => api<P[]>("/contacts?limit=200"));
  const a = useAction();
  const [pick, setPick] = useState("");
  const inGroup = new Set(g.data?.contacts.map(c => c.id));
  return (
    <Shell label="Navigation principale" current="groupes" items={APP_NAV}>
      <p><a href="/groupes">Retour aux groupes</a></p>
      <h1>{g.data?.name ?? "Groupe"}</h1>
      <Notice error={g.error} />
      {g.data && (<>
        <Section title="Synthèses en un clic"><Syntheses subject={{ cohort_id: id }} kinds={["groupe", "financeur"]} /></Section>
        <Section title={`Participants (${g.data.members})`}>
          <Notice error={a.error} ok={a.ok} />
          {g.data.contacts.map(c => (
            <div key={c.id} className="adm-user adm-user-head">
              <div><a href={`/contacts/${c.id}`}><strong>{c.first_name} {c.last_name}</strong></a><br /><span className="muted">{c.status ?? "—"}</span></div>
              <button className="ghost" onClick={() => a.run(() => api(`/cohorts/${id}/members/${c.id}`, { method: "DELETE" }).then(g.reload), "Participant retiré")}>Retirer</button>
            </div>
          ))}
          <div className="adm-inline">
            <select aria-label="Ajouter un participant" value={pick} onChange={e => setPick(e.target.value)}>
              <option value="">Ajouter un participant…</option>
              {all.data?.filter(c => !inGroup.has(c.id)).map(c => <option key={c.id} value={c.id}>{c.last_name} {c.first_name}</option>)}
            </select>
            <button disabled={!pick} onClick={() => a.run(() => api(`/cohorts/${id}/members/${pick}`, { method: "PUT" }).then(() => { setPick(""); g.reload(); }), "Participant ajouté")}>Ajouter</button>
          </div>
        </Section>
      </>)}
    </Shell>
  );
}
