"use client";
/** Liste des synthèses d'un sujet (coaché ou groupe) + boutons de génération « en un clic », avec suivi automatique. */
import { useEffect, useState } from "react";
import { Notice, Toggle, useAction, useLoad } from "@/components/admin/ui";
import { api } from "@/lib/api";
import { KINDS, SYN_STATUS } from "@/lib/nav";

type S = { id: string; kind: string; title: string; status: string; created_at: string; error: string | null; shared_with_coachee: boolean };

export default function Syntheses({ subject, kinds }: { subject: { contact_id?: string; cohort_id?: string }; kinds: string[] }) {
  const qs = new URLSearchParams(subject as Record<string, string>).toString();
  const l = useLoad(() => api<S[]>(`/syntheses?${qs}`), [qs]);
  const quota = useLoad(() => api<{ left: number | null; enabled: boolean }>("/syntheses/quota"));
  const a = useAction();
  const pending = l.data?.some(x => x.status === "pending" || x.status === "running");
  useEffect(() => {  // suivi tant qu'une rédaction est en cours
    if (!pending) return;
    const t = setInterval(l.reload, 3000);
    return () => clearInterval(t);
  }, [pending, l.reload]);
  return (
    <div>
      <Notice error={a.error ?? l.error} ok={a.ok} />
      {quota.data && !quota.data.enabled && <p className="muted">Les synthèses ne sont pas activées pour votre organisme.</p>}
      <div className="mfa-actions">
        {kinds.map(k => (
          <button key={k} disabled={!quota.data?.enabled || quota.data.left === 0} onClick={() => a.run(async () => {
            await api("/syntheses", { method: "POST", body: JSON.stringify({ kind: k, ...subject }) }); l.reload(); quota.reload();
          }, "Demande enregistrée : la rédaction prend en général moins d'une minute")}>Générer : {KINDS[k]}</button>
        ))}
      </div>
      {quota.data?.left != null && <p className="muted">Synthèses restantes ce mois-ci : {quota.data.left}</p>}
      {l.data?.length === 0 && <p className="muted">Aucune synthèse pour l'instant.</p>}
      {l.data?.map(x => (
        <div key={x.id} className="adm-user syn-row">
          <div><strong>{x.title}</strong><br /><span className="muted">{new Date(x.created_at).toLocaleString("fr-FR")}</span>
            {x.error && <><br /><span className="err">{x.error}</span></>}</div>
          <div className="appt-actions">
            <span className={`pill${x.status === "done" ? " on" : ""}`}>{SYN_STATUS[x.status] ?? x.status}</span>
            {x.status === "done" && x.kind === "parcours" && <Toggle label="Partagée au coaché" checked={x.shared_with_coachee}
              onChange={v => a.run(() => api(`/syntheses/${x.id}/share`, { method: "POST", body: JSON.stringify({ shared: v }) }).then(l.reload),
                v ? "Synthèse visible dans l'espace du coaché" : "Synthèse retirée de l'espace du coaché")} />}
            {x.status === "done" && <a className="btn" href={`/syntheses/${x.id}`}>Ouvrir</a>}
          </div>
        </div>
      ))}
    </div>
  );
}
