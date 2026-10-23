'use client';

import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Search } from 'lucide-react';
import { isPlainClick } from '../../lib/links';
import { cn } from '../../lib/utils';
import { motionTransitions } from '../../lib/motion-tokens';

export interface SpotlightSearchItem {
  id: string;
  title: string;
  category: 'Components' | 'Documentation' | 'Actions' | 'Navigation' | string;
  description?: string;
  icon?: React.ReactNode;
  shortcut?: string[];
  action?: () => void;
  /** WATA: where it leads: the entry is then a link (opened elsewhere with ⌘ or Ctrl), followed with Enter too. */
  href?: string;
  /** WATA: more words it is found by. */
  keywords?: string[];
}

export interface SpotlightSearchProps {
  /** Controlled open state */
  open?: boolean;
  /** Callback when open state changes */
  onOpenChange?: (open: boolean) => void;
  /** Optional close callback */
  onClose?: () => void;
  /** Searchable items database override */
  items?: SpotlightSearchItem[];
  /** Search input placeholder */
  placeholder?: string;
  /** Recent searches list */
  recentSearches?: string[];
  /** Callback when an item is selected */
  onSelect?: (item: SpotlightSearchItem) => void;
  /** Enable internal global Cmd+K shortcut */
  enableGlobalShortcut?: boolean;
  /** WATA: what follows an entry's `href` when Enter chooses it (the router's navigate). */
  onNavigate?: (href: string) => void;
  /** WATA: what draws an entry with an `href` (React Router's link). */
  linkComponent?: React.ElementType;
  /** WATA: told of what is typed, for entries made from it. */
  onQueryChange?: (query: string) => void;
  /** WATA: the most entries listed at once; the others are found by typing. */
  limit?: number;
  /** WATA: what the dialog is called. */
  label?: string;
  /** Custom class name */
  className?: string;
}

/** WATA: every word typed is somewhere in the entry: "ema us100" finds "EMA cross on US100". */
function matches(item: SpotlightSearchItem, words: string[]): boolean {
  const text = [item.title, item.category, item.description ?? '', ...(item.keywords ?? [])].join(' ').toLowerCase();
  return words.every((word) => text.includes(word));
}

export const SpotlightSearch: React.FC<SpotlightSearchProps> = ({
  open,
  onOpenChange,
  onClose,
  items: customItems,
  placeholder = 'Search components, guides, documentation...',
  onSelect,
  enableGlobalShortcut = false,
  onNavigate,
  linkComponent: LinkComponent = 'a',
  onQueryChange,
  limit = 50,
  label = 'Spotlight Search',
  className,
}) => {
  const uniqueId = React.useId();
  const [internalOpen, setInternalOpen] = useState(false);
  const isControlled = open !== undefined;
  const isOpen = isControlled ? open : internalOpen;

  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  // WATA: what had the focus before, given it back on closing.
  const returnFocus = useRef<HTMLElement | null>(null);

  const handleClose = useCallback(() => {
    if (isControlled) {
      onOpenChange?.(false);
      onClose?.();
    } else {
      setInternalOpen(false);
      onOpenChange?.(false);
      onClose?.();
    }
  }, [isControlled, onOpenChange, onClose]);

  const handleOpen = useCallback(() => {
    if (isControlled) {
      onOpenChange?.(true);
    } else {
      setInternalOpen(true);
      onOpenChange?.(true);
    }
    setQuery('');
    setSelectedIndex(0);
  }, [isControlled, onOpenChange]);

  // Global shortcut (only active when enableGlobalShortcut is true and not externally controlled)
  useEffect(() => {
    if (!enableGlobalShortcut) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (isOpen) {
          handleClose();
        } else {
          handleOpen();
        }
      }
      if (e.key === 'Escape' && isOpen) {
        e.preventDefault();
        handleClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [enableGlobalShortcut, isOpen, handleClose, handleOpen]);

  // Auto-focus input on open
  useEffect(() => {
    if (isOpen) {
      returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setQuery('');
      setSelectedIndex(0);
      const timer = setTimeout(() => inputRef.current?.focus(), 30);
      return () => clearTimeout(timer);
    }
    returnFocus.current?.focus?.();
    returnFocus.current = null;
  }, [isOpen]);

  useEffect(() => {
    onQueryChange?.(query);
  }, [query, onQueryChange]);

  // WATA: the caller's entries only (EasyUI listed its own site's).
  const items = useMemo<SpotlightSearchItem[]>(() => customItems ?? [], [customItems]);

  const matching = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return words.length === 0 ? items : items.filter((item) => matches(item, words));
  }, [items, query]);
  const filteredItems = matching.slice(0, limit);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  const choose = (item: SpotlightSearchItem) => {
    item.action?.();
    onSelect?.(item);
    if (item.href !== undefined) onNavigate?.(item.href);
    handleClose();
  };

  const handleInputKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      handleClose();
      return;
    }

    if (filteredItems.length === 0) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((prev) => (prev + 1) % filteredItems.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((prev) => (prev - 1 + filteredItems.length) % filteredItems.length);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const current = filteredItems[selectedIndex];
      if (current) choose(current);
    }
  };

  const listId = `${uniqueId}-list`;
  const optionId = (index: number) => `${uniqueId}-option-${index}`;

  return (
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center pt-16 sm:pt-24 px-4 font-sans select-none">
          {/* Calm backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={handleClose}
            className="fixed inset-0 bg-black/60 backdrop-blur-sm"
          />

          {/* Ultra-minimal Spotlight Container */}
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label={label}
            initial={{ opacity: 0, scale: 0.98, y: -6 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98, y: -6 }}
            transition={motionTransitions.springSnappy}
            className={cn(
              'relative w-full max-w-lg rounded-2xl border border-border bg-surface-raised shadow-elevated overflow-hidden z-10',
              className
            )}
          >
            {/* Top Search Input Bar with ESC */}
            <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
              <Search className="w-4 h-4 text-text-subtle shrink-0" />
              {/* WATA: a combobox over a listbox, the entry shown named for screen readers. */}
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={handleInputKeyDown}
                placeholder={placeholder}
                className="flex-1 bg-transparent text-[16px] text-text-primary placeholder-text-muted focus:outline-none"
                aria-label="Search"
                role="combobox"
                aria-expanded="true"
                aria-controls={listId}
                aria-autocomplete="list"
                aria-activedescendant={filteredItems[selectedIndex] ? optionId(selectedIndex) : undefined}
              />
              <kbd
                onClick={handleClose}
                className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-mono text-text-muted bg-surface border border-border rounded cursor-pointer hover:text-text-primary transition-colors shrink-0"
              >
                ESC
              </kbd>
            </div>

            {/* Search Results List (WATA: with what each is, and its category) */}
            <div id={listId} role="listbox" aria-label={label} className="max-h-[340px] overflow-y-auto p-1.5 space-y-0.5">
              {filteredItems.length > 0 ? (
                filteredItems.map((item, index) => {
                  const isSelected = index === selectedIndex;
                  const content = (
                    <>
                      {/* Smooth Active Highlight Pill */}
                      {isSelected && (
                        <motion.div
                          layoutId={`spotlight-active-${uniqueId}`}
                          transition={motionTransitions.springGentle}
                          className="absolute inset-0 rounded-xl bg-surface-hover border border-border-hover z-0"
                        />
                      )}

                      <span className="relative z-10 min-w-0">
                        <span className="block font-medium truncate">{item.title}</span>
                        {item.description && <span className="block truncate text-xs text-text-muted">{item.description}</span>}
                      </span>
                      <span className="relative z-10 shrink-0 text-[11px] text-text-muted">{item.category}</span>
                    </>
                  );
                  const rowClass = cn(
                    'relative flex items-center justify-between gap-3 px-3.5 py-2.5 rounded-xl cursor-pointer transition-colors text-sm',
                    isSelected ? 'text-text-primary' : 'text-text-secondary hover:text-text-primary'
                  );

                  return item.href !== undefined ? (
                    <LinkComponent
                      key={item.id}
                      id={optionId(index)}
                      role="option"
                      aria-selected={isSelected}
                      href={item.href}
                      tabIndex={-1}
                      onClick={(e: React.MouseEvent) => {
                        // A click with ⌘ or Ctrl opens it elsewhere and leaves the palette open.
                        if (!isPlainClick(e)) return;
                        item.action?.();
                        onSelect?.(item);
                        handleClose();
                      }}
                      onMouseEnter={() => setSelectedIndex(index)}
                      className={rowClass}
                    >
                      {content}
                    </LinkComponent>
                  ) : (
                    <div
                      key={item.id}
                      id={optionId(index)}
                      role="option"
                      aria-selected={isSelected}
                      onClick={() => choose(item)}
                      onMouseEnter={() => setSelectedIndex(index)}
                      className={rowClass}
                    >
                      {content}
                    </div>
                  );
                })
              ) : (
                <div className="py-10 text-center text-xs text-text-muted">
                  No results found for <span className="text-text-primary">"{query}"</span>
                </div>
              )}
              {matching.length > filteredItems.length && (
                <p className="px-3.5 py-2 text-xs text-text-muted">
                  {matching.length - filteredItems.length} more: type to narrow them down.
                </p>
              )}
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
};
