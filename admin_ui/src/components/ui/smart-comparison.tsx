'use client';

import React, { useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Check, Minus, Search, Sparkles, Filter, ChevronDown, Info } from 'lucide-react';
import { cn } from '../../lib/utils';
import { motionTransitions } from '../../lib/motion-tokens';

export interface ComparisonPlan {
  id: string;
  name: string;
  tagline: string;
  price: string;
  billingPeriod?: string;
  badge?: string;
  featured?: boolean;
  ctaText?: string;
  onCtaClick?: () => void;
  /** WATA: where its name leads (a link, drawn by `linkComponent`). */
  href?: string;
  /** WATA: a colour drawn before its name (the plan's colour elsewhere on the page, a chart's). */
  accent?: string;
}

export interface ComparisonFeature {
  id: string;
  name: string;
  description?: string;
  values: Record<string, string | boolean | React.ReactNode>;
  /** WATA: each plan's value as text, compared for "differences only" (React nodes cannot be). */
  keys?: Record<string, string>;
}

export interface ComparisonCategory {
  id: string;
  title: string;
  features: ComparisonFeature[];
}

export interface SmartComparisonProps extends React.HTMLAttributes<HTMLDivElement> {
  plans: ComparisonPlan[];
  categories: ComparisonCategory[];
  defaultPlanId?: string;
  enableSearch?: boolean;
  enableDiffFilter?: boolean;
  className?: string;
  /** WATA: what the matrix is called, for screen readers. */
  label?: string;
  /** WATA: the words of the matrix's own column, and of its count of rows. */
  matrixTitle?: string;
  countLabel?: (count: number) => string;
  searchPlaceholder?: string;
  diffLabel?: string;
  emptyText?: string;
  /** WATA: what draws a plan's link (React Router's). */
  linkComponent?: React.ElementType;
}

// WATA: each colour has a light value in front of EasyUI's own, kept behind `dark:`.
export const SmartComparison: React.FC<SmartComparisonProps> = ({
  plans = [],
  categories = [],
  defaultPlanId,
  enableSearch = true,
  enableDiffFilter = true,
  className,
  label = 'Feature and plan comparison',
  matrixTitle = 'Feature Matrix',
  countLabel = (count) => `${count} specifications`,
  searchPlaceholder = 'Filter capabilities...',
  diffLabel = 'Differences only',
  emptyText = 'No features match your current search or difference filters.',
  linkComponent: LinkComponent = 'a',
  ...props
}) => {
  const [selectedPlanId, setSelectedPlanId] = useState<string>(
    defaultPlanId || (plans.find((p) => p.featured)?.id || plans[0]?.id || '')
  );
  const [searchQuery, setSearchQuery] = useState('');
  const [diffOnly, setDiffOnly] = useState(false);
  const [collapsedCategories, setCollapsedCategories] = useState<Set<string>>(new Set());
  const [activeTooltip, setActiveTooltip] = useState<string | null>(null);

  const toggleCategory = (catId: string) => {
    setCollapsedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(catId)) next.delete(catId);
      else next.add(catId);
      return next;
    });
  };

  // Filter categories and features
  const filteredCategories = useMemo(() => {
    const keyOf = (feat: ComparisonFeature, planId: string) =>
      feat.keys ? feat.keys[planId] : JSON.stringify(feat.values[planId]);
    const isFeatureDifferentiated = (feat: ComparisonFeature) => {
      if (plans.length <= 1) return true;
      const firstVal = keyOf(feat, plans[0].id);
      for (let i = 1; i < plans.length; i++) {
        if (keyOf(feat, plans[i].id) !== firstVal) {
          return true;
        }
      }
      return false;
    };

    return categories
      .map((cat) => {
        const matchingFeatures = cat.features.filter((feat) => {
          const matchSearch =
            !searchQuery.trim() ||
            feat.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
            (feat.description && feat.description.toLowerCase().includes(searchQuery.toLowerCase()));

          const matchDiff = !diffOnly || isFeatureDifferentiated(feat);

          return matchSearch && matchDiff;
        });

        return {
          ...cat,
          features: matchingFeatures,
        };
      })
      .filter((cat) => cat.features.length > 0);
  }, [categories, searchQuery, diffOnly, plans]);

  const renderValueCell = (value: string | boolean | React.ReactNode) => {
    if (typeof value === 'boolean') {
      return value ? (
        <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-emerald-500/10 text-emerald-700 border border-emerald-500/20 dark:text-emerald-400">
          <Check className="w-3 h-3" />
        </span>
      ) : (
        <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-neutral-100 text-neutral-500 dark:bg-[#141414] dark:text-muted-foreground">
          <Minus className="w-3 h-3" />
        </span>
      );
    }

    if (typeof value === 'string') {
      return <span className="text-xs font-mono text-neutral-900 dark:text-[#FAFAFA]">{value}</span>;
    }

    return value;
  };

  // WATA: one column for the rows' names and one per plan, whatever their number (EasyUI's grid had three plans).
  const columns = {
    '--plans': plans.length,
  } as React.CSSProperties;
  const grid = 'grid grid-cols-1 sm:grid-cols-[minmax(9rem,1.1fr)_repeat(var(--plans),minmax(0,1fr))]';

  return (
    // WATA: a table for screen readers (rows, column and row headers), drawn as EasyUI's grid.
    <div
      role="table"
      aria-label={label}
      className={cn(
        'w-full rounded-xl border border-neutral-200 bg-white p-3.5 sm:p-5 text-neutral-900 overflow-hidden dark:border-[#1F1F1F] dark:bg-[#0E0E0E] dark:text-[#FAFAFA]',
        className
      )}
      style={columns}
      {...props}
    >
      {/* Controls Bar: Search + Diff Filter Toggle + Mobile Plan Switcher */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 pb-4 mb-4 border-b border-neutral-200 dark:border-[#1F1F1F]">
        <div className="flex items-center gap-3">
          {enableSearch && (
            <div className="relative flex-1 sm:w-64">
              <Search className="w-3.5 h-3.5 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={searchPlaceholder}
                aria-label={searchPlaceholder}
                className="w-full pl-9 pr-3 py-1.5 text-xs rounded-lg bg-neutral-50 border border-neutral-200 text-neutral-900 placeholder:text-muted-foreground focus-visible:outline-none dark:bg-[#141414] dark:border-[#1F1F1F] dark:text-[#FAFAFA]"
              />
            </div>
          )}

          {enableDiffFilter && (
            <button
              type="button"
              onClick={() => setDiffOnly((prev) => !prev)}
              aria-pressed={diffOnly}
              className={cn(
                'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors cursor-pointer shrink-0',
                diffOnly
                  ? 'bg-neutral-900 text-white border-neutral-900 dark:bg-[#FAFAFA] dark:text-[#050505] dark:border-[#FAFAFA]'
                  : 'bg-neutral-50 text-neutral-600 border-neutral-200 hover:text-neutral-900 hover:border-neutral-400 dark:bg-[#141414] dark:text-[#A1A1A1] dark:border-[#1F1F1F] dark:hover:text-[#FAFAFA] dark:hover:border-[#4A4A4A]'
              )}
            >
              <Filter className="w-3 h-3" />
              <span>{diffLabel}</span>
            </button>
          )}
        </div>

        {/* Mobile Plan Switcher Segment */}
        <div className="sm:hidden flex items-center p-1 bg-neutral-100 rounded-xl border border-neutral-200 dark:bg-[#141414] dark:border-[#1F1F1F]">
          {plans.map((p) => {
            const isSelected = selectedPlanId === p.id;
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => setSelectedPlanId(p.id)}
                aria-pressed={isSelected}
                className={cn(
                  'relative flex-1 py-1.5 px-2 text-xs font-medium text-center rounded-lg transition-colors cursor-pointer truncate',
                  isSelected ? 'text-neutral-900 dark:text-white' : 'text-muted-foreground hover:text-neutral-900 dark:hover:text-[#A1A1A1]'
                )}
              >
                {isSelected && (
                  <motion.div
                    layoutId="smartComparisonMobileTab"
                    className="absolute inset-0 bg-white border border-neutral-200 rounded-lg -z-10 dark:bg-[#0E0E0E] dark:border-[#1F1F1F]"
                    transition={motionTransitions.springSnappy}
                  />
                )}
                <span>{p.name}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Plans Header Row */}
      <div role="row" className={cn(grid, 'gap-4 pb-6 border-b border-neutral-200 dark:border-[#1F1F1F]')}>
        {/* Left Column Label spacer */}
        <div role="columnheader" className="hidden sm:flex flex-col justify-end">
          <span className="text-[11px] font-mono uppercase tracking-widest text-muted-foreground">
            {matrixTitle}
          </span>
          <span className="text-xs text-neutral-600 mt-0.5 dark:text-[#A1A1A1]">
            {countLabel(categories.reduce((acc, c) => acc + c.features.length, 0))}
          </span>
        </div>

        {/* Plan Cards */}
        {plans.map((plan) => {
          const isMobileActive = selectedPlanId === plan.id;
          return (
            <div
              key={plan.id}
              role="columnheader"
              onClick={() => setSelectedPlanId(plan.id)}
              className={cn(
                'relative p-4 rounded-xl border transition-all cursor-pointer flex flex-col justify-between min-w-0',
                isMobileActive ? 'block' : 'hidden sm:flex',
                plan.featured
                  ? 'bg-gradient-to-b from-neutral-50 to-white border-neutral-400 shadow-md dark:from-[#141414] dark:to-[#0E0E0E] dark:border-white/30'
                  : 'bg-white border-neutral-200 hover:border-neutral-400 dark:bg-[#0E0E0E] dark:border-[#1F1F1F] dark:hover:border-[#4A4A4A]'
              )}
            >
              {plan.badge && (
                <span className="absolute -top-2.5 right-3 px-2 py-0.5 rounded-full text-[10px] font-mono uppercase tracking-wider bg-neutral-900 text-white font-semibold shadow-sm dark:bg-[#FAFAFA] dark:text-[#050505]">
                  {plan.badge}
                </span>
              )}

              <div>
                <div className="flex items-center justify-between gap-2 mb-1">
                  <h3 className="min-w-0 text-sm font-semibold text-neutral-900 flex items-center gap-1.5 dark:text-[#FAFAFA]">
                    {plan.accent && (
                      <span aria-hidden className="h-0.5 w-4 shrink-0 rounded-full" style={{ backgroundColor: plan.accent }} />
                    )}
                    {plan.href !== undefined ? (
                      <LinkComponent href={plan.href} onClick={(e: React.MouseEvent) => e.stopPropagation()} className="truncate underline-offset-4 hover:underline">
                        {plan.name}
                      </LinkComponent>
                    ) : (
                      <span className="truncate">{plan.name}</span>
                    )}
                    {plan.featured && <Sparkles className="w-3.5 h-3.5 text-neutral-900 dark:text-white" />}
                  </h3>
                </div>
                <p className="text-[11px] text-neutral-600 mb-3 leading-snug dark:text-[#A1A1A1]">{plan.tagline}</p>
              </div>

              <div>
                <div className="flex items-baseline gap-1 mb-3">
                  <span className="text-xl font-bold font-mono text-neutral-900 tracking-tight dark:text-[#FAFAFA]">
                    {plan.price}
                  </span>
                  {plan.billingPeriod && (
                    <span className="text-[10px] font-mono text-muted-foreground">
                      /{plan.billingPeriod}
                    </span>
                  )}
                </div>

                {/* WATA: a button only when it does something. */}
                {(plan.ctaText || plan.onCtaClick) && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      plan.onCtaClick?.();
                    }}
                    className={cn(
                      'w-full py-2 px-3 rounded-lg text-xs font-medium transition-all cursor-pointer focus-visible:outline-none',
                      plan.featured
                        ? 'bg-neutral-900 text-white hover:bg-neutral-800 font-semibold dark:bg-[#FAFAFA] dark:text-[#050505] dark:hover:bg-white'
                        : 'bg-neutral-50 hover:bg-white text-neutral-600 hover:text-neutral-900 border border-neutral-200 dark:bg-[#141414] dark:hover:bg-[#0E0E0E] dark:text-[#A1A1A1] dark:hover:text-[#FAFAFA] dark:border-[#1F1F1F]'
                    )}
                  >
                    {plan.ctaText || 'Get Started'}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Comparison Rows Grouped by Category */}
      <div className="divide-y divide-neutral-200 dark:divide-[#1F1F1F]">
        {filteredCategories.length === 0 ? (
          <div className="py-12 text-center text-xs text-muted-foreground">
            {emptyText}
          </div>
        ) : (
          filteredCategories.map((category) => {
            const isCollapsed = collapsedCategories.has(category.id);

            return (
              <div key={category.id} role="rowgroup" aria-label={category.title} className="py-3">
                {/* Category Header Accordion */}
                <button
                  type="button"
                  onClick={() => toggleCategory(category.id)}
                  aria-expanded={!isCollapsed}
                  className="w-full flex items-center justify-between py-2 text-left group cursor-pointer focus-visible:outline-none"
                >
                  <span className="text-xs font-semibold uppercase tracking-wider text-neutral-900 group-hover:text-black transition-colors flex items-center gap-2 dark:text-[#FAFAFA] dark:group-hover:text-white">
                    <span className="w-1.5 h-1.5 rounded-full bg-neutral-400 dark:bg-white/70" />
                    {category.title}
                  </span>
                  <motion.div
                    animate={{ rotate: isCollapsed ? -90 : 0 }}
                    transition={{ duration: 0.15 }}
                    className="text-muted-foreground group-hover:text-neutral-900 dark:group-hover:text-white"
                  >
                    <ChevronDown className="w-4 h-4" />
                  </motion.div>
                </button>

                {/* Features in Category */}
                <AnimatePresence initial={false}>
                  {!isCollapsed && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={motionTransitions.springGentle}
                      className="overflow-hidden space-y-1 pt-1"
                    >
                      {category.features.map((feat) => (
                        <div
                          key={feat.id}
                          role="row"
                          className={cn(grid, 'gap-4 py-2.5 px-2 rounded-lg hover:bg-neutral-50 transition-colors items-center text-xs dark:hover:bg-[#141414]')}
                        >
                          {/* Feature Name & Info */}
                          <div role="rowheader" className="flex items-center gap-1.5 min-w-0 pr-2">
                            <span className="text-xs text-neutral-900 font-normal truncate dark:text-[#FAFAFA]">
                              {feat.name}
                            </span>
                            {feat.description && (
                              <div className="relative">
                                <button
                                  type="button"
                                  onMouseEnter={() => setActiveTooltip(feat.id)}
                                  onMouseLeave={() => setActiveTooltip(null)}
                                  onClick={() =>
                                    setActiveTooltip((prev) => (prev === feat.id ? null : feat.id))
                                  }
                                  className="text-muted-foreground hover:text-neutral-900 cursor-pointer dark:hover:text-[#FAFAFA]"
                                  aria-label={`Info about ${feat.name}`}
                                >
                                  <Info className="w-3 h-3" />
                                </button>

                                {activeTooltip === feat.id && (
                                  <div className="absolute left-0 bottom-full mb-1 z-30 w-48 p-2 rounded-lg bg-white border border-neutral-200 text-[10px] text-neutral-600 shadow-md backdrop-blur-md dark:bg-[#141414] dark:border-[#1F1F1F] dark:text-[#A1A1A1]">
                                    {feat.description}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>

                          {/* Plan Values */}
                          {plans.map((plan) => {
                            const isMobileActive = selectedPlanId === plan.id;
                            return (
                              <div
                                key={plan.id}
                                role="cell"
                                className={cn(
                                  'sm:flex sm:justify-center items-center',
                                  isMobileActive ? 'flex justify-between py-1' : 'hidden sm:flex'
                                )}
                              >
                                <span className="sm:hidden text-[11px] font-mono text-muted-foreground">
                                  {plan.name}:
                                </span>
                                <div>{renderValueCell(feat.values[plan.id])}</div>
                              </div>
                            );
                          })}
                        </div>
                      ))}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
