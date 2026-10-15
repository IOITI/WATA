"use client";

import * as React from "react";
import { AnimatePresence, motion, type Variants } from "framer-motion";
import { cn } from "../../lib/utils";

const spring = { type: "spring", stiffness: 420, damping: 34, mass: 0.8 } as const;
const fill = { duration: 0.35, ease: [0.32, 0.72, 0, 1] } as const;

export interface Step {
  title: string;
  description?: string;
}

export interface StepperProps {
  steps: Step[];
  /** Zero-based index of the active step. */
  current: number;
  /** Makes completed steps clickable (jump back). */
  onStepChange?: (index: number) => void;
  orientation?: "horizontal" | "vertical";
  className?: string;
}

export function Stepper({ steps, current, onStepChange, orientation = "horizontal", className }: StepperProps) {
  const uid = React.useId();
  const vertical = orientation === "vertical";
  const last = steps.length - 1;

  // don't replay the check-draw for steps that are already done on first paint
  const ready = React.useRef(false);
  React.useEffect(() => {
    ready.current = true;
  }, []);

  return (
    <ol aria-label="Progress" className={cn(vertical ? "flex flex-col" : "flex w-full items-center", className)}>
      {steps.map((step, i) => {
        const state = i < current ? "complete" : i === current ? "current" : "upcoming";
        const clickable = !!onStepChange && state === "complete";

        const indicator = (
          <span
            className={cn(
              "relative grid size-7 shrink-0 place-items-center rounded-full border text-xs font-medium tabular-nums transition-colors duration-300",
              state === "complete" &&
                "border-neutral-900 bg-neutral-900 text-white dark:border-white dark:bg-white dark:text-neutral-900",
              state === "current" && "border-neutral-900 text-neutral-900 dark:border-white dark:text-white",
              state === "upcoming" && "border-neutral-200 text-neutral-400 dark:border-neutral-800 dark:text-neutral-500"
            )}
          >
            {state === "current" && (
              <motion.span
                layoutId={`${uid}-ring`}
                transition={spring}
                className="absolute -inset-[5px] rounded-full border border-neutral-300 dark:border-neutral-700"
              />
            )}
            {state === "complete" ? (
              <svg
                viewBox="0 0 24 24"
                className="size-3.5"
                fill="none"
                stroke="currentColor"
                strokeWidth={3}
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden
              >
                <motion.path
                  d="M5 12.5l4.5 4.5L19 7.5"
                  initial={ready.current ? { pathLength: 0 } : false}
                  animate={{ pathLength: 1 }}
                  transition={{ duration: 0.3, delay: 0.08, ease: "easeOut" }}
                />
              </svg>
            ) : (
              i + 1
            )}
          </span>
        );

        const label = (
          <span
            className={cn(
              "flex min-w-0 flex-col text-left",
              !vertical && state !== "current" && "hidden sm:flex"
            )}
          >
            <span
              className={cn(
                "truncate text-sm transition-colors duration-300",
                state === "upcoming" ? "text-neutral-400 dark:text-neutral-500" : "font-medium text-neutral-900 dark:text-neutral-100"
              )}
            >
              {step.title}
              {state === "complete" && <span className="sr-only"> (completed)</span>}
            </span>
            {step.description && (
              <span className="truncate text-xs text-neutral-400 dark:text-neutral-500">{step.description}</span>
            )}
          </span>
        );

        const row = clickable ? (
          <button
            type="button"
            onClick={() => onStepChange!(i)}
            className={cn(
              "flex gap-3 rounded-lg text-left outline-none focus-visible:ring-2 focus-visible:ring-neutral-400/50 dark:focus-visible:ring-neutral-600/60",
              vertical ? "items-start" : "items-center"
            )}
          >
            {indicator}
            {label}
          </button>
        ) : (
          <div className={cn("flex gap-3", vertical ? "items-start" : "items-center")}>
            {indicator}
            {label}
          </div>
        );

        return (
          <li
            key={i}
            aria-current={state === "current" ? "step" : undefined}
            className={cn(
              vertical ? "relative pb-7 last:pb-0" : "flex items-center",
              !vertical && i < last && "flex-1"
            )}
          >
            {row}

            {i < last && (
              <span
                aria-hidden
                className={cn(
                  "overflow-hidden bg-neutral-200 dark:bg-neutral-800",
                  vertical ? "absolute bottom-1 left-[13.5px] top-[34px] w-px" : "relative mx-3 h-px flex-1"
                )}
              >
                <motion.span
                  initial={false}
                  animate={vertical ? { scaleY: i < current ? 1 : 0 } : { scaleX: i < current ? 1 : 0 }}
                  transition={fill}
                  className={cn(
                    "absolute inset-0 bg-neutral-900 dark:bg-white",
                    vertical ? "origin-top" : "origin-left"
                  )}
                />
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/* ───────────── panels: slide in the direction you're travelling ───────────── */

const slide: Variants = {
  enter: (d: number) => ({ x: d * 24, opacity: 0 }),
  center: { x: 0, opacity: 1 },
  exit: (d: number) => ({ x: d * -24, opacity: 0 }),
};

export interface StepPanelsProps {
  current: number;
  /** One child per step, in order. */
  children: React.ReactNode;
  className?: string;
}

export function StepPanels({
  current,
  children,
  className,
}: StepPanelsProps) {
  const panels = React.Children.toArray(children);
  const prev = React.useRef(current);
  const dir = current >= prev.current ? 1 : -1;
  React.useEffect(() => {
    prev.current = current;
  }, [current]);

  return (
    <div className={className}>
      <AnimatePresence mode="wait" initial={false} custom={dir}>
        <motion.div
          key={current}
          custom={dir}
          variants={slide}
          initial="enter"
          animate="center"
          exit="exit"
          transition={{ duration: 0.18, ease: [0.32, 0.72, 0, 1] }}
        >
          {panels[current]}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
