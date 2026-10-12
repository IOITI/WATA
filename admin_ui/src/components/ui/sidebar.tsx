"use client";

import * as React from "react";
import { AnimatePresence, motion, type PanInfo } from "framer-motion";
import { ChevronRight, PanelLeft } from "lucide-react";
import { cn } from "../../lib/utils";

function useControllableState<T>(
  value: T | undefined,
  defaultValue: T,
  onChange?: (value: T) => void
): [T, (next: T) => void] {
  const [internal, setInternal] = React.useState<T>(defaultValue);
  const controlled = value !== undefined;
  const current = controlled ? (value as T) : internal;

  const onChangeRef = React.useRef(onChange);
  React.useEffect(() => {
    onChangeRef.current = onChange;
  });

  const set = React.useCallback(
    (next: T) => {
      if (!controlled) setInternal(next);
      onChangeRef.current?.(next);
    },
    [controlled]
  );

  return [current, set];
}

const spring = { type: "spring", stiffness: 420, damping: 36, mass: 0.8 } as const;
const EXPANDED = 248;
const COLLAPSED = 56;

export type SidebarChild = { id: string; label: string; badge?: string | number };
export type SidebarItem = {
  id: string;
  label: string;
  icon?: React.ReactNode;
  badge?: string | number;
  children?: SidebarChild[];
};
export type SidebarSection = { title?: string; items: SidebarItem[] };

const SidebarContext = React.createContext({ collapsed: false });
/** Use inside `header` / `footer` to adapt to the icon-only state. */
export const useSidebar = () => React.useContext(SidebarContext);

export interface SidebarProps {
  sections: SidebarSection[];
  activeId: string;
  onActiveChange: (id: string) => void;
  header?: React.ReactNode;
  footer?: React.ReactNode;
  collapsed?: boolean;
  defaultCollapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
  mobileOpen?: boolean;
  onMobileOpenChange?: (open: boolean) => void;
  className?: string;
}

export function Sidebar({
  collapsed: collapsedProp,
  defaultCollapsed = false,
  onCollapsedChange,
  mobileOpen: mobileProp,
  onMobileOpenChange,
  className,
  ...content
}: SidebarProps) {
  const [collapsed, setCollapsed] = useControllableState(collapsedProp, defaultCollapsed, onCollapsedChange);
  const [mobileOpen, setMobileOpen] = useControllableState(mobileProp, false, onMobileOpenChange);

  // ⌘B / Ctrl+B
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "b") {
        e.preventDefault();
        if (window.matchMedia("(min-width: 768px)").matches) setCollapsed(!collapsed);
        else setMobileOpen(!mobileOpen);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [collapsed, mobileOpen, setCollapsed, setMobileOpen]);

  // drawer: Esc to close + scroll lock
  React.useEffect(() => {
    if (!mobileOpen || window.matchMedia("(min-width: 768px)").matches) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMobileOpen(false);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [mobileOpen, setMobileOpen]);

  return (
    <>
      <motion.aside
        initial={false}
        animate={{ width: collapsed ? COLLAPSED : EXPANDED }}
        transition={spring}
        className={cn(
          "hidden h-full shrink-0 overflow-hidden border-r border-neutral-200 bg-white md:block dark:border-neutral-800 dark:bg-neutral-950",
          className
        )}
      >
        <Panel {...content} collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} />
      </motion.aside>

      <AnimatePresence>
        {mobileOpen && (
          <div key="drawer" className="fixed inset-0 z-50 md:hidden">
            <motion.div
              aria-hidden
              className="absolute inset-0 bg-neutral-950/40 backdrop-blur-[2px]"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setMobileOpen(false)}
            />
            <motion.aside
              role="dialog"
              aria-modal="true"
              aria-label="Navigation"
              className="absolute inset-y-0 left-0 w-[17rem] max-w-[85vw] overflow-hidden border-r border-neutral-200 bg-white shadow-2xl dark:border-neutral-800 dark:bg-neutral-950"
              initial={{ x: "-100%" }}
              animate={{ x: 0 }}
              exit={{ x: "-100%" }}
              transition={spring}
              drag="x"
              dragConstraints={{ left: 0, right: 0 }}
              dragElastic={{ left: 0.5, right: 0 }}
              dragMomentum={false}
              onDragEnd={(_, info: PanInfo) => {
                if (info.offset.x < -80 || info.velocity.x < -400) setMobileOpen(false);
              }}
            >
              <Panel {...content} collapsed={false} onNavigate={() => setMobileOpen(false)} />
            </motion.aside>
          </div>
        )}
      </AnimatePresence>
    </>
  );
}

/** Put anywhere (e.g. your top bar). Only visible below md. */
export function SidebarTrigger({ className, ...props }: React.ComponentProps<"button">) {
  return (
    <button
      type="button"
      aria-label="Open navigation"
      className={cn(
        "grid size-9 place-items-center rounded-lg text-neutral-500 transition-colors hover:bg-neutral-100 hover:text-neutral-900 md:hidden dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:text-white",
        className
      )}
      {...props}
    >
      <PanelLeft className="size-[18px]" />
    </button>
  );
}

/* ───────────────────────── internals ───────────────────────── */

type PanelProps = Pick<SidebarProps, "sections" | "activeId" | "onActiveChange" | "header" | "footer"> & {
  collapsed: boolean;
  onToggle?: () => void;
  onNavigate?: () => void;
};

function Panel({
  sections,
  activeId,
  onActiveChange,
  header,
  footer,
  collapsed,
  onToggle,
  onNavigate,
}: PanelProps) {
  const uid = React.useId();
  const [hoverBox, setHoverBox] = React.useState<{ y: number; h: number } | null>(null);
  const [hovering, setHovering] = React.useState(false);
  const [tip, setTip] = React.useState<{ label: string; x: number; y: number } | null>(null);
  const [open, setOpen] = React.useState<Set<string>>(
    () =>
      new Set(
        sections
          .flatMap((s) => s.items)
          .filter((i) => i.children?.some((c) => c.id === activeId))
          .map((i) => i.id)
      )
  );

  const enter = (e: React.MouseEvent<HTMLElement> | React.FocusEvent<HTMLElement>, label: string) => {
    const el = e.currentTarget;
    setHoverBox({ y: el.offsetTop, h: el.offsetHeight });
    setHovering(true);
    if (collapsed) {
      const r = el.getBoundingClientRect();
      setTip({ label, x: r.right + 10, y: r.top + r.height / 2 });
    } else setTip(null);
  };

  const renderItem = (item: SidebarItem) => {
    const hasChildren = !!item.children?.length;
    const isOpen = hasChildren && open.has(item.id) && !collapsed;
    const childActive = !!item.children?.some((c) => c.id === activeId);
    const isActive = !hasChildren && activeId === item.id;

    const onClick = () => {
      if (!hasChildren) {
        onActiveChange(item.id);
        onNavigate?.();
      } else if (collapsed) {
        onToggle?.();
        setOpen((p) => new Set(p).add(item.id));
      } else {
        setOpen((p) => {
          const n = new Set(p);
          if (n.has(item.id)) n.delete(item.id);
          else n.add(item.id);
          return n;
        });
      }
    };

    return (
      <div key={item.id}>
        <button
          type="button"
          onClick={onClick}
          onMouseEnter={(e) => enter(e, item.label)}
          onFocus={(e) => enter(e, item.label)}
          aria-label={collapsed ? item.label : undefined}
          aria-expanded={hasChildren ? isOpen : undefined}
          aria-current={isActive ? "page" : undefined}
          className={cn(
            "relative flex h-9 w-full items-center gap-3 rounded-lg px-2.5 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-neutral-400/50 dark:focus-visible:ring-neutral-600/60",
            isActive || childActive
              ? "text-neutral-900 dark:text-white"
              : "text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-white"
          )}
        >
          {isActive && (
            <motion.span
              layoutId={`${uid}-active`}
              transition={spring}
              className="absolute inset-0 rounded-lg bg-neutral-200/70 dark:bg-neutral-800"
            />
          )}
          <span className="relative grid size-5 shrink-0 place-items-center [&>svg]:size-[18px]">{item.icon}</span>
          <motion.span
            initial={false}
            animate={{ opacity: collapsed ? 0 : 1 }}
            transition={{ duration: 0.15 }}
            className="relative flex-1 truncate whitespace-nowrap text-left"
          >
            {item.label}
          </motion.span>
          {!collapsed && item.badge !== undefined && (
            <motion.span
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="relative rounded-md bg-neutral-200/70 px-1.5 text-[11px] tabular-nums text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"
            >
              {item.badge}
            </motion.span>
          )}
          {!collapsed && hasChildren && (
            <ChevronRight
              className={cn(
                "relative size-3.5 shrink-0 text-neutral-400 transition-transform duration-200",
                isOpen && "rotate-90"
              )}
            />
          )}
        </button>

        <AnimatePresence initial={false}>
          {isOpen && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
              className="overflow-hidden"
            >
              <div className="ml-[19px] mt-0.5 space-y-0.5 border-l border-neutral-200 py-0.5 pl-3 dark:border-neutral-800">
                {item.children!.map((c) => {
                  const active = c.id === activeId;
                  return (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => {
                        onActiveChange(c.id);
                        onNavigate?.();
                      }}
                      onMouseEnter={() => {
                        setHovering(false);
                        setTip(null);
                      }}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "relative flex h-8 w-full items-center gap-2 rounded-md px-2 text-[13px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-neutral-400/50 dark:focus-visible:ring-neutral-600/60",
                        active
                          ? "text-neutral-900 dark:text-white"
                          : "text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-white"
                      )}
                    >
                      {active && (
                        <motion.span
                          layoutId={`${uid}-dot`}
                          transition={spring}
                          className="absolute -left-[15.5px] top-[13px] size-1.5 rounded-full bg-neutral-900 dark:bg-white"
                        />
                      )}
                      <span className="flex-1 truncate text-left">{c.label}</span>
                      {c.badge !== undefined && (
                        <span className="text-[11px] tabular-nums text-neutral-400">{c.badge}</span>
                      )}
                    </button>
                  );
                })}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    );
  };

  return (
    <SidebarContext.Provider value={{ collapsed }}>
      <div className="flex h-full flex-col">
        {header && (
          <div
            className={cn(
              "flex h-14 shrink-0 items-center overflow-hidden px-4 transition-all",
              collapsed && "w-14 justify-center px-0 [&>div>*:not(:first-child)]:hidden [&>div]:gap-0 [&>*:not(:first-child)]:hidden"
            )}
          >
            {header}
          </div>
        )}

        <nav
          aria-label="Main"
          onMouseLeave={() => {
            setHovering(false);
            setTip(null);
          }}
          className="relative flex-1 space-y-1 overflow-y-auto overflow-x-hidden p-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {hoverBox && (
            <motion.span
              aria-hidden
              initial={false}
              animate={{ y: hoverBox.y, height: hoverBox.h, opacity: hovering ? 1 : 0 }}
              transition={{ y: spring, height: spring, opacity: { duration: 0.15 } }}
              className="pointer-events-none absolute inset-x-2 top-0 rounded-lg bg-neutral-100 dark:bg-neutral-900"
            />
          )}

          {sections.map((section, i) => (
            <div key={i} className="space-y-0.5">
              {section.title &&
                (collapsed ? (
                  <div className="mx-2.5 my-2 h-px bg-neutral-200 dark:bg-neutral-800" />
                ) : (
                  <p className="truncate px-2.5 pb-1 pt-4 text-xs text-neutral-400 dark:text-neutral-500">
                    {section.title}
                  </p>
                ))}
              {section.items.map(renderItem)}
            </div>
          ))}
        </nav>

        {(footer || onToggle) && (
          <div className="shrink-0 space-y-1 border-t border-neutral-200 p-2 dark:border-neutral-800">
            {footer}
            {onToggle && (
              <button
                type="button"
                onClick={onToggle}
                onMouseEnter={(e) => {
                  if (!collapsed) return;
                  const r = e.currentTarget.getBoundingClientRect();
                  setTip({ label: "Expand", x: r.right + 10, y: r.top + r.height / 2 });
                }}
                onMouseLeave={() => setTip(null)}
                aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                className="flex h-9 w-full items-center gap-3 rounded-lg px-2.5 text-sm text-neutral-500 outline-none transition-colors hover:bg-neutral-100 hover:text-neutral-900 focus-visible:ring-2 focus-visible:ring-neutral-400/50 dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-white dark:focus-visible:ring-neutral-600/60"
              >
                <span className="grid size-5 shrink-0 place-items-center">
                  <PanelLeft className="size-[18px]" />
                </span>
                <motion.span
                  initial={false}
                  animate={{ opacity: collapsed ? 0 : 1 }}
                  transition={{ duration: 0.15 }}
                  className="flex-1 whitespace-nowrap text-left"
                >
                  Collapse
                </motion.span>
                {!collapsed && (
                  <kbd className="rounded border border-neutral-200 px-1 font-sans text-[10px] text-neutral-400 dark:border-neutral-800">
                    ⌘B
                  </kbd>
                )}
              </button>
            )}
          </div>
        )}
      </div>

      <AnimatePresence>
        {collapsed && tip && (
          <div
            key="tip"
            className="pointer-events-none fixed z-[60] -translate-y-1/2"
            style={{ left: tip.x, top: tip.y }}
          >
            <motion.div
              initial={{ opacity: 0, x: -4 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.12 }}
              className="whitespace-nowrap rounded-md bg-neutral-900 px-2 py-1 text-xs font-medium text-white shadow-lg dark:bg-white dark:text-neutral-900"
            >
              {tip.label}
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </SidebarContext.Provider>
  );
}
