'use client';

import React, { useState, useId } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Check, Clock, AlertCircle, AlertTriangle, Loader2, GitCommit, Copy, CheckCheck, ChevronDown } from 'lucide-react';
import { cn, copyToClipboard } from '../../lib/utils';
import { motionTransitions } from '../../lib/motion-tokens';

// WATA: `warning`, a step that went on but not as it should (amber).
export type TimelineStatus = 'completed' | 'in-progress' | 'pending' | 'failed' | 'warning';

export interface TimelineMetric {
  label: string;
  value: string;
}

export interface TimelineItem {
  id: string;
  title: string;
  timestamp: string;
  status: TimelineStatus;
  /** WATA: the words of the status badge. Without them there is no badge: the title says the step. */
  statusLabel?: string;
  description?: string;
  tag?: string;
  commitHash?: string;
  metrics?: TimelineMetric[];
  author?: {
    name: string;
    role?: string;
  };
}

export interface InteractiveTimelineProps extends React.HTMLAttributes<HTMLDivElement> {
  items: TimelineItem[];
  defaultSelectedId?: string;
  /** WATA: the items open at first (EasyUI opens the selected one alone). */
  defaultExpandedIds?: string[];
  /** WATA: what the list of items is called (whose moments they are). */
  label?: string;
  onItemSelect?: (item: TimelineItem) => void;
  className?: string;
  collapsible?: boolean;
}

/** WATA: an item with nothing more than its title and time has no fold. */
function hasDetails(item: TimelineItem): boolean {
  return Boolean(item.description || item.metrics?.length || item.author || item.commitHash);
}

export const InteractiveTimeline: React.FC<InteractiveTimelineProps> = ({
  items = [],
  defaultSelectedId,
  defaultExpandedIds,
  label,
  onItemSelect,
  className,
  collapsible = true,
  ...props
}) => {
  const [selectedId, setSelectedId] = useState<string | undefined>(
    defaultSelectedId || items[0]?.id
  );
  const [expandedIds, setExpandedIds] = useState<Set<string>>(
    new Set(defaultExpandedIds ?? (defaultSelectedId ? [defaultSelectedId] : items.length > 0 ? [items[0].id] : []))
  );
  const [copiedHash, setCopiedHash] = useState<string | null>(null);
  const listId = useId();

  const handleToggleExpand = (item: TimelineItem) => {
    setSelectedId(item.id);
    onItemSelect?.(item);

    if (!collapsible || !hasDetails(item)) return;

    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(item.id)) {
        next.delete(item.id);
      } else {
        next.add(item.id);
      }
      return next;
    });
  };

  const handleCopyHash = (e: React.MouseEvent, hash: string) => {
    e.stopPropagation();
    copyToClipboard(hash);
    setCopiedHash(hash);
    setTimeout(() => setCopiedHash(null), 2000);
  };

  // WATA: each colour has a light value in front of EasyUI's own, kept behind `dark:`.
  const getStatusIcon = (status: TimelineStatus) => {
    switch (status) {
      case 'completed':
        return <Check className="w-3.5 h-3.5 text-blue-600 dark:text-white" />;
      case 'in-progress':
        return <Loader2 className="w-3.5 h-3.5 text-blue-600 dark:text-white animate-spin" />;
      case 'failed':
        return <AlertCircle className="w-3.5 h-3.5 text-rose-600 dark:text-rose-400" />;
      case 'warning':
        return <AlertTriangle className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400" />;
      case 'pending':
      default:
        return <Clock className="w-3.5 h-3.5 text-neutral-500 dark:text-[#606060]" />;
    }
  };

  const getStatusBadgeStyle = (status: TimelineStatus) => {
    switch (status) {
      case 'completed':
        return 'bg-neutral-100 text-neutral-700 border-neutral-200 dark:bg-white/10 dark:text-white dark:border-white/20';
      case 'in-progress':
        return 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-white/15 dark:text-white dark:border-white/30 dark:shadow-[0_0_12px_rgba(255,255,255,0.15)]';
      case 'failed':
        return 'bg-rose-500/10 text-rose-700 border-rose-500/30 dark:text-rose-300';
      case 'warning':
        return 'bg-amber-500/10 text-amber-800 border-amber-500/30 dark:text-amber-300';
      case 'pending':
      default:
        return 'bg-neutral-100 text-neutral-600 border-neutral-200 dark:bg-[#141414] dark:text-muted-foreground dark:border-[#1F1F1F]';
    }
  };

  const getStatusNodeStyle = (status: TimelineStatus, isSelected: boolean) => {
    switch (status) {
      case 'completed':
        return 'bg-white border-blue-500 text-blue-600 ring-1 ring-blue-500/20 dark:bg-[#141414] dark:border-[#3B82F6] dark:text-[#3B82F6] dark:ring-[#3B82F6]/20';
      case 'in-progress':
        return 'bg-white border-blue-500 text-blue-600 shadow-xs ring-2 ring-blue-500/30 dark:bg-[#141414] dark:border-[#3B82F6] dark:text-[#3B82F6] dark:ring-[#3B82F6]/30';
      case 'failed':
        return 'bg-rose-500/10 border-rose-500 text-rose-600 ring-1 ring-rose-500/30 dark:text-rose-400';
      case 'warning':
        return 'bg-amber-500/10 border-amber-500 text-amber-600 ring-1 ring-amber-500/30 dark:text-amber-400';
      case 'pending':
      default:
        return isSelected
          ? 'bg-neutral-100 border-neutral-400 text-neutral-600 dark:bg-[#141414] dark:border-[#4A4A4A] dark:text-[#A1A1A1]'
          : 'bg-white border-neutral-200 text-neutral-500 dark:bg-[#0E0E0E] dark:border-[#1F1F1F] dark:text-muted-foreground';
    }
  };

  // Calculate completed count for progress line indicator
  const completedIndex = items.reduce((acc, curr, idx) => {
    return curr.status === 'completed' || curr.status === 'in-progress' ? idx : acc;
  }, -1);

  const progressPercent =
    items.length > 1 && completedIndex >= 0
      ? (completedIndex / (items.length - 1)) * 100
      : 0;

  // WATA: a plain block named by its list, not a landmark: a page of orders opens several.
  return (
    <div
      className={cn(
        'w-full rounded-xl border border-neutral-200 bg-white p-3.5 sm:p-5 text-neutral-900 dark:border-[#1F1F1F] dark:bg-[#0E0E0E] dark:text-[#FAFAFA]',
        className
      )}
      {...props}
    >
      <div className="relative">
        {/* Continuous Background Track Line */}
        <div className="absolute left-[17px] top-6 bottom-6 w-[2px] bg-neutral-200 dark:bg-[#1F1F1F]" />

        {/* Animated Progress Filled Line */}
        <motion.div
          className="absolute left-[17px] top-6 w-[2px] bg-gradient-to-b from-[#3B82F6] via-[#3B82F6]/80 to-transparent origin-top"
          initial={{ height: 0 }}
          animate={{ height: `${progressPercent}%` }}
          transition={motionTransitions.springGentle}
        />

        {/* Timeline Items List */}
        <div className="space-y-3 sm:space-y-4 relative" role="list" aria-label={label}>
          {items.map((item) => {
            const details = hasDetails(item);
            const isExpanded = details && expandedIds.has(item.id);
            const isSelected = selectedId === item.id;
            const itemId = `${listId}-item-${item.id}`;

            return (
              <div
                key={item.id}
                role="listitem"
                className="relative flex items-start gap-4 group"
              >
                {/* Status Indicator Node */}
                <button
                  type="button"
                  onClick={() => handleToggleExpand(item)}
                  aria-expanded={collapsible && details ? isExpanded : undefined}
                  aria-controls={collapsible && details ? itemId : undefined}
                  className={cn(
                    'relative z-10 w-9 h-9 rounded-full flex items-center justify-center border transition-all duration-200 shrink-0 focus-visible:outline-none cursor-pointer',
                    getStatusNodeStyle(item.status, isSelected)
                  )}
                  title={`${item.title} - ${item.statusLabel ?? item.status}`}
                >
                  {getStatusIcon(item.status)}
                  {item.status === 'in-progress' && (
                    <span className="absolute inset-0 rounded-full animate-ping bg-[#3B82F6]/20 -z-10" />
                  )}
                </button>

                {/* Content Card */}
                <div
                  onClick={() => handleToggleExpand(item)}
                  className={cn(
                    'flex-1 min-w-0 rounded-xl border transition-all duration-200 cursor-pointer overflow-hidden p-3.5 sm:p-4',
                    isSelected
                      ? 'bg-neutral-50 border-neutral-300 shadow-xs dark:bg-[#141414] dark:border-[#4A4A4A]'
                      : 'bg-white border-neutral-200 hover:border-neutral-300 hover:bg-neutral-50 dark:bg-[#0E0E0E] dark:border-[#1F1F1F] dark:hover:border-[#4A4A4A] dark:hover:bg-[#141414]'
                  )}
                >
                  {/* Item Header */}
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2 mb-1">
                        <span className="text-xs sm:text-sm font-medium text-neutral-900 dark:text-[#FAFAFA] truncate">
                          {item.title}
                        </span>
                        {item.tag && (
                          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-neutral-100 border border-neutral-200 text-neutral-600 dark:bg-[#141414] dark:border-[#1F1F1F] dark:text-[#A1A1A1]">
                            {item.tag}
                          </span>
                        )}
                        {item.statusLabel && (
                          <span
                            className={cn(
                              'text-[10px] font-mono uppercase tracking-wider px-2 py-0.5 rounded border',
                              getStatusBadgeStyle(item.status)
                            )}
                          >
                            {item.statusLabel}
                          </span>
                        )}
                      </div>

                      <span className="text-[11px] font-mono text-muted-foreground block">
                        {item.timestamp}
                      </span>
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0">
                      {collapsible && details && (
                        <motion.div
                          animate={{ rotate: isExpanded ? 180 : 0 }}
                          transition={{ duration: 0.2 }}
                          className="text-muted-foreground group-hover:text-neutral-900 dark:group-hover:text-[#FAFAFA] p-1"
                        >
                          <ChevronDown className="w-4 h-4" />
                        </motion.div>
                      )}
                    </div>
                  </div>

                  {/* Expandable Detail Section */}
                  <AnimatePresence initial={false}>
                    {isExpanded && (
                      <motion.div
                        id={itemId}
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: 'auto' }}
                        exit={{ opacity: 0, height: 0 }}
                        transition={motionTransitions.springGentle}
                        className="overflow-hidden"
                      >
                        <div className="pt-3 mt-3 border-t border-neutral-200 dark:border-[#1F1F1F] space-y-3">
                          {item.description && (
                            <p className="text-xs text-neutral-600 dark:text-[#A1A1A1] leading-relaxed [overflow-wrap:anywhere]">
                              {item.description}
                            </p>
                          )}

                          {/* Metrics Grid */}
                          {item.metrics && item.metrics.length > 0 && (
                            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 pt-1">
                              {item.metrics.map((m, i) => (
                                <div
                                  key={i}
                                  className="p-2.5 rounded-lg bg-neutral-50 border border-neutral-200 dark:bg-[#0E0E0E] dark:border-[#1F1F1F]"
                                >
                                  <span className="text-[10px] font-mono text-muted-foreground block uppercase">
                                    {m.label}
                                  </span>
                                  <span className="text-xs font-mono font-medium text-neutral-900 dark:text-[#FAFAFA]">
                                    {m.value}
                                  </span>
                                </div>
                              ))}
                            </div>
                          )}

                          {/* Footer Meta: Commit + Author */}
                          {(item.author || item.commitHash) && (
                            <div className="flex flex-wrap items-center justify-between gap-2 pt-2 text-[11px] font-mono text-muted-foreground">
                              {item.author && (
                                <div className="flex items-center gap-1.5">
                                  <span className="w-1.5 h-1.5 rounded-full bg-neutral-400 dark:bg-[#6B6B6B]" />
                                  <span className="text-neutral-600 dark:text-[#A1A1A1]">{item.author.name}</span>
                                  {item.author.role && (
                                    <span className="text-muted-foreground">({item.author.role})</span>
                                  )}
                                </div>
                              )}

                              {item.commitHash && (
                                <button
                                  type="button"
                                  onClick={(e) => handleCopyHash(e, item.commitHash!)}
                                  className="inline-flex items-center gap-1.5 px-2 py-1 rounded bg-white hover:bg-neutral-50 border border-neutral-200 hover:border-neutral-300 text-neutral-600 hover:text-neutral-900 transition-colors focus-visible:outline-none cursor-pointer dark:bg-[#0E0E0E] dark:hover:bg-[#141414] dark:border-[#1F1F1F] dark:hover:border-[#4A4A4A] dark:text-[#A1A1A1] dark:hover:text-[#FAFAFA]"
                                  title="Copy commit hash"
                                >
                                  <GitCommit className="w-3 h-3" />
                                  <span>{item.commitHash.slice(0, 7)}</span>
                                  {copiedHash === item.commitHash ? (
                                    <CheckCheck className="w-3 h-3 text-neutral-900 dark:text-white" />
                                  ) : (
                                    <Copy className="w-3 h-3 text-muted-foreground" />
                                  )}
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
