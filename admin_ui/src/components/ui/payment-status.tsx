'use client';

import React, { useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Check, AlertCircle, Copy, Send, Hourglass, CircleHelp, ArrowRight, X } from 'lucide-react';
import { cn, copyToClipboard } from '../../lib/utils';
import { motionTransitions } from '../../lib/motion-tokens';

// WATA: the steps of what the operator asked for (a close, a signal sent by hand) rather than of a payment: asked,
// sent and waiting for its answer, done, refused, or no news ('unknown'). EasyUI's 'refunded' is gone.
export type PaymentLifecycleStatus = 'processing' | 'verifying' | 'success' | 'failed' | 'unknown';

/** WATA: one figure of the outcome, in place of the receipt's items. */
export interface PaymentStatusFigure {
  label: string;
  value: React.ReactNode;
}

export interface PaymentStatusProps {
  /** Current payment status */
  status?: PaymentLifecycleStatus;
  /** WATA: the step's heading, in the caller's words (EasyUI's were a payment's). */
  title: string;
  /** WATA: what the step means, in the caller's words. */
  description?: React.ReactNode;
  /** WATA: what it is about, in the pill (EasyUI's amount). */
  subject?: React.ReactNode;
  /** WATA: the outcome's figures, once known (EasyUI's transaction details and receipt). */
  figures?: PaymentStatusFigure[];
  /** WATA: a reference to copy (an order, a signal), with its name. */
  reference?: { label: string; value: string };
  /** WATA: where it is read further. */
  link?: { href: string; label: string };
  /** WATA: what draws the link (React Router's). */
  linkComponent?: React.ElementType;
  /** WATA: the card is put away. It watched, and acted on nothing. */
  onDismiss?: () => void;
  /** Custom class name */
  className?: string;
}

// WATA: each colour has a light value in front of EasyUI's own, kept behind `dark:`.
const BADGE = 'w-11 h-11 shrink-0 rounded-full flex items-center justify-center relative border';

// WATA: no Retry, no other payment method, no receipt to view or download: the card follows what was asked and
// acts on nothing. Laid out in a row (the badge beside the words): docked at the edge of the screen, EasyUI's
// centred column took half of a phone's height.
export const PaymentStatus: React.FC<PaymentStatusProps> = ({
  status = 'processing',
  title,
  description,
  subject,
  figures = [],
  reference,
  link,
  linkComponent: LinkComponent = 'a',
  onDismiss,
  className,
}) => {
  const [copied, setCopied] = useState(false);
  const reduce = useReducedMotion();
  // WATA: a spinner stands still where motion is reduced.
  const spin = reduce ? undefined : { rotate: 360 };

  const handleCopyId = () => {
    if (!reference) return;
    void copyToClipboard(reference.value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      className={cn(
        'w-full rounded-2xl border border-neutral-200 bg-white p-5 text-left font-sans shadow-[0_12px_30px_-10px_rgba(0,0,0,0.18)] transition-all dark:border-[#1F1F1F] dark:bg-[#0E0E0E] dark:shadow-[0_12px_30px_-10px_rgba(0,0,0,0.6)]',
        className
      )}
    >
      <div className="flex items-start gap-4">
        {/* Status Indicator Badge */}
        <div className="relative" aria-hidden="true">
          {status === 'processing' && (
            <div className={cn(BADGE, 'bg-neutral-100 border-neutral-200 dark:bg-[#141414] dark:border-[#1F1F1F]')}>
              <motion.div
                className="absolute inset-0 rounded-full border border-neutral-900/25 border-t-neutral-900 dark:border-white/30 dark:border-t-white"
                animate={spin}
                transition={{ duration: 1.2, repeat: Infinity, ease: 'linear' }}
              />
              <Send className="w-4 h-4 text-neutral-500 dark:text-[#A1A1A1]" />
            </div>
          )}

          {status === 'verifying' && (
            <div className={cn(BADGE, 'bg-neutral-100 border-neutral-200 dark:bg-[#141414] dark:border-[#1F1F1F]')}>
              <motion.div
                className="absolute inset-1 rounded-full border border-sky-600/30 border-t-sky-600 dark:border-sky-400/40 dark:border-t-sky-400"
                animate={spin}
                transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
              />
              <Hourglass className="w-5 h-5 text-sky-700 dark:text-sky-400" />
            </div>
          )}

          {status === 'success' && (
            <motion.div
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={motionTransitions.springSnappy}
              className={cn(BADGE, 'bg-emerald-500/10 border-emerald-600/30 text-emerald-700 dark:border-emerald-500/30 dark:text-emerald-400')}
            >
              <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <motion.path
                  d="M20 6L9 17l-5-5"
                  initial={{ pathLength: reduce ? 1 : 0 }}
                  animate={{ pathLength: 1 }}
                  transition={{ duration: 0.4, delay: 0.1, ease: 'easeOut' }}
                />
              </svg>
            </motion.div>
          )}

          {status === 'failed' && (
            <motion.div
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={motionTransitions.springSnappy}
              className={cn(BADGE, 'bg-rose-500/10 border-rose-600/30 text-rose-700 dark:border-rose-500/30 dark:text-rose-400')}
            >
              <AlertCircle className="w-6 h-6" />
            </motion.div>
          )}

          {status === 'unknown' && (
            <motion.div
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={motionTransitions.springSnappy}
              className={cn(BADGE, 'bg-neutral-100 border-neutral-200 text-neutral-500 dark:bg-[#141414] dark:border-[#1F1F1F] dark:text-[#A1A1A1]')}
            >
              <CircleHelp className="w-6 h-6" />
            </motion.div>
          )}
        </div>

        {/* WATA: read out as it changes. */}
        <div className="min-w-0 flex-1" role="status" aria-live="polite">
          <h3 className="text-base font-semibold text-neutral-900 tracking-tight dark:text-[#FAFAFA]">{title}</h3>
          {description && (
            <p className="text-xs sm:text-sm text-neutral-600 mt-1 dark:text-[#A1A1A1]">{description}</p>
          )}
          {/* Amount Pill. WATA: what it is about. */}
          {subject && (
            <div className="mt-3 inline-flex max-w-full items-center gap-1.5 px-3 py-1 rounded-full bg-neutral-100 border border-neutral-200 dark:bg-[#141414] dark:border-[#1F1F1F]">
              <span className="truncate text-xs font-medium text-neutral-900 dark:text-[#FAFAFA]">{subject}</span>
            </div>
          )}
        </div>

        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Close"
            className="-mr-1 -mt-1 shrink-0 rounded-md p-1 text-neutral-500 transition-colors hover:text-neutral-900 dark:text-[#6B6B6B] dark:hover:text-[#FAFAFA]"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Transaction Details Staggered Card. WATA: whenever there is something to show, a refusal too. */}
      <AnimatePresence mode="wait">
        {(figures.length > 0 || reference || link) && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 12 }}
            transition={motionTransitions.springGentle}
            className="mt-4 pt-4 border-t border-neutral-200 space-y-1.5 text-xs dark:border-[#1F1F1F]"
          >
            {figures.map((figure) => (
              <div key={figure.label} className="flex items-baseline justify-between gap-3 py-0.5">
                <span className="text-neutral-600 dark:text-[#A1A1A1]">{figure.label}</span>
                <span className="min-w-0 text-right tabular-nums text-neutral-900 dark:text-[#FAFAFA]">{figure.value}</span>
              </div>
            ))}

            {reference && (
              <div className="flex items-center justify-between gap-3 py-0.5">
                <span className="text-neutral-600 dark:text-[#A1A1A1]">{reference.label}</span>
                <button
                  type="button"
                  onClick={handleCopyId}
                  className="inline-flex min-w-0 items-center gap-1 font-mono text-neutral-600 hover:text-neutral-900 transition-colors px-1.5 py-0.5 rounded bg-neutral-100 border border-neutral-200 cursor-pointer dark:text-[#A1A1A1] dark:hover:text-[#FAFAFA] dark:bg-[#141414] dark:border-[#1F1F1F]"
                  aria-label={`Copy ${reference.label.toLowerCase()} ${reference.value}`}
                >
                  <span className="truncate">{reference.value}</span>
                  {copied ? (
                    <Check className="w-3 h-3 shrink-0 text-emerald-700 dark:text-emerald-400" />
                  ) : (
                    <Copy className="w-3 h-3 shrink-0 text-neutral-500 dark:text-[#6B6B6B]" />
                  )}
                </button>
              </div>
            )}

            {link && (
              <div className="pt-2">
                <LinkComponent
                  href={link.href}
                  className="inline-flex items-center gap-1.5 text-xs font-medium text-neutral-900 underline-offset-4 hover:underline dark:text-[#FAFAFA]"
                >
                  {link.label}
                  <ArrowRight className="w-3.5 h-3.5" aria-hidden="true" />
                </LinkComponent>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
