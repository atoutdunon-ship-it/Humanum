"use client";
/**
 * Graphiques du pilotage — SVG sans dépendance, une seule série (pas de légende : le titre la nomme).
 * Barres ≤ 24 px, extrémité arrondie de 4 px, base carrée, grille en filet, étiquettes sélectives
 * (maximum et dernière valeur), infobulle au survol et au clavier, vue tableau équivalente.
 */
import { useEffect, useRef, useState } from "react";

const SERIES = "var(--series)";

function ticks(max: number): number[] {
  if (max <= 0) return [0, 1];
  const raw = max / 4, mag = 10 ** Math.floor(Math.log10(raw)), step = [1, 2, 5, 10].map(m => m * mag).find(s => s >= raw)!;
  return Array.from({ length: Math.ceil(max / step) + 1 }, (_, i) => i * step);
}

function bar(x: number, y: number, w: number, h: number): string {  // haut arrondi, base carrée
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

export function ColumnChart({ title, data, unit }: { title: string; data: { label: string; value: number; tip: string }[]; unit: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(720);  // largeur réelle : textes à leur taille nominale, sans réduction d'échelle
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [table]);
  const H = 240, L = 36, B = 28, T = 16;
  const tk = ticks(Math.max(...data.map(d => d.value), 0));
  const top = tk[tk.length - 1] || 1;
  const band = (W - L) / data.length, bw = Math.min(24, band * 0.6);
  const y = (v: number) => T + (H - T - B) * (1 - v / top);
  const maxI = data.reduce((m, d, i) => (d.value > data[m].value ? i : m), 0);
  return (
    <figure className="chart">
      <figcaption><strong>{title}</strong><button type="button" className="link" onClick={() => setTable(!table)}>{table ? "Voir le graphique" : "Voir le tableau"}</button></figcaption>
      {table ? (
        <table><thead><tr><th>Période</th><th>{unit}</th></tr></thead>
          <tbody>{data.map(d => <tr key={d.label}><td>{d.tip}</td><td>{d.value}</td></tr>)}</tbody></table>
      ) : (
        <div className="chart-box" ref={box}>
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title} : ${data.map(d => `${d.tip} ${d.value}`).join(", ")}`}>
            {tk.map(t => (<g key={t}><line x1={L} x2={W} y1={y(t)} y2={y(t)} className="grid" />
              <text x={L - 8} y={y(t) + 4} textAnchor="end" className="tick">{t}</text></g>))}
            {data.map((d, i) => {
              const cx = L + band * i + band / 2, h = H - B - y(d.value);
              return (
                <g key={d.label} tabIndex={0} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover(i)} onBlur={() => setHover(null)} aria-label={`${d.tip} : ${d.value} ${unit}`}>
                  <rect x={L + band * i} y={T} width={band} height={H - T - B} fill="transparent" />
                  {d.value > 0 && <path d={bar(cx - bw / 2, y(d.value), bw, h)} fill={SERIES} opacity={hover === null || hover === i ? 1 : 0.55} />}
                  {(i === maxI || i === data.length - 1) && d.value > 0 && <text x={cx} y={y(d.value) - 6} textAnchor="middle" className="val">{d.value}</text>}
                  {(data.length <= Math.floor((W - L) / 56) || i % Math.ceil(data.length / Math.floor((W - L) / 56)) === (data.length - 1) % Math.ceil(data.length / Math.floor((W - L) / 56))) && <text x={cx} y={H - 8} textAnchor="middle" className="tick">{d.label}</text>}
                </g>
              );
            })}
            <line x1={L} x2={W} y1={H - B} y2={H - B} className="axis" />
          </svg>
          {hover !== null && (
            <div className="tooltip" style={{ left: `${((L + band * hover + band / 2) / W) * 100}%`, top: `${(y(data[hover].value) / H) * 100}%` }}>
              <strong>{data[hover].value}</strong> {unit}<br /><span>{data[hover].tip}</span>
            </div>
          )}
        </div>
      )}
    </figure>
  );
}

export function BarList({ title, data }: { title: string; data: { label: string; value: number }[] }) {
  const max = Math.max(...data.map(d => d.value), 1);
  return (
    <figure className="chart">
      <figcaption><strong>{title}</strong></figcaption>
      <ul className="barlist">{data.map(d => (
        <li key={d.label}>
          <span className="bl-label">{d.label}</span>
          <span className="bl-track" aria-hidden="true">{d.value > 0 && <span className="bl-fill" style={{ width: `${(100 * d.value) / max}%` }} />}</span>
          <span className="bl-val">{d.value}</span>
        </li>
      ))}</ul>
    </figure>
  );
}

export function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return <div className="tile"><span className="tile-l">{label}</span><span className="tile-v">{value}</span>{hint && <span className="tile-h">{hint}</span>}</div>;
}
