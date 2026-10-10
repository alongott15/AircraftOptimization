/** Product mark (DESIGN_SYSTEM.md §2.7): a check mark whose long stroke becomes the climb of a departing aircraft —
 *  "ready to fly". Same artwork as public/favicon.svg. Decorative: the product name is always written next to it. */
import { useId } from 'react';

const PLANE =
  'M32 9 C33.6 11 34.4 14 34.4 18 L34.4 25 L50 36.5 L50 40.5 L34.4 35 L34.4 44 L40.5 49.5 L40.5 52.5 L32 50 ' +
  'L23.5 52.5 L23.5 49.5 L29.6 44 L29.6 35 L14 40.5 L14 36.5 L29.6 25 L29.6 18 C29.6 14 30.4 11 32 9 Z';

export function LogoMark({ size = 30 }: { size?: number }) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, ''); // gradient ids must be unique per instance and url()-safe
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden focusable="false" style={{ flexShrink: 0 }}>
      <defs>
        <linearGradient id={`${id}-bg`} x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#0B2240" />
          <stop offset="0.55" stopColor="#1D4E89" />
          <stop offset="1" stopColor="#3F7FD9" />
        </linearGradient>
        <linearGradient id={`${id}-trail`} x1="12" y1="0" x2="42" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#7FE3FF" />
          <stop offset="1" stopColor="#FFFFFF" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="15" fill={`url(#${id}-bg)`} />
      <path d="M8 46 Q32 36 56 46" fill="none" stroke="#FFFFFF" strokeOpacity="0.18" strokeWidth="2" strokeLinecap="round" />
      <path d="M12.5 33.5 L23 44 L36.5 30.5" fill="none" stroke={`url(#${id}-trail)`} strokeWidth="5.6"
        strokeLinecap="round" strokeLinejoin="round" />
      <path d={PLANE} fill="#FFFFFF" transform="translate(45 20) rotate(45) scale(0.7) translate(-32 -30)" />
    </svg>
  );
}
