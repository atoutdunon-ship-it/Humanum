"use client";
import { useEffect, useState } from "react";
import { adminApi, clearAdminToken, getAdminToken } from "@/lib/admin";
import Shell from "@/components/Shell";
import { AccessTab, AdminsTab, AuditTab, FeaturesTab, OrgsTab, PlansTab, PlatformBoardTab, PromotionsTab, SecurityTab, TextsTab } from "@/components/admin/tabs";

const TABS = [
  ["orgs", "Organismes", OrgsTab], ["pilotage", "Pilotage", PlatformBoardTab], ["access", "Accès", AccessTab], ["plans", "Tarifs", PlansTab], ["promotions", "Promotions et TVA", PromotionsTab],
  ["features", "Options", FeaturesTab], ["texts", "Textes", TextsTab], ["admins", "Administrateurs", AdminsTab], ["security", "Ma sécurité", SecurityTab], ["audit", "Journal", AuditTab],
] as const;

export default function Admin() {
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("orgs");
  const [me, setMe] = useState<{ username: string; must_change_password: boolean } | null>(null);
  useEffect(() => {
    if (!getAdminToken()) { window.location.href = "/admin/login"; return; }
    adminApi<{ username: string; must_change_password: boolean }>("/me").then(async m => {
      if (m.must_change_password) { window.location.href = "/admin/login"; return; }
      const st = await adminApi<{ required: boolean; enabled: boolean }>("/auth/mfa");
      if (st.required && !st.enabled) window.location.href = "/admin/login"; else setMe(m);
    }).catch(() => (window.location.href = "/admin/login"));
  }, []);
  useEffect(() => {  // onglet demandé par l'adresse ; onglet inconnu -> retour à « Organismes »
    const h = window.location.hash.slice(1);
    if (h) setTab((TABS.some(t => t[0] === h) ? h : "orgs") as typeof tab);
  }, []);
  if (!me) return null;
  const active = TABS.find(t => t[0] === tab) ?? TABS[0];
  const Current = active[2];
  return (
    <Shell label="Administration" current={tab} items={TABS.map(([id, label]) => ({ id, label }))}
      onNavigate={id => { setTab(id as typeof tab); history.replaceState(null, "", `#${id}`); }}
      footer={<><p className="side-user">{me.username}</p><a href="/admin/login" onClick={() => clearAdminToken()}>Se déconnecter</a></>}>
      <h1>{active[1]}</h1><Current />
    </Shell>
  );
}
