"""Synthèses en un clic : assemblage des sources, pseudonymisation, appel à Claude, indicateurs calculés.

Principes :
- Claude ne reçoit que le nécessaire : résumés Fathom, actions, extraits de transcription bornés, données de parcours.
- Pseudonymisation réversible : les noms connus (coachés, coach) sont remplacés par des repères avant l'envoi,
  puis restaurés dans le texte reçu.
- Les chiffres d'un rapport financeur (présences, heures, annulations) sont calculés par le code, jamais par le modèle.
- Le modèle a pour consigne de ne rien inventer et de signaler les manques.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, Cohort, CohortMember, Contact, Meeting, Organization, Plan, Synthesis, User

MAX_TRANSCRIPT_CHARS = 6000
MAX_PROMPT_CHARS = 150_000

SYSTEM = """Tu es l'assistant de rédaction de HUMANUM CONSULTING, cabinet de coaching, de conseil et de facilitation.
Tu rédiges en français professionnel, clair et bienveillant, des documents destinés à des coachs, des coachés et des financeurs publics.
Règles impératives :
- N'utilise QUE les informations fournies. N'invente aucun fait, chiffre, date, citation ni résultat.
- Quand une information manque, écris « Non renseigné dans les séances » plutôt que de supposer.
- Cite les séances par leur date (format JJ/MM/AAAA) quand tu t'y réfères.
- Les personnes apparaissent sous des repères entre crochets (ex. [COACHÉ 1]) : conserve ces repères tels quels.
- Ne mentionne aucune donnée de santé, de vie privée ou d'opinion qui n'est pas indispensable à l'objet du document.
- Réponds uniquement par le document, en Markdown, avec les titres demandés (## ...), sans préambule."""

TEMPLATES = {
    "parcours": """Rédige la SYNTHÈSE DE PARCOURS de [COACHÉ 1].
Titres attendus :
## Situation de départ
## Objectifs de l'accompagnement
## Séances réalisées (une ligne par séance : date, thème, apports)
## Progrès constatés
## Points de vigilance
## Actions en cours et prochaines étapes
## Recommandations du coach""",
    "groupe": """Rédige la SYNTHÈSE DE GROUPE de la cohorte décrite.
Titres attendus :
## Présentation du groupe
## Dynamique collective
## Thèmes travaillés
## Avancement individuel (un paragraphe court par participant, avec son repère)
## Réussites et points de blocage communs
## Recommandations pour la suite du programme""",
    "financeur": """Rédige le RAPPORT D'ACTIVITÉ destiné au financeur de la cohorte décrite.
Ton institutionnel, factuel. Les indicateurs chiffrés sont fournis : reprends-les, ne les recalcule pas.
Titres attendus :
## Rappel de l'action et de ses objectifs
## Public accompagné
## Déroulement et méthodes
## Indicateurs de réalisation (reprendre les chiffres fournis)
## Résultats et effets observés
## Difficultés rencontrées et ajustements
## Perspectives""",
}


@dataclass
class Pseudo:
    """Remplace les noms connus par des repères, puis les restaure."""
    names: dict[str, str] = field(default_factory=dict)    # nom réel (et variantes) → repère
    display: dict[str, str] = field(default_factory=dict)  # repère → nom affiché (première forme déclarée)

    def add(self, real: str | None, label: str) -> str:
        if real and real.strip():
            self.names[real.strip()] = label
            self.display.setdefault(label, real.strip())
        return label

    def hide(self, txt: str | None) -> str:
        out = txt or ""
        for real, label in sorted(self.names.items(), key=lambda kv: -len(kv[0])):
            out = re.sub(rf"\b{re.escape(real)}\b", label, out, flags=re.IGNORECASE)
        return out

    def show(self, txt: str) -> str:
        for label, real in self.display.items():
            txt = txt.replace(label, real)
        return txt


def _date(d: datetime | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "date inconnue"


def _meeting_block(m: Meeting, p: Pseudo) -> str:
    parts = [f"### Séance du {_date(m.started_at)} — {p.hide(m.title)}"]
    if m.summary_md:
        parts.append(p.hide(m.summary_md))
    if m.action_items:
        parts.append("Actions : " + "; ".join(p.hide(a.get("description")) + (" (faite)" if a.get("completed") else "")
                                                for a in m.action_items))
    if not m.summary_md and m.transcript:
        txt = " ".join(f"{p.hide(t.get('speaker'))} : {p.hide(t.get('text'))}" for t in m.transcript)
        parts.append("Extrait de transcription : " + txt[:MAX_TRANSCRIPT_CHARS])
    return "\n".join(parts)


async def _register_people(s: AsyncSession, p: Pseudo, contacts: list[Contact]) -> None:
    for i, c in enumerate(contacts, 1):
        label = f"[COACHÉ {i}]"
        p.add(f"{c.first_name} {c.last_name}", label)
        p.add(f"{c.last_name} {c.first_name}", label)
        if len(c.first_name) > 2:
            p.add(c.first_name, label)
        if len(c.last_name) > 2:
            p.add(c.last_name, label)


async def quota_left(s: AsyncSession) -> int | None:
    org = await s.scalar(select(Organization))
    limit = await s.scalar(select(Plan.ai_syntheses_per_month).where(Plan.code == org.plan)) if org else None
    if limit is None:
        return None
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = await s.scalar(select(func.count()).select_from(Synthesis).where(Synthesis.created_at >= start, Synthesis.status != "error"))
    return max(limit - (used or 0), 0)


async def build(s: AsyncSession, syn: Synthesis) -> tuple[str, Pseudo, dict, str]:
    """→ (prompt, pseudonymiseur, sources, annexe calculée)."""
    p = Pseudo()
    appendix = ""
    if syn.kind == "parcours":
        c = await s.get(Contact, syn.contact_id)
        await _register_people(s, p, [c])
        ms = (await s.scalars(select(Meeting).where(Meeting.contact_id == c.id).order_by(Meeting.started_at))).all()
        appts = (await s.scalars(select(Appointment).where(Appointment.contact_id == c.id).order_by(Appointment.starts_at))).all()
        for m in ms:
            coach = await s.get(User, m.coach_id)
            p.add(coach.full_name if coach else None, "[COACH]")
        body = [f"Coaché : [COACHÉ 1] — statut : {c.status or 'non renseigné'} — étape : {c.pipeline_stage}",
                f"Notes du coach : {p.hide(c.notes) or 'aucune'}",
                "Rendez-vous : " + ("; ".join(f"{_date(a.starts_at)} ({'annulé' if a.status == 'cancelled' else 'réalisé ou prévu'})"
                                               for a in appts) or "aucun"),
                "", "Séances enregistrées :", *([_meeting_block(m, p) for m in ms] or ["Aucune séance enregistrée."])]
        sources = {"meetings": [str(m.id) for m in ms], "appointments": len(appts)}
    else:
        co = await s.get(Cohort, syn.cohort_id)
        members = (await s.scalars(select(Contact).join(CohortMember, CohortMember.contact_id == Contact.id)
                                   .where(CohortMember.cohort_id == co.id).order_by(Contact.last_name))).all()
        await _register_people(s, p, list(members))
        body = [f"Cohorte : {co.name} — programme : {co.program or 'non renseigné'} — financeur : {co.funder or 'non renseigné'}",
                f"Période : {_date(co.starts_on)} au {_date(co.ends_on)} — {len(members)} participants", ""]
        rows, used = [], []
        for i, c in enumerate(members, 1):
            ms = (await s.scalars(select(Meeting).where(Meeting.contact_id == c.id).order_by(Meeting.started_at))).all()
            ap = (await s.scalars(select(Appointment).where(Appointment.contact_id == c.id))).all()
            done = [a for a in ap if a.status == "confirmed" and a.ends_at <= datetime.now(UTC)]
            hours = sum((a.ends_at - a.starts_at).total_seconds() for a in done) / 3600
            rows.append((c, len(done), len([a for a in ap if a.status == "cancelled"]), hours, len(ms)))
            body += [f"## Participant [COACHÉ {i}] — statut : {c.status or 'non renseigné'}",
                     *([_meeting_block(m, p) for m in ms] or ["Aucune séance enregistrée."]), ""]
            used += [str(m.id) for m in ms]
        total_done = sum(r[1] for r in rows)
        total_cancel = sum(r[2] for r in rows)
        indicators = (f"Indicateurs calculés : participants {len(members)} ; séances réalisées {total_done} ; "
                      f"heures d'accompagnement {sum(r[3] for r in rows):.1f} ; annulations {total_cancel} ; "
                      f"taux de réalisation {100 * total_done / max(total_done + total_cancel, 1):.0f} %.")
        body.insert(2, indicators)
        sources = {"meetings": used, "participants": len(members)}
        if syn.kind == "financeur":
            appendix = "\n\n## Annexe — suivi individuel (calculé par la plateforme)\n\n| Participant | Séances réalisées | Annulations | Heures |\n|---|---|---|---|\n" + \
                "\n".join(f"| {c.first_name} {c.last_name} | {d} | {x} | {h:.1f} |" for c, d, x, h, _ in rows)
    prompt = (TEMPLATES[syn.kind] + "\n\nSOURCES\n\n" + "\n".join(body))[:MAX_PROMPT_CHARS]
    return prompt, p, sources, appendix


