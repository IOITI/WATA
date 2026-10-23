'use client';

import React, { useId, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { RotateCcw, ChevronDown, User, Clock, Loader2 } from 'lucide-react';
import { cn } from '../../lib/utils';
import { motionTransitions } from '../../lib/motion-tokens';

export interface LedgerEntry {
  id: string;
  /** WATA: optional (the admin says the date in `action`). */
  timestamp?: Date | string;
  action: string;
  description: string;
  author?: string;
  /** WATA: a name shown in the footer, as written (a file's). */
  reference?: string;
  details?: {
    diff?: string;
    preview?: string;
    metadata?: Record<string, any>;
    /** WATA: what the entry opens on, drawn as given (the changes a restore would make). */
    content?: React.ReactNode;
  };
  restorable?: boolean;
}

export interface RecoveryLedgerProps {
  /** Array of chronological version history or state recovery entries. */
  entries: LedgerEntry[];
  /** Currently active / restored state entry ID. */
  currentEntryId?: string;
  /** Revert / restore callback. WATA: the admin opens its confirmation there; the ledger restores nothing itself. */
  onRestore?: (id: string) => Promise<void> | void;
  /** Entry selection handler. */
  onSelect?: (id: string) => void;
  /** WATA: the entry opened, when the caller holds it (null: none). */
  expandedId?: string | null;
  /** WATA: called with the entry opened, or null once it is closed. */
  onExpandedChange?: (id: string | null) => void;
  /** WATA: the header's title. */
  title?: string;
  /** WATA: beside the title. */
  countLabel?: string;
  /** WATA: the text of the restore button. */
  restoreLabel?: string;
  /** WATA: the badge of the current entry. */
  currentLabel?: string;
  /** Visual presentation mode. Default is 'timeline'. */
  variant?: 'timeline' | 'compact';
  /** Custom CSS class names. */
  className?: string;
}

export const RecoveryLedger: React.FC<RecoveryLedgerProps> = ({
  entries,
  currentEntryId,
  onRestore,
  onSelect,
  expandedId: controlledExpandedId,
  onExpandedChange,
  title = 'Recovery Ledger',
  countLabel,
  restoreLabel = 'Revert',
  currentLabel = 'Current Head',
  variant = 'timeline',
  className,
}) => {
  const [internalExpandedId, setInternalExpandedId] = useState<string | null>(null);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const baseId = useId();
  const expandedId = controlledExpandedId !== undefined ? controlledExpandedId : internalExpandedId;

  const toggleExpand = (id: string) => {
    const next = expandedId === id ? null : id;
    if (controlledExpandedId === undefined) setInternalExpandedId(next);
    onExpandedChange?.(next);
    if (onSelect) onSelect(id);
  };

  const handleRestore = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!onRestore || restoringId) return;
    setRestoringId(id);
    try {
      await onRestore(id);
    } finally {
      setRestoringId(null);
    }
  };

  // WATA: each colour has a light value in front of EasyUI's own, kept behind `dark:`.
  return (
    <div
      className={cn(
        'w-full rounded-2xl bg-white border border-neutral-200 overflow-hidden dark:bg-[#0E0E0E] dark:border-[#1F1F1F]',
        className
      )}
    >
      {/* Header */}
      <div className="px-4 py-3 border-b border-neutral-200 bg-neutral-50 flex items-center justify-between dark:border-[#1F1F1F] dark:bg-[#141414]">
        <div className="flex items-center gap-2">
          <Clock aria-hidden="true" className="w-4 h-4 text-neutral-500 dark:text-[#525252]" />
          <h3 className="text-xs font-semibold text-neutral-900 dark:text-[#FAFAFA]">{title}</h3>
          <span className="text-[10px] font-mono text-muted-foreground bg-white px-2 py-0.5 rounded-full border border-neutral-200 dark:bg-[#141414] dark:border-[#1F1F1F]">
            {countLabel ?? `${entries.length} snapshots`}
          </span>
        </div>
      </div>

      {/* Ledger List */}
      <div className="relative p-4">
        {variant === 'timeline' && (
          // WATA: through the middle of the pips (16 px from the edge, 16 px wide); EasyUI's ran 5 px to their right.
          <div aria-hidden="true" className="absolute top-6 bottom-6 left-[23px] w-[2px] bg-neutral-200 pointer-events-none dark:bg-[#1F1F1F]" />
        )}

        {/* WATA: a list, in the order given. */}
        <ol aria-label={title} className="space-y-4">
          {entries.map((entry, idx) => {
            const isCurrent = entry.id === currentEntryId;
            const isExpanded = expandedId === entry.id;
            const isRestoring = restoringId === entry.id;
            const isRestorable = entry.restorable !== false;
            // WATA: only an entry with something to show opens (a real button: EasyUI's card took clicks alone).
            const canExpand = entry.details !== undefined;
            const detailsId = `${baseId}-${idx}-details`;

            const dateStr =
              entry.timestamp === undefined
                ? null
                : typeof entry.timestamp === 'string'
                ? entry.timestamp
                : entry.timestamp.toLocaleString();

            const summary = (
              <>
                <span className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-semibold text-neutral-900 dark:text-[#FAFAFA]">
                    {entry.action}
                  </span>
                  {isCurrent && (
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-emerald-500/10 text-emerald-700 border border-emerald-500/20 dark:text-emerald-400">
                      {currentLabel}
                    </span>
                  )}
                </span>
                <span className="block text-[11px] text-neutral-600 mt-0.5 leading-relaxed dark:text-[#A1A1A1]">
                  {entry.description}
                </span>

                {/* Metadata Footer */}
                {(dateStr || entry.author || entry.reference) && (
                  <span className="flex items-center gap-3 flex-wrap text-[10px] font-mono text-muted-foreground mt-2">
                    {dateStr && <span>{dateStr}</span>}
                    {entry.reference && <span className="break-all">{entry.reference}</span>}
                    {entry.author && (
                      <span className="flex items-center gap-1">
                        <User aria-hidden="true" className="w-2.5 h-2.5" />
                        {entry.author}
                      </span>
                    )}
                  </span>
                )}
              </>
            );

            return (
              <motion.li
                key={entry.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: idx * 0.05 }}
                className="relative pl-8"
              >
                {/* Timeline Indicator Pip */}
                <div
                  aria-hidden="true"
                  className={cn(
                    'absolute left-0 top-3 w-4 h-4 rounded-full border-2 flex items-center justify-center transition-colors bg-white dark:bg-[#0E0E0E]',
                    isCurrent
                      ? 'border-emerald-600 text-emerald-600 dark:border-emerald-400 dark:text-emerald-400'
                      : isExpanded
                      ? 'border-neutral-500 dark:border-[#4A4A4A]'
                      : 'border-neutral-300 dark:border-[#1F1F1F]'
                  )}
                >
                  {isCurrent && <div className="w-1.5 h-1.5 rounded-full bg-emerald-600 dark:bg-emerald-400" />}
                </div>

                {/* Entry Card */}
                <div
                  className={cn(
                    'p-3 rounded-xl border transition-all',
                    isCurrent
                      ? 'bg-neutral-50 border-emerald-500/30 shadow-xs dark:bg-[#141414]'
                      : isExpanded
                      ? 'bg-neutral-50 border-neutral-400 dark:bg-[#141414] dark:border-[#4A4A4A]'
                      : canExpand
                      ? 'bg-white border-neutral-200 hover:border-neutral-400 hover:bg-neutral-50 dark:bg-[#0E0E0E] dark:border-[#1F1F1F] dark:hover:border-[#4A4A4A] dark:hover:bg-[#141414]'
                      : 'bg-white border-neutral-200 dark:bg-[#0E0E0E] dark:border-[#1F1F1F]'
                  )}
                >
                  {/* WATA: the button's ::after covers the header, so the whole of it opens the entry, as EasyUI's card did. */}
                  <div className="relative flex items-start justify-between gap-3">
                    {canExpand ? (
                      <button
                        type="button"
                        onClick={() => toggleExpand(entry.id)}
                        aria-expanded={isExpanded}
                        aria-controls={isExpanded ? detailsId : undefined}
                        className="group min-w-0 text-left rounded-md cursor-pointer after:absolute after:inset-0 after:content-['']"
                      >
                        {summary}
                      </button>
                    ) : (
                      <div className="min-w-0">{summary}</div>
                    )}

                    {/* Actions (Restore & Expand) */}
                    <div className="flex items-center gap-1.5 shrink-0">
                      {!isCurrent && isRestorable && onRestore && (
                        <button
                          type="button"
                          disabled={Boolean(restoringId)}
                          onClick={(e) => handleRestore(entry.id, e)}
                          className="relative z-10 px-2.5 py-1 rounded-lg text-[11px] font-medium bg-white text-neutral-700 border border-neutral-200 hover:bg-neutral-900 hover:text-white transition-colors flex items-center gap-1 cursor-pointer dark:bg-[#141414] dark:text-[#A1A1A1] dark:border-[#1F1F1F] dark:hover:bg-[#FAFAFA] dark:hover:text-[#050505]"
                          // WATA: named by the entry's action (its id is a file's name).
                          aria-label={`${restoreLabel}: ${entry.action}`}
                        >
                          {isRestoring ? (
                            <Loader2 aria-hidden="true" className="w-3 h-3 animate-spin" />
                          ) : (
                            <RotateCcw aria-hidden="true" className="w-3 h-3" />
                          )}
                          <span>{restoreLabel}</span>
                        </button>
                      )}

                      {canExpand && (
                        <div aria-hidden="true" className="p-1 text-neutral-500 pointer-events-none dark:text-[#525252]">
                          <motion.div
                            animate={{ rotate: isExpanded ? 180 : 0 }}
                            transition={motionTransitions.springSnappy}
                          >
                            <ChevronDown className="w-3.5 h-3.5" />
                          </motion.div>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Expanded Diff / Details Panel */}
                  <AnimatePresence>
                    {isExpanded && entry.details && (
                      <motion.div
                        id={detailsId}
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={motionTransitions.springSmooth}
                        className="mt-3 pt-2.5 border-t border-neutral-200 overflow-hidden dark:border-[#1F1F1F]"
                      >
                        {entry.details.diff && (
                          <div className="p-2 rounded-lg bg-neutral-50 border border-neutral-200 font-mono text-[10px] text-neutral-700 leading-relaxed overflow-x-auto dark:bg-[#050505] dark:border-[#1F1F1F] dark:text-[#A1A1A1]">
                            <pre>{entry.details.diff}</pre>
                          </div>
                        )}
                        {entry.details.preview && (
                          <p className="text-xs text-neutral-600 mt-2 dark:text-[#A1A1A1]">
                            {entry.details.preview}
                          </p>
                        )}
                        {entry.details.content}
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              </motion.li>
            );
          })}
        </ol>
      </div>
    </div>
  );
};
