'use client';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Bell, CheckCheck, X } from 'lucide-react';
import { isPlainClick } from '../../lib/links';
import { cn } from '../../lib/utils';
import { motionTransitions } from '../../lib/motion-tokens';

/** WATA: what kind of news an entry is, for its dot. */
export type NotificationTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';

export interface Notification {
  id: string;
  message: string;
  /** WATA: a second line under the message. */
  detail?: string;
  timestamp: Date | string;
  read: boolean;
  action?: () => void;
  /** WATA: where it leads: the message is then a link (opened elsewhere with ⌘ or Ctrl). */
  href?: string;
  tone?: NotificationTone;
}

export interface NotificationBellProps {
  /** List of notifications. */
  notifications: Notification[];
  /** Handler when an item is marked as read. */
  onMarkAsRead: (id: string) => void;
  /** Optional handler to mark all as read. */
  onMarkAllAsRead?: () => void;
  /** Controlled open state of notification drawer/panel. */
  isOpen?: boolean;
  /** Open state change handler. */
  onOpenChange?: (isOpen: boolean) => void;
  /** Show numeric counter inside red badge. Default is true. */
  showCount?: boolean;
  /** WATA: what draws an entry's link (React Router's). */
  linkComponent?: React.ElementType;
  /** WATA: under the list (a setting of the notifications). */
  footer?: React.ReactNode;
  /** WATA: the trigger's own look, to sit with the buttons around it. */
  triggerClassName?: string;
  /** Custom CSS class names. */
  className?: string;
}

// WATA: each colour has a light value in front of EasyUI's own, kept behind `dark:`.
const DOT: Record<NotificationTone, string> = {
  neutral: 'bg-neutral-400 dark:bg-neutral-500',
  info: 'bg-blue-500',
  success: 'bg-emerald-500',
  warning: 'bg-amber-500',
  danger: 'bg-rose-500 animate-pulse',
};

export const NotificationBell: React.FC<NotificationBellProps> = ({
  notifications,
  onMarkAsRead,
  onMarkAllAsRead,
  isOpen: controlledIsOpen,
  onOpenChange,
  showCount = true,
  linkComponent: LinkComponent = 'a',
  footer,
  triggerClassName,
  className,
}) => {
  const [internalIsOpen, setInternalIsOpen] = useState(false);
  const [isShaking, setIsShaking] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const isPanelOpen = controlledIsOpen !== undefined ? controlledIsOpen : internalIsOpen;
  const setPanelOpen = useCallback(
    (open: boolean) => {
      if (onOpenChange) onOpenChange(open);
      else setInternalIsOpen(open);
    },
    [onOpenChange]
  );

  const unreadCount = notifications.filter((n) => !n.read).length;
  const prevCountRef = useRef(unreadCount);

  // Trigger subtle bell shake when new unread notifications arrive
  useEffect(() => {
    if (unreadCount > prevCountRef.current) {
      setIsShaking(true);
      const timer = setTimeout(() => setIsShaking(false), 600);
      return () => clearTimeout(timer);
    }
    prevCountRef.current = unreadCount;
  }, [unreadCount]);

  // Click outside to close panel
  useEffect(() => {
    const handleOutsideClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setPanelOpen(false);
      }
    };
    if (isPanelOpen) {
      document.addEventListener('mousedown', handleOutsideClick);
      return () => document.removeEventListener('mousedown', handleOutsideClick);
    }
  }, [isPanelOpen, setPanelOpen]);

  // WATA: Escape closes the panel, and the bell has the focus again.
  useEffect(() => {
    if (!isPanelOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      setPanelOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isPanelOpen, setPanelOpen]);

  return (
    <div ref={containerRef} className={cn('relative inline-block', className)}>
      {/* Bell Trigger Button */}
      <motion.button
        ref={triggerRef}
        type="button"
        onClick={() => setPanelOpen(!isPanelOpen)}
        animate={
          isShaking
            ? {
                rotate: [0, -12, 10, -8, 6, -2, 0],
              }
            : {}
        }
        transition={{ duration: 0.5, ease: 'easeInOut' }}
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        aria-expanded={isPanelOpen}
        aria-label={
          unreadCount > 0
            ? `${unreadCount} unread notifications`
            : 'Notifications'
        }
        className={cn(
          'relative cursor-pointer transition-colors',
          triggerClassName ??
            cn(
              'p-2.5 rounded-xl border',
              isPanelOpen
                ? 'bg-neutral-100 border-neutral-400 text-neutral-900 dark:bg-[#141414] dark:border-[#4A4A4A] dark:text-[#FAFAFA]'
                : 'bg-white border-neutral-200 text-neutral-500 hover:text-neutral-900 hover:border-neutral-400 dark:bg-[#0E0E0E] dark:border-[#1F1F1F] dark:text-[#525252] dark:hover:text-[#FAFAFA] dark:hover:border-[#4A4A4A]'
            )
        )}
      >
        <Bell className="w-5 h-5" />

        {/* Unread Badge / Red Dot */}
        <AnimatePresence>
          {unreadCount > 0 && (
            <motion.span
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0, opacity: 0 }}
              transition={motionTransitions.springSnappy}
              className={cn(
                'absolute -top-1 -right-1 flex items-center justify-center rounded-full bg-rose-600 text-white font-mono font-bold border-2 border-background',
                showCount
                  ? 'min-w-[18px] h-[18px] px-1 text-[9px]'
                  : 'w-2.5 h-2.5'
              )}
            >
              {showCount && (unreadCount > 99 ? '99+' : unreadCount)}
            </motion.span>
          )}
        </AnimatePresence>
      </motion.button>

      {/* Floating Notification Panel */}
      <AnimatePresence>
        {isPanelOpen && (
          <motion.div
            initial={{ opacity: 0, y: 8, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.95 }}
            transition={motionTransitions.springSnappy}
            role="dialog"
            aria-label="Notifications"
            // WATA: on a phone the panel spans the screen under the top bar (centred on the bell, it went past the edge).
            className="fixed left-4 right-4 top-14 sm:absolute sm:left-auto sm:right-0 sm:top-auto mt-2 sm:w-96 rounded-2xl bg-white/95 backdrop-blur-xl border border-neutral-200 shadow-[0_16px_40px_rgba(0,0,0,0.12)] z-50 overflow-hidden dark:bg-[#0E0E0E]/95 dark:border-[#1F1F1F] dark:shadow-[0_16px_40px_rgba(0,0,0,0.6)]"
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-200 bg-neutral-50 dark:border-[#1F1F1F] dark:bg-[#141414]">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-neutral-900 dark:text-[#FAFAFA]">Notifications</span>
                {unreadCount > 0 && (
                  <span className="px-1.5 py-0.5 rounded-full bg-rose-500/10 text-rose-700 border border-rose-500/20 text-[10px] font-mono dark:text-rose-400">
                    {unreadCount} new
                  </span>
                )}
              </div>

              <div className="flex items-center gap-1">
                {unreadCount > 0 && onMarkAllAsRead && (
                  <button
                    type="button"
                    onClick={onMarkAllAsRead}
                    className="p-1 rounded-lg text-[10px] text-muted-foreground hover:text-neutral-900 hover:bg-neutral-100 transition-colors flex items-center gap-1 cursor-pointer dark:hover:text-white dark:hover:bg-[#141414]"
                    title="Mark all as read"
                  >
                    <CheckCheck className="w-3.5 h-3.5" />
                    <span>Read all</span>
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setPanelOpen(false)}
                  className="p-1 rounded-lg text-muted-foreground hover:text-neutral-900 hover:bg-neutral-100 transition-colors cursor-pointer dark:hover:text-white dark:hover:bg-[#141414]"
                  aria-label="Close notifications"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            {/* List */}
            <div className="max-h-80 overflow-y-auto divide-y divide-neutral-200 dark:divide-[#1F1F1F]">
              {notifications.length === 0 ? (
                <div className="py-8 px-4 text-center">
                  <Bell className="w-6 h-6 text-muted-foreground mx-auto mb-2" />
                  <p className="text-xs text-muted-foreground">No notifications right now</p>
                </div>
              ) : (
                notifications.map((item) => {
                  const dateString =
                    typeof item.timestamp === 'string'
                      ? item.timestamp
                      : item.timestamp.toLocaleDateString();
                  const messageClass = cn(
                    'text-xs leading-relaxed text-neutral-600 dark:text-[#A1A1A1]',
                    !item.read && 'font-medium text-neutral-900 dark:text-[#FAFAFA]'
                  );

                  return (
                    <motion.div
                      key={item.id}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      className={cn(
                        'p-3 flex items-start gap-3 transition-colors hover:bg-black/[0.02] dark:hover:bg-white/[0.02]',
                        !item.read && 'bg-neutral-50 dark:bg-[#141414]'
                      )}
                    >
                      <span
                        aria-hidden="true"
                        className={cn(
                          'w-2 h-2 rounded-full mt-1.5 shrink-0',
                          item.read ? 'bg-transparent' : DOT[item.tone ?? 'danger']
                        )}
                      />

                      <div className="flex-1 min-w-0">
                        {/* WATA: an entry that leads somewhere is a link; followed, it is read and the panel closes. */}
                        {item.href !== undefined ? (
                          <LinkComponent
                            href={item.href}
                            onClick={(e: React.MouseEvent) => {
                              onMarkAsRead(item.id);
                              if (isPlainClick(e)) setPanelOpen(false);
                            }}
                            className={cn(messageClass, 'block underline-offset-2 hover:underline')}
                          >
                            {item.message}
                          </LinkComponent>
                        ) : (
                          <p className={messageClass}>{item.message}</p>
                        )}
                        {item.detail && (
                          <p className="text-xs text-muted-foreground leading-relaxed [overflow-wrap:anywhere]">{item.detail}</p>
                        )}
                        <span className="text-[10px] font-mono text-muted-foreground mt-1 block">
                          {dateString}
                        </span>
                      </div>

                      {!item.read && (
                        <button
                          type="button"
                          onClick={() => onMarkAsRead(item.id)}
                          className="p-1 rounded text-muted-foreground hover:text-neutral-900 hover:bg-neutral-100 transition-colors shrink-0 cursor-pointer dark:hover:text-white dark:hover:bg-[#0E0E0E]"
                          title="Mark as read"
                          aria-label="Mark notification as read"
                        >
                          <CheckCheck className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </motion.div>
                  );
                })
              )}
            </div>

            {footer && (
              <div className="border-t border-neutral-200 px-4 py-2.5 text-xs text-muted-foreground dark:border-[#1F1F1F]">{footer}</div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
