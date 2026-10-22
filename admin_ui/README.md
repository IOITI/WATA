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
  (2026-10-11), MIT License, Copyright (c) 2026 Suraj Maurya.
- Components in use: `advanced-data-table`, `alert`, `animated-tabs`, `badge`, `breadcrumb`, `button`,
  `calendar`, `date-picker`, `drag-to-confirm`, `form`, `loader`, `not-found`, `otp-input`, `settle-modal`,
  `sidebar`, `skeleton`, `smooth-accordion`, `spring-select`, `stepper`, `stretch-switch`, `velocity-toast`.
- `src/lib/motion-tokens.ts`, `src/lib/utils.ts` and `src/styles/tokens.css` come from it too.

Changes made to the copied files:

- `advanced-data-table.tsx`: new `selectable` prop (default `true`) to hide the row checkboxes of tables
  that have no bulk action; `defaultHidden` on a column (shown once picked in the column list); `emptyTitle` /
  `emptyDescription` on `AdvancedDataTable`, and "Clear filters" only when a search or a filter is active;
  cells padded 20 px instead of 24, so that the seven columns of the closed positions fit a 1440 px screen;
  `initialSearch` and `initialExpandedRowIds`, for a link that lands on one row found and opened (an order
  from its position, a position from its order: `?find=<id>`); `initialFilters`, for a link that opens the
  table filtered (the Assets View from a provider); `expandOnRowClick` (a click anywhere on a row opens it,
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
- `not-found.tsx`: import of `./button` in lower case, as the file is named (the original casing only
  resolves on case-insensitive file systems).
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
changing it.

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
(`ScriptEditorPage.tsx`, `editor/`), Charts (`ChartsPage.tsx`, `chart/`) and Signal rules (`signals/`). They
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
  bars), `signals/ruleModel.ts` (with `ruleDraftOfRun`: a backtest made a rule, its range in the feed's tick)
  and `signals/readiness.ts` (whether a rule is ready to go live, said in the confirmation, never stopping it). The editor itself (CodeMirror) and the charts are replaced in page tests, and
  checked in a browser against `npm run dev:api`.
- **The fake server** keeps the scripts and the rules in the real store, on its temporary directory, seeded
  with examples and a rule in shadow (`dev/fake_ta.py`), and makes up what a TA engine would have done with
  the rules: its state, a week of signals, and TradingView's near them.

