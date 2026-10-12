"use client";

import * as React from "react";
import { AnimatePresence, motion, type Variants } from "framer-motion";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "../../lib/utils";

function useControllableState<T>(
  value: T | undefined,
  defaultValue: T,
  onChange?: (value: T) => void
): [T, (next: T) => void] {
  const [internal, setInternal] = React.useState<T>(defaultValue);
  const controlled = value !== undefined;
  const current = controlled ? (value as T) : internal;

  const onChangeRef = React.useRef(onChange);
  React.useEffect(() => {
    onChangeRef.current = onChange;
  });

  const set = React.useCallback(
    (next: T) => {
      if (!controlled) setInternal(next);
      onChangeRef.current?.(next);
    },
    [controlled]
  );

  return [current, set];
}

const spring = { type: "spring", stiffness: 460, damping: 38, mass: 0.7 } as const;

const slide: Variants = {
  enter: (d: number) => ({ x: d * 24, opacity: 0 }),
  center: { x: 0, opacity: 1 },
  exit: (d: number) => ({ x: d * -24, opacity: 0 }),
};

export type DateRange = { from: Date | null; to: Date | null };

interface CalendarBase {
  defaultMonth?: Date;
  minDate?: Date;
  maxDate?: Date;
  isDateDisabled?: (date: Date) => boolean;
  weekStartsOn?: 0 | 1;
  locale?: string;
  className?: string;
}
export type CalendarOptions = Omit<CalendarBase, "className">;

export interface CalendarSingleProps extends CalendarBase {
  mode?: "single";
  value?: Date | null;
  defaultValue?: Date | null;
  onChange?: (date: Date | null) => void;
}
export interface CalendarRangeProps extends CalendarBase {
  mode: "range";
  value?: DateRange;
  defaultValue?: DateRange;
  onChange?: (range: DateRange) => void;
}
export type CalendarProps = CalendarSingleProps | CalendarRangeProps;

/* ── date helpers (no dependencies) ── */
const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const addMonths = (d: Date, n: number) => {
  const first = new Date(d.getFullYear(), d.getMonth() + n, 1);
  const last = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
  return new Date(first.getFullYear(), first.getMonth(), Math.min(d.getDate(), last));
};
const isSameDay = (a?: Date | null, b?: Date | null) =>
  !!a && !!b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
const isSameMonth = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth();
const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;

const EMPTY: DateRange = { from: null, to: null };
const toRange = (v: Date | null | DateRange | undefined): DateRange | undefined =>
  v === undefined ? undefined : v === null ? EMPTY : v instanceof Date ? { from: v, to: null } : v;

export function Calendar(props: CalendarProps) {
  const { defaultMonth, minDate, maxDate, isDateDisabled, weekStartsOn = 0, locale, className } = props;
  const isRange = props.mode === "range";
  const uid = React.useId();

  const [selected, setSelected] = useControllableState<DateRange>(
    toRange(props.value),
    toRange(props.defaultValue) ?? EMPTY,
    (next) => {
      if (props.mode === "range") props.onChange?.(next);
      else props.onChange?.(next.from);
    }
  );
  const { from, to } = selected;

  const today = React.useMemo(() => startOfDay(new Date()), []);
  const [view, setView] = React.useState(() => {
    const base = defaultMonth ?? from ?? today;
    return new Date(base.getFullYear(), base.getMonth(), 1);
  });
  const [dir, setDir] = React.useState(1);
  const [focus, setFocus] = React.useState<Date>(from ?? today);
  const [hover, setHover] = React.useState<Date | null>(null);
  const gridRef = React.useRef<HTMLDivElement>(null);
  const shouldFocus = React.useRef(false);

  const min = minDate ? startOfDay(minDate) : null;
  const max = maxDate ? startOfDay(maxDate) : null;
  const disabled = (d: Date) => (!!min && d < min) || (!!max && d > max) || !!isDateDisabled?.(d);

  const y = view.getFullYear();
  const m = view.getMonth();
  const canPrev = !min || new Date(y, m, 0) >= min;
  const canNext = !max || new Date(y, m + 1, 1) <= max;

  const go = (target: Date, direction?: number) => {
    setDir(direction ?? (target > view ? 1 : -1));
    setView(new Date(target.getFullYear(), target.getMonth(), 1));
  };

  const monthFmt = React.useMemo(() => new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" }), [locale]);
  const dayFmt = React.useMemo(() => new Intl.DateTimeFormat(locale, { dateStyle: "full" }), [locale]);
  const weekdays = React.useMemo(() => {
    const f = new Intl.DateTimeFormat(locale, { weekday: "narrow" });
    // 1 Aug 2021 is a Sunday
    return Array.from({ length: 7 }, (_, i) => f.format(new Date(2021, 7, 1 + ((weekStartsOn + i) % 7))));
  }, [locale, weekStartsOn]);

  const dim = new Date(y, m + 1, 0).getDate();
  const offset = (new Date(y, m, 1).getDay() - weekStartsOn + 7) % 7;
  const cells = Array.from({ length: 42 }, (_, i) => {
    const n = i - offset + 1;
    return n < 1 || n > dim ? null : new Date(y, m, n);
  });
  const tabDate = isSameMonth(focus, view) ? focus : new Date(y, m, 1);

  const pick = (d: Date) => {
    if (disabled(d)) return;
    setFocus(d);
    if (!isRange) setSelected({ from: d, to: null });
    else if (!from || to || d < from) setSelected({ from: d, to: null });
    else setSelected({ from, to: d });
  };

  const rangeEnd = isRange && from ? (to ?? (hover && hover > from ? hover : null)) : null;

  const onKeyDown = (e: React.KeyboardEvent) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>("[data-date]");
    if (!el) return;
    const [yy, mm, dd] = el.dataset.date!.split("-").map(Number);
    const cur = new Date(yy, mm - 1, dd);
    const col = (cur.getDay() - weekStartsOn + 7) % 7;
    let next: Date | null = null;
    if (e.key === "ArrowLeft") next = addDays(cur, -1);
    else if (e.key === "ArrowRight") next = addDays(cur, 1);
    else if (e.key === "ArrowUp") next = addDays(cur, -7);
    else if (e.key === "ArrowDown") next = addDays(cur, 7);
    else if (e.key === "Home") next = addDays(cur, -col);
    else if (e.key === "End") next = addDays(cur, 6 - col);
    else if (e.key === "PageUp") next = addMonths(cur, e.shiftKey ? -12 : -1);
    else if (e.key === "PageDown") next = addMonths(cur, e.shiftKey ? 12 : 1);
    if (!next) return;
    e.preventDefault();
    if (min && next < min) next = min;
    if (max && next > max) next = max;
    shouldFocus.current = true;
    setFocus(next);
    if (!isSameMonth(next, view)) go(next);
  };

  React.useEffect(() => {
    if (!shouldFocus.current) return;
    shouldFocus.current = false;
    gridRef.current?.querySelector<HTMLElement>(`[data-date="${dayKey(focus)}"]`)?.focus();
  }, [focus, view]);

  return (
    <div className={cn("w-fit select-none p-3", className)}>
      {/* header */}
      <div className="mb-2 flex h-8 items-center gap-1">
        <NavButton label="Previous month" disabled={!canPrev} onClick={() => go(addMonths(view, -1), -1)}>
          <ChevronLeft className="size-4" />
        </NavButton>
        <div className="relative h-5 flex-1 overflow-hidden text-center text-sm font-medium text-neutral-900 dark:text-neutral-100">
          <AnimatePresence initial={false} custom={dir}>
            <motion.span
              key={y * 12 + m}
              custom={dir}
              variants={slide}
              initial="enter"
              animate="center"
              exit="exit"
              transition={spring}
              aria-live="polite"
              className="absolute inset-0"
            >
              {monthFmt.format(view)}
            </motion.span>
          </AnimatePresence>
        </div>
        <NavButton label="Next month" disabled={!canNext} onClick={() => go(addMonths(view, 1), 1)}>
          <ChevronRight className="size-4" />
        </NavButton>
      </div>

      {/* weekdays */}
      <div className="flex" aria-hidden>
        {weekdays.map((w, i) => (
          <div key={i} className="grid h-8 w-9 place-items-center text-xs text-neutral-400 dark:text-neutral-500">
            {w}
          </div>
        ))}
      </div>

      {/* days */}
      <div className="-m-1 overflow-hidden p-1">
        <div
          ref={gridRef}
          role="grid"
          aria-label={monthFmt.format(view)}
          onKeyDown={onKeyDown}
          onMouseLeave={() => setHover(null)}
          className="relative h-[216px]"
        >
          <AnimatePresence initial={false} custom={dir}>
            <motion.div
              key={y * 12 + m}
              custom={dir}
              variants={slide}
              initial="enter"
              animate="center"
              exit="exit"
              transition={spring}
              className="absolute inset-0"
            >
              {Array.from({ length: 6 }, (_, r) => (
                <div key={r} role="row" className="flex">
                  {cells.slice(r * 7, r * 7 + 7).map((d, col) => {
                    if (!d) return <div key={col} role="gridcell" className="h-9 w-9" />;

                    const isStart = isSameDay(d, from);
                    const isEnd = !isStart && isRange && isSameDay(d, to);
                    const picked = isStart || isEnd;
                    const inBand =
                      isRange && !!from && !!rangeEnd && !isSameDay(from, rangeEnd) && d >= from && d <= rangeEnd;
                    const off = disabled(d);
                    const isToday = isSameDay(d, today);

                    return (
                      <div
                        key={col}
                        role="gridcell"
                        aria-selected={picked || inBand}
                        className={cn(
                          "relative h-9 w-9",
                          inBand && "bg-neutral-100 dark:bg-neutral-800/70",
                          inBand && (isSameDay(d, from) || col === 0 || d.getDate() === 1) && "rounded-l-lg",
                          inBand && (isSameDay(d, rangeEnd) || col === 6 || d.getDate() === dim) && "rounded-r-lg"
                        )}
                      >
                        <button
                          type="button"
                          data-date={dayKey(d)}
                          tabIndex={isSameDay(d, tabDate) ? 0 : -1}
                          aria-label={dayFmt.format(d)}
                          aria-disabled={off || undefined}
                          aria-current={isToday ? "date" : undefined}
                          onClick={() => pick(d)}
                          onMouseEnter={() => setHover(d)}
                          className={cn(
                            "group relative grid size-9 place-items-center rounded-lg text-sm tabular-nums outline-none transition-colors focus-visible:ring-2 focus-visible:ring-neutral-400/60 dark:focus-visible:ring-neutral-600",
                            picked ? "text-white dark:text-neutral-900" : "text-neutral-900 dark:text-neutral-100",
                            !picked &&
                              !off &&
                              (inBand
                                ? "hover:bg-neutral-200/80 dark:hover:bg-neutral-700/60"
                                : "hover:bg-neutral-100 dark:hover:bg-neutral-800"),
                            off && "cursor-not-allowed opacity-30"
                          )}
                        >
                          {picked && (
                            <motion.span
                              layoutId={isRange ? `${uid}-${isStart ? "start" : "end"}` : `${uid}-sel`}
                              transition={spring}
                              className="absolute inset-0 rounded-lg bg-neutral-900 dark:bg-white"
                            />
                          )}
                          <span className="relative transition-transform duration-150 group-active:scale-90">
                            {d.getDate()}
                          </span>
                          {isToday && !picked && (
                            <span className="absolute bottom-1 size-1 rounded-full bg-neutral-400 dark:bg-neutral-500" />
                          )}
                        </button>
                      </div>
                    );
                  })}
                </div>
              ))}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

function NavButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="grid size-8 place-items-center rounded-lg text-neutral-500 outline-none transition hover:bg-neutral-100 hover:text-neutral-900 focus-visible:ring-2 focus-visible:ring-neutral-400/60 active:scale-95 disabled:pointer-events-none disabled:opacity-30 dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:text-white dark:focus-visible:ring-neutral-600"
    >
      {children}
    </button>
  );
}
