"use client";
/**
 * Coque applicative responsive — standard « navigation mobile premium ».
 * Sous 900 px : barre de contexte (où suis-je / Menu), tiroir avec voile, verrou du défilement,
 * cinq voies de fermeture (bouton, voile, Échap, balayage gauche, navigation), focus sur l'entrée active.
 * Les tableaux `table.rt` deviennent des fiches empilées (libellés de colonnes recopiés automatiquement).
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

export type NavItem = { id: string; label: string; href?: string };

export default function Shell({ items, current, onNavigate, label, footer, children }: {
  items: NavItem[]; current: string; onNavigate?: (id: string) => void; label: string;
  footer?: ReactNode; children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const navRef = useRef<HTMLElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const x0 = useRef<number | null>(null);
  const title = items.find(i => i.id === current)?.label ?? items[0]?.label ?? "";

  const closeNav = useCallback(() => setOpen(false), []);
  const openNav = useCallback(() => setOpen(true), []);

  useEffect(() => {  // verrou de défilement + focus sur l'entrée active
    document.body.classList.toggle("nav-open", open);
    if (open) {
      const cur = navRef.current?.querySelector<HTMLElement>("a[aria-current='page']") ?? navRef.current?.querySelector<HTMLElement>("a");
      try { cur?.focus({ preventScroll: true }); } catch { cur?.focus(); }
    }
    return () => document.body.classList.remove("nav-open");
  }, [open]);

  useEffect(() => {  // Échap, retour en affichage large, balayage vers la gauche
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeNav(); };
    const onStart = (e: TouchEvent) => { x0.current = e.touches[0]?.clientX ?? null; };
    const onEnd = (e: TouchEvent) => {
      if (x0.current !== null && navRef.current?.classList.contains("open") && (e.changedTouches[0]?.clientX ?? 0) - x0.current < -55) closeNav();
      x0.current = null;
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("touchstart", onStart, { passive: true });
    document.addEventListener("touchend", onEnd, { passive: true });
    let cleanupWide = () => {};
    if (typeof window.matchMedia === "function") {
      const wide = window.matchMedia("(min-width: 901px)");
      const onWide = (e: MediaQueryListEvent) => { if (e.matches) closeNav(); };
      if (wide.addEventListener) { wide.addEventListener("change", onWide); cleanupWide = () => wide.removeEventListener("change", onWide); }
    } else {
      const onResize = () => { if (window.innerWidth > 900) closeNav(); };
      window.addEventListener("resize", onResize); cleanupWide = () => window.removeEventListener("resize", onResize);
    }
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("touchstart", onStart);
      document.removeEventListener("touchend", onEnd);
      cleanupWide();
    };
  }, [closeNav]);

  useEffect(() => {  // libellés de colonnes pour l'affichage en fiches sur mobile
    const root = mainRef.current;
    if (!root) return;
    const label = () => root.querySelectorAll<HTMLTableElement>("table").forEach(t => {
      const heads = Array.from(t.querySelectorAll("thead th")).map(th => th.textContent?.trim() ?? "");
      t.classList.add("rt");
      t.querySelectorAll("tbody tr").forEach(tr => {
        let col = 0;
        Array.from(tr.children).forEach(td => {
          const span = Number((td as HTMLTableCellElement).colSpan) || 1;
          const l = heads.at(col) ?? "";
          if (span === 1 && l && td.getAttribute("data-label") !== l) td.setAttribute("data-label", l);
          col += span;
        });
      });
    });
    label();
    const mo = new MutationObserver(label);
    mo.observe(root, { childList: true, subtree: true });
    return () => mo.disconnect();
  }, []);

  const go = (id: string, e?: { preventDefault: () => void }) => {
    const item = items.find(i => i.id === id);
    if (!item?.href && e) e.preventDefault();
    onNavigate?.(id);
    closeNav();
    if (typeof window.scrollTo === "function") window.scrollTo(0, 0);
  };

  return (
    <div className="shell">
      <div className="mobilebar">
        <span className="mb-lbl">
          <span className="mb-where">Vous consultez</span>
          <span className="mb-title" id="mb-title">{title}</span>
        </span>
        <button className="mb-btn" type="button" aria-controls="sidebar" aria-expanded={open}
          onClick={() => (open ? closeNav() : openNav())}>{open ? "Fermer" : "Menu"}</button>
      </div>
      <div className={`backdrop${open ? " on" : ""}`} onClick={closeNav} aria-hidden="true" />
      <nav id="sidebar" ref={navRef} className={`side${open ? " open" : ""}`} aria-label={label}>
        <div className="drawer-head">
          <span className="dh-t">Navigation</span>
          <button className="dh-x" type="button" onClick={closeNav}>Fermer</button>
        </div>
        <img src="/logo-humanum.png" alt="HUMANUM CONSULTING" className="side-logo" />
        <p className="side-label">{label}</p>
        {items.map(i => (
          <a key={i.id} href={i.href ?? `#${i.id}`} data-page={i.id} tabIndex={0}
            aria-current={i.id === current ? "page" : undefined}
            onClick={e => go(i.id, e)}
            onKeyDown={e => { if (e.key === " ") { e.preventDefault(); go(i.id); } }}>{i.label}</a>
        ))}
        {footer}
      </nav>
      <main ref={mainRef}>{children}</main>
    </div>
  );
}
