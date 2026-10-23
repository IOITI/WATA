'use client';

// WATA: EasyUI's island reduced to its two first states, collapsed and expanded, with the caller's content in both.
// Its profile and share states, avatar, greeting and social links are gone. The pill stays in the flow of the top
// bar it sits in, and the opened island grows from it below (shared layoutId), so the bar keeps its height.

import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { X } from 'lucide-react';
import { cn } from '../../lib/utils';

export type DynamicIslandState = 'collapsed' | 'expanded';

export interface DynamicIslandProps {
  /** WATA: what the pill shows, collapsed: the whole pill opens the island. */
  compact: React.ReactNode;
  /** WATA: what the island shows, opened. */
  children: React.ReactNode;
  /** WATA: the name of the pill and of the opened island, for screen readers. */
  label: string;
  defaultState?: DynamicIslandState;
  state?: DynamicIslandState;
  onStateChange?: (state: DynamicIslandState) => void;
  className?: string;
}

/**
 * WATA: a dialog over the page (the confirmation of a Close opened from the island) takes its own clicks and its
 * Escape, which starts in it: it holds the focus while it is open.
 */
function inDialog(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest('[aria-modal="true"]') !== null;
}

export const DynamicIsland: React.FC<DynamicIslandProps> = ({
  compact,
  children,
  label,
  defaultState = 'collapsed',
  state: controlledState,
  onStateChange,
  className,
}) => {
  const [internalState, setInternalState] = useState<DynamicIslandState>(defaultState);
  const isControlled = controlledState !== undefined;
  const currentState = isControlled ? controlledState : internalState;

  const rawId = useId();
  const instanceId = `dyn-island-${rawId.replace(/[:]/g, '')}`;

  const containerRef = useRef<HTMLDivElement>(null);
  const pillRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const shouldReduceMotion = useReducedMotion();

  const handleStateChange = useCallback(
    (nextState: DynamicIslandState) => {
      if (!isControlled) {
        setInternalState(nextState);
      }
      onStateChange?.(nextState);
    },
    [isControlled, onStateChange]
  );

  // WATA: the keyboard follows the island: into it when it opens, back to the pill when it closes.
  const opened = useRef(false);
  useEffect(() => {
    if (currentState === 'expanded') {
      opened.current = true;
      closeRef.current?.focus();
    } else if (opened.current) {
      opened.current = false;
      pillRef.current?.focus();
    }
  }, [currentState]);

  // Close when clicking outside or pressing Escape
  useEffect(() => {
    if (currentState === 'collapsed') return;

    const handlePointerDown = (event: PointerEvent) => {
      if (inDialog(event.target)) return;
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        handleStateChange('collapsed');
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !inDialog(event.target)) {
        handleStateChange('collapsed');
      }
    };

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [currentState, handleStateChange]);

  // Spring physics matching EasyUI motion philosophy
  const springTransition = shouldReduceMotion
    ? { duration: 0 }
    : {
        type: 'spring' as const,
        stiffness: 400,
        damping: 30,
        mass: 0.8,
      };

  const contentTransition = shouldReduceMotion
    ? { duration: 0 }
    : { duration: 0.2, ease: [0.22, 1, 0.36, 1] as const };

  const surface = cn(
    'border backdrop-blur-md transition-colors',
    'bg-white/95 dark:bg-[#141416]/95',
    'border-black/[0.08] dark:border-white/[0.12]',
    'text-[#0A0A0A] dark:text-[#F4F4F5]',
    'shadow-[0_12px_32px_-6px_rgba(0,0,0,0.12),0_0_0_1px_rgba(0,0,0,0.04)] dark:shadow-[0_16px_40px_-8px_rgba(0,0,0,0.7),0_0_0_1px_rgba(255,255,255,0.06)]'
  );

  const expanded = currentState === 'expanded';

  return (
    <div ref={containerRef} className={cn('relative inline-flex items-center justify-center', className)}>
      {/* STATE 1: COLLAPSED. WATA: the pill is a button, as wide as it is when the island is open (it keeps its place). */}
      <motion.button
        ref={pillRef}
        type="button"
        layoutId={expanded ? undefined : instanceId}
        transition={springTransition}
        onClick={() => handleStateChange('expanded')}
        aria-expanded={expanded}
        aria-controls={expanded ? `${instanceId}-panel` : undefined}
        className={cn(
          surface,
          'inline-flex h-8 items-center gap-2 rounded-full px-3 text-xs font-medium cursor-pointer hover:bg-white dark:hover:bg-[#1A1A1D]',
          expanded && 'invisible'
        )}
      >
        <span className="sr-only">{label}:</span>{' '}
        {compact}
      </motion.button>

      {/* STATE 2: EXPANDED */}
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="island-expanded"
            id={`${instanceId}-panel`}
            role="region"
            aria-label={label}
            layoutId={instanceId}
            transition={springTransition}
            className={cn(
              surface,
              // From the pill's left edge, over the page rather than the menu beside it; on a phone across the screen
              // under the top bar.
              'fixed inset-x-4 top-16 z-40 overflow-hidden rounded-[26px] sm:absolute sm:inset-x-auto sm:left-0 sm:top-0 sm:w-[22rem]'
            )}
          >
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={contentTransition}
              className="relative p-4"
            >
              <button
                ref={closeRef}
                type="button"
                onClick={() => handleStateChange('collapsed')}
                aria-label={`Close ${label.toLowerCase()}`}
                className="absolute right-3 top-3 w-7 h-7 flex items-center justify-center rounded-full text-neutral-500 dark:text-neutral-400 hover:text-neutral-950 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/10 active:scale-95 transition-all cursor-pointer"
              >
                <X className="w-3.5 h-3.5" />
              </button>
              {children}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
