"use client";

import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, ChevronsUpDown, Search } from "lucide-react";
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

type Side = "bottom" | "top";
type CloseReason = "escape" | "outside";

function usePopover({
  open,
  onClose,
  rootRef,
  height = 340,
}: {
  open: boolean;
  onClose: (reason: CloseReason) => void;
  rootRef: React.RefObject<HTMLElement | null>;
  height?: number;
}) {
  const [side, setSide] = React.useState<Side>("bottom");

  React.useLayoutEffect(() => {
    if (!open || !rootRef.current) return;
    const rect = rootRef.current.getBoundingClientRect();
    const below = window.innerHeight - rect.bottom;
    setSide(below < height && rect.top > below ? "top" : "bottom");
  }, [open, height, rootRef]);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) onClose("outside");
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose("escape");
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose, rootRef]);

  return side;
}

const popoverMotion = (side: Side, align: "start" | "end" = "start") =>
  ({
    initial: { opacity: 0, scale: 0.96 },
    animate: { opacity: 1, scale: 1 },
    exit: { opacity: 0, scale: 0.97, transition: { duration: 0.1 } },
    transition: { type: "spring", stiffness: 520, damping: 38, mass: 0.7 },
    style: {
      transformOrigin: `${side === "top" ? "bottom" : "top"} ${align === "end" ? "right" : "left"}`,
    },
  }) as const;

const spring = { type: "spring", stiffness: 520, damping: 40, mass: 0.6 } as const;

export interface ComboboxOption {
  value: string;
  label: string;
  description?: string;
  icon?: React.ReactNode;
  keywords?: string[];
  disabled?: boolean;
}

export interface ComboboxProps {
  options: ComboboxOption[];
  value?: string | null;
  defaultValue?: string | null;
  onValueChange?: (value: string | null) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyText?: string;
  align?: "start" | "end";
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
  /** WATA: the trigger's id, for a `<label htmlFor>`. */
  id?: string;
}

const filterOptions = (options: ComboboxOption[], q: string) =>
  q
    ? options.filter((o) => [o.label, o.description, ...(o.keywords ?? [])].some((s) => s?.toLowerCase().includes(q)))
    : options;

const firstEnabled = (items: ComboboxOption[]) => items.findIndex((o) => !o.disabled);

export function Combobox({
  options,
  value,
  defaultValue = null,
  onValueChange,
  placeholder = "Select…",
  searchPlaceholder = "Search…",
  emptyText = "No results.",
  align = "start",
  disabled,
  className,
  "aria-label": ariaLabel,
  id,
}: ComboboxProps) {
  const [selected, setSelected] = useControllableState<string | null>(value, defaultValue, onValueChange);
  const [open, setOpen] = React.useState(false);
  const rootRef = React.useRef<HTMLDivElement>(null);
  const triggerRef = React.useRef<HTMLButtonElement>(null);
  const listId = React.useId();

  const side = usePopover({
    open,
    rootRef,
    height: 330,
    onClose: (reason) => {
      setOpen(false);
      if (reason === "escape") triggerRef.current?.focus();
    },
  });

  const current = options.find((o) => o.value === selected);

  return (
    <div ref={rootRef} className={cn("relative w-64", className)}>
      <button
        ref={triggerRef}
        id={id}
        type="button"
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        // WATA: named by its label when it has one (a wrapping <label>, or htmlFor), not by its placeholder.
        aria-label={ariaLabel}
        aria-controls={open ? listId : undefined}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            setOpen(true);
          }
        }}
        className={cn(
          "flex h-10 w-full items-center gap-2.5 rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none transition-colors hover:border-neutral-300 disabled:pointer-events-none disabled:opacity-50 dark:border-neutral-800 dark:bg-neutral-950 dark:hover:border-neutral-700",
          open && "border-neutral-400 hover:border-neutral-400 dark:border-neutral-600 dark:hover:border-neutral-600"
        )}
      >
        {current?.icon && (
          <span className="grid size-4 shrink-0 place-items-center text-neutral-500 [&>svg]:size-4">
            {current.icon}
          </span>
        )}
        <span
          className={cn(
            "flex-1 truncate text-left",
            current ? "text-neutral-900 dark:text-neutral-100" : "text-neutral-500 dark:text-neutral-500"
          )}
        >
          {current?.label ?? placeholder}
        </span>
        <ChevronsUpDown className="size-4 shrink-0 text-neutral-400" />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            {...popoverMotion(side, align)}
            className={cn(
              "absolute z-50 w-full min-w-[14rem] overflow-hidden rounded-xl border border-neutral-200 bg-white shadow-lg shadow-neutral-900/5 dark:border-neutral-800 dark:bg-neutral-950 dark:shadow-black/40",
              side === "top" ? "bottom-full mb-2" : "top-full mt-2",
              align === "end" ? "right-0" : "left-0"
            )}
          >
            <Panel
              id={listId}
              options={options}
              selected={selected}
              searchPlaceholder={searchPlaceholder}
              emptyText={emptyText}
              onSelect={(v) => {
                setSelected(v);
                setOpen(false);
                triggerRef.current?.focus();
              }}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* mounted only while open, so its state resets every time */
function Panel({
  id,
  options,
  selected,
  searchPlaceholder,
  emptyText,
  onSelect,
}: {
  id: string;
  options: ComboboxOption[];
  selected: string | null;
  searchPlaceholder: string;
  emptyText: string;
  onSelect: (value: string) => void;
}) {
  const [query, setQuery] = React.useState("");
  const q = query.trim().toLowerCase();
  const items = React.useMemo(() => filterOptions(options, q), [options, q]);
  const [active, setActive] = React.useState(() => {
    const i = options.findIndex((o) => o.value === selected);
    return i >= 0 ? i : firstEnabled(options);
  });
  const [box, setBox] = React.useState<{ y: number; h: number } | null>(null);

  const inputRef = React.useRef<HTMLInputElement>(null);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const itemRefs = React.useRef<(HTMLDivElement | null)[]>([]);
  const byKeyboard = React.useRef(false);

  React.useEffect(() => {
    inputRef.current?.focus({ preventScroll: true });
  }, []);

  // one highlight that glides to the active row (measured, so it survives scrolling)
  React.useLayoutEffect(() => {
    const el = itemRefs.current[active];
    const scroller = scrollRef.current;
    if (!el || !scroller) {
      setBox(null);
      return;
    }
    setBox({ y: el.offsetTop, h: el.offsetHeight });
    if (byKeyboard.current) {
      byKeyboard.current = false;
      if (el.offsetTop < scroller.scrollTop) scroller.scrollTop = el.offsetTop - 4;
      else if (el.offsetTop + el.offsetHeight > scroller.scrollTop + scroller.clientHeight)
        scroller.scrollTop = el.offsetTop + el.offsetHeight - scroller.clientHeight + 4;
    }
  }, [active, items]);

  const move = (dir: 1 | -1) => {
    if (!items.length) return;
    let i = active;
    for (let n = 0; n < items.length; n++) {
      i = (i + dir + items.length) % items.length;
      if (!items[i].disabled) {
        byKeyboard.current = true;
        setActive(i);
        return;
      }
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      move(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      move(-1);
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      const list = e.key === "Home" ? items : [...items].reverse();
      const idx = firstEnabled(list);
      if (idx >= 0) {
        byKeyboard.current = true;
        setActive(e.key === "Home" ? idx : items.length - 1 - idx);
      }
    } else if (e.key === "Enter") {
      e.preventDefault();
      const o = items[active];
      if (o && !o.disabled) onSelect(o.value);
    }
  };

  return (
    <>
      <div className="flex items-center gap-2 border-b border-neutral-200 px-3 dark:border-neutral-800">
        <Search className="size-4 shrink-0 text-neutral-400" />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            const v = e.target.value;
            setQuery(v);
            setActive(firstEnabled(filterOptions(options, v.trim().toLowerCase())));
          }}
          onKeyDown={onKeyDown}
          placeholder={searchPlaceholder}
          role="combobox"
          aria-expanded
          aria-controls={id}
          aria-autocomplete="list"
          aria-activedescendant={items[active] ? `${id}-${active}` : undefined}
          className="h-10 w-full bg-transparent text-sm text-neutral-900 outline-none placeholder:text-neutral-500 dark:text-neutral-100 dark:placeholder:text-neutral-500"
        />
      </div>

      <div ref={scrollRef} className="max-h-64 overflow-y-auto overscroll-contain">
        <div id={id} role="listbox" className="relative p-1">
          {box && items.length > 0 && (
            <motion.div
              aria-hidden
              initial={false}
              animate={{ y: box.y, height: box.h }}
              transition={spring}
              className="pointer-events-none absolute inset-x-1 top-0 rounded-lg bg-neutral-100 dark:bg-neutral-800/80"
            />
          )}

          {items.map((o, i) => (
            <div
              key={o.value}
              id={`${id}-${i}`}
              ref={(el) => {
                itemRefs.current[i] = el;
              }}
              role="option"
              aria-selected={o.value === selected}
              aria-disabled={o.disabled || undefined}
              onMouseMove={() => !o.disabled && active !== i && setActive(i)}
              onClick={() => !o.disabled && onSelect(o.value)}
              className={cn(
                "relative flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors",
                active === i ? "text-neutral-900 dark:text-white" : "text-neutral-600 dark:text-neutral-400",
                o.disabled && "cursor-not-allowed opacity-40"
              )}
            >
              {o.icon && (
                <span className="relative grid size-4 shrink-0 place-items-center [&>svg]:size-4">{o.icon}</span>
              )}
              <span className="relative min-w-0 flex-1">
                <span className="block truncate">
                  <Highlight text={o.label} query={q} />
                </span>
                {o.description && (
                  <span className="block truncate text-xs text-neutral-500 dark:text-neutral-500">
                    {o.description}
                  </span>
                )}
              </span>
              {o.value === selected && (
                <motion.span
                  initial={{ scale: 0.4, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={spring}
                  className="relative"
                >
                  <Check className="size-4" />
                </motion.span>
              )}
            </div>
          ))}

          {items.length === 0 && (
            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="px-3 py-6 text-center text-sm text-neutral-500 dark:text-neutral-500"
            >
              {emptyText}
            </motion.p>
          )}
        </div>
      </div>
    </>
  );
}

function Highlight({ text, query }: { text: string; query: string }) {
  const i = query ? text.toLowerCase().indexOf(query) : -1;
  if (i < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, i)}
      <span className="text-neutral-950 underline decoration-neutral-300 underline-offset-[3px] dark:text-white dark:decoration-neutral-600">
        {text.slice(i, i + query.length)}
      </span>
      {text.slice(i + query.length)}
    </>
  );
}
