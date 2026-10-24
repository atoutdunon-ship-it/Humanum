"use client";
/** Sélecteur de créneaux par semaine : heures affichées dans le fuseau du coach, mention explicite du fuseau. */
import { useEffect, useState } from "react";
import { fmt } from "@/lib/nav";

export type Slots = { timezone: string; duration_minutes: number; days: { date: string; slots: string[] }[] };

const addDays = (d: Date, n: number) => new Date(d.getTime() + n * 86400000);

export default function SlotPicker({ load, onPick, selected, maxDays = 120 }: {
  load: (start: string, days: number) => Promise<Slots>; onPick: (iso: string, tz: string) => void; selected?: string | null; maxDays?: number;
}) {
  const [week, setWeek] = useState(0);
  const [data, setData] = useState<Slots | null>(null);
  const [error, setError] = useState<string | null>(null);
  const start = addDays(new Date(), week * 7);
  const key = fmt.dateKey(start);
  useEffect(() => {
    setData(null); setError(null);
    load(key, 7).then(setData).catch(e => setError((e as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const lastWeek = Math.floor(maxDays / 7);

  return (
    <div className="slots">
      <div className="slots-nav">
        <button type="button" className="ghost" disabled={week === 0} onClick={() => setWeek(week - 1)}>Semaine précédente</button>
        <span className="slots-range">{fmt.day(start.toISOString(), data?.timezone ?? "UTC")} — 7 jours</span>
        <button type="button" className="ghost" disabled={week >= lastWeek} onClick={() => setWeek(week + 1)}>Semaine suivante</button>
      </div>
      {error && <p className="err" role="alert">{error}</p>}
      {!data && !error && <p className="muted">Recherche des disponibilités…</p>}
      {data && data.days.length === 0 && <p className="muted">Aucun créneau libre cette semaine : essayez la suivante.</p>}
      {data && data.days.map(d => (
        <div key={d.date} className="slots-day">
          <h3>{fmt.day(d.slots[0], data.timezone)}</h3>
          <div className="slots-grid" role="group" aria-label={fmt.day(d.slots[0], data.timezone)}>
            {d.slots.map(s => (
              <button key={s} type="button" className={`slot${selected === s ? " on" : ""}`} aria-pressed={selected === s}
                onClick={() => onPick(s, data.timezone)}>{fmt.time(s, data.timezone)}</button>
            ))}
          </div>
        </div>
      ))}
      {data && <p className="muted tz">Heures affichées : fuseau {data.timezone.replace("_", " ")}.</p>}
    </div>
  );
}
