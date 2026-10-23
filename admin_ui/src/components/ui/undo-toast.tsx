'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Check,
  X,
  AlertTriangle,
  AlertCircle,
  Info,
  Trash2,
  Undo2
} from 'lucide-react';
import { cn } from '../../lib/utils';
import { motionTransitions } from '../../lib/motion-tokens';

export type UndoToastVariant = 'default' | 'success' | 'warning' | 'error' | 'info';
export type UndoToastPosition =
  | 'top-left'
  | 'top-center'
  | 'top-right'
  | 'bottom-left'
  | 'bottom-center'
  | 'bottom-right';

export interface UndoToastProps {
  /** Visibility state */
  open?: boolean;
  /** Toast message title (e.g. "File deleted") */
  title?: string;
  /** Secondary description or metadata */
  description?: string;
  /** Undo action button label */
  undoLabel?: string;
  /** Restored state message when Undo is triggered */
  restoredMessage?: string;
  /** Duration in milliseconds before automatic dismissal */
  duration?: number;
  /** Toast variant visual tone */
  variant?: UndoToastVariant;
  /** Toast screen placement */
  position?: UndoToastPosition;
  /** Callback fired when user clicks Undo */
  onUndo?: () => void;
  /** Callback fired when toast finishes timer or is closed */
  onDismiss?: () => void;
  /** Whether to show the countdown bar */
  showProgress?: boolean;
  /** Custom icon */
  icon?: React.ReactNode;
  /** Custom class name */
  className?: string;
  /** WATA: classes for the positioned wrapper, e.g. to clear a bar fixed at the bottom (as velocity-toast's). */
  wrapperClassName?: string;
}

export const UndoToast: React.FC<UndoToastProps> = ({
  open = true,
  title = 'Project archived',
  description = 'Changes will be permanent in a few seconds',
  undoLabel = 'Undo',
  restoredMessage = 'Restored successfully',
  duration = 5000,
  variant = 'default',
  position = 'bottom-center',
  onUndo,
  onDismiss,
  showProgress = true,
  icon,
  className,
  wrapperClassName,
}) => {
  const [isVisible, setIsVisible] = useState(open);
  const [isRestored, setIsRestored] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [progress, setProgress] = useState(100);

  const remainingTimeRef = useRef<number>(duration);
  const requestRef = useRef<number | null>(null);
  // WATA: the caller's callback through a ref: a new one at each render of the caller restarted the countdown.
  const onDismissRef = useRef(onDismiss);
  onDismissRef.current = onDismiss;

  useEffect(() => {
    setIsVisible(open);
    if (open) {
      setIsRestored(false);
      setProgress(100);
      remainingTimeRef.current = duration;
    }
  }, [open, duration]);

  const handleDismiss = useCallback(() => {
    setIsVisible(false);
    onDismissRef.current?.();
  }, []);

  const handleUndoClick = () => {
    if (isRestored) return;
    setIsRestored(true);
    if (requestRef.current) cancelAnimationFrame(requestRef.current);
    onUndo?.();
    setTimeout(() => {
      handleDismiss();
    }, 1400);
  };

  // WATA: the time left is kept at each frame, so that a pause takes up where it stopped, and the bar shows what
  // is left of the whole (EasyUI's went back to full after a pause, its time being the rest).
  useEffect(() => {
    if (!isVisible || isRestored || duration <= 0 || isPaused) return;

    const localStartTime = Date.now();
    const left = remainingTimeRef.current;

    const animate = () => {
      const elapsed = Date.now() - localStartTime;
      remainingTimeRef.current = Math.max(0, left - elapsed);
      setProgress((remainingTimeRef.current / duration) * 100);

      if (remainingTimeRef.current <= 0) {
        handleDismiss();
      } else {
        requestRef.current = requestAnimationFrame(animate);
      }
    };

    requestRef.current = requestAnimationFrame(animate);

    return () => {
      if (requestRef.current) cancelAnimationFrame(requestRef.current);
    };
  }, [isVisible, isPaused, isRestored, duration, handleDismiss]);

  const positionClasses: Record<UndoToastPosition, string> = {
    'top-left': 'top-5 left-5 items-start',
    'top-center': 'top-5 left-1/2 -translate-x-1/2 items-center',
    'top-right': 'top-5 right-5 items-end',
    'bottom-left': 'bottom-5 left-5 items-start',
    'bottom-center': 'bottom-5 left-1/2 -translate-x-1/2 items-center',
    'bottom-right': 'bottom-5 right-5 items-end',
  };

  const getVariantIcon = () => {
    if (icon) return icon;
    switch (variant) {
      case 'success':
        return <Check className="w-4 h-4 text-emerald-700 dark:text-emerald-400" />;
      case 'warning':
        return <AlertTriangle className="w-4 h-4 text-amber-700 dark:text-amber-400" />;
      case 'error':
        return <AlertCircle className="w-4 h-4 text-rose-700 dark:text-rose-400" />;
      case 'info':
        return <Info className="w-4 h-4 text-sky-700 dark:text-sky-400" />;
      default:
        return <Trash2 className="w-4 h-4 text-neutral-500 dark:text-[#A1A1A1]" />;
    }
  };

  return (
    <div
      className={cn(
        'fixed z-50 pointer-events-none flex flex-col',
        positionClasses[position],
        wrapperClassName
      )}
    >
      <AnimatePresence>
        {isVisible && (
          <motion.div
            layout
            initial={{ opacity: 0, y: position.startsWith('top') ? -16 : 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, scale: 0.94, y: position.startsWith('top') ? -10 : 10 }}
            transition={motionTransitions.springSnappy}
            onMouseEnter={() => setIsPaused(true)}
            onMouseLeave={() => setIsPaused(false)}
            // WATA: paused while the keyboard is in it too, as under the pointer.
            onFocus={() => setIsPaused(true)}
            onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setIsPaused(false);
            }}
            // WATA: each colour has a light value in front of EasyUI's own, kept behind `dark:`.
            className={cn(
              'pointer-events-auto relative overflow-hidden rounded-xl border border-neutral-200 bg-white text-neutral-900 shadow-[0_12px_32px_rgba(0,0,0,0.14)] p-3.5 sm:p-4 min-w-[280px] sm:min-w-[340px] max-w-md font-sans select-none dark:border-[#1F1F1F] dark:bg-[#0E0E0E] dark:text-[#FAFAFA] dark:shadow-[0_12px_32px_rgba(0,0,0,0.6)]',
              className
            )}
            role="status"
            aria-live="polite"
          >
            <div className="flex items-center justify-between gap-3 relative z-10">
              {/* Icon / Status indicator */}
              <div className="w-8 h-8 rounded-lg bg-neutral-100 border border-neutral-200 flex items-center justify-center shrink-0 dark:bg-[#141414] dark:border-[#1F1F1F]">
                {isRestored ? (
                  <motion.div
                    initial={{ scale: 0 }}
                    animate={{ scale: 1 }}
                    transition={motionTransitions.springSnappy}
                  >
                    <Check className="w-4 h-4 text-emerald-700 dark:text-emerald-400" />
                  </motion.div>
                ) : (
                  getVariantIcon()
                )}
              </div>

              {/* Text copy */}
              <div className="flex-1 min-w-0 pr-1">
                <p className="text-xs font-semibold text-neutral-900 truncate dark:text-[#FAFAFA]">
                  {isRestored ? restoredMessage : title}
                </p>
                {description && !isRestored && (
                  <p className="text-[11px] text-neutral-600 truncate mt-0.5 dark:text-[#A1A1A1]">
                    {description}
                  </p>
                )}
              </div>

              {/* Actions */}
              <div className="flex items-center gap-1.5 shrink-0">
                {!isRestored && (
                  <button
                    type="button"
                    onClick={handleUndoClick}
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-neutral-100 hover:bg-white border border-neutral-200 hover:border-neutral-400 text-xs font-medium text-neutral-900 transition-colors focus-ring cursor-pointer dark:bg-[#141414] dark:hover:bg-[#0E0E0E] dark:border-[#1F1F1F] dark:hover:border-[#4A4A4A] dark:text-[#FAFAFA]"
                  >
                    <Undo2 className="w-3 h-3 text-neutral-500 dark:text-[#A1A1A1]" />
                    <span>{undoLabel}</span>
                  </button>
                )}

                <button
                  type="button"
                  onClick={handleDismiss}
                  className="p-1 rounded-md text-neutral-500 hover:text-neutral-900 hover:bg-neutral-100 transition-colors focus-ring cursor-pointer dark:text-[#6B6B6B] dark:hover:text-[#FAFAFA] dark:hover:bg-[#141414]"
                  aria-label="Close notification"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            {/* Live Progress Bar countdown indicator */}
            {showProgress && !isRestored && (
              <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-neutral-100 dark:bg-[#050505]">
                <div
                  className="h-full bg-neutral-900 transition-all dark:bg-[#FAFAFA]"
                  style={{ width: `${progress}%`, transition: isPaused ? 'none' : 'width 50ms linear' }}
                />
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
