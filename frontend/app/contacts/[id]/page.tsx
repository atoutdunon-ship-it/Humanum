"use client";
import { useParams } from "next/navigation";
import { useState } from "react";
import Markdown from "@/components/Markdown";
import Shell from "@/components/Shell";
import Syntheses from "@/components/Syntheses";
import { Notice, Section, Toggle, useAction, useLoad } from "@/components/admin/ui";
import { api } from "@/lib/api";
import { APP_NAV, STAGES } from "@/lib/nav";

type C = { id: string; first_name: string; last_name: string; email: string | null; phone: string | null; status: string | null; pipeline_stage: string; notes: string | null };
type M = { id: string; title: string; started_at: string | null; share_url: string | null; summary_md: string | null; action_items: { description: string; completed: boolean }[]; shared_with_coachee: boolean };
type Sp = { status: "none" | "invited" | "active"; email?: string; expires_at?: string };
type T = { id: string; title: string; notes: string | null; due_on: string | null; done: boolean; visible_to_coachee: boolean; source_meeting_id: string | null };

function Space({ contactId, hasEmail }: { contactId: string; hasEmail: boolean }) {
  const sp = useLoad(() => api<Sp>(`/contacts/${contactId}/space`), [contactId]);
  const a = useAction();
  const [link, setLink] = useState<string | null>(null);
  const label = { none: "Pas d'accès", invited: "Invitation envoyée", active: "Espace actif" }[sp.data?.status ?? "none"];
  return (
    <Section title="Espace coaché" actions={sp.data && <span className={`pill${sp.data.status === "active" ? " on" : ""}`}>{label}</span>}>
      <Notice error={a.error ?? sp.error} ok={a.ok} />
      <p className="muted">Le coaché y retrouve ses rendez-vous, les comptes rendus et les synthèses que vous partagez, et ses actions à cocher. Rien n'y apparaît sans votre partage explicite.</p>
      {sp.data?.status === "invited" && <p>Invitation en attente pour <strong>{sp.data.email}</strong>, valable jusqu'au {new Date(sp.data.expires_at!).toLocaleDateString("fr-FR")}.</p>}
      {link && (<div className="adm-inline">
        <input readOnly aria-label="Lien d'invitation" value={link} onFocus={e => e.currentTarget.select()} />
        <button className="ghost" onClick={() => a.run(() => navigator.clipboard.writeText(link), "Lien copié : transmettez-le au coaché")}>Copier le lien</button>
      </div>)}
      <div className="mfa-actions">
        {sp.data?.status !== "active" && <button disabled={!hasEmail} onClick={() => a.run(async () => {
          setLink((await api<{ invite_url: string }>(`/contacts/${contactId}/invite`, { method: "POST" })).invite_url); sp.reload();
        }, "Invitation créée (valable 7 jours, usage unique)")}>{sp.data?.status === "invited" ? "Générer un nouveau lien" : "Inviter dans son espace"}</button>}
        {sp.data?.status !== "none" && <button className="ghost" onClick={() => a.run(() => api(`/contacts/${contactId}/space`, { method: "DELETE" }).then(() => { setLink(null); sp.reload(); }),
          "Accès retiré")}>Retirer l'accès</button>}
      </div>
      {!hasEmail && <p className="muted">Renseignez l'e-mail du coaché pour l'inviter.</p>}
    </Section>
  );
}

function Tasks({ contactId, version }: { contactId: string; version: number }) {
  const l = useLoad(() => api<T[]>(`/contacts/${contactId}/tasks`), [contactId, version]);
  const a = useAction();
  const [n, setN] = useState({ title: "", due_on: "" });
  return (
    <Section title="Actions de parcours">
      <Notice error={a.error ?? l.error} ok={a.ok} />
      {l.data?.length === 0 && <p className="muted">Aucune action. Ajoutez-en ou reprenez celles détectées par Fathom dans une séance.</p>}
      <ul className="tasks">{l.data?.map(t => (
        <li key={t.id} className={t.done ? "done" : ""}>
          <label className="check"><input type="checkbox" checked={t.done} onChange={e => a.run(() => api(`/tasks/${t.id}`, { method: "PATCH", body: JSON.stringify({ done: e.target.checked }) }).then(l.reload), "Action mise à jour")} />
            <span><strong>{t.title}</strong>{t.due_on && <span className="muted"> · avant le {new Date(t.due_on).toLocaleDateString("fr-FR")}</span>}
              <span className="muted">{t.visible_to_coachee ? " · visible par le coaché" : " · note interne"}</span></span></label>
          <button className="ghost" onClick={() => a.run(() => api(`/tasks/${t.id}`, { method: "DELETE" }).then(l.reload), "Action supprimée")}>Supprimer</button>
        </li>))}</ul>
      <div className="adm-inline">
        <input aria-label="Nouvelle action" placeholder="Nouvelle action (ex. finaliser la grille tarifaire)" value={n.title} onChange={e => setN({ ...n, title: e.target.value })} />
        <input type="date" aria-label="Échéance" value={n.due_on} onChange={e => setN({ ...n, due_on: e.target.value })} />
        <button disabled={n.title.length < 2} onClick={() => a.run(() => api(`/contacts/${contactId}/tasks`, { method: "POST", body: JSON.stringify({
          title: n.title, due_on: n.due_on ? new Date(`${n.due_on}T12:00:00`).toISOString() : null }) }).then(() => { setN({ title: "", due_on: "" }); l.reload(); }), "Action ajoutée")}>Ajouter</button>
      </div>
    </Section>
  );
}

function Meetings({ contactId, onTasks }: { contactId: string; onTasks: () => void }) {
  const l = useLoad(() => api<M[]>(`/fathom/meetings?contact_id=${contactId}`), [contactId]);
  const a = useAction();
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Section title="Séances enregistrées (Fathom)">
      <Notice error={a.error ?? l.error} ok={a.ok} />
      {l.data?.length === 0 && <p className="muted">Aucune séance enregistrée pour ce coaché.</p>}
      {l.data?.map(m => (
        <div key={m.id} className="adm-user">
          <div className="adm-user-head">
            <div><strong>{m.started_at ? new Date(m.started_at).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long", year: "numeric" }) : "Date inconnue"}</strong>
              <br /><span className="muted">{m.title}</span></div>
            <div className="appt-actions">
              <button className="ghost" aria-expanded={open === m.id} onClick={() => setOpen(open === m.id ? null : m.id)}>{open === m.id ? "Masquer" : "Résumé"}</button>
              {m.share_url && <a className="btn ghost" href={m.share_url} target="_blank" rel="noopener noreferrer">Fathom</a>}
            </div>
          </div>
          {open === m.id && (<div className="meeting-body">
            {m.summary_md ? <Markdown text={m.summary_md} /> : <p className="muted">Pas de résumé Fathom.</p>}
            {m.action_items.length > 0 && <><h4>Actions</h4><ul>{m.action_items.map((x, i) => <li key={i}>{x.completed ? "Faite — " : "À faire — "}{x.description}</li>)}</ul>
              <p><button className="ghost" onClick={() => a.run(() => api(`/fathom/meetings/${m.id}/tasks`, { method: "POST" }).then(onTasks), "Actions reprises dans le parcours")}>Reprendre ces actions dans le parcours</button></p></>}
          </div>)}
          <Toggle label="Compte rendu partagé au coaché (résumé et actions, jamais la transcription)" checked={m.shared_with_coachee}
            onChange={v => a.run(() => api(`/fathom/meetings/${m.id}/share`, { method: "POST", body: JSON.stringify({ shared: v }) }).then(l.reload),
              v ? "Compte rendu visible dans l'espace du coaché" : "Compte rendu retiré de l'espace du coaché")} />
        </div>
      ))}
    </Section>
  );
}

export default function Fiche() {
  const { id } = useParams<{ id: string }>();
  const c = useLoad(() => api<C>(`/contacts/${id}`), [id]);
  const [tv, setTv] = useState(0);
  return (
    <Shell label="Navigation principale" current="contacts" items={APP_NAV}>
      <p><a href="/contacts">Retour aux contacts</a></p>
      <h1>{c.data ? `${c.data.first_name} ${c.data.last_name}` : "Fiche coaché"}</h1>
      <Notice error={c.error} />
      {c.data && (<>
        <Section title="Informations">
          <dl className="facts">
            <dt>E-mail</dt><dd>{c.data.email ?? "—"}</dd>
            <dt>Téléphone</dt><dd>{c.data.phone ?? "—"}</dd>
            <dt>Statut</dt><dd>{c.data.status ?? "—"}</dd>
            <dt>Étape</dt><dd>{STAGES[c.data.pipeline_stage] ?? c.data.pipeline_stage}</dd>
          </dl>
          {c.data.notes && <p className="muted">{c.data.notes}</p>}
        </Section>
        <Space contactId={id} hasEmail={!!c.data.email} />
        <Section title="Synthèse en un clic"><Syntheses subject={{ contact_id: id }} kinds={["parcours"]} /></Section>
        <Tasks contactId={id} version={tv} />
        <Meetings contactId={id} onTasks={() => setTv(tv + 1)} />
      </>)}
    </Shell>
  );
}
