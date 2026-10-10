/** Product mark "אוריון" (DESIGN_SYSTEM.md §2.7): Orion's belt as a flight path — the three belt stars rise
 *  diagonally across a night-sky badge and an aircraft continues the line as its next point; faint Betelgeuse (warm)
 *  and Rigel (blue-white) hint at the rest of the constellation. Same artwork as public/favicon.svg.
 *  Decorative: the product name is always written next to it. */
import { useId } from 'react';

type Star = { x: number; y: number; r: number; color: string; warm?: boolean };

const STARS: Star[] = [
  { x: 15, y: 18, r: 2, color: '#FFB566', warm: true }, // Betelgeuse
  { x: 47, y: 49, r: 2.2, color: '#CFE6FF' }, // Rigel
  { x: 18, y: 43, r: 4, color: '#FFFFFF' }, // belt: Alnitak
  { x: 28, y: 35, r: 4.4, color: '#FFFFFF' }, // belt: Alnilam
  { x: 38, y: 27, r: 4, color: '#FFFFFF' }, // belt: Mintaka
];
const DUST: [number, number, number][] = [[10, 12, 0.45], [55, 52, 0.35], [8, 30, 0.3], [30, 8, 0.35], [58, 34, 0.3]];
const PLANE =
  'M32 9 C33.6 11 34.4 14 34.4 18 L34.4 25 L50 36.5 L50 40.5 L34.4 35 L34.4 44 L40.5 49.5 L40.5 52.5 L32 50 ' +
  'L23.5 52.5 L23.5 49.5 L29.6 44 L29.6 35 L14 40.5 L14 36.5 L29.6 25 L29.6 18 C29.6 14 30.4 11 32 9 Z';

export function LogoMark({ size = 30 }: { size?: number }) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, ''); // gradient ids must be unique per instance and url()-safe
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden focusable="false" style={{ flexShrink: 0 }}>
      <defs>
        <linearGradient id={`${id}-sky`} x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#050B1E" />
          <stop offset="0.6" stopColor="#132B5E" />
          <stop offset="1" stopColor="#2D57B0" />
        </linearGradient>
        <radialGradient id={`${id}-glow`}>
          <stop offset="0" stopColor="#FFFFFF" stopOpacity="0.6" />
          <stop offset="1" stopColor="#FFFFFF" stopOpacity="0" />
        </radialGradient>
        <radialGradient id={`${id}-warm`}>
          <stop offset="0" stopColor="#FFB566" stopOpacity="0.6" />
          <stop offset="1" stopColor="#FFB566" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="64" height="64" rx="15" fill={`url(#${id}-sky)`} />
      {DUST.map(([x, y, o]) => <circle key={`${x}-${y}`} cx={x} cy={y} r="0.9" fill="#FFFFFF" fillOpacity={o} />)}
      <path d="M13 47 L50 17.5" stroke="#7FE3FF" strokeOpacity="0.55" strokeWidth="1.6" strokeLinecap="round"
        strokeDasharray="0.1 3.4" />
      {STARS.map((s) => (
        <g key={`${s.x}-${s.y}`}>
          <circle cx={s.x} cy={s.y} r={s.r * 2.3} fill={`url(#${id}-${s.warm ? 'warm' : 'glow'})`} />
          <circle cx={s.x} cy={s.y} r={s.r} fill={s.color} />
        </g>
      ))}
      <path d={PLANE} fill="#FFFFFF" transform="translate(50.5 16.5) rotate(51.34) scale(0.42) translate(-32 -30)" />
    </svg>
  );
}
