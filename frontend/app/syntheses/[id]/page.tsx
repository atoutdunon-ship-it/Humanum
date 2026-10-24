"use client";
import { useParams } from "next/navigation";
import Markdown from "@/components/Markdown";
import Shell from "@/components/Shell";
import { Notice, useAction, useLoad } from "@/components/admin/ui";
import { api } from "@/lib/api";
import { APP_NAV, KINDS } from "@/lib/nav";

type S = { id: string; kind: string; title: string; status: string; content_md: string | null; contact_id: string | null; cohort_id: string | null;
  created_at: string; model: string | null };

export default function Lecteur() {
  const { id } = useParams<{ id: string }>();
  const x = useLoad(() => api<S>(`/syntheses/${id}`), [id]);
  const a = useAction();
  const back = x.data?.contact_id ? `/contacts/${x.data.contact_id}` : x.data?.cohort_id ? `/groupes/${x.data.cohort_id}` : "/contacts";
  const download = () => {
    const blob = new Blob([`# ${x.data!.title}\n\n${x.data!.content_md}\n`], { type: "text/markdown" });
    const el = document.createElement("a"); el.href = URL.createObjectURL(blob); el.download = `${x.data!.title.replace(/[^\p{L}\p{N}]+/gu, "-")}.md`; el.click();
    URL.revokeObjectURL(el.href);
  };
  return (
    <Shell label="Navigation principale" current={x.data?.cohort_id ? "groupes" : "contacts"} items={APP_NAV}>
      <p className="no-print"><a href={back}>Retour</a></p>
      <Notice error={x.error ?? a.error} ok={a.ok} />
      {x.data && (
        <article className="card doc">
          <div className="doc-head">
            <img src="/logo-humanum.png" alt="HUMANUM CONSULTING" />
            <div><p className="eyebrow">{KINDS[x.data.kind]}</p><h1>{x.data.title.split(" — ").slice(1, -1).join(" — ") || x.data.title}</h1>
              <p className="muted">Rédigé le {new Date(x.data.created_at).toLocaleDateString("fr-FR")} à partir des séances enregistrées — à relire et valider par le coach avant diffusion.</p></div>
          </div>
          {x.data.content_md ? <Markdown text={x.data.content_md} /> : <p className="muted">Synthèse en cours de rédaction.</p>}
          <div className="mfa-actions no-print">
            <button onClick={() => window.print()}>Imprimer ou enregistrer en PDF</button>
            <button className="ghost" onClick={download}>Télécharger (.md)</button>
            <button className="ghost" onClick={() => a.run(() => navigator.clipboard.writeText(x.data!.content_md ?? ""), "Texte copié")}>Copier le texte</button>
          </div>
        </article>
      )}
    </Shell>
  );
}
