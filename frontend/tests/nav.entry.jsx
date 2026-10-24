import React, { useState, act } from "react";
import { createRoot } from "react-dom/client";
import Shell from "../components/Shell.tsx";
const TABS=[["orgs","Organismes"],["access","Accès"],["plans","Tarifs"],["features","Options"],["texts","Textes"],["admins","Administrateurs"],["audit","Journal"]];
function App(){ const [tab,setTab]=useState("orgs");
  const t=TABS.find(x=>x[0]===tab)??TABS[0];
  return <Shell label="Administration" current={t[0]} items={TABS.map(([id,label])=>({id,label}))} onNavigate={id=>setTab(TABS.some(x=>x[0]===id)?id:"orgs")}>
    <h1>{t[1]}</h1><table><thead><tr><th>Nom</th><th>Formule</th></tr></thead><tbody><tr><td>A</td><td>Pro</td></tr><tr><td colSpan={2}>vide</td></tr></tbody></table></Shell>;}
window.__run = async () => {
  const r=[]; const ok=(n,c)=>r.push([n,!!c]); const $=s=>document.querySelector(s);
  globalThis.IS_REACT_ACT_ENVIRONMENT=true;
  const root=createRoot(document.getElementById("root"));
  await act(async()=>root.render(<App/>));
  const btn=$(".mb-btn"), nav=$("#sidebar"), bd=$(".backdrop");
  ok("barre de contexte présente",$(".mobilebar")); ok("voile présent",bd); ok("en-tête de tiroir présent",$(".drawer-head"));
  ok("un seul bouton d'ouverture",document.querySelectorAll("[aria-controls=sidebar]").length===1);
  const open=async()=>act(async()=>btn.click());
  const isOpen=()=>nav.classList.contains("open")&&bd.classList.contains("on")&&document.body.classList.contains("nav-open")&&btn.getAttribute("aria-expanded")==="true"&&btn.textContent==="Fermer";
  const isClosed=()=>!nav.classList.contains("open")&&!bd.classList.contains("on")&&!document.body.classList.contains("nav-open")&&btn.getAttribute("aria-expanded")==="false"&&btn.textContent==="Menu";
  ok("état initial fermé",isClosed());
  await open(); ok("ouverture : classe, voile, verrou, aria, libellé",isOpen()); ok("focus sur l'entrée active",document.activeElement===$("#sidebar a[aria-current=page]"));
  await act(async()=>btn.click()); ok("fermeture : bouton Menu/Fermer",isClosed());
  await open(); await act(async()=>$(".dh-x").click()); ok("fermeture : bouton Fermer du tiroir",isClosed());
  await open(); await act(async()=>bd.click()); ok("fermeture : voile",isClosed());
  await open(); await act(async()=>document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape"}))); ok("fermeture : Échap",isClosed());
  const touch=(t,x)=>{const e=new Event(t);e.touches=[{clientX:x}];e.changedTouches=[{clientX:x}];document.dispatchEvent(e)};
  await open(); await act(async()=>{touch("touchstart",250);touch("touchend",260+80)}); ok("balayage à droite ne ferme pas",isOpen());
  await act(async()=>{touch("touchstart",250);touch("touchend",120)}); ok("fermeture : balayage à gauche",isClosed());
  await open(); await act(async()=>$("#sidebar a[data-page=plans]").click()); ok("fermeture : navigation",isClosed());
  await act(async()=>{window.__mql.matches=true; window.__mqlFire({matches:true})}); 
  await open(); await act(async()=>window.__mqlFire({matches:true})); ok("fermeture : passage en affichage large",isClosed());
  let all=true; for(const [id,l] of TABS){ await act(async()=>$(`#sidebar a[data-page=${id}]`).click()); if($("#mb-title").textContent!==l||$("h1").textContent!==l||$("#sidebar a[aria-current=page]").dataset.page!==id) all=false; }
  ok(`titre de contexte exact sur les ${TABS.length} pages`,all);
  await act(async()=>$("#sidebar a[data-page=texts]").dispatchEvent(new KeyboardEvent("keydown",{key:" ",bubbles:true}))); ok("clavier : Espace",$("#mb-title").textContent==="Textes");
  await act(async()=>$("#sidebar a[data-page=access]").dispatchEvent(new MouseEvent("click",{bubbles:true}))); ok("clavier : Entrée (activation native du lien)",$("#mb-title").textContent==="Accès");
  ok("entrées tabindex=0",[...document.querySelectorAll("#sidebar a")].every(a=>a.tabIndex===0));
  ok("libellés de colonnes recopiés",$("tbody td").dataset.label==="Nom"&&document.querySelectorAll("tbody td")[1].dataset.label==="Formule"&&!document.querySelectorAll("tbody td")[2].dataset.label);
  ok("scrollTo simulé appelé",window.__scrolls>0);
  return r;
};
