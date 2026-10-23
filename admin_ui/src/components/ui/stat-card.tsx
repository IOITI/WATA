"use client";

import React, {
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import {
  animate,
  motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
} from "framer-motion";
import {
  CHART_COLORS,
  EASE_OUT,
  clamp,
  cx,
  defaultFormat,
  useDismissOutside,
  useElementWidth,
} from "../../lib/chart-primitives";
import { monotonePath, pctText } from "../../lib/dashboard-chart-utils";

/* -------------------------------------------------------------------------- */
/*  StatCard / StatGrid                                                       */
/*                                                                            */
/*  KPI tiles with a count-up value, a delta pill, and a scrubable sparkline. */
/*  Hand-written SVG · light and dark · measures its own container.            */
/* -------------------------------------------------------------------------- */

export interface StatCardProps {
  label: string;
  /** The headline number (usually the latest point of `trend`) */
  value: number;
  /** Points for the sparkline, oldest → newest */
  trend: number[];
  /** Names for each trend point, shown while scrubbing (e.g. "Oct 12") */
  trendLabels?: string[];
  /** Previous-period value → the delta is computed from it */
  previous?: number;
  /** …or give the delta in percent directly */
  delta?: number;
  /** Which direction is good news. Use "down" for churn, latency, costs */
  goodWhen?: "up" | "down";
  /** Text under the number, e.g. "vs last 30 days" */
  period?: string;
  format?: (n: number) => string;
  /** WATA: a class for the headline by the value shown (a profit green, a loss red), the scrubbed point's too. */
  toneOf?: (n: number) => string | undefined;
  color?: string;
  animated?: boolean;
  className?: string;
}

// WATA: secondary text at 4.5:1 in both themes (neutral-500 is 4.0:1 on the dark card).
const MUTED = "text-neutral-500 dark:text-neutral-400";

export function StatCard({
  label,
  value,
  trend,
  trendLabels,
  previous,
  delta,
  goodWhen = "up",
  period = "vs previous period",
  format = defaultFormat,
  toneOf,
  color = CHART_COLORS[0],
  animated = true,
  className = "",
}: StatCardProps) {
  const reduce = useReducedMotion();
  const anim = animated && !reduce;
  const uid = useId().replace(/:/g, "");
  const [boxRef, width] = useElementWidth<HTMLDivElement>(220);
  const svgRef = useRef<SVGSVGElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<number | null>(null);
  useDismissOutside(cardRef, active !== null, useCallback(() => setActive(null), []));

  const n = trend.length;
  const H = 52, padX = 0, padT = 8, padB = 6;
  const min = n ? Math.min(...trend) : 0;
  const span = (n ? Math.max(...trend) : 1) - min || 1;
  const stepX = n > 1 ? (width - padX * 2) / (n - 1) : 0;
  const pts = trend.map((v, i) => [padX + i * stepX, padT + (1 - (v - min) / span) * (H - padT - padB)] as [number, number]);
  const line = monotonePath(pts);
  const area = n > 1 ? `${line}L${pts[n - 1][0].toFixed(2)} ${H}L${pts[0][0].toFixed(2)} ${H}Z` : "";

  const pct =
    delta ??
    (previous !== undefined && previous !== 0
      ? ((value - previous) / Math.abs(previous)) * 100
      : n > 1 && trend[0] !== 0
        ? ((trend[n - 1] - trend[0]) / Math.abs(trend[0])) * 100
        : 0);
  const rounded = Math.round(pct * 10) / 10;
  // WATA: a change only when the caller gives what it is from: EasyUI worked one out from the line's two ends,
  // which means nothing for a profit (it changes sign).
  const hasDelta = delta !== undefined || previous !== undefined;
  const good = rounded === 0 ? null : goodWhen === "up" ? rounded > 0 : rounded < 0;

  /* number: counts up on mount, follows the scrubber afterwards */
  const fmt = useRef(format);
  fmt.current = format;
  const shown = useMotionValue(anim ? 0 : value);
  const text = useTransform(shown, (v) => fmt.current(v));
  const mounted = useRef(false);
  useEffect(() => {
    const target = active === null ? value : trend[active];
    const intro = !mounted.current;
    mounted.current = true;
    const c = animate(shown, target, { duration: reduce ? 0 : intro ? 1.1 : 0.22, ease: EASE_OUT });
    return () => c.stop();
  }, [active, value, trend, shown, reduce]);

  /* scrub cursor */
  const curX = useSpring(0, { stiffness: 600, damping: 44, mass: 0.5 });
  const curY = useSpring(0, { stiffness: 600, damping: 44, mass: 0.5 });
  const seen = useRef(false);
  useEffect(() => {
    if (active === null || !pts[active]) {
      seen.current = false;
      return;
    }
    const [px, py] = pts[active];
    if (!seen.current) {
      curX.jump(px);
      curY.jump(py);
      seen.current = true;
    } else {
      curX.set(px);
      curY.set(py);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, width, curX, curY]);

  const move = (e: React.PointerEvent) => {
    if (n < 2 || !svgRef.current) return;
    const box = svgRef.current.getBoundingClientRect();
    setActive(clamp(Math.round((e.clientX - box.left - padX) / stepX), 0, n - 1));
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowRight") setActive((a) => clamp((a ?? n - 2) + 1, 0, n - 1));
    else if (e.key === "ArrowLeft") setActive((a) => clamp((a ?? n) - 1, 0, n - 1));
    else if (e.key === "Escape") setActive(null);
    else return;
    e.preventDefault();
  };

  const sub = active === null ? period : (trendLabels?.[active] ?? `Point ${active + 1}`);
  const tone = toneOf?.(active === null ? value : trend[active]);

  return (
    <div
      ref={cardRef}
      tabIndex={0}
      role="group"
      aria-label={
        hasDelta
          ? `${label}: ${format(value)}, ${rounded > 0 ? "up" : rounded < 0 ? "down" : "unchanged"} ${pctText(Math.abs(rounded))}`
          : `${label}: ${format(value)}`
      }
      onPointerMove={move}
      onPointerDown={move}
      onPointerLeave={(e) => e.pointerType === "mouse" && setActive(null)}
      onKeyDown={onKey}
      onBlur={() => setActive(null)}
      // WATA: the look of the admin's other tiles (KpiTile), which it sits among: their border, surface, padding
      // and type sizes, and the admin's focus ring (EasyUI's card was white, ringed and shadowed).
      className={`group relative flex min-h-[7.5rem] min-w-0 select-none flex-col overflow-hidden rounded-2xl border border-border/70 bg-surface/50 pt-4 text-neutral-700 sm:pt-5 dark:bg-surface/25 dark:text-neutral-300 ${className}`}
    >
      <div className="flex items-start justify-between gap-3 px-4 sm:px-5">
        <p className={`truncate text-xs ${MUTED}`}>{label}</p>
        {hasDelta && (
          <span
            className={cx(
              "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] font-medium tabular-nums",
              // WATA: the admin's status colours, 4.5:1 as text (emerald-600 on its pale pill was 3.6:1).
              good === null && "bg-black/[0.05] text-neutral-500 dark:bg-white/[0.06] dark:text-neutral-400",
              good === true && "bg-emerald-500/10 text-positive",
              good === false && "bg-rose-500/10 text-negative",
            )}
          >
            {rounded !== 0 && (
              <svg viewBox="0 0 10 10" className={cx("h-2 w-2", rounded < 0 && "rotate-180")} fill="currentColor" aria-hidden>
                <path d="M5 1.5 9 7.5H1Z" />
              </svg>
            )}
            {pctText(Math.abs(rounded))}
          </span>
        )}
      </div>

      <p className={cx("mt-2 px-4 text-xl font-semibold tracking-tight tabular-nums sm:px-5 sm:text-2xl", tone ?? "text-neutral-900 dark:text-neutral-50")}>
        <motion.span>{text}</motion.span>
      </p>
      <p className={`mt-1 truncate px-4 text-xs sm:px-5 ${MUTED}`}>{sub}</p>

      <div ref={boxRef} className="mt-auto pt-3" style={{ height: H + 12 }}>
        {n > 1 && (
          <svg
            ref={svgRef}
            width={width}
            height={H}
            role="img"
            aria-label={`${label} trend from ${format(trend[0])} to ${format(trend[n - 1])}`}
            className="block overflow-visible"
          >
            <defs>
              <linearGradient id={`${uid}-g`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor={color} stopOpacity="0.28" />
                <stop offset="1" stopColor={color} stopOpacity="0" />
              </linearGradient>
            </defs>
            <motion.path
              d={area}
              fill={`url(#${uid}-g)`}
              initial={anim ? { opacity: 0 } : false}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.8, delay: 0.5 }}
            />
            <motion.path
              d={line}
              fill="none"
              stroke={color}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              initial={anim ? { pathLength: 0 } : false}
              animate={{ pathLength: 1 }}
              transition={{ duration: 1.1, ease: EASE_OUT, delay: 0.15 }}
            />
            {/* latest point */}
            <g style={{ opacity: active === null ? 1 : 0, transition: "opacity .15s" }}>
              {anim && (
                <motion.circle
                  cx={pts[n - 1][0]}
                  cy={pts[n - 1][1]}
                  r={3.5}
                  fill={color}
                  style={{ transformBox: "fill-box", transformOrigin: "center" }}
                  animate={{ scale: [1, 2.8], opacity: [0.4, 0] }}
                  transition={{ repeat: Infinity, duration: 1.8, ease: "easeOut" }}
                />
              )}
              <circle cx={pts[n - 1][0]} cy={pts[n - 1][1]} r={3.5} fill={color} />
            </g>
            {/* scrubber */}
            <motion.g initial={false} animate={{ opacity: active === null ? 0 : 1 }} transition={{ duration: 0.12 }}>
              <motion.line y1={0} y2={H} x1={curX} x2={curX} className="stroke-black/15 dark:stroke-white/20" strokeDasharray="2 3" />
              <motion.circle cx={curX} cy={curY} r={4.5} fill={color} className="stroke-white dark:stroke-[#0b0b0c]" strokeWidth={2} />
            </motion.g>
          </svg>
        )}
      </div>
      <p className="sr-only" role="status" aria-live="polite">
        {active !== null ? `${trendLabels?.[active] ?? `Point ${active + 1}`}: ${format(trend[active])}` : ""}
      </p>
    </div>
  );
}

export function StatGrid({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`grid gap-3 sm:gap-4 ${className}`}
      style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 210px), 1fr))" }}
    >
      {children}
    </div>
  );
}
