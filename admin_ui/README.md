# WATA Admin interface

The React interface of the `admin` service. It is built into `../src/admin/static/`, which the admin FastAPI
application (`../src/admin/app.py`) serves and which ships with `./src` in the deployment package. Nothing is
built on the server.

React 19, Vite, TypeScript, Tailwind CSS 4, [EasyUI](https://easyui.site) components, TanStack Query,
React Router, Recharts, and TradingView's Lightweight Charts for the charts of the stored prices (the Data section).
The Technical Analysis pages add PineTS, Vela and Vela-pinets, and CodeMirror 6 (see below).

## Working on it

Node.js is required, in the version of `.nvmrc` (`nvm use`).

```bash
npm ci
npm run dev:api     # the real admin application over fake data, on 127.0.0.1:8082
npm run dev         # Vite dev server; /api is proxied to the server above
```

`npm run dev:api` needs the Python dependencies of the repository. It prints the password to sign in with,
keeps everything in memory and in a temporary directory, and never talks to Saxo, PostgreSQL or RabbitMQ.
Its recorded prices (`dev/fake_prices.py`) are made up from the time asked for: an index, six turbos that
follow it, two recording gaps a day, and a couple of trades and signals on them. Three of the turbos are not
quoted all day, each in its own way (holes, a stop, a knock-out), and three have a volume. Its Dukascopy
provider is the real one, on the days kept under `var/lib/price_history` of the checkout: an instrument added
from the Providers page goes to `var/lib/price_history/dukascopy/instruments.json` there. Its backtests are
real runs of `python -m src.backtest.run` (Pine scripts through the real runner, which `npm run build:runner`
builds) on the last eight weekdays of those made-up prices, written at start as the files the command reads
(`dev/fake_recorded.py`); the runs are kept under `var/lib/backtests` of the checkout.

The proxy of `vite.config.ts` passes the `Host` header on as the browser sent it. The admin refuses a change
whose `Origin` is not its own `Host`; with the header rewritten to the API's address, signing in from the dev
server is refused.
Set `FAKE_SAXO_EXPIRED=1` to see the Saxo re-authorisation flow, and `FAKE_TOTP=1` to require a one-time code
at login (the key to add to an authenticator app is printed at start).

```bash
npm run lint
npm run typecheck
npm test
npm run build       # writes ../src/admin/static (ignored by git; ./package.sh runs this)
```

## Layout

| Path | Content |
|---|---|
| `src/components/ui/` | EasyUI components, copied in (see below) |
| `src/components/` | Shell, cards, status badges, the confirmation dialog for live actions, charts |
| `src/features/<page>/` | One directory per page |
| `src/lib/` | API client and types (`api.ts`, `types.ts` mirror `src/admin/routers/`), formatting |
| `src/test/` | Test setup, and the stand-ins for the charting library and for the system's theme |
| `src/styles/` | `tokens.css` (EasyUI, unchanged), `theme.css` (WATA additions), `index.css` (Tailwind mapping) |
| `dev/fake_api.py`, `dev/fake_prices.py`, `dev/fake_recorded.py` | The fake-data server, its made-up recorded prices, and those prices written as the days its real backtests trade on |

## Themes

The interface is light or dark. It follows the system setting, and keeps following it when it changes; the
button in the top bar (and on the login page) goes from there to always light, to always dark, and back. A
choice is kept in the browser (`localStorage`, key `wata.theme`): it is a matter of screen and room, not of
operator.

- **`dark` on `<html>` is the switch.** Every dark style is a `dark:` class or a `.dark` token; `:root` holds
  the light values (`styles/tokens.css` from EasyUI, `styles/theme.css` for what WATA adds).
- **`public/theme.js` sets it before the first paint**, so a page does not flash in the other theme while
  the application loads. It is a file, not a few lines in `index.html`, because the admin's
  Content-Security-Policy allows no inline script. `src/lib/theme.ts` holds the same rule for the running
  application (`ThemeProvider`, `ThemeToggle`): keep the two in step.
- **A colour needs both values.** A class that names a colour is written `light dark:dark`
  (`text-neutral-600 dark:text-neutral-400`), or goes through a token that has both. A literal with one
  value shows in one theme only.
- **Text colours are checked, not judged by eye.** The status colours as text (`--positive`, `--negative`,
  `--warning`, `--info`) and the secondary greys reach 4.5:1 on every surface of their theme, a hovered row
  included; the light values of `theme.css` say which of EasyUI's own were lowered for that.
- **The price chart draws on a canvas** with the colours it read when it was made: every page that shows one
  gives it the theme as its `key` (an asset's chart, a backtest's day and its trade, the editor's test, the
  TradingView comparison), so another theme makes another chart. The Recharts charts read CSS variables and
  follow by themselves.
- Tests stand in for the system setting with `src/test/systemTheme.ts` (jsdom has no `matchMedia`). A
  component rendered without a `ThemeProvider` is shown dark.

The admin answers with a strict Content-Security-Policy (own origin only), so nothing may be loaded from
another origin or inlined as a `data:` URI. Fonts come from `@fontsource-variable/*` and
`build.assetsInlineLimit` is 0 for that reason.

## EasyUI

EasyUI is a collection of components that are copied into the project rather than installed, with the shadcn CLI:

```bash
npx shadcn@latest add Surajmaurya1/easyui/<slug>
```

- Source: https://github.com/Surajmaurya1/easyui at commit `4dbc8b18f4ce0f1173807e8ba22e586619d52b3e`
  (2026-10-11), MIT License, Copyright (c) 2026 Suraj Maurya. `activity-heatmap`, `animated-number`, `combobox`,
  `directional-tooltip`, `interactive-timeline`, `not-found` (redesigned since), `notification-bell`,
  `recovery-ledger`, `scroll-progress-nav`, `selection-basket`, `smart-comparison` and `spotlight-search` at
  `50effaf67bb4cfcebac4dea7c4ba1fb40812a2cd` (2026-10-23); `ai-agent-activity`, `airport-matrix-clock`,
  `animated-file-upload`, `dependency-trace`, `dynamic-island`, `funnel-chart`, `live-line-chart`,
  `payment-status`, `peek-card`, `split-button`, `stat-card` and `undo-toast` at
  `7c106d4a195f9ce566ffbbc672fc48bed68d30bd` (2026-10-23, the merge that brought the funnel, the live line and the
  stat card; the copies above are the same there). Copied by hand from those commits' `src/components/ui/`
  (`ActivityHeatmap.tsx` is `activity-heatmap.tsx` here, and so on): the CLI takes the newest and would offer
  to overwrite `src/lib/utils.ts` and `src/lib/motion-tokens.ts`, which are the same there.
- Components in use: `activity-heatmap`, `advanced-data-table`, `ai-agent-activity`, `airport-matrix-clock`,
  `alert`, `animated-file-upload`, `animated-number`, `animated-tabs`, `badge`, `breadcrumb`, `button`, `calendar`,
  `combobox`, `date-picker`, `dependency-trace`, `directional-tooltip`, `drag-to-confirm`, `dynamic-island`,
  `form`, `funnel-chart`, `interactive-timeline`, `live-line-chart`, `loader`, `not-found`, `notification-bell`,
  `otp-input`, `payment-status`, `peek-card`, `recovery-ledger`, `scroll-progress-nav`, `selection-basket`,
  `settle-modal`, `sidebar`, `skeleton`, `smart-comparison`, `smooth-accordion`, `split-button`,
  `spotlight-search`, `spring-select`, `stat-card`, `stepper`, `stretch-switch`, `undo-toast`, `velocity-toast`.
- `src/lib/motion-tokens.ts`, `src/lib/utils.ts` and `src/styles/tokens.css` come from it too, and
  `src/lib/chart-primitives.tsx` (the heatmap's, at `50effaf`, the same at `7c106d4`) and
  `src/lib/dashboard-chart-utils.ts` (the funnel's, the live line's and the stat card's, at `7c106d4`, unchanged).

Changes made to the copied files:

- `advanced-data-table.tsx`: new `selectable` prop (default `true`) to hide the row checkboxes of tables
  that have no bulk action; `defaultHidden` on a column (shown once picked in the column list); `emptyTitle` /
  `emptyDescription` on `AdvancedDataTable`, and "Clear filters" only when a search or a filter is active;
  cells padded 20 px instead of 24, so that the seven columns of the closed positions fit a 1440 px screen;
  `initialSearch` and `initialExpandedRowIds`, for a link that lands on one row found and opened (an order
  from its position, a position from its order: `?find=<id>`); `initialFilters`, for a link that opens the
  table filtered (the Assets View from a provider); a selection the page holds (`selectedRowIds` with
  `onSelectionChange`), `isRowSelectable` (a row that cannot be chosen has no box, and the header's box leaves it
  out, disabled when no row of the page can be chosen), `selectionBasket` (the bulk actions in `selection-basket` after the table's frame, outside its
  `overflow-hidden`, its "All" every selectable row the search and filters leave, on every page; the table's own
  counter and bar not shown then) and each row's box named after `rowLabel`; `expandOnRowClick` (a click anywhere on a row opens it,
  except on a link, a button, a field or the end of a selection of its text; the arrow stays for the
  keyboard), `singleExpand` (one row open at a time) and `rowLabel` (each arrow named after its row, "Open
  …", instead of all of them "Expand row details"); a search goes back to the first page, where what it
  found starts; from a tablet up, a filter with more options than the bar is wide wraps onto a second line
  (the Orders page's Result has nine), where it went past the edge.
- `breadcrumb.tsx`: its text in `text-muted-foreground` and `text-foreground`, not `neutral-500` (4.54:1 on
  the light background, a hair from the limit) and `neutral-900`. Copied by hand at the pinned commit: the
  CLI would also offer to overwrite `src/lib/utils.ts`, which is the same. React Router's `Link` reads `to`
  where this component gives `href`: it is given `components/RouterLink.tsx`. `wrap={false}` keeps the trail
  on one line, for the top bar: each crumb cut short with an ellipsis, the last one first.
- `settle-modal.tsx`: rendered in `document.body` (a dialog opened from a table cell took the cell's
  alignment and wrapping); focus moves into the dialog, stays in it and returns to what opened it. An `xl`
  size (`max-w-2xl`), a `full` one (`max-w-[110rem]`, two versions of a script side by side), and a panel no
  taller than the screen, what does not fit scrolling inside it.
- `otp-input.tsx` (the login page's one-time code; the same at the pinned commit and at EasyUI's `d5f660f` of
  2026-10-14): `id`, given to the first box for a `<label htmlFor>`. A box no longer keeps one character
  (`maxLength`): several digits arriving in one box (a keyboard's suggestion) go on from it, a whole code (the
  autofill of a password manager or of a phone) fills every box as a paste does, a digit typed beside the one
  a box holds replaces it, a letter is ignored. Its pop is a 0.25 s tween: framer-motion 13 animates a spring
  between two keyframes only, and EasyUI's `[1, 1.25, 1]` threw at each digit in development (in production the
  box stopped at 1.25 until the animation ended). Given a `value`, the component lays the digits out from the
  first box, so a box left empty moves them: the login page leaves it uncontrolled, and makes it again to empty it.
- `sidebar.tsx`, `animated-tabs.tsx`: an entry or a tab with an `href` is a link, drawn by `linkComponent`
  (`components/RouterLink.tsx`), so that it can be opened in another tab; a click with ⌘, Ctrl, Shift or Alt is
  left to the browser (`isPlainClick`, `src/lib/links.ts`). The sidebar's `onActiveChange` is optional; a tab's
  plain click still goes through `onChange`, and a tab without `href` is `type="button"`.
- `button.tsx`: `buttonClassName`, the classes of a variant and size, for a link drawn as a button
  (`components/ButtonLink.tsx`).
- `drag-to-confirm.tsx`: the hidden fallback button is out of the Tab order (it was a stop nobody could see,
  on which Enter confirmed); ArrowRight no longer confirms, only Enter and Space.
- `velocity-toast.tsx`: `wrapperClassName`, to lift the toast above the bar at the bottom of the configuration page.
- `stretch-switch.tsx`: the switch is named by its label and described by its description
  (`aria-labelledby`, `aria-describedby`); it had no name, so a screen reader only said "switch, on".
- `form.tsx`, `animated-tabs.tsx`, `smooth-accordion.tsx`: the literal grey `#6B6B6B` (3.6:1 on the dark
  surface) replaced by `text-muted-foreground`; hints at 12 px instead of 11.
- `not-found.tsx` (EasyUI's 404 of 2026-10-23): a `<section>` named by its heading, not a `<main>` (the shell
  has one), drawn in the page's area rather than filling the screen; no `<title>` or `<meta name="robots">` (the
  page names the tab as every page does, `useTabTitle`; nothing behind the login is indexed); light values for the
  text, the heading's gradient, the address chip, the buttons and the suggestions; its own white focus ring dropped
  for the admin's; `ResizeObserver` and `matchMedia` used only where they exist (jsdom has neither). The
  admin's pages show it through `components/NotFoundState.tsx`.
- `scroll-progress-nav.tsx`: a part with an `href` is a link: a plain click scrolls to it and writes it in the
  address in place (`history.replaceState`, no step more to go back through), a click with ⌘, Ctrl, Shift or
  Alt is the browser's (`isPlainClick`); a part is scrolled into view in its container too, so that its
  `scroll-margin-top` keeps it clear of a sticky nav (EasyUI scrolled the container to 20 px of its top, under the
  nav), at once when motion is reduced; from a tablet up the parts are a row that scrolls sideways when wider than
  the page, the current one kept in it, with room for the focus ring (ten parts do not fit at 1,280 px); the
  phone's menu centred under the pill and as wide as its longest part; `aria-current` on the current part,
  `aria-expanded` on the phone's toggle, the parts' numbers hidden from screen readers, and a `label` (default
  "Table of contents"); light values for every colour.
- `interactive-timeline.tsx`: a `warning` status (amber); the status badge only for an item with a
  `statusLabel` (EasyUI printed the raw status on every item); `defaultExpandedIds`; a `label` that names the list,
  and no landmark region (a page of orders opens several); an item with nothing but its title and time has no
  fold (EasyUI drew an empty footer under it); its note wraps; light values for every colour, its white focus
  rings dropped for the admin's.
- `selection-basket.tsx`: a `sticky` mode (at the bottom of the screen while its list is on it, resting under
  the list past it, so that it never covers what follows); an action with an `href` is a link drawn by
  `linkComponent`, and an action can be `disabled` with a `title` saying why, in a tooltip
  (`directional-tooltip`) on a wrapper that takes the focus the disabled button cannot; `noun` ("2 runs selected");
  `allSelected` (EasyUI compared the counts, wrong once a search hides a selected row); the count's circle grows
  with three digits; the bar alone scrolls on a narrow screen (an inner scroll cut the focus ring); light values
  for every colour.
- `notification-bell.tsx` (the top bar's events, `features/live/EventsBell.tsx`): an entry's `detail` (a second
  line), `href` (a link drawn by `linkComponent`: a plain click marks it read and closes the panel, a click with a
  modifier is the browser's) and `tone` (its dot's colour; only `danger` pulses, EasyUI pulsed every unread one);
  a `footer` and a `triggerClassName`; Escape closes the panel and gives the bell the focus again; the panel is a
  dialog named "Notifications", across the screen under the top bar on a phone (centred on the bell, it went past
  the edge); light values for every colour.
- `spotlight-search.tsx` (⌘K, `components/CommandPalette.tsx`): EasyUI's site imports and own entries removed
  (`GITHUB_URL`, `CATALOG_INDEX`, its callbacks for them); an entry's `href` makes it a link, followed with Enter
  too (`onNavigate`), and its `keywords` find it; every word typed must be in an entry; `onQueryChange`, `limit`
  (50 listed, the others found by typing, said under the list) and `label`; a combobox over a listbox with
  `aria-activedescendant`; the focus given back on closing; each entry shows what it is and its category; light
  values for every colour.
- `combobox.tsx` (the long lists of a form: an asset, a sweep's axis): `id` on the trigger for a `<label htmlFor>`
  (a `<label>` around it passed the clicks on its options to the trigger, which opened again); named by its label,
  not by its placeholder; placeholder, description and empty text a darker grey in light; its own focus ring
  dropped for the admin's.
- `directional-tooltip.tsx` (`components/Hint.tsx` puts it on a control, a disabled one through a wrapper that
  takes the focus): drawn in `document.body` at fixed coordinates beside its trigger (a table's overflow cut it),
  on the other side when there is no room, and inside the screen; shown on a tap too, hidden by Escape, a tap
  elsewhere or a scroll; its arrow points at the trigger's middle, also when the tooltip was moved to stay on the
  screen; its text always in the page for screen readers (`aria-describedby`), the drawn tooltip hidden from them;
  nothing drawn, said or added to its trigger when its content is empty, so that a field keeps its wrapper, and
  its focus, while a hint comes and goes; `wrapperClassName`; long text wraps within the screen; light values for
  every colour.
- `recovery-ledger.tsx` (the configuration's previous versions, `features/config/BackupsPanel.tsx`): an entry
  opens from a button (`aria-expanded`) whose box covers its header (EasyUI's card took clicks only), and one
  without details does not open; the entry opened can be held by the page (`expandedId`, `onExpandedChange`);
  `details.content` (any node: the changes a restore would make), `reference` (a name in the footer, the file's),
  `timestamp` optional, `title`, `countLabel`, `restoreLabel`, `currentLabel`; Restore only calls `onRestore`
  (the page asks for the slide); an ordered list; the timeline's line through the pips (it ran 5 px to their
  right); light values for every colour.
- `smart-comparison.tsx` (runs compared, `features/backtests/ComparePage.tsx`): a table for screen readers (rows,
  column and row headers, a group per category) drawn as EasyUI's grid, one column per plan whatever their number
  (EasyUI's grid had three); a plan's `href` (its name a link, `linkComponent`) and `accent` (a colour before its
  name: the run's on the chart); a feature's `keys`, its values as text for "differences only" (React nodes
  cannot be compared); `label`, `matrixTitle`, `countLabel`, `searchPlaceholder`, `diffLabel`, `emptyText`; a
  plan's button only when it does something; `aria-pressed` and `aria-expanded` on the toggles; light values for
  every colour.
- `activity-heatmap.tsx` (Performance's calendar, the Exits page's and a run's weekday × hour,
  `components/charts/Heatmaps.tsx`): `diverging`, signed values on two scales (`--chart-gain-*`,
  `--chart-loss-*` of `theme.css`), the steps of size counted on gains and losses together, the best and the
  worst said above it, the legend from the largest loss to the largest gain; `format`, `summary`, `emptyText`
  (a cell with nothing is not a cell of zero), `legend`; a line under each cell's value (`HeatDay.detail`,
  `details`); in hours mode, `hours` (the columns), as many rows as `rowLabels`, cells 64 px wide at most (two
  hours made two bars across the panel); in calendar mode, `startDate` (no day before the period), and the first
  column's month gives way to the next one when that starts within three weeks; dates as the admin writes them;
  the best and the worst each on one line; the cell under the pointer found again (EasyUI took the labels' room
  off twice: a column and a row off); the keyboard starts on the first cell there is (a period that starts
  mid-week had none where it started, and the keys led nowhere); the admin's focus ring; small text at 4.5:1 in
  both themes. `src/lib/chart-primitives.tsx`: measured once where there is no `ResizeObserver`
  (jsdom); excluded from the linter with the copies.
- `animated-number.tsx` (`PnlValue`'s `animate`: the live values of open positions, the day's profit and loss on
  the dashboard): `text`, the value in the admin's format (sign, € and %), whose digits roll and the rest stays;
  read by screen readers as that text, the columns of digits hidden from them (an `aria-label` on a span is not
  read); plain text when motion is reduced; the tabular figures of the admin's font (EasyUI's monospace digits
  were 0.58 em wide); every character in a box clipped by `clip-path`, which keeps the text's baseline (with
  EasyUI's `overflow: hidden`, digits stood higher than a sign beside them); colour inherited (EasyUI's was white).
- `payment-status.tsx` (the card that follows a close or a signal sent by hand, `features/live/FollowProvider.tsx`):
  the steps of what was asked rather than of a payment (`processing` asked, `verifying` sent and waiting for its
  answer, `success`, `failed`, `unknown` for no news; `refunded` gone), each with the caller's `title` and
  `description`; `subject` (EasyUI's amount), `figures` (EasyUI's receipt and transaction details), a copied
  `reference` and one `link` (`linkComponent`); `onDismiss` and its close button; no Retry, other payment method,
  receipt or download: the card acts on nothing; laid out in a row, the badge beside the words (docked at the edge
  of the screen, EasyUI's centred column took half a phone's height); read out as it changes (`role="status"`);
  its spinners still where motion is reduced; light values for every colour.
- `live-line-chart.tsx` (an open position's performance, `features/positions/LivePositionChart.tsx`):
  `initialPoints`, values known with their own times (EasyUI spaced them `intervalMs` apart up to now);
  `formatTick` and `formatChange` (the change since the window's start in the caller's words: EasyUI gave a
  percentage of the first value ever pushed, which means nothing for a value that is itself a percentage); a zero
  line while zero is in view; times in minutes from two of them ("−15 min": the axis read "−900s"); a frame when
  the line has moved half a pixel or something changed (EasyUI drew 60 frames a second, which a window of fifteen
  minutes, moving under a pixel a second, does not need), its time step as long as that pace; drawn without an
  `IntersectionObserver` where there is none (jsdom); the admin's status colours for the change (emerald-500 and
  rose-500 are under 4.5:1 on white) and its focus ring on the pause button.
- `funnel-chart.tsx` (where a run's entry signals went, `features/backtests/runPanels.tsx`, and the journal's,
  `features/signals/JournalPanel.tsx`): the stages in `--chart-funnel-1..5` of `theme.css`, one hue (EasyUI mixed
  indigo and teal); a stage's `dropLabel`, what was lost on the way to the next one, in the summary and the
  tooltip; `summaryLabel`, `noun` (EasyUI's "Users") and `emptyText`; a stage that loses nothing says "0%"
  (EasyUI wrote "−0.0%"); the loss in the admin's colour (rose-500 is 3.7:1 on white); muted text at 4.5:1 in both
  themes; the admin's focus ring.
- `stat-card.tsx` (the dashboard's day profit and loss, the Report's account value): the look of the admin's
  tiles (`KpiTile`), which it sits among (border, surface, padding, type sizes; EasyUI's card was white, ringed
  and shadowed); `toneOf`, the headline's colour by the value shown, the scrubbed one's too; the change pill only
  when the caller gives what it is from (`previous`, `delta`): EasyUI worked one out from the line's two ends,
  which means nothing for a profit that changes sign; the pill in the admin's status colours (emerald-600 on its
  pale pill was 3.6:1); muted text at 4.5:1; the admin's focus ring.
- `undo-toast.tsx` (removing backtests, `features/backtests/PendingRemovalsProvider.tsx`): `wrapperClassName`;
  the time left kept at each frame, so that a pause takes up where it stopped and the bar shows what is left of
  the whole (after a pause, EasyUI's went back to full); the caller's `onDismiss` through a ref (a new one at each
  render of the caller restarted the countdown); paused while the keyboard is in it, as under the pointer; light
  values for every colour.
- `split-button.tsx` (a run's actions): the primary action and an option may have an `href` (a link drawn by
  `linkComponent`; an option chosen from the keyboard is followed as a click would follow it); an option's
  `description` (a line under it) and `tone: 'danger'`; `icon` and `menuLabel` (EasyUI named the chevron "More
  <label> options"); the option the arrows are on announced (`aria-activedescendant`: the menu has the focus); a
  lighter shadow in the light theme.
- `peek-card.tsx` (a run's, a position's or a rule's preview, `components/PeekLink.tsx`,
  `components/PeekRouterLink.tsx`): reworked around EasyUI's card. EasyUI's wrapped the trigger in a second tab
  stop and opened on any click, which also followed the link inside: here the trigger keeps its own focus and
  click; the card opens on hover (with a moment to move into it), on keyboard focus (not on a click's) and on a
  first tap with a touch screen, whose link is then followed by a second tap or by the card's own (`href`); it is
  drawn in `document.body` at fixed coordinates, centred on the trigger and kept in the window (a table's
  overflow cut it), closed by Escape, a tap elsewhere or a scroll; what it shows is the caller's, read once it
  opens (EasyUI's payment card, its customer and e-mail copy, are gone); the trigger described by it
  (`aria-describedby`); light values for every colour.
- `ai-agent-activity.tsx` (Claude's calls, `features/system/ClaudePanel.tsx`): an item that opens is a button
  (`aria-expanded`; EasyUI's was a div taking clicks alone); its `statusLabel` in words and its `timestamp`
  (EasyUI's item had one and never showed it) beside its duration; the header's `summary`, and `inputLabel` /
  `outputLabel` (EasyUI's "Parameters" and "Output Result"); arguments as JSON with their lines kept.
- `dependency-trace.tsx` (the services map, `features/system/serviceMap.ts`): a node's health as a ring, an icon
  and a word, never colour alone (`status`, `statusLabel`, `detail`); its whole name under it (EasyUI cut names to
  seven letters inside the circle); an arrow from what calls to what is called, and each link's name while traced;
  nodes the keyboard reaches and traces, Enter or Space keeping a trace as a click does (`aria-pressed`); the
  status bar says what the traced node calls and what calls it; `width`, `height` and `label`; light values for
  every colour.
- `airport-matrix-clock.tsx` (New York and the session's city on the dashboard): the caller's cities, each with its
  IANA zone (EasyUI's list of 32, five countries and no Paris, its country and city selectors and its add and
  remove buttons are gone); `title` (null for none), `footer` and a `sm` size for the side of a page's header;
  light values for the footer.
- `animated-file-upload.tsx` (the Indicators page's drop zone): `arrange`, the order the files are handled in
  (libraries first), read before they are listed; `sequential`, one file after the other (EasyUI started them all
  at once); a handler may answer with a link to what the file became, shown once done; `workingLabel` and
  `doneLabel`; an error wrapped whole and announced; the drop zone named by its words, its colours by classes (a
  light value in front of EasyUI's, which framer animated as dark ones only); light values for every colour.
- `dynamic-island.tsx` (the top bar's live pill, `features/dashboard/LivePill.tsx`): reduced to its collapsed and
  expanded states with the caller's content in both (`compact`, `children`, `label`); its profile and share states,
  avatar, greeting and social links are gone. The pill is a button that keeps its place in the top bar, and the
  island grows from it (shared `layoutId`) over the page from the pill's left edge, across the screen under the top
  bar on a phone, so that the bar keeps its height; the keyboard follows it in and back out; a dialog over the page
  (the confirmation of a Close opened from it) takes its own clicks and its Escape, which starts in it.
- For the light theme, which several components did not have: `form.tsx`, `smooth-accordion.tsx` and
  `loader.tsx` hardcoded dark colours (`bg-[#141414]`, `text-[#FAFAFA]`…); each literal now has a light value
  in front and stays as it was behind `dark:`. `velocity-toast.tsx` (icons), `spring-select.tsx` (error
  text), `button.tsx` (danger variant), `drag-to-confirm.tsx` (slider label), `sidebar.tsx`, `calendar.tsx`
  and `date-picker.tsx` (section titles, placeholders, icons) had light values under 4.5:1; they were given
  a darker step in light. Nothing dark was changed by any of this.

Two rules of `src/styles/` apply to every component, copied or not: the keyboard focus ring (`index.css`,
outside any layer so that it wins over the components' `outline-none`) and the raised `--text-muted` /
`--text-subtle` of the dark palette (`theme.css`; `tokens.css` stays the unchanged copy).

The copied components are excluded from the linter (`.oxlintrc.json`) to keep them close to their source.
Read a component before adding it: some carry demo behaviour (`activity-feed` has a simulation mode that
adds made-up events) or site-specific imports, and none may load anything from another origin.

Chart colours are in `theme.css`, one set per theme. Each profit and loss pair was checked for colour-blind
separation and lightness against the card surface of its theme; check a new pair the same way before
changing it. A heatmap's gains are teal, not the bars' emerald: the paler steps of emerald and red look alike to
deuteranopes; `theme.css` says what its eight steps were checked for. A funnel's stages are one ramp of the
series' blue (`--chart-funnel-1..5`), a sequence and not categories: its lightness rises step by step, and only
its last step may recede against the card, each stage's figure being written beside it.

## Lightweight Charts

The candles of the stored prices are drawn by [Lightweight Charts](https://github.com/tradingview/lightweight-charts)
5.2.1 (`lightweight-charts`, Apache License 2.0). Recharts stays for the other charts, the spread of an asset
through the day included. It is loaded with the pages that draw candles only.

- **Its licence asks for a notice and a link on the page that shows the charts.** The notice is
  "TradingView Lightweight Charts™ Copyright (с) 2025 TradingView, Inc. https://www.tradingview.com/".
  `ChartAttribution.tsx` writes it under the chart, on both pages that show one, and the chart keeps the
  library's own logo (`layout.attributionLogo`). Keep both if a page is reworked, and on any new one.
- **`features/prices/PriceChart.tsx` is the only file that imports it.** It is given data that is ready to
  draw and draws it. Everything that can be wrong without a canvas (times, holes, markers, intervals) is in
  `chartData.ts`, which is plain functions with tests.
- **Five places show it**: an asset's page and an opened row of the Assets View (`features/data/AssetChart.tsx`,
  full and compact), a rule's day beside TradingView (`features/ta/signals/ComparisonPanel.tsx`), a day of a
  backtest with a trade's turbo (`features/backtests/DayPage.tsx`), and the day of the editor's test
  (`features/ta/editor/TestResult.tsx`). The last two give it `overlays`: lines over the prices, on their scale.
  On a backtest's day, the price paid, the stop and the day's target, the trailing stop as it moved, told by
  more than their colour: the price paid is thick, a limit thin and dashed, the trailing stop climbs in steps.
  On the test, the script's plots near the prices, four at most (`series-1` to `series-4`, the
  `--chart-compare-*` colours), each named in a legend under the chart. `features/prices/ChartAttribution.tsx` is the licence notice: it goes on every page where
  `PriceChart` goes.
- **The library only knows UTC.** To show the trading timezone, `chartData.ts` shifts every time by that
  zone's offset before the chart sees it; what comes back from the chart (the time under the pointer) is in
  the same shifted time.
- **The canvas takes no CSS variable.** `PriceChart.tsx` reads the tokens of `theme.css` once, when the
  chart is made.
- **One known fault of 5.2.1 is worked around** in `PriceChart.tsx` (the note is beside the code): a chart
  that holds one series and is given the same time points again is left with two internal indexes that no
  longer agree, which shows as a misplaced or empty panel once a second series joins. The price series is
  emptied before the index line is added. `PriceChart.test.tsx` holds the order of the calls; when
  upgrading the library, check in a browser that an index followed by one of its turbos, then another bar
  length, still draws both panels over the same times.
- **Two kinds of bars, one chart.** Bars of a fixed length and range bars are each turned into a
  `ChartModel` (`chartModel.ts`); `PriceChart.tsx` is given candles, a line and markers and does not know
  which kind they are. Range bars have irregular times, and several can begin in the same ten seconds: the
  server gives each its own second, since the library refuses two points at the same time.
- **Tests replace the library** with `src/test/fakeCharts.ts` (jsdom has no canvas), which records what was
  asked of it. What the chart looks like can only be checked in a browser, against `npm run dev:api`.

## Technical Analysis

The pages of `features/ta/`: Indicators (the Pine scripts, `IndicatorsPage.tsx`), the editor with its test
(`ScriptEditorPage.tsx`, `editor/`), Charts (`ChartsPage.tsx`, `chart/`), Signal rules (`signals/`) and the Engine
monitor (`monitor/`: what the TA engine measured of itself, the checks of the last minutes, a period of runs, delivery and
results, its charts in `components/charts/PerformanceCharts.tsx`). They
are loaded with these pages only (lazy routes), and the TradingView reference of the editor
(`pinescript-v6-validator`) with the editor only.

| Library | Version | Licence |
|---|---|---|
| `pinets` | 0.12.0 | AGPL-3.0-only |
| `@luxalgo/vela-pinets` | 0.2.17 | AGPL-3.0-only |
| `@luxalgo/vela` | 0.8.5 | Apache-2.0, with a NOTICE to keep |
| `@codemirror/*`, `pinescript-v6-validator` | | MIT |

The three LuxAlgo packages are pinned exactly: Vela's interface is young and changes. `public/third-party-notices.txt`
carries their notices and ships with the admin, and the System page links to it and to the public repository,
which is the source the AGPL asks to offer.

- **`engine/` is shared with the server.** `prepare.ts` (every script goes through it: native Pine v5 or v6
  only, libraries inlined), `tickers.ts`, `timeframes.ts`, `provider.ts` and `runnerCore.ts` are plain
  TypeScript. The pages run them in the Pine worker; `npm run build:runner` (`vite.runner.config.ts`) builds
  `runner.ts` into `../src/ta_engine/runner/runner.mjs`, PineTS inside, for the TA engine. `package.sh`
  builds both.
- **PineTS compiles scripts with `new Function`**, which the pages' Content-Security-Policy forbids. Only the
  two worker files may: `pine-worker-<hash>.js` (ours, named by `worker.rolldownOptions` in `vite.config.ts`)
  and `vela-pine-worker-<hash>.js` (Vela-pinets' worker, which it inlines as a string, taken out into a file
  by `vite/velaPineWorker.ts`). The admin serves both with a policy of their own that allows `eval` and no
  connection at all (`src/admin/app.py`): a series a script reads through `request.security` is asked of the
  page, which reads it from the admin's API. A worker started any other way would get the pages' policy and
  fail to compile.
- **Vela is confined to `chart/`**: `wataProvider.ts` serves WATA's three providers as Vela's data
  providers, and `TaChart.tsx` builds the workspace. Tests replace it with `src/test/fakeVela.ts`.
- **The chart is live** (`live: true`): without it Vela never calls a provider's `subscribe`, and the chart
  stands still. Its scripts are not run in PineTS's live mode: `pineEngine.ts` gives Vela an engine that cannot
  stream, so each change of the bars runs a script in full again, a second apart at most while quotes come. A
  run raises the alerts of the last bar again: each is passed on once.
- **Bars of seconds** (`engine/timeframes.ts`): a chart, the editor's test and a backtest take any length
  from 10 seconds to a day, named as Pine names it (`10S`, `45S`, `1`, `60`); a signal rule keeps the minutes
  (`RULE_TIMEFRAMES`). Vela's menu offers the usual lengths, the Length field above the chart takes any other.
  Vela's chart itself reads no seconds (`10S` would be an hour, its times stop at the minute):
  `vite/velaSeconds.ts` patches it when it is built, in the dev server too, and stops the build if a new
  version of Vela no longer has the code it changes.
- **PineTS keeps a formatter per time zone** (`vite/pinetsZoneFormat.ts`): PineTS makes an
  `Intl.DateTimeFormat` at each `hour(time, "Europe/Paris")` or `timestamp("Europe/Paris", ...)` (0.11.1 did,
  0.12.0 still does), most of the time of a script that reads the time in a zone. The plugin changes the two
  pieces of PineTS that make them, in the three copies the admin ships: the TA engine's runner
  (`vite.runner.config.ts`, whose hello names it: `0.12.0+zone-format`), the pages' Pine worker
  (`worker.plugins`, and `optimizeDeps` in the dev server) and Vela-pinets' worker (`vite/velaPineWorker.ts`).
  The build stops if a new version of PineTS no longer has them; `vite/pinetsZoneFormat.test.ts` runs PineTS
  patched beside PineTS as it is around the clock changes.
- **What can be wrong without a browser is in plain functions with tests**: `prepare.ts`, `outline.ts` (the
  alerts and plots of a script, for a rule), `data/wataBars.ts`, `editor/diagnostics.ts`, `editor/testChart.ts`
  (what the test draws: its day, the plots near the prices, an oscillator listed apart, the alerts on their
  bars), `signals/ruleModel.ts` (with `ruleDraftOfRun`: a backtest made a rule, its range in the feed's tick),
  `signals/readiness.ts` (whether a rule is ready to go live, said in the confirmation, never stopping it) and
  `monitor/monitorModel.ts` (the monitor's address, its tiles against their limits, the gaps of its charts, the bars
  of the delivery steps, the results beside the days on paper). The editor itself (CodeMirror) and the charts are replaced in page tests, and
  checked in a browser against `npm run dev:api`.
- **The fake server** keeps the scripts and the rules in the real store, on its temporary directory, seeded
  with examples, a live rule and one of Claude's in shadow on ten-second bars (`dev/fake_ta.py`), and makes up
  what a TA engine would have done with the rules: its state, a week of signals, TradingView's near them, and what
  it measured of itself for the Engine monitor (`FAKE_TA_TROUBLE=slow` makes a live run slow now, for its warning
  and health's).

