/** Navigation de l'espace organisme (coque NAV-PREMIUM). */
export const APP_NAV = [
  { id: "pilotage", label: "Pilotage", href: "/pilotage" },
  { id: "contacts", label: "CRM — Contacts", href: "/contacts" },
  { id: "groupes", label: "Groupes", href: "/groupes" },
  { id: "agenda", label: "Agenda et rendez-vous", href: "/agenda" },
  { id: "fathom", label: "Fathom", href: "/fathom" },
  { id: "facturation", label: "Facturation", href: "/facturation" },
  { id: "securite", label: "Sécurité du compte", href: "/securite" },
];

export const fmt = {
  day: (iso: string, tz: string) => new Intl.DateTimeFormat("fr-FR", { timeZone: tz, weekday: "long", day: "numeric", month: "long" }).format(new Date(iso)),
  time: (iso: string, tz: string) => new Intl.DateTimeFormat("fr-FR", { timeZone: tz, hour: "2-digit", minute: "2-digit" }).format(new Date(iso)),
  full: (iso: string, tz: string) => new Intl.DateTimeFormat("fr-FR", { timeZone: tz, weekday: "long", day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(iso)),
  dateKey: (d: Date) => d.toISOString().slice(0, 10),
};

export const STAGES: Record<string, string> = { prospect: "Prospect", devis: "Devis", contractualise: "Contractualisé", en_cours: "En cours", termine: "Terminé" };
export const KINDS: Record<string, string> = { parcours: "Synthèse de parcours", groupe: "Synthèse de groupe", financeur: "Rapport financeur" };
export const SYN_STATUS: Record<string, string> = { pending: "En file d'attente", running: "Rédaction en cours", done: "Prête", error: "Échec" };

/** Navigation de l'espace coaché. */
export const ME_NAV = [
  { id: "accueil", label: "Accueil" },
  { id: "rendez-vous", label: "Mes rendez-vous" },
  { id: "seances", label: "Mes séances" },
  { id: "actions", label: "Mes actions" },
  { id: "documents", label: "Mes documents" },
  { id: "securite", label: "Sécurité du compte", href: "/securite" },
];
