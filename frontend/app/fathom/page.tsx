"use client";
import { useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { Notice, Section, useAction, useLoad } from "@/components/admin/ui";
import { api, getToken } from "@/lib/api";
import { APP_NAV } from "@/lib/nav";

type St = { connected: boolean; fathom_email: string | null; status: string | null; last_error: string | null; last_sync_at: string | null; meetings: number };
type M = { id: string; title: string; started_at: string | null; invitees: { name: string | null; email: string | null; is_external: boolean }[] };
type C = { id: string; first_name: string; last_name: string };

function Body() {
  const st = useLoad(() => api<St>("/fathom"));
  const un = useLoad(() => api<M[]>("/fathom/meetings?unmatched=true"));
  const contacts = useLoad(() => api<C[]>("/contacts?limit=200"));
  const a = useAction();
  const [f, setF] = useState({ api_key: "", fathom_email: "" });
  return (<>
    <Section title="Compte Fathom" actions={st.data && <span className={`pill${st.data.connected ? " on" : ""}`}>{st.data.connected ? "Connecté" : "Non connecté"}</span>}>
      <Notice error={a.error ?? st.error} ok={a.ok} />
      {st.data?.connected ? (<>
        <p>Compte : <strong>{st.data.fathom_email}</strong> · {st.data.meetings} séance{st.data.meetings > 1 ? "s" : ""} importée{st.data.meetings > 1 ? "s" : ""}
          {st.data.last_sync_at && <> · dernière synchronisation le {new Date(st.data.last_sync_at).toLocaleString("fr-FR")}</>}</p>
        {st.data.status === "error" && <p className="banner" role="status">Fathom a refusé l'accès : {st.data.last_error}. Reconnectez le compte avec une nouvelle clé.</p>}
        <p>Chaque nouvel enregistrement arrive automatiquement à la fin de la réunion (résumé, actions, transcription) et se range dans la fiche du coaché.</p>
        <div className="mfa-actions">
          <button className="ghost" onClick={() => a.run(() => api("/fathom/sync", { method: "POST" }).then(() => setTimeout(() => { st.reload(); un.reload(); }, 4000)), "Synchronisation lancée")}>Synchroniser l'historique</button>
          <button className="ghost" onClick={() => a.run(() => api("/fathom", { method: "DELETE" }).then(st.reload), "Fathom déconnecté : les séances déjà importées sont conservées")}>Déconnecter</button>
        </div>
      </>) : (<>
        <p>Dans Fathom, ouvrez Réglages › Accès API, générez une clé et collez-la ici. La plateforme crée elle-même le webhook :
          vos enregistrements arriveront sans autre manipulation. La clé est chiffrée et n'est plus jamais affichée.</p>
        <div className="adm-grid">
          <div><label htmlFor="f-key">Clé d'API Fathom</label><input id="f-key" type="password" autoComplete="off" value={f.api_key} onChange={e => setF({ ...f, api_key: e.target.value })} /></div>
          <div><label htmlFor="f-mail">Adresse du compte Fathom (si différente)</label><input id="f-mail" type="email" value={f.fathom_email} onChange={e => setF({ ...f, fathom_email: e.target.value })} /></div>
        </div>
        <p><button disabled={f.api_key.length < 10} onClick={() => a.run(() => api("/fathom/connect", { method: "POST",
          body: JSON.stringify({ api_key: f.api_key, fathom_email: f.fathom_email || null }) }).then(() => { setF({ api_key: "", fathom_email: "" }); st.reload(); }),
          "Fathom connecté : import des 90 derniers jours en cours")}>Connecter Fathom</button></p>
      </>)}
    </Section>
    <Section title="Séances à rattacher">
      <p className="muted">Séances dont aucun invité ne correspond à un contact : choisissez le coaché concerné.</p>
      {un.data?.length === 0 && <p className="muted">Toutes les séances sont rattachées.</p>}
      {un.data?.map(m => (
        <div key={m.id} className="adm-user">
          <strong>{m.title}</strong><br /><span className="muted">{m.started_at ? new Date(m.started_at).toLocaleString("fr-FR") : "—"} ·
            {" "}{m.invitees.filter(i => i.is_external).map(i => i.name || i.email).join(", ") || "aucun invité externe"}</span>
          <div className="adm-inline">
            <select aria-label="Coaché concerné" defaultValue="" onChange={e => e.target.value && a.run(() => api(`/fathom/meetings/${m.id}`, { method: "PATCH",
              body: JSON.stringify({ contact_id: e.target.value }) }).then(un.reload), "Séance rattachée")}>
              <option value="">Rattacher à…</option>
              {contacts.data?.map(c => <option key={c.id} value={c.id}>{c.last_name} {c.first_name}</option>)}
            </select>
          </div>
        </div>
      ))}
    </Section>
  </>);
}

export default function Fathom() {
  const [ok, setOk] = useState(false);
  useEffect(() => { if (!getToken()) window.location.href = "/login"; else setOk(true); }, []);
  return <Shell label="Navigation principale" current="fathom" items={APP_NAV}><h1>Fathom</h1>{ok && <Body />}</Shell>;
}
