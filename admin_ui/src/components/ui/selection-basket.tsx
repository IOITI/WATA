'use client';

import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Trash2,
  Share2,
  Download,
  FolderInput,
  X
} from 'lucide-react';
import { cn } from '../../lib/utils';
import { motionTransitions } from '../../lib/motion-tokens';
import { DirectionalTooltip } from './directional-tooltip';

export interface SelectionActionItem {
  id: string;
  label: string;
  icon?: React.ReactNode;
  variant?: 'default' | 'danger' | 'primary';
  onClick?: () => void;
  /** WATA: where it leads, when it leads to another page: a link, drawn by `linkComponent`. */
  href?: string;
  /** WATA: not possible for the selection now; `title` says why, in a tooltip (hover, focus, tap). */
  disabled?: boolean;
  title?: string;
}

export interface SelectionBasketProps {
  /** Count of selected items */
  selectedCount: number;
  /** Total count of all items (for select all calculation) */
  totalCount?: number;
  /**
   * Position mode: 'fixed' attaches to browser viewport, 'contained' / 'inline' stays within parent container.
   * WATA: 'sticky' stays at the bottom of the screen while its list is on it, and rests under the list past it.
   */
  mode?: 'fixed' | 'contained' | 'inline' | 'sticky';
  /** Custom actions list */
  actions?: SelectionActionItem[];
  /** Callback to clear all selections */
  onClearSelection?: () => void;
  /** Callback to select all items */
  onSelectAll?: () => void;
  /** WATA: whether every item that can be is selected (EasyUI compares the counts, wrong once a search hides some). */
  allSelected?: boolean;
  /** WATA: what an item is, one and several ("run", "runs"). */
  noun?: [string, string];
  /** WATA: what draws an action that has an `href` (React Router's link). */
  linkComponent?: React.ElementType;
  /** Custom class name */
  className?: string;
}

const defaultActions: SelectionActionItem[] = [
  { id: 'export', label: 'Export', icon: <Download className="w-3.5 h-3.5" /> },
  { id: 'move', label: 'Move to Folder', icon: <FolderInput className="w-3.5 h-3.5" /> },
  { id: 'share', label: 'Share', icon: <Share2 className="w-3.5 h-3.5" /> },
  { id: 'delete', label: 'Delete', icon: <Trash2 className="w-3.5 h-3.5" />, variant: 'danger' },
];

export const SelectionBasket: React.FC<SelectionBasketProps> = ({
  selectedCount,
  totalCount,
  mode = 'fixed',
  actions = defaultActions,
  onClearSelection,
  onSelectAll,
  allSelected,
  noun = ['item', 'items'],
  linkComponent: LinkComponent = 'a',
  className,
}) => {
  const isVisible = selectedCount > 0;
  const isAllSelected = allSelected ?? (totalCount !== undefined && selectedCount === totalCount && totalCount > 0);

  const wrapperClass =
    mode === 'fixed'
      ? 'fixed bottom-6 left-1/2 -translate-x-1/2 z-40 max-w-[95vw]'
      : mode === 'contained'
      ? 'absolute bottom-3 left-1/2 -translate-x-1/2 z-30 max-w-[95%]'
      : mode === 'sticky'
      ? 'sticky bottom-4 mx-auto w-fit max-w-full z-30'
      : 'relative mt-4 mx-auto w-fit z-20';

  // WATA: each colour has a light value in front of EasyUI's own, kept behind `dark:`.
  return (
    <AnimatePresence>
      {isVisible && (
        <div className={cn('pointer-events-none font-sans select-none', wrapperClass)}>
          <motion.div
            initial={{ opacity: 0, y: 24, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.95, transition: { duration: 0.15 } }}
            transition={motionTransitions.springResponsive}
            className={cn(
              'pointer-events-auto flex items-center gap-2 sm:gap-3 p-1.5 sm:p-2 rounded-2xl border border-neutral-200 bg-white/95 backdrop-blur-md shadow-[0_20px_50px_rgba(0,0,0,0.15)] overflow-x-auto max-w-full dark:border-[#1F1F1F] dark:bg-[#0E0E0E]/95 dark:shadow-[0_20px_50px_rgba(0,0,0,0.6)]',
              className
            )}
            role="toolbar"
            aria-label="Bulk actions toolbar"
          >
            {/* Selected Count Indicator */}
            <div className="flex items-center gap-2 pl-3 pr-2 py-1 border-r border-neutral-200 dark:border-[#1F1F1F] shrink-0">
              <span className="min-w-5 h-5 px-1 rounded-full bg-neutral-900 text-white dark:bg-[#FAFAFA] dark:text-[#050505] text-[11px] font-bold font-mono flex items-center justify-center">
                {selectedCount}
              </span>
              <span className="text-xs font-medium text-neutral-900 dark:text-[#FAFAFA] whitespace-nowrap">
                {selectedCount === 1 ? `${noun[0]} selected` : `${noun[1]} selected`}
              </span>

              {/* Select all toggle */}
              {onSelectAll && totalCount && (
                <button
                  type="button"
                  onClick={onSelectAll}
                  className="text-[11px] font-mono text-muted-foreground hover:text-neutral-900 dark:hover:text-white transition-colors ml-1 underline underline-offset-2 cursor-pointer"
                >
                  {isAllSelected ? 'Select none' : `All (${totalCount})`}
                </button>
              )}
            </div>

            {/* Action Buttons (the bar itself scrolls on narrow screens: WATA, so that no focus ring is cut) */}
            <div className="flex items-center gap-1.5 shrink-0 py-0.5">
              {actions.map((action) => {
                const isDanger = action.variant === 'danger';
                const isPrimary = action.variant === 'primary';
                const classes = cn(
                  'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors focus-ring whitespace-nowrap cursor-pointer disabled:cursor-not-allowed disabled:opacity-40',
                  isDanger
                    ? 'bg-rose-500/10 hover:bg-rose-500 hover:text-white border border-rose-500/30 text-rose-700 dark:text-rose-400 disabled:hover:bg-rose-500/10 disabled:hover:text-rose-700 dark:disabled:hover:text-rose-400'
                    : isPrimary
                    ? 'bg-neutral-900 hover:bg-neutral-800 text-white dark:bg-[#FAFAFA] dark:hover:bg-white dark:text-[#050505]'
                    : 'bg-white hover:bg-neutral-50 border border-neutral-200 hover:border-neutral-400 text-neutral-700 hover:text-neutral-900 dark:bg-[#141414] dark:hover:bg-[#0E0E0E] dark:border-[#1F1F1F] dark:hover:border-[#4A4A4A] dark:text-[#A1A1A1] dark:hover:text-[#FAFAFA]'
                );
                const content = (
                  <>
                    {action.icon}
                    <span>{action.label}</span>
                  </>
                );

                // WATA: what leads to another page is a link (a click with ⌘ or Ctrl opens it in another tab).
                const control =
                  action.href !== undefined && !action.disabled ? (
                    <LinkComponent href={action.href} onClick={action.onClick} className={classes}>
                      {content}
                    </LinkComponent>
                  ) : (
                    <button type="button" onClick={action.onClick} disabled={action.disabled} className={classes}>
                      {content}
                    </button>
                  );
                if (!action.title) return <React.Fragment key={action.id}>{control}</React.Fragment>;
                // WATA: a disabled control takes neither focus nor pointer: a wrapper takes them for its tooltip.
                return (
                  <DirectionalTooltip key={action.id} content={action.title} side="top">
                    {action.disabled ? (
                      <span tabIndex={0} className="inline-flex rounded-lg [&>:disabled]:pointer-events-none">
                        {control}
                      </span>
                    ) : (
                      control
                    )}
                  </DirectionalTooltip>
                );
              })}
            </div>

            {/* Clear selection close button */}
            {onClearSelection && (
              <button
                type="button"
                onClick={onClearSelection}
                className="w-7 h-7 rounded-lg bg-neutral-100 hover:bg-white border border-neutral-200 hover:border-neutral-400 flex items-center justify-center text-muted-foreground hover:text-neutral-900 transition-colors focus-ring shrink-0 ml-1 cursor-pointer dark:bg-[#141414] dark:hover:bg-[#0E0E0E] dark:border-[#1F1F1F] dark:hover:border-[#4A4A4A] dark:hover:text-[#FAFAFA]"
                title="Clear selection"
                aria-label="Clear selection"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
};
