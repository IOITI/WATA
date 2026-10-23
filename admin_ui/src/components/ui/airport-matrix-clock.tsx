'use client';

import { memo, useEffect, useMemo, useState, type PointerEvent, type ReactNode } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { motionTransitions } from '../../lib/motion-tokens';


// WATA: the cities are the caller's, each with its IANA zone; EasyUI's list of 32 (five countries, no Paris), its
// country and city selectors and its add and remove buttons are gone.
export interface AirportMatrixCity {
  id: string;
  name: string;
  timeZone: string;
}

export interface AirportMatrixClockProps {
  /** WATA: one to five cities, each with its IANA time zone. */
  cities: AirportMatrixCity[];
  /** 24-hour airport-board time by default. */
  format?: '12h' | '24h';
  /** Include seconds in each city time. */
  showSeconds?: boolean;
  /** Refresh cadence in milliseconds. Defaults to one second. */
  updateInterval?: number;
  /** WATA: the board's heading (EasyUI's "WORLD CLOCK"); null for none. */
  title?: string | null;
  /** WATA: the board's last line, in the caller's words (EasyUI's "N CITIES / IANA TIME ZONES"). */
  footer?: ReactNode;
  /** WATA: 'sm', a board for the side of a page's header (EasyUI's characters are up to 20 px wide). */
  size?: 'md' | 'sm';
  className?: string;
}

const GLYPH_WIDTH = { md: 'w-[clamp(10px,3.4vw,20px)]', sm: 'w-[9px]' } as const;

const GLYPHS: Readonly<Record<string, readonly string[]>> = {
  A: ['01110', '10001', '10001', '11111', '10001', '10001', '10001'], B: ['11110', '10001', '10001', '11110', '10001', '10001', '11110'],
  C: ['01111', '10000', '10000', '10000', '10000', '10000', '01111'], D: ['11110', '10001', '10001', '10001', '10001', '10001', '11110'],
  E: ['11111', '10000', '10000', '11110', '10000', '10000', '11111'], F: ['11111', '10000', '10000', '11110', '10000', '10000', '10000'],
  G: ['01111', '10000', '10000', '10111', '10001', '10001', '01111'], H: ['10001', '10001', '10001', '11111', '10001', '10001', '10001'],
  I: ['11111', '00100', '00100', '00100', '00100', '00100', '11111'], J: ['00111', '00010', '00010', '00010', '10010', '10010', '01100'],
  K: ['10001', '10010', '10100', '11000', '10100', '10010', '10001'], L: ['10000', '10000', '10000', '10000', '10000', '10000', '11111'],
  M: ['10001', '11011', '10101', '10101', '10001', '10001', '10001'], N: ['10001', '11001', '10101', '10011', '10001', '10001', '10001'],
  O: ['01110', '10001', '10001', '10001', '10001', '10001', '01110'], P: ['11110', '10001', '10001', '11110', '10000', '10000', '10000'],
  Q: ['01110', '10001', '10001', '10001', '10101', '10010', '01101'], R: ['11110', '10001', '10001', '11110', '10100', '10010', '10001'],
  S: ['01111', '10000', '10000', '01110', '00001', '00001', '11110'], T: ['11111', '00100', '00100', '00100', '00100', '00100', '00100'],
  U: ['10001', '10001', '10001', '10001', '10001', '10001', '01110'], V: ['10001', '10001', '10001', '10001', '10001', '01010', '00100'],
  W: ['10001', '10001', '10001', '10101', '10101', '10101', '01010'], X: ['10001', '10001', '01010', '00100', '01010', '10001', '10001'],
  Y: ['10001', '10001', '01010', '00100', '00100', '00100', '00100'], Z: ['11111', '00001', '00010', '00100', '01000', '10000', '11111'],
  '0': ['01110', '10001', '10011', '10101', '11001', '10001', '01110'], '1': ['00100', '01100', '00100', '00100', '00100', '00100', '01110'],
  '2': ['01110', '10001', '00001', '00010', '00100', '01000', '11111'], '3': ['11110', '00001', '00001', '01110', '00001', '00001', '11110'],
  '4': ['00010', '00110', '01010', '10010', '11111', '00010', '00010'], '5': ['11111', '10000', '10000', '11110', '00001', '00001', '11110'],
  '6': ['01110', '10000', '10000', '11110', '10001', '10001', '01110'], '7': ['11111', '00001', '00010', '00100', '01000', '01000', '01000'],
  '8': ['01110', '10001', '10001', '01110', '10001', '10001', '01110'], '9': ['01110', '10001', '10001', '01111', '00001', '00001', '01110'],
  ':': ['00000', '00100', '00100', '00000', '00100', '00100', '00000'], '-': ['00000', '00000', '00000', '11111', '00000', '00000', '00000'],
  '/': ['00001', '00010', '00010', '00100', '01000', '01000', '10000'], ' ': ['00000', '00000', '00000', '00000', '00000', '00000', '00000'],
};

const DOTS = Array.from({ length: 35 }, (_, index) => ({ x: index % 5, y: Math.floor(index / 5) }));
const DOT_RADIUS = 0.34;

function createDotPath(char: string | null) {
  return DOTS.filter(({ x, y }) => char === null || GLYPHS[char][y][x] === '1')
    .map(({ x, y }) => `M${x - DOT_RADIUS},${y}a${DOT_RADIUS},${DOT_RADIUS} 0 1,0 ${DOT_RADIUS * 2},0a${DOT_RADIUS},${DOT_RADIUS} 0 1,0 ${-DOT_RADIUS * 2},0`)
    .join('');
}

const INACTIVE_DOT_PATH = createDotPath(null);
const GLYPH_PATHS = Object.fromEntries(Object.keys(GLYPHS).map((char) => [char, createDotPath(char)])) as Record<string, string>;


function formatCityTime(date: Date, timeZone: string, format: '12h' | '24h', showSeconds: boolean) {
  const formatter = new Intl.DateTimeFormat(format === '24h' ? 'en-GB' : 'en-US', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    ...(showSeconds ? { second: '2-digit' as const } : {}),
    ...(format === '24h' ? { hourCycle: 'h23' as const } : { hour12: true }),
  });
  const parts = formatter.formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? '';
  const hour = part('hour').padStart(2, '0');
  const minute = part('minute');
  const second = showSeconds ? `:${part('second')}` : '';
  const period = format === '12h' ? part('dayPeriod').toUpperCase() : '';
  return { matrix: `${hour}:${minute}${second}${period ? ` ${period}` : ''}`, accessible: `${hour}:${minute}${second}${period ? ` ${period}` : ''}` };
}

const MatrixGlyph = memo(function MatrixGlyph({ char, size }: { char: string; size: 'md' | 'sm' }) {
  return (
    <svg aria-hidden="true" focusable="false" viewBox="-0.5 -0.5 5 7" className={`block h-auto ${GLYPH_WIDTH[size]} overflow-visible`}>
      <path d={INACTIVE_DOT_PATH} fill="currentColor" opacity="0.12" />
      <path d={GLYPH_PATHS[char] ?? GLYPH_PATHS[' ']} fill="currentColor" />
    </svg>
  );
});

const MatrixCharacter = memo(function MatrixCharacter({ char, index, reduceMotion, rebuildId, size }: { char: string; index: number; reduceMotion: boolean; rebuildId: number; size: 'md' | 'sm' }) {
  const shouldAnimate = rebuildId > 0 && !reduceMotion;
  return (
    <span className={`relative inline-flex ${GLYPH_WIDTH[size]} shrink-0 items-center justify-center overflow-hidden align-middle`} aria-hidden="true">
      <AnimatePresence initial={false} mode="sync">
        <motion.span
          key={rebuildId}
          initial={shouldAnimate ? { y: '72%', opacity: 0.45 } : false}
          animate={{ y: '0%', opacity: 1 }}
          exit={shouldAnimate ? { y: '-72%', opacity: 0.45 } : { opacity: 0 }}
          transition={shouldAnimate ? { ...motionTransitions.springSnappy, delay: Math.min(index * 0.012, 0.09) } : { duration: 0 }}
          className="absolute inset-0 flex items-center justify-center"
        >
          <MatrixGlyph char={char} size={size} />
        </motion.span>
      </AnimatePresence>
      <span className="invisible"><MatrixGlyph char="0" size={size} /></span>
    </span>
  );
});

const MatrixText = memo(function MatrixText({ text, reduceMotion, rebuildId, label, size, className = '' }: { text: string; reduceMotion: boolean; rebuildId: number; label: string; size: 'md' | 'sm'; className?: string }) {
  return (
    <span
      aria-label={label}
      className={`inline-flex min-w-0 items-center gap-[clamp(0.5px,0.2vw,2px)] text-current ${className}`}
    >
      <span aria-hidden="true" className="sr-only">{label}</span>
      {Array.from(text.toUpperCase()).map((char, index) => (
        <MatrixCharacter key={index} char={GLYPHS[char] ? char : ' '} index={index} reduceMotion={reduceMotion} rebuildId={rebuildId} size={size} />
      ))}
    </span>
  );
});


type AirportClockRowData = AirportMatrixCity & { time: ReturnType<typeof formatCityTime> };

const AirportMatrixClockRow = memo(function AirportMatrixClockRow({
  row, rowIndex, rowCount, reduceMotion, size,
}: {
  row: AirportClockRowData;
  rowIndex: number;
  rowCount: number;
  reduceMotion: boolean;
  size: 'md' | 'sm';
}) {
  const [rebuildId, setRebuildId] = useState(0);
  const handlePointerEnter = (event: PointerEvent<HTMLLIElement>) => {
    if (!reduceMotion && event.pointerType !== 'touch') setRebuildId((current) => current + 1);
  };

  return (
    <li
      onPointerEnter={handlePointerEnter}
      className={`grid items-center gap-2 ${size === 'sm' ? 'grid-cols-[auto_minmax(0,1fr)] gap-3 px-3 py-1.5' : 'grid-cols-[minmax(84px,0.8fr)_minmax(0,1.2fr)] px-3 py-2 sm:grid-cols-[minmax(140px,0.8fr)_minmax(0,1.2fr)] sm:px-5 sm:py-2.5'} ${rowIndex < rowCount - 1 ? 'border-b border-border/50 dark:border-[#363636]/70' : ''}`}
    >
      <div className="min-w-0 text-text-primary dark:text-[#F5F5F5]">
        <MatrixText text={row.time.matrix} label={`${row.time.accessible}, ${row.name}`} reduceMotion={reduceMotion} rebuildId={rebuildId} size={size} />
      </div>
      <div className="min-w-0 text-text-primary dark:text-[#F5F5F5]">
        <MatrixText text={row.name} label={row.name} reduceMotion={reduceMotion} rebuildId={rebuildId} size={size} />
      </div>
    </li>
  );
});

export function AirportMatrixClock({
  cities,
  format = '24h',
  showSeconds = false,
  updateInterval = 1000,
  title = 'WORLD CLOCK',
  footer,
  size = 'md',
  className = '',
}: AirportMatrixClockProps) {
  const reduceMotion = useReducedMotion() ?? false;
  const [now, setNow] = useState(() => Date.now());
  const selectedCities = useMemo(() => cities.slice(0, 5), [cities]);

  useEffect(() => {
    const interval = Number.isFinite(updateInterval) && updateInterval > 0 ? updateInterval : 1000;
    const timer = window.setInterval(() => setNow(Date.now()), interval);
    return () => window.clearInterval(timer);
  }, [updateInterval]);

  const rows = useMemo(() => selectedCities.map((city) => (
    { ...city, time: formatCityTime(new Date(now), city.timeZone, format, showSeconds) }
  )), [selectedCities, now, format, showSeconds]);

  return (
    <section
      aria-label={`World times: ${rows.map((row) => `${row.time.accessible} ${row.name}`).join(', ')}`}
      className={`relative ${size === 'sm' ? 'w-fit' : 'w-full max-w-3xl'} overflow-hidden rounded-xl border border-border bg-[#F5F5F5] text-text-primary shadow-sm [--amc-grid:rgba(0,0,0,0.008)] dark:border-[#363636] dark:bg-[#202020] dark:text-[#F5F5F5] dark:[--amc-grid:rgba(255,255,255,0.01)] ${className}`}
      style={{ backgroundImage: 'linear-gradient(var(--amc-grid) 1px, transparent 1px), linear-gradient(90deg, var(--amc-grid) 1px, transparent 1px)', backgroundSize: '22px 22px' }}
    >
      <div>
        {title && (
          <header className={`flex items-center justify-between border-b border-border/70 dark:border-[#363636] ${size === 'sm' ? 'px-3 py-1.5' : 'px-4 py-3 sm:px-6'}`}>
            <p className="font-mono text-[10px] tracking-[0.18em] text-text-muted dark:text-[#8A8A8A]">{title}</p>
          </header>
        )}
        <ul className="m-0 list-none p-0">
          {rows.map((row, rowIndex) => (
            <AirportMatrixClockRow key={row.id} row={row} rowIndex={rowIndex} rowCount={rows.length} reduceMotion={reduceMotion} size={size} />
          ))}
        </ul>
        {/* WATA: a light value in front of EasyUI's dark-only footer colours. */}
        <footer className={`border-t border-border/70 font-mono text-[9px] tracking-[0.12em] text-text-muted dark:border-[#363636] dark:text-[#8A8A8A] ${size === 'sm' ? 'px-3 py-1.5' : 'px-3 py-2 sm:px-5'}`}>
          {footer ?? (
            <>
              {rows.length} CITIES <span className="px-1.5 opacity-50">/</span> IANA TIME ZONES
            </>
          )}
        </footer>
      </div>
    </section>
  );
}
