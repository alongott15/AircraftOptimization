/** Product mark "אוריון" (DESIGN_SYSTEM.md §2.7): the Orion constellation in a night sky — the hourglass of seven
 *  stars with the three-star belt, warm Betelgeuse and blue-white Rigel — and an aircraft departing it along a dotted
 *  contrail. Same artwork as public/favicon.svg. Decorative: the product name is always written next to it. */
import { useId } from 'react';

type Star = { x: number; y: number; r: number; color: string; warm?: boolean };

// Positions as Orion is seen in the sky: Betelgeuse upper-left, Rigel lower-right, the belt rising to the right.
const STARS: Star[] = [
  { x: 19.5, y: 15.5, r: 3.4, color: '#FFB566', warm: true }, // Betelgeuse
  { x: 42.5, y: 18, r: 2.7, color: '#EAF2FF' }, // Bellatrix
  { x: 26.5, y: 33.5, r: 2.6, color: '#FFFFFF' }, // Alnitak
  { x: 32, y: 31.5, r: 2.6, color: '#FFFFFF' }, // Alnilam
  { x: 37.5, y: 29.5, r: 2.6, color: '#FFFFFF' }, // Mintaka
  { x: 23, y: 49, r: 2.6, color: '#EAF2FF' }, // Saiph
  { x: 45.5, y: 47, r: 3.6, color: '#CFE6FF' }, // Rigel
];
// shoulders, shoulders → belt, the belt, belt → feet, feet (indices into STARS)
const LINES: [number, number][] = [[0, 1], [0, 2], [1, 4], [2, 4], [2, 5], [4, 6], [5, 6]];
const DUST: [number, number, number][] = [
  [9, 10, 0.5], [54, 8, 0.45], [11, 40, 0.35], [56, 30, 0.4], [14, 57, 0.35], [52, 57, 0.3], [33, 9, 0.35], [6, 26, 0.3],
];
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
          <stop offset="0" stopColor="#FFFFFF" stopOpacity="0.55" />
          <stop offset="1" stopColor="#FFFFFF" stopOpacity="0" />
        </radialGradient>
        <radialGradient id={`${id}-warm`}>
          <stop offset="0" stopColor="#FFB566" stopOpacity="0.6" />
          <stop offset="1" stopColor="#FFB566" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="64" height="64" rx="15" fill={`url(#${id}-sky)`} />
      {DUST.map(([x, y, o]) => <circle key={`${x}-${y}`} cx={x} cy={y} r="0.9" fill="#FFFFFF" fillOpacity={o} />)}
      {LINES.map(([a, b]) => {
        const s = STARS.at(a);
        const e = STARS.at(b);
        return s && e ? (
          <line key={`${a}-${b}`} x1={s.x} y1={s.y} x2={e.x} y2={e.y} stroke="#A9C3EA" strokeOpacity="0.5"
            strokeWidth="1.5" strokeLinecap="round" />
        ) : null;
      })}
      {STARS.map((s) => (
        <g key={`${s.x}-${s.y}`}>
          <circle cx={s.x} cy={s.y} r={s.r * 2.4} fill={`url(#${id}-${s.warm ? 'warm' : 'glow'})`} />
          <circle cx={s.x} cy={s.y} r={s.r} fill={s.color} />
        </g>
      ))}
      <path d="M44.5 25.5 Q48.5 20.5 51 15.5" fill="none" stroke="#7FE3FF" strokeOpacity="0.7" strokeWidth="1.6"
        strokeLinecap="round" strokeDasharray="0.1 3" />
      <path d={PLANE} fill="#FFFFFF" transform="translate(53.5 10.5) rotate(40) scale(0.36) translate(-32 -30)" />
    </svg>
  );
}
