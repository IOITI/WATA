'use client';

// WATA: reworked around EasyUI's card (its look, motion and placement). EasyUI's wrapped the trigger in a second tab
// stop and opened on any click, which also followed the link inside. Here the trigger keeps its own focus and
// click, the card opens on hover, on keyboard focus and on a first tap (touch), and it is drawn in document.body
// (a table's overflow cut it). The payment card it showed by default (customer, e-mail copy) is gone: the content
// is the caller's.

import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { ArrowRight } from 'lucide-react';
import { cn } from '../../lib/utils';
import { motionTransitions } from '../../lib/motion-tokens';

export interface PeekCardProps {
  /** Trigger element (e.g. text link, badge, invoice pill). WATA: one element, which keeps its own focus and click. */
  children: React.ReactElement;
  /** Custom peek preview renderer. WATA: called only while the card is open: what it reads is read then. */
  renderContent: () => React.ReactNode;
  /** Hover delay in milliseconds */
  delay?: number;
  /** Placement preference: 'top' | 'bottom' | 'auto' */
  placement?: 'top' | 'bottom' | 'auto';
  /**
   * WATA: on a touch screen, the first tap on the trigger opens the card instead of following the trigger's link;
   * a second tap follows it, and so does the card's own link. Off, a tap follows the link at once.
   */
  openOnTap?: boolean;
  /** WATA: the card's own link to what it shows ("Open"), drawn by `linkComponent`. */
  href?: string;
  hrefLabel?: string;
  linkComponent?: React.ElementType;
  /** Custom class name for trigger */
  className?: string;
}

const CARD_WIDTH = 288;
const MARGIN = 8;

interface Place {
  left: number;
  top: number;
  side: 'top' | 'bottom';
}

export const PeekCard: React.FC<PeekCardProps> = ({
  children,
  renderContent,
  delay = 200,
  placement = 'auto',
  openOnTap = true,
  href,
  hrefLabel = 'Open',
  linkComponent: LinkComponent = 'a',
  className,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [place, setPlace] = useState<Place | null>(null);
  const triggerRef = useRef<HTMLSpanElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const openTimer = useRef<number | null>(null);
  const closeTimer = useRef<number | null>(null);
  const lastPointer = useRef<string>('mouse');
  const id = useId();

  const clearTimers = () => {
    if (openTimer.current) window.clearTimeout(openTimer.current);
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
    openTimer.current = null;
    closeTimer.current = null;
  };

  const open = useCallback(() => {
    clearTimers();
    setIsOpen(true);
  }, []);

  const close = useCallback(() => {
    clearTimers();
    setIsOpen(false);
  }, []);

  // A moment to move from the trigger into the card, which is elsewhere in the page.
  const closeSoon = () => {
    if (openTimer.current) window.clearTimeout(openTimer.current);
    openTimer.current = null;
    closeTimer.current = window.setTimeout(close, 120);
  };

  const openSoon = () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
    closeTimer.current = null;
    if (isOpen || openTimer.current) return;
    openTimer.current = window.setTimeout(open, delay);
  };

  useEffect(() => clearTimers, []);

  // Auto: prefer top unless space above is tight (< 220px). WATA: centred on the trigger, kept inside the window.
  useLayoutEffect(() => {
    if (!isOpen || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const spaceAbove = rect.top;
    const spaceBelow = window.innerHeight - rect.bottom;
    const side =
      placement === 'auto' ? (spaceAbove < 220 && spaceBelow > spaceAbove ? 'bottom' : 'top') : placement;
    const width = Math.min(CARD_WIDTH, window.innerWidth - MARGIN * 2);
    const left = Math.min(Math.max(rect.left + rect.width / 2 - width / 2, MARGIN), window.innerWidth - width - MARGIN);
    setPlace({ left, top: side === 'top' ? rect.top - MARGIN : rect.bottom + MARGIN, side });
  }, [isOpen, placement]);

  // Closed by a tap or a click elsewhere, Escape, a scroll or a new size of the window (it would be left behind).
  useEffect(() => {
    if (!isOpen) return;
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!triggerRef.current?.contains(target) && !cardRef.current?.contains(target)) close();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [isOpen, close]);

  const trigger = React.cloneElement(children as React.ReactElement<Record<string, unknown>>, {
    'aria-describedby': isOpen ? id : undefined,
  });

  return (
    <span
      ref={triggerRef}
      className={cn('inline', className)}
      onPointerDownCapture={(event) => {
        lastPointer.current = event.pointerType;
      }}
      onPointerEnter={(event) => {
        if (event.pointerType !== 'touch') openSoon();
      }}
      onPointerLeave={(event) => {
        if (event.pointerType !== 'touch') closeSoon();
      }}
      // A first tap opens the card; the link it is on is followed by a second.
      onClickCapture={(event) => {
        if (!openOnTap || lastPointer.current !== 'touch' || isOpen) return;
        event.preventDefault();
        event.stopPropagation();
        open();
      }}
      onFocus={(event) => {
        // Keyboard focus only: a click focuses a link too, and follows it.
        let keyboard = true;
        try {
          keyboard = (event.target as Element).matches(':focus-visible');
        } catch {
          keyboard = true;
        }
        if (keyboard) openSoon();
      }}
      onBlur={(event) => {
        if (!cardRef.current?.contains(event.relatedTarget as Node | null)) closeSoon();
      }}
    >
      {/* Anchor Trigger Component */}
      {trigger}

      {/* Floating Connected Peek Card */}
      {typeof document !== 'undefined' &&
        createPortal(
          <AnimatePresence>
            {isOpen && place && (
              <div
                className="fixed z-50"
                style={{
                  left: place.left,
                  top: place.top,
                  width: Math.min(CARD_WIDTH, window.innerWidth - MARGIN * 2),
                  transform: place.side === 'top' ? 'translateY(-100%)' : undefined,
                }}
              >
                <motion.div
                  ref={cardRef}
                  id={id}
                  initial={{
                    opacity: 0,
                    y: place.side === 'top' ? 6 : -6,
                    scale: 0.96,
                  }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{
                    opacity: 0,
                    y: place.side === 'top' ? 4 : -4,
                    scale: 0.96,
                    transition: { duration: 0.12 },
                  }}
                  transition={motionTransitions.springSnappy}
                  onPointerEnter={() => {
                    if (closeTimer.current) window.clearTimeout(closeTimer.current);
                    closeTimer.current = null;
                  }}
                  onPointerLeave={(event) => {
                    if (event.pointerType !== 'touch') closeSoon();
                  }}
                  // WATA: each colour has a light value in front of EasyUI's own, kept behind `dark:`.
                  className="w-full rounded-xl border border-neutral-200 bg-white p-4 text-left text-xs text-neutral-700 shadow-[0_16px_40px_rgba(0,0,0,0.14)] cursor-default dark:border-[#1F1F1F] dark:bg-[#0E0E0E] dark:text-[#A1A1A1] dark:shadow-[0_16px_40px_rgba(0,0,0,0.6)]"
                >
                  {renderContent()}
                  {href && (
                    <div className="mt-3 border-t border-neutral-200 pt-2.5 dark:border-[#1F1F1F]">
                      <LinkComponent
                        href={href}
                        onClick={close}
                        className="inline-flex items-center gap-1.5 font-medium text-neutral-900 underline-offset-4 hover:underline dark:text-[#FAFAFA]"
                      >
                        {hrefLabel}
                        <ArrowRight className="w-3.5 h-3.5" aria-hidden="true" />
                      </LinkComponent>
                    </div>
                  )}
                </motion.div>
              </div>
            )}
          </AnimatePresence>,
          document.body
        )}
    </span>
  );
};
