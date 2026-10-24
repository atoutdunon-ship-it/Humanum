"use client";
import { useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { Notice, Section, useAction, useLoad } from "@/components/admin/ui";
import { api, getToken } from "@/lib/api";
import { APP_NAV } from "@/lib/nav";

type G = { id: string; name: string; program: string | null; funder: string | null; members: number };

function Body() {
  const l = useLoad(() => api<G[]>("/cohorts"));
  const a = useAction();
  const [n, setN] = useState({ name: "", program: "", funder: "" });
  return (<>
    <Section title="Groupes de coachés">
      <Notice error={l.error} />
      {l.data?.length === 0 && <p className="muted">Aucun groupe : créez-en un pour vos ateliers ou dispositifs financés.</p>}
      {l.data?.map(g => (
        <div key={g.id} className="adm-user adm-user-head">
          <div><a href={`/groupes/${g.id}`}><strong>{g.name}</strong></a><br />
            <span className="muted">{[g.program, g.funder].filter(Boolean).join(" · ") || "—"} · {g.members} participant{g.members > 1 ? "s" : ""}</span></div>
          <a className="btn ghost" href={`/groupes/${g.id}`}>Ouvrir</a>
        </div>
      ))}
    </Section>
    <Section title="Nouveau groupe">
      <Notice error={a.error} ok={a.ok} />
      <div className="adm-grid">
        <div><label htmlFor="g-n">Nom</label><input id="g-n" value={n.name} placeholder="SOLO BOOST — Groupe 2" onChange={e => setN({ ...n, name: e.target.value })} /></div>
        <div><label htmlFor="g-p">Programme</label><input id="g-p" value={n.program} placeholder="SOLO BOOST" onChange={e => setN({ ...n, program: e.target.value })} /></div>
        <div><label htmlFor="g-f">Financeur</label><input id="g-f" value={n.funder} placeholder="CTM / FSE+" onChange={e => setN({ ...n, funder: e.target.value })} /></div>
      </div>
      <p><button disabled={n.name.length < 2} onClick={() => a.run(() => api("/cohorts", { method: "POST",
        body: JSON.stringify({ name: n.name, program: n.program || null, funder: n.funder || null }) }).then(() => { setN({ name: "", program: "", funder: "" }); l.reload(); }), "Groupe créé")}>Créer le groupe</button></p>
    </Section>
  </>);
}

export default function Groupes() {
  const [ok, setOk] = useState(false);
  useEffect(() => { if (!getToken()) window.location.href = "/login"; else setOk(true); }, []);
  return <Shell label="Navigation principale" current="groupes" items={APP_NAV}><h1>Groupes</h1>{ok && <Body />}</Shell>;
}
