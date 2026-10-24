"use client";
import { useEffect, useState } from "react";
import { Notice, Section, useAction, useLoad } from "@/components/admin/ui";
import Shell from "@/components/Shell";
import { api, getToken } from "@/lib/api";
import { APP_NAV, STAGES } from "@/lib/nav";

type Contact = { id: string; first_name: string; last_name: string; email: string | null; status: string | null; pipeline_stage: string };

function List() {
  const [q, setQ] = useState("");
  const rows = useLoad(() => api<Contact[]>(`/contacts${q ? `?q=${encodeURIComponent(q)}` : ""}`), [q]);
  const a = useAction();
  const [n, setN] = useState({ first_name: "", last_name: "", email: "", status: "" });
  return (<>
    <Section title="Coachés et contacts" actions={<input className="adm-search" placeholder="Rechercher" aria-label="Rechercher un contact" value={q} onChange={e => setQ(e.target.value)} />}>
      <Notice error={rows.error} />
      {rows.data?.length === 0 && <p className="muted">Aucun contact.</p>}
      {rows.data && rows.data.length > 0 && (
        <table>
          <thead><tr><th>Nom</th><th>E-mail</th><th>Statut</th><th>Étape</th></tr></thead>
          <tbody>{rows.data.map(c => (
            <tr key={c.id}><td><a href={`/contacts/${c.id}`}><strong>{c.last_name} {c.first_name}</strong></a></td><td>{c.email ?? "—"}</td>
              <td>{c.status ?? "—"}</td><td>{STAGES[c.pipeline_stage] ?? c.pipeline_stage}</td></tr>
          ))}</tbody>
        </table>
      )}
    </Section>
    <Section title="Nouveau contact">
      <Notice error={a.error} ok={a.ok} />
      <div className="adm-grid">
        <div><label htmlFor="c-fn">Prénom</label><input id="c-fn" value={n.first_name} onChange={e => setN({ ...n, first_name: e.target.value })} /></div>
        <div><label htmlFor="c-ln">Nom</label><input id="c-ln" value={n.last_name} onChange={e => setN({ ...n, last_name: e.target.value })} /></div>
        <div><label htmlFor="c-em">E-mail</label><input id="c-em" type="email" value={n.email} onChange={e => setN({ ...n, email: e.target.value })} /></div>
        <div><label htmlFor="c-st">Statut</label>
          <select id="c-st" value={n.status} onChange={e => setN({ ...n, status: e.target.value })}>
            <option value="">—</option><option>porteur d'idée</option><option>porteur de projet</option><option>indépendant</option></select></div>
      </div>
      <p><button disabled={!n.first_name || !n.last_name} onClick={() => a.run(() => api("/contacts", { method: "POST",
        body: JSON.stringify({ ...n, email: n.email || null, status: n.status || null }) }).then(() => { setN({ first_name: "", last_name: "", email: "", status: "" }); rows.reload(); }), "Contact créé")}>Créer le contact</button></p>
    </Section>
  </>);
}

export default function Contacts() {
  const [ok, setOk] = useState(false);
  useEffect(() => { if (!getToken()) window.location.href = "/login"; else setOk(true); }, []);
  return (
    <Shell label="Navigation principale" current="contacts" items={APP_NAV}>
      <h1>Contacts</h1>
      {ok && <List />}
    </Shell>
  );
}
