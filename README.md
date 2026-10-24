# HUMANUM CONSULTING — plateforme (socle · rendez-vous · Fathom et synthèses · espace coaché · facturation · pilotage)

Plateforme SaaS de gestion du coaching individuel et collectif : CRM relié à Fathom, planification, espace coaché,
pilotage, facturation et synthèses générées en un clic. Ce dépôt contient le **lot 0 : le socle**.
Le cahier des charges et le dossier d'architecture décrivent les lots suivants.

## Ce que contient le lot 0

| Brique | Contenu |
| --- | --- |
| API | FastAPI asynchrone, SQLAlchemy 2, routes `/api/v1` : administration, public, authentification, membres, contacts (CRM) |
| Administration | Panneau `/admin` en édition complète : organismes, accès, tarifs, options, textes, super-administrateurs, journal |
| Cloisonnement | PostgreSQL *Row Level Security* **forcée** sur toutes les tables métier ; contexte `app.current_org` fixé par transaction |
| Comptes | Argon2id, jetons JWT liés à **un** organisme, compte unique membre de plusieurs organismes |
| Double authentification | TOTP (RFC 6238) **obligatoire** pour les rôles d'encadrement (administrateur, coach, coordinateur) et la super-administration, facultative pour coachés et financeurs. Secret chiffré au repos (Fernet, rotation des clés), anti-rejeu, 10 codes de secours à usage unique, connexion en deux étapes (aucune donnée révélée avant le second facteur), réinitialisation par un autre super-administrateur, purge horaire des enrôlements abandonnés |
| Limitation des tentatives | Fenêtre glissante de 15 min **partagée dans Redis** entre toutes les instances (mot de passe, codes, activation), repli en mémoire si Redis tombe |
| File de tâches | Arq (Redis) : worker, sonde de bout en bout `/health/worker`, mise en file idempotente — prête pour Fathom, synthèses et rappels |
| Intégration continue | GitHub Actions : ruff, 63 tests avec PostgreSQL 16 et Redis 7, migrations aller-retour, typage, tests de navigation mobile, build |
| Rôles | `org_admin`, `coach`, `coordinator`, `coachee`, `funder` ; super-administration par comptes dédiés (`platform_admins`) |
| Traçabilité | Journal d'audit en **ajout seul** (aucun droit UPDATE ni DELETE pour l'application) |
| Migrations | Alembic, 0001 (socle, RLS), 0002 (administration, tarifs 59/89/149 €, options, textes), 0003 (double authentification), 0004 (prise de rendez-vous), 0005 (Fathom, groupes, synthèses), 0006 (espace coaché), 0007 (facturation), 0008 (TVA, promotions), 0009 (pilotage), 0010 (promotions ajustables), réversibles |
| Tests | 63 tests (dont 3 pour la TVA et les promotions, ajustement compris ; 1 pour le pilotage ; 6 pour la facturation : signature, publication et changement de prix, paiement, webhooks rejoués, factures, prorata, quotas, suspension, panne Stripe ; 4 pour l'espace coaché : invitation à usage unique, cloisonnement entre coachés et vis-à-vis de l'équipe, partage explicite, retrait d'accès ; 8 pour Fathom et les synthèses : signature, import paginé, idempotence, isolement, confidentialité entre coachs, pseudonymisation, indicateurs, quotas, reprise sur surcharge ; 11 pour le lot 2 : créneaux, changement d'heure, Meet, simultanéité, report, annulation, pannes Google, révocation, option, rôles, RLS) : étanchéité entre organismes (API **et** SQL direct), rôles, multi-organismes, administration, double authentification (rejeu, codes de secours, limitation), Redis et repli, aller-retour Arq |
| Frontend | Next.js 15 / React 19 : connexion avec choix d'organisme, liste des contacts, logo HUMANUM, **charte LAZR DECO** (Fraunces, DM Sans, encre, accent bleu, dégradé décoratif), contrastes audités ≥ 7:1 sur titres et menus |
| Responsive | Coque commune `components/Shell.tsx` : sous 900 px, barre « Vous consultez » + bouton Menu, tiroir avec voile, verrou du défilement, cinq voies de fermeture, cibles tactiles ≥ 44 px, champs à 16 px (pas de zoom iOS), tableaux convertis en fiches. `npm run test:nav` : 28 tests jsdom |

## Lot 2 — prise de rendez-vous Google Agenda + Google Meet

| Brique | Contenu |
| --- | --- |
| Agenda du coach | Connexion OAuth Google par coach (accès hors ligne), jetons chiffrés au repos ; lecture des **plages occupées uniquement** (freeBusy), jamais du contenu des événements ; révocation détectée et réservation fermée automatiquement |
| Disponibilités | Plages hebdomadaires, fuseau horaire du coach (changements d'heure gérés), délai de prévenance, horizon (4 mois par défaut), intervalle entre départs, battements avant/après |
| Réservation en ligne | Page publique `/r/<organisme>/<type>` sans compte : créneaux calculés sur les disponibilités, les rendez-vous et l'agenda Google réel ; consentement ; coaché ajouté au CRM (étape « prospect ») |
| Google Agenda + Meet | Événement créé avec lien Google Meet, invitation au coaché, rappels (e-mail la veille, notification une heure avant) ; identifiant d'événement déterministe : aucun doublon même en cas de nouvel envoi |
| Report / annulation | Lien personnel `/rdv/<jeton>` (jeton aléatoire de 256 bits, seule son empreinte sert à la recherche) ; le report conserve le même lien Meet ; Google prévient les participants |
| Fiabilité | Double réservation **impossible** : contrainte d'exclusion PostgreSQL sur la plage occupée du coach (testée avec des réservations simultanées et en SQL direct). Google indisponible : la réservation est garantie, l'envoi est rejoué par le worker, rattrapage toutes les 10 minutes |
| Cloisonnement | Cinq nouvelles tables sous RLS forcée ; les pages publiques ne franchissent la RLS que par deux fonctions qui renvoient un identifiant d'organisme ; option « Prise de rendez-vous Google » activable par organisme |

Configuration Google (une fois, console Google Cloud) : activer l'API Google Calendar, écran de consentement OAuth, identifiants
« Application Web » avec l'URI de redirection `https://<domaine>/api/v1/integrations/google/callback`, puis renseigner
`HC_GOOGLE_CLIENT_ID`, `HC_GOOGLE_CLIENT_SECRET`, `HC_GOOGLE_REDIRECT_URI` et `HC_PUBLIC_BASE_URL`. Les étendues demandées
(`calendar.events`, `calendar.freebusy`) imposent la vérification de l'application par Google avant l'ouverture au public.

Démonstration sans compte Google : `cd backend && python -m tests.demo_server` (Google simulé : agenda, événements et liens Meet fictifs).

## Fathom et synthèses en un clic

| Brique | Contenu |
| --- | --- |
| Compte Fathom par coach | Le coach colle sa clé d'API (vérifiée, chiffrée, jamais réaffichée) ; la plateforme crée elle-même le webhook Fathom limité à **ses** enregistrements, avec résumé, actions et transcription ; import des 90 derniers jours |
| Réception | Webhook à chemin secret par coach, signature « Standard Webhooks » vérifiée sur le corps brut (HMAC-SHA256), messages de plus de 5 minutes rejetés, enregistrement idempotent par `recording_id` |
| Rattachement | Chaque séance est rangée dans la fiche du coaché (e-mail des invités) et liée au rendez-vous correspondant (même coach, même invité, ±45 min) ; séances non reconnues rattachables à la main |
| Confidentialité | Un coach ne voit que ses séances ; administrateur et coordinateur voient tout l'organisme ; chaque lecture de transcription est journalisée |
| Groupes | Promotions, ateliers ou dispositifs financés (programme, financeur), participants issus du CRM |
| Synthèses en un clic | Synthèse de parcours (un coaché), synthèse de groupe, rapport financeur ; rédaction par Claude en arrière-plan, suivi en direct, lecture, impression / PDF, téléchargement |
| Garde-fous | **Pseudonymisation** : aucun nom réel n'est envoyé au modèle (repères [COACHÉ n], restaurés au retour) ; consignes « ne rien inventer, signaler les manques » ; les chiffres du rapport financeur (séances, heures, annulations, taux de réalisation) sont **calculés par la plateforme**, avec annexe nominative produite par le code ; quota mensuel par formule ; option et IA désactivables par organisme |

Variables : `HC_ANTHROPIC_API_KEY`, `HC_ANTHROPIC_MODEL` (par défaut `claude-sonnet-5-5`), `HC_PUBLIC_BASE_URL` (adresse publique
qui reçoit les webhooks Fathom). Démonstration complète sans comptes tiers : `cd backend && python -m tests.demo_server`
(Google, Fathom et Claude simulés, worker intégré ; clé Fathom de démonstration : `fk-cle-valide-du-coach`).

Traitement par Claude (Anthropic, États-Unis) : choix assumé par HUMANUM CONSULTING. À mentionner dans les contrats
d'accompagnement et au registre des traitements RGPD (sous-traitant ultérieur hors UE, transfert encadré par les clauses
contractuelles d'Anthropic). Les noms ne sont jamais transmis ; le contenu des séances l'est. Le rapport financeur conserve
une annexe nominative, produite par la plateforme et non par le modèle.

## Espace coaché

| Brique | Contenu |
| --- | --- |
| Invitation | Depuis la fiche du coaché : lien personnel à usage unique, valable 7 jours (empreinte seule en base) ; création du mot de passe, ou rattachement d'un compte existant avec son mot de passe actuel ; accès retirable à tout moment |
| Accueil | Prochain rendez-vous (Google Meet, report ou annulation), liens de prise de rendez-vous de son coach, état de son parcours |
| Contenus | Rendez-vous passés et à venir ; comptes rendus de séance **partagés explicitement** par le coach (résumé et actions, jamais la transcription) ; synthèses de parcours partagées (jamais une synthèse de groupe ni un rapport financeur) ; actions de parcours à cocher |
| Côté coach | Bloc « Espace coaché » (invitation, état, retrait), actions de parcours (visibles ou notes internes, échéances), reprise des actions détectées par Fathom, interrupteurs de partage |
| Cloisonnement | Le rôle « coaché » n'atteint que les routes `/me` et ne voit que SA fiche ; aucune route de l'équipe ne lui répond (testé route par route) ; double authentification facultative |

## Facturation Stripe

| Brique | Contenu |
| --- | --- |
| Formules | Starter 59 €, Pro 89 € (30 jours d'essai), Entreprise 149 € HT par mois et par organisme, éditables dans l'administration ; « Publier dans Stripe » crée produits et prix (prix immuables : un nouveau tarif crée un nouveau prix et archive l'ancien, les abonnements en cours gardent le leur) |
| Souscription | Page « Facturation » : l'administrateur de l'organisme choisit une formule et paie sur Stripe Checkout (carte, SEPA selon le compte), essai appliqué une seule fois, numéro de TVA intracommunautaire et adresse de facturation collectés, codes promotionnels acceptés |
| Gestion | Changement de formule au prorata, refusé s'il descend sous l'usage réel (coachs, espaces coachés) ; portail client Stripe pour le moyen de paiement, les factures et la résiliation |
| Webhooks | Signature `Stripe-Signature` vérifiée (HMAC-SHA256, 5 minutes de tolérance), idempotence par identifiant d'événement ; Stripe fait foi : statut, formule, échéances et factures ne changent que sur événement signé |
| Quotas et suspension | Nombre de coachs et d'espaces coachés actifs plafonné par formule ; abonnement résilié ou impayé : synthèses et invitations suspendues, données toujours consultables ; organismes hors Stripe (partenaires) non concernés |
| Factures | Copie locale (lecture seule pour l'organisme, sous RLS), liens PDF Stripe |

Configuration : `HC_STRIPE_SECRET_KEY`, `HC_STRIPE_WEBHOOK_SECRET`, point de terminaison Stripe
`https://<domaine>/api/v1/integrations/stripe/webhook` (événements `checkout.session.completed`, `customer.subscription.*`,
`invoice.*`), portail client activé dans le tableau de bord Stripe. `HC_STRIPE_AUTOMATIC_TAX=true` si Stripe Tax est activé
(TVA DOM 8,5 %, métropole 20 %).

### TVA optionnelle et promotions

| Brique | Contenu |
| --- | --- |
| TVA | Tarifs hors TVA par défaut. Onglet « Promotions et TVA » : activation de la TVA à 8,5 % (Martinique), publiée comme taux Stripe et appliquée aux nouveaux abonnements ; « Appliquer aux abonnements en cours » aligne les abonnements existants ; la page Facturation affiche alors le montant TTC |
| Promotions | Codes créés par l'administrateur : pourcentage ou montant, première facture / plusieurs mois / sans limite, formules visées, plafond d'utilisations, date de fin, réservé aux nouveaux clients ; publiés dans Stripe (coupon + code) ; contrôle du code par l'organisme avant paiement ; geste commercial sur un abonnement existant ; utilisations relues dans Stripe |
| Ajustement | Toutes les conditions d'une promotion publiée restent modifiables : la plateforme publie une nouvelle version sous le **même code** (Stripe rend remises et limites immuables), retire l'ancienne, cumule les utilisations dans le plafond, et rétablit l'ancienne version si la publication échoue ; les abonnements déjà remisés gardent leur remise |

## Pilotage

| Brique | Contenu |
| --- | --- |
| Organisme | Page « Pilotage » sur 30 jours, 90 jours ou 12 mois : séances réalisées et heures, coachés actifs, nouveaux contacts, annulations, part des réservations en ligne, activité hebdomadaire, parcours par étape et conversion, production (séances Fathom rattachées, synthèses), actions ; vue par coach ; export CSV |
| Périmètre | Administrateur et coordinateur : tout l'organisme ; coach : sa propre activité |
| Plateforme | Onglet « Pilotage » de l'administration : revenu mensuel récurrent, abonnements par statut, usage par organisme — **comptages uniquement**, par une fonction dédiée : le rôle plateforme n'accède toujours à aucun contenu de coaching |

## Principe de sécurité central

L'application se connecte avec le rôle `humanum_app`, **ni superuser ni BYPASSRLS** : sinon PostgreSQL ignorerait la RLS.
Le panneau d'administration utilise un second rôle, `humanum_platform` : il voit tous les organismes, mais n'a **aucun droit**
sur les données de coaching (contacts, puis séances et synthèses) — un test le vérifie.
Aucune requête de l'API ne filtre sur `organization_id` : c'est la base qui l'impose. Une requête sans contexte
d'organisme ne renvoie rien. Une écriture vers un autre organisme est refusée par PostgreSQL, même en SQL direct.
Les tests le prouvent, et ils échouent si l'on retire la RLS d'une table (vérifié).

## Lancer sur un Mac (mode démonstration)

1. Installer et ouvrir **Docker Desktop** (https://www.docker.com/products/docker-desktop/).
2. Double-cliquer sur **« Lancer HUMANUM.command »** (au premier lancement, si macOS bloque le fichier : clic droit › Ouvrir).
   Le script crée des secrets locaux (`backend/.env`), construit et démarre la plateforme (quelques minutes la première fois),
   puis demande le mot de passe initial du compte **ADMIN** — il est saisi au clavier, jamais enregistré en clair, et devra
   être changé à la première connexion.
3. Le navigateur s'ouvre sur http://localhost:3000/admin/login. Créez un organisme dans l'onglet « Organismes » (vous fixez
   l'e-mail et le mot de passe de son administrateur), puis connectez-vous à cet organisme sur http://localhost:3000/login.
4. **« Arrêter HUMANUM.command »** arrête tout ; les données sont conservées pour le lancement suivant.

En démonstration, Google Agenda, Fathom, Stripe et Claude sont **simulés** : connexion d'agenda en un clic, paiement fictif
(aucune carte), clé Fathom de démonstration `fk-cle-valide-du-coach`, synthèses factices. La double authentification y est
facultative (`HC_MFA_ENFORCED=true` dans `backend/.env` pour l'imposer). Pour la production : `docker compose up -d` avec
`docker-compose.yml` seul et les vraies clés.

## Démarrage (développement)

```bash
cp backend/.env.example backend/.env        # renseigner HC_JWT_SECRET
docker compose up -d db redis
# HC_MFA_KEYS : générer une clé Fernet (voir .env.example) ; HC_REDIS_URL=redis://127.0.0.1:6379/0
cd backend && pip install -e ".[dev]"
psql -h 127.0.0.1 -p 5433 -U postgres -f scripts/bootstrap_db.sql   # rôles humanum_app et humanum_platform (une fois)
alembic upgrade head
uvicorn app.main:app --reload               # http://127.0.0.1:8000/docs
arq app.worker.WorkerSettings               # worker (autre terminal)
cd ../frontend && npm install && npm run dev # http://127.0.0.1:3000
```

Créer le compte de super-administration (le mot de passe initial est demandé au clavier, jamais stocké en clair) :

```bash
cd backend && python -m app.cli create-superadmin --username ADMIN
```

Puis ouvrir http://127.0.0.1:3000/admin/login : à la première connexion, le changement du mot de passe est imposé
(12 caractères minimum, lettres et chiffres), puis l'activation de la double authentification (QR code, codes de secours). Les organismes se créent ensuite depuis l'onglet « Organismes ».
`--reset` réinitialise le mot de passe **et** la double authentification d'un compte existant (accès de secours depuis le serveur).
Hors `HC_ENV=dev`, l'API refuse de démarrer avec les secrets de démonstration (`HC_JWT_SECRET`, `HC_MFA_KEYS`).

Tests : `cd backend && pytest` (PostgreSQL 16 sur 127.0.0.1:5433 et Redis sur 127.0.0.1:6379 base 15 ; variables `HC_TEST_PG`, `HC_TEST_REDIS`).

Les migrations s'exécutent avec un rôle propriétaire superutilisateur ou `BYPASSRLS` : les fonctions publiques
(`hc_public_org`, `hc_appointment_org`, `hc_pending_syncs`) en héritent pour lire l'identifiant d'organisme, et rien d'autre.

## Ajouter une table métier (règle pour tous les lots)

1. Hériter de `TenantMixin` : `organization_id` est alors renseigné depuis le contexte de session.
2. Dans la migration : `ENABLE` et `FORCE ROW LEVEL SECURITY`, politique `tenant_isolation`, `GRANT` à `humanum_app`.
3. Ajouter la table à `TENANT_TABLES` et un test d'étanchéité.

## Prochaines étapes

- Lot 0 : terminé.
- Lot 1 : CRM complet (organismes prescripteurs, pipeline, tâches, historique).
- Lot 2 : terminé.
- Intégration Fathom et synthèses : terminées.
- Espace coaché : terminé.
- Facturation Stripe : terminée.
- TVA optionnelle, promotions et pilotage : terminés.
