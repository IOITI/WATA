# WATA Admin interface

The React interface of the `admin` service. It is built into `../src/admin/static/`, which the admin FastAPI
application (`../src/admin/app.py`) serves and which ships with `./src` in the deployment package. Nothing is
built on the server.

React 19, Vite, TypeScript, Tailwind CSS 4, IOITI's design system [ioiti-ui](https://github.com/IOITI/ioiti-ui)
(the design and [EasyUI](https://easyui.site)'s components, see below), TanStack Query,
React Router, and TradingView's Lightweight Charts for the charts of the stored prices (the Data section); the other
charts are ioiti-ui's (`LineChart`, `BarChart`).
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
| `src/components/` | What the shell holds and its menu, the confirmation of live actions, WATA's badges, charts (the rest is ioiti-ui's, see below) |
| `src/features/<page>/` | One directory per page |
| `src/lib/` | API client and types (`api.ts`, `types.ts` mirror `src/admin/routers/`), labels, the server's wall times |
| `src/test/` | Test setup, and the stand-ins for the charting library and for the system's theme |
| `src/styles/` | `index.css`: Tailwind and ioiti-ui's design, in one import |
| `dev/fake_api.py`, `dev/fake_prices.py`, `dev/fake_recorded.py` | The fake-data server, its made-up recorded prices, and those prices written as the days its real backtests trade on |

## Themes

The interface is light or dark. It follows the system setting, and keeps following it when it changes; the
button in the top bar (and on the login page) goes from there to always light, to always dark, and back. A
choice is kept in the browser (`localStorage`, key `wata.theme`): it is a matter of screen and room, not of
operator.

- **`dark` on `<html>` is the switch.** Every dark style is a `dark:` class or a `.dark` token; `:root` holds
  the light values (ioiti-ui's `tokens.css` from EasyUI and `theme.css`).
- **ioiti-ui's pre-paint script sets it before the first paint**, so a page does not flash in the other theme
  while the application loads: `ioiti({ themeStorageKey: 'wata.theme' })` in `vite.config.ts` writes it as
  `ioiti-theme.js` and puts it at the top of `<head>`. It is a file, not a few lines in `index.html`, because the
  admin's Content-Security-Policy allows no inline script. The package's `ThemeProvider` (in `IoitiProvider`) and
  `ThemeToggle` hold the same rule for the running application, under the key the script was given.
- **A colour needs both values.** A class that names a colour is written `light dark:dark`
  (`text-neutral-600 dark:text-neutral-400`), or goes through a token that has both. A literal with one
  value shows in one theme only.
- **Text colours are checked, not judged by eye.** The status colours as text (`--positive`, `--negative`,
  `--warning`, `--info`) and the secondary greys reach 4.5:1 on every surface of their theme, a hovered row
  included; the light values of ioiti-ui's `theme.css` say which of EasyUI's own were lowered for that.
- **The price chart draws on a canvas** with the colours it read when it was made: every page that shows one
  gives it the theme as its `key` (an asset's chart, a backtest's day and its trade, the editor's test, the
  TradingView comparison), so another theme makes another chart. ioiti-ui's charts are SVG drawn with CSS
  variables and follow by themselves.
- **An accent and a material, each browser's own**, chosen with ioiti-ui's `LookPicker` in the panel "Accent and
  material" of the Configuration page (its overview): kept beside the theme (`wata.theme.accent`,
  `wata.theme.material`) and set by the same script before the first paint, neutral and flat (the interface as
  it always was) until chosen, never part of `config.json`. The interface's own classes follow them as the
  package's components do: a card drawn here on the page is glass under the glass material as a `Panel` is
  (its utility, `glass-card`), and what is chosen, current or a progress takes the accent (the `accent:`
  variant and the `--brand` tokens). A field drawn here shows its focus by its own border, or by its frame's
  (the Pine editor's): since ioiti-ui 0.5.3 a field that takes text has no focus ring. The price chart reads
  the accent's line colour (`--chart-series`) as it reads the others, once; no chart is on the page where the
  accent is chosen.
- Tests stand in for the system setting with `src/test/systemTheme.ts` (jsdom has no `matchMedia`). A
  component rendered without a `ThemeProvider` is shown dark.

The admin answers with a strict Content-Security-Policy (own origin only), so nothing may be loaded from
another origin or inlined as a `data:` URI. Fonts come from `@fontsource-variable/*` and
`build.assetsInlineLimit` is 0 for that reason.

## ioiti-ui

The design of this interface and its generic components are a package of their own,
[ioiti-ui](https://github.com/IOITI/ioiti-ui) (IOITI's design system, MIT), made from this interface and
installed from a git tag (`"ioiti-ui": "github:IOITI/ioiti-ui#v<version>"` in `package.json`; never from npm).
It holds:

- the design: EasyUI's tokens, what this interface adds to them (`theme.css`: the status colours, the chart
  colours, the lowered greys), the mapping to Tailwind's utilities, the fonts, the global focus ring. `src/styles/index.css`
  imports `ioiti-ui/styles.css`, Tailwind and the design in one;
- EasyUI's components (https://github.com/Surajmaurya1/easyui, MIT License, Copyright (c) 2026 Suraj Maurya),
  with every change this interface made to them, listed in its `docs/easyui-changes.md`;
- the blocks the pages are made of (`Panel`, `PageHeader`, `KpiTile`, `Meter`, `Hint`, `ButtonLink`, `PeekLink`,
  `CheckList`, `ExportButton`, `DateRangeFilter`, `QueryState`, `SignedValue`, `StatusBadge`, `ConfirmAction`),
  the formats (`formatMoney`, `formatDateTime`…), CSV, the date ranges;
- the shell (`RouterAppShell`: the sidebar, the top bar and the trail of where a page is, the page's column), the
  404 (`NotFoundState`, `NotFoundPage`), the theme (`ThemeProvider`, `ThemeToggle`, `LookPicker`, the pre-paint
  script and the Vite plugin that serves it, `ioiti()`), the toasts.

Everything is imported from `'ioiti-ui'`, the router's part from `'ioiti-ui/react-router'` (`RouterRoot`,
`RouterAppShell`, `RouterLink`). What is WATA's stays here: the menu (`components/navigation.ts`), what the shell
holds (`components/AppShell.tsx`), `ConfirmLiveAction` (ioiti-ui's `ConfirmAction` saying first that the account
is live), `NotFoundState` (the way back to the dashboard), `ActionBadge`, `ClaudeBadge`, `BrandMark`, what the
package's charts are given from WATA's types (`components/charts/`), `formatWallTime` (`lib/wallTime.ts`) and the
pages.

- **A change to a component is made in ioiti-ui**, never in a copy here: there, with its test and a line of its
  CHANGELOG, then released as a tag (`npm run release -- <version>` in ioiti-ui, pushed by the user), and the tag
  installed here (`npm install github:IOITI/ioiti-ui#v<version>`). Read ioiti-ui's CHANGELOG between the two
  versions. A new EasyUI component is added there too (its `docs/easyui-changes.md` says how).
- **`App.tsx` puts `IoitiProvider` around the router** (the theme, the animations as the system asks, the name
  ending every tab's title) **and `RouterRoot` at its root route** (every component's links are the router's;
  the toasts, which rise above a page's `SaveBar` while it is there). A test that renders a page alone and follows a link a
  component of the package draws mounts it the same way: under `RouterRoot`, as `src/test/WithTrail.tsx` does,
  or the link is a plain `<a>` that jsdom does not follow.
- **The accents and the glass material** of ioiti-ui (0.3's bloom included) are not offered here: no
  `LookPicker`, no accent or material given to `ioiti()`, so the interface is neutral and flat, as it was.
- **ioiti-ui 0.3.0's motion and components came with its update**: a change of page is a cross-fade of the page
  alone (React 19.3's `ViewTransition`), a change of theme one fade of the whole page, toasts leave before the
  next comes, what arrives where a skeleton was fades in, dialogs grow from what opened them; the tables' filters
  are a filter group (a column's options a list opened from its button, a column of two an on/off pair); the
  check boxes draw their mark; the first Tab shows "Skip to the page".
- **Since 0.4 the admin writes none of what the package has.** The Configuration page's bar of unsaved changes is
  its `SaveBar` (there only while something is unsaved, Save held back and why while a field holds what cannot be
  used), leaving the page asks through its `RouterLeaveGuard` (its own views, `within="/config"`, are no leaving;
  the sign-in a lapsed session goes to is let through, `isSignIn` of `features/auth/returnPath.ts`, as in the
  script editor); every list of changes (a save's review, a backup, the price recorder's and Capital.com's
  settings, a run's settings) is its `ChangeList` (`features/config/keyChanges.tsx` for config.json's keys); an
  open position's live value is its `SignedValue`, `LiveValue` and `RelativeTime`
  (`features/positions/PositionLiveValue.tsx`: muted and said stale after a minute, its age never in the future
  when the server's clock is ahead), every clock its `useNow`; the charts Recharts drew are its `LineChart` and
  `BarChart` (`components/charts/PerformanceCharts.tsx`, the same names and props for the pages). What they
  lacked was added to the package for this (0.4.0), and one difference found in a browser fixed there (0.4.1: a
  line chart wrote "7 Oct" where the rest writes "07 Oct").
- **The top bar's trail gives way where it does not fit** (ioiti-ui 0.4.2): whole on a wide screen as long as it
  fits the room the chips and the controls leave it (the page's name cut to 8rem at most), its last two steps
  otherwise. Before, its steps kept their width and ran under the live pill, or under a chip, at 1,024 px. The
  tests read both trails, the whole one first (jsdom lays nothing out, so the whole one always fits there).
- **When the admin moved onto the package**, the built CSS holds every rule of the build before, in the same
  order (the package adds its accents' and glass's rules, inert without their attribute on `<html>`), and in a
  browser the computed styles of every element of 26 pages, in both themes, were the same. One regression was
  found and fixed in ioiti-ui (0.2.1): its `RouterLink` sent a check's link to a part of the page (`#trust`) through
  the router, which scrolled nothing and dropped the address's query.

Chart colours are in ioiti-ui's `theme.css`, one set per theme. Each profit and loss pair was checked for colour-blind
separation and lightness against the card surface of its theme; check a new pair the same way before
changing it. A heatmap's gains are teal, not the bars' emerald: the paler steps of emerald and red look alike to
deuteranopes; `theme.css` says what its eight steps were checked for. A funnel's stages are one ramp of the
series' blue (`--chart-funnel-1..5`), a sequence and not categories: its lightness rises step by step, and only
its last step may recede against the card, each stage's figure being written beside it.

## Lightweight Charts

The candles of the stored prices are drawn by [Lightweight Charts](https://github.com/tradingview/lightweight-charts)
5.2.1 (`lightweight-charts`, Apache License 2.0). The other charts, the spread of an asset through the day
included, are ioiti-ui's `LineChart` and `BarChart` (`components/charts/PerformanceCharts.tsx`). It is loaded with
the pages that draw candles only.

- **Its licence asks for a notice and a link on the page that shows the charts.** The notice is
  "TradingView Lightweight Charts™ Copyright (с) 2025 TradingView, Inc. https://www.tradingview.com/".
  `ChartAttribution.tsx` writes it under the chart, on both pages that show one, and the chart keeps the
  library's own logo (`layout.attributionLogo`). Keep both if a page is reworked, and on any new one.
- **`features/prices/PriceChart.tsx` is the only file that imports it.** It is given data that is ready to
  draw and draws it. Everything that can be wrong without a canvas (times, holes, markers, intervals) is in
  `chartData.ts`, which is plain functions with tests.
- **Five places show it**: an asset's page and an opened row of the Assets View (`features/data/AssetChart.tsx`,
  full and compact), a rule's day beside TradingView (`features/ta/signals/ComparisonPanel.tsx`), a day of a
  backtest with a trade's turbo (`features/backtests/DayPage.tsx`; the day itself on Vela when a script of WATA
  made its signals), and the day of the editor's test
  (`features/ta/editor/TestResult.tsx`). The last two give it `overlays`: lines over the prices, on their scale.
  On a backtest's day, the price paid, the stop and the day's target, the trailing stop as it moved, told by
  more than their colour: the price paid is thick, a limit thin and dashed, the trailing stop climbs in steps.
  On the test, the script's plots near the prices, four at most (`series-1` to `series-4`, the
  `--chart-compare-*` colours), each named in a legend under the chart. `features/prices/ChartAttribution.tsx` is the licence notice: it goes on every page where
  `PriceChart` goes.
- **The library only knows UTC.** To show the trading timezone, `chartData.ts` shifts every time by that
  zone's offset before the chart sees it; what comes back from the chart (the time under the pointer) is in
  the same shifted time.
- **The canvas takes no CSS variable.** `PriceChart.tsx` reads the tokens of ioiti-ui's `theme.css` once, when
  the chart is made.
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
  providers, `TaChart.tsx` builds the workspace, and `RunDayChart.tsx` a day of a backtest. Tests replace it
  with `src/test/fakeVela.ts`. Vela's `destroy()` leaves a Pine engine's worker running: both end theirs.
- **A day of a backtest is drawn on Vela** when a script of WATA made its signals (a Pine run, a rule's on
  paper; `DayPage.tsx` loads it lazily): Vela's plain chart, not live, on the bars the server says the script
  ran on (`/api/backtests/<id>/days/<day>/indicator`: from the engine's restart and its days of history),
  the script drawn as on Charts with the run's inputs, and the run's signals and trades over it as a second
  indicator (`runMarksEngine.ts`), each hidden from Vela's legend. Its pure parts, with tests, are
  `runDayModel.ts`: the bars, where a moment falls among them, the inputs (by variable name, else by `in_N` on
  the version they were set for: Vela loses a value given under a title), the run's marks as Vela's labels and
  trade markers, the chart's data provider. A replay of TradingView's alerts keeps `PriceChart`.
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
  `signals/readiness.ts` (whether a rule is ready to go live, said in the confirmation; `holdingBack` is what the
  server holds, and a rule short of it goes live only once the operator ticks "Go live anyway") and
  `monitor/monitorModel.ts` (the monitor's address, its tiles against their limits, the gaps of its charts, the bars
  of the delivery steps, the results beside the days on paper). The editor itself (CodeMirror) and the charts are replaced in page tests, and
  checked in a browser against `npm run dev:api`.
- **The fake server** keeps the scripts and the rules in the real store, on its temporary directory, seeded
  with examples, a live rule and one of Claude's in shadow on ten-second bars (`dev/fake_ta.py`), and makes up
  what a TA engine would have done with the rules: its state, a week of signals, TradingView's near them, and what
  it measured of itself for the Engine monitor (`FAKE_TA_TROUBLE=slow` makes a live run slow now, for its warning
  and health's).

