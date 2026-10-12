"use client";

import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CalendarDays } from "lucide-react";
import { cn } from "../../lib/utils";
import { Calendar, type CalendarOptions, type DateRange } from "./calendar";

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

type Side = "bottom" | "top";
type CloseReason = "escape" | "outside";

function usePopover({
  open,
  onClose,
  rootRef,
  height = 340,
  side: preferredSide = "auto",
}: {
  open: boolean;
  onClose: (reason: CloseReason) => void;
  rootRef: React.RefObject<HTMLElement | null>;
  height?: number;
  side?: Side | "auto";
}) {
  const [side, setSide] = React.useState<Side>("bottom");

  React.useLayoutEffect(() => {
    if (!open || !rootRef.current) return;
    if (preferredSide && preferredSide !== "auto") {
      setSide(preferredSide);
      return;
    }
    // Always open downward on mobile viewports (< 640px)
    const isMobile = typeof window !== "undefined" && window.innerWidth < 640;
    if (isMobile) {
      setSide("bottom");
      return;
    }
    const rect = rootRef.current.getBoundingClientRect();
    const below = window.innerHeight - rect.bottom;
    // On desktop, only flip upward if space below is tight AND there is sufficient space above
    setSide(below < height && rect.top >= height ? "top" : "bottom");
  }, [open, height, rootRef, preferredSide]);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) onClose("outside");
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose("escape");
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose, rootRef]);

  return side;
}

const popoverMotion = (side: Side, align: "start" | "end" = "start") =>
  ({
    initial: { opacity: 0, scale: 0.96 },
    animate: { opacity: 1, scale: 1 },
    exit: { opacity: 0, scale: 0.97, transition: { duration: 0.1 } },
    transition: { type: "spring", stiffness: 520, damping: 38, mass: 0.7 },
    style: {
      transformOrigin: `${side === "top" ? "bottom" : "top"} ${align === "end" ? "right" : "left"}`,
    },
  }) as const;

const fmt = (d: Date, locale?: string) => new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(d);

/* ───────────── shell: trigger + animated popover ───────────── */

function PickerShell({
  text,
  placeholder,
  align = "start",
  side: preferredSide = "auto",
  disabled,
  className,
  children,
}: {
  text: string | null;
  placeholder: string;
  align?: "start" | "end";
  side?: Side | "auto";
  disabled?: boolean;
  className?: string;
  children: (api: { close: () => void }) => React.ReactNode;
}) {
  const [open, setOpen] = React.useState(false);
  const rootRef = React.useRef<HTMLDivElement>(null);
  const triggerRef = React.useRef<HTMLButtonElement>(null);
  const timer = React.useRef<number | undefined>(undefined);

  const side = usePopover({
    open,
    rootRef,
    height: 340,
    side: preferredSide,
    onClose: (reason) => {
      setOpen(false);
      if (reason === "escape") triggerRef.current?.focus();
    },
  });

  // brief beat so the selection pill lands before the popover closes
  const close = React.useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const a = document.activeElement;
      setOpen(false);
      // only hand focus back if it hasn't moved elsewhere in the meantime
      if (!a || a === document.body || rootRef.current?.contains(a)) triggerRef.current?.focus();
    }, 160);
  }, []);
  React.useEffect(() => () => window.clearTimeout(timer.current), []);

  return (
    <div ref={rootRef} className={cn("relative inline-block w-64 max-w-full", className)}>
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex h-10 w-full items-center gap-2.5 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none transition-colors hover:border-neutral-300 focus-visible:ring-2 focus-visible:ring-neutral-400/40 disabled:pointer-events-none disabled:opacity-50 dark:border-neutral-800 dark:bg-neutral-950 dark:hover:border-neutral-700 dark:focus-visible:ring-neutral-600/50",
          open && "border-neutral-400 hover:border-neutral-400 dark:border-neutral-600 dark:hover:border-neutral-600"
        )}
      >
        <CalendarDays className="size-4 shrink-0 text-neutral-400" />
        <span
          className={cn(
            "truncate",
            text ? "text-neutral-900 dark:text-neutral-100" : "text-neutral-400 dark:text-neutral-500"
          )}
        >
          {text ?? placeholder}
        </span>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            role="dialog"
            aria-label="Choose date"
            {...popoverMotion(side, align)}
            className={cn(
              "absolute z-50 w-max max-w-[calc(100vw-2rem)] rounded-xl border border-neutral-200 bg-white shadow-lg shadow-neutral-900/5 dark:border-neutral-800 dark:bg-neutral-950 dark:shadow-black/40",
              side === "top" ? "bottom-full mb-2" : "top-full mt-2",
              align === "end" ? "right-0" : "left-0"
            )}
          >
            {children({ close })}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* ───────────── single date ───────────── */

export interface DatePickerProps extends CalendarOptions {
  value?: Date | null;
  defaultValue?: Date | null;
  onChange?: (date: Date | null) => void;
  placeholder?: string;
  align?: "start" | "end";
  side?: Side | "auto";
  disabled?: boolean;
  className?: string;
}

export function DatePicker({
  value,
  defaultValue = null,
  onChange,
  placeholder = "Pick a date",
  align,
  side,
  disabled,
  className,
  ...calendar
}: DatePickerProps) {
  const [date, setDate] = useControllableState<Date | null>(value, defaultValue, onChange);

  return (
    <PickerShell
      text={date ? fmt(date, calendar.locale) : null}
      placeholder={placeholder}
      align={align}
      side={side}
      disabled={disabled}
      className={className}
    >
      {({ close }) => (
        <Calendar
          mode="single"
          value={date}
          onChange={(d) => {
            setDate(d);
            close();
          }}
          {...calendar}
        />
      )}
    </PickerShell>
  );
}

/* ───────────── date range ───────────── */

export interface DateRangePickerProps extends CalendarOptions {
  value?: DateRange;
  defaultValue?: DateRange;
  onChange?: (range: DateRange) => void;
  placeholder?: string;
  align?: "start" | "end";
  side?: Side | "auto";
  disabled?: boolean;
  className?: string;
}

export function DateRangePicker({
  value,
  defaultValue = { from: null, to: null },
  onChange,
  placeholder = "Pick a range",
  align,
  side,
  disabled,
  className,
  ...calendar
}: DateRangePickerProps) {
  const [range, setRange] = useControllableState<DateRange>(value, defaultValue, onChange);
  const text = range.from
    ? `${fmt(range.from, calendar.locale)} – ${range.to ? fmt(range.to, calendar.locale) : "…"}`
    : null;

  return (
    <PickerShell
      text={text}
      placeholder={placeholder}
      align={align}
      side={side}
      disabled={disabled}
      className={cn("w-72", className)}
    >
      {({ close }) => (
        <Calendar
          mode="range"
          value={range}
          onChange={(r) => {
            setRange(r);
            if (r.from && r.to) close();
          }}
          {...calendar}
        />
      )}
    </PickerShell>
  );
}
