"use client";

import * as React from "react";
import { AnimatePresence, motion, type HTMLMotionProps } from "framer-motion";
import { X } from "lucide-react";
import { cn } from "../../lib/utils";

const spring = { type: "spring", stiffness: 520, damping: 36, mass: 0.6 } as const;

export type BadgeVariant = "soft" | "solid" | "outline";
export type BadgeTone = "neutral" | "success" | "warning" | "danger";

const styles: Record<BadgeVariant, Record<BadgeTone, string>> = {
  soft: {
    neutral: "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300",
    success: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400",
    warning: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-400",
    danger: "bg-red-50 text-red-700 dark:bg-red-500/10 dark:text-red-400",
  },
  solid: {
    neutral: "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900",
    success: "bg-emerald-600 text-white dark:bg-emerald-500 dark:text-emerald-950",
    warning: "bg-amber-500 text-amber-950",
    danger: "bg-red-600 text-white dark:bg-red-500 dark:text-red-950",
  },
  outline: {
    neutral: "border-neutral-200 text-neutral-700 dark:border-neutral-800 dark:text-neutral-300",
    success: "border-emerald-200 text-emerald-700 dark:border-emerald-500/30 dark:text-emerald-400",
    warning: "border-amber-200 text-amber-700 dark:border-amber-500/30 dark:text-amber-400",
    danger: "border-red-200 text-red-700 dark:border-red-500/30 dark:text-red-400",
  },
};

const dots: Record<BadgeTone, string> = {
  neutral: "bg-neutral-400",
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-red-500",
};

export type BadgeProps = Omit<HTMLMotionProps<"span">, "children"> & {
  variant?: BadgeVariant;
  tone?: BadgeTone;
  /** Leading status dot. */
  dot?: boolean;
  /** Soft ping on the dot, use for live status only. */
  pulse?: boolean;
  /** Shows an × button (chip). */
  onRemove?: () => void;
  children?: React.ReactNode;
};

export function Badge({
  variant = "soft",
  tone = "neutral",
  dot,
  pulse,
  onRemove,
  className,
  children,
  ...props
}: BadgeProps) {
  // strings & numbers roll when they change (counts, status labels)
  const roll = typeof children === "string" || typeof children === "number";

  return (
    <motion.span
      style={{ fontFamily: "var(--font-sans, 'Geist', sans-serif)", ...props.style }}
      className={cn(
        "font-sans relative inline-flex h-6 items-center gap-1.5 whitespace-nowrap rounded-full border border-transparent px-2.5 text-xs font-medium",
        styles[variant][tone],
        onRemove && "pr-1.5",
        className
      )}
      {...props}
    >
      {dot && (
        <span className="relative flex size-1.5 shrink-0">
          {pulse && (
            <span
              className={cn(
                "absolute inset-0 animate-ping rounded-full opacity-60 motion-reduce:animate-none",
                variant === "solid" ? "bg-current" : dots[tone]
              )}
            />
          )}
          <span className={cn("relative size-1.5 rounded-full", variant === "solid" ? "bg-current" : dots[tone])} />
        </span>
      )}

      {roll ? (
        <span className="relative inline-flex overflow-hidden py-px">
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.span
              key={String(children)}
              initial={{ y: "70%", opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              exit={{ y: "-70%", opacity: 0 }}
              transition={spring}
              className="inline-block tabular-nums"
            >
              {children}
            </motion.span>
          </AnimatePresence>
        </span>
      ) : (
        children
      )}

      {onRemove && (
        <button
          type="button"
          aria-label="Remove"
          onClick={onRemove}
          className="grid size-4 place-items-center rounded-full opacity-60 outline-none transition hover:bg-black/10 hover:opacity-100 focus-visible:ring-2 focus-visible:ring-current dark:hover:bg-white/15"
        >
          <X className="size-3" />
        </button>
      )}
    </motion.span>
  );
}
