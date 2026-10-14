"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { ChevronRight, Ellipsis } from "lucide-react";
import { cn } from "../../lib/utils";

const spring = { type: "spring", stiffness: 500, damping: 40, mass: 0.6 } as const;

export interface BreadcrumbItem {
  label: string;
  href?: string;
  icon?: React.ReactNode;
}

export interface BreadcrumbProps extends Omit<React.ComponentProps<"nav">, "children"> {
  items: BreadcrumbItem[];
  separator?: "chevron" | "slash";
  /** Collapse the middle into "…" when there are more items than this. */
  maxItems?: number;
  /** Your router's link, e.g. next/link. Defaults to <a>. */
  as?: React.ElementType;
}

export function Breadcrumb({
  items,
  separator = "chevron",
  maxItems = 4,
  as,
  className,
  ...props
}: BreadcrumbProps) {
  const LinkComponent = (as ?? "a") as React.ElementType;
  const uid = React.useId();
  const listRef = React.useRef<HTMLOListElement>(null);
  const [expanded, setExpanded] = React.useState(false);
  const [hover, setHover] = React.useState<number | null>(null);

  const canCollapse = items.length > maxItems;
  const tail = Math.max(1, maxItems - 2); // items kept at the end while collapsed
  const hiddenFrom = 1;
  const hiddenTo = items.length - tail; // exclusive
  const collapsed = canCollapse && !expanded;

  // move focus to the first revealed crumb
  React.useEffect(() => {
    if (expanded) listRef.current?.querySelector<HTMLElement>("[data-revealed]")?.focus();
  }, [expanded]);

  const entries: (number | "more")[] = [];
  items.forEach((_, i) => {
    if (collapsed && i >= hiddenFrom && i < hiddenTo) {
      if (i === hiddenFrom) entries.push("more");
    } else entries.push(i);
  });

  const sep = (
    <span aria-hidden className="text-neutral-300 dark:text-neutral-700 [&>svg]:size-3.5">
      {separator === "slash" ? <span className="text-sm">/</span> : <ChevronRight />}
    </span>
  );

  const linkBase =
    "relative flex h-7 items-center gap-1.5 rounded-md px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-neutral-400/50 dark:focus-visible:ring-neutral-600/60";

  return (
    <nav aria-label="Breadcrumb" className={className} {...props}>
      <ol
        ref={listRef}
        onMouseLeave={() => setHover(null)}
        className="flex flex-wrap items-center gap-x-0.5 gap-y-1 text-muted-foreground"
      >
        {entries.map((entry, n) => {
          const first = n === 0;

          if (entry === "more") {
            return (
              <motion.li key="more" layout="position" className="flex items-center gap-0.5">
                {sep}
                <button
                  type="button"
                  aria-label="Show hidden pages"
                  onClick={() => setExpanded(true)}
                  className="grid h-7 w-8 place-items-center rounded-md outline-none transition-colors hover:bg-neutral-100 hover:text-foreground focus-visible:ring-2 focus-visible:ring-neutral-400/50 dark:hover:bg-neutral-800 dark:focus-visible:ring-neutral-600/60"
                >
                  <Ellipsis className="size-4" />
                </button>
              </motion.li>
            );
          }

          const item = items[entry];
          const isLast = entry === items.length - 1;
          const revealed = expanded && canCollapse && entry >= hiddenFrom && entry < hiddenTo;
          const body = (
            <span className="relative flex items-center gap-1.5 [&>svg]:size-4">
              {item.icon}
              <span>{item.label}</span>
            </span>
          );

          return (
            <motion.li
              key={entry}
              layout="position"
              initial={revealed ? { opacity: 0, x: -6 } : false}
              animate={{ opacity: 1, x: 0 }}
              transition={spring}
              className="flex items-center gap-0.5"
            >
              {!first && sep}
              {isLast || !item.href ? (
                <span
                  aria-current={isLast ? "page" : undefined}
                  className={cn(linkBase, isLast && "font-medium text-foreground")}
                >
                  {body}
                </span>
              ) : (
                <LinkComponent
                  href={item.href}
                  data-revealed={revealed && entry === hiddenFrom ? "" : undefined}
                  onMouseEnter={() => setHover(entry)}
                  onFocus={() => setHover(entry)}
                  onBlur={() => setHover(null)}
                  className={cn(linkBase, "transition-colors hover:text-foreground")}
                >
                  {hover === entry && (
                    <motion.span
                      layoutId={`${uid}-hover`}
                      transition={spring}
                      className="absolute inset-0 rounded-md bg-neutral-100 dark:bg-neutral-800"
                    />
                  )}
                  {body}
                </LinkComponent>
              )}
            </motion.li>
          );
        })}
      </ol>
    </nav>
  );
}
