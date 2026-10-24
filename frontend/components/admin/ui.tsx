"use client";
import { useCallback, useEffect, useState, type ReactNode } from "react";

export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => { setError(null); fn().then(setData).catch(e => setError((e as Error).message)); },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    deps);
  useEffect(() => { reload(); }, [reload]);
  return { data, error, reload, setData };
}

export function Notice({ error, ok }: { error?: string | null; ok?: string | null }) {
  if (error) return <p className="err" role="alert">{error}</p>;
  if (ok) return <p className="ok" role="status">{ok}</p>;
  return null;
}

export function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="card adm-section">
      <div className="adm-head"><h2>{title}</h2>{actions}</div>
      {children}
    </section>
  );
}

/** Exécute une action et affiche son résultat. */
export function useAction() {
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>, success: string) => {
    setError(null); setOk(null);
    try { await fn(); setOk(success); } catch (e) { setError((e as Error).message); }
  };
  return { error, ok, run };
}

export const Toggle = ({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) => (
  <label className="adm-toggle"><input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} /> {label}</label>
);
