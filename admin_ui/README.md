# WATA Admin interface

The React interface of the `admin` service. It is built into `../src/admin/static/`, which the admin FastAPI
application (`../src/admin/app.py`) serves and which ships with `./src` in the deployment package. Nothing is
built on the server.

React 19, Vite, TypeScript, Tailwind CSS 4, [EasyUI](https://easyui.site) components, TanStack Query,
React Router, Recharts, and TradingView's Lightweight Charts for the Prices page.

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
quoted all day, each in its own way (holes, a stop, a knock-out), and three have a volume.

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
| `dev/fake_api.py`, `dev/fake_prices.py` | The fake-data server and its made-up recorded prices. Its backtests are real ones, on the prices kept under `var/lib/price_history` of the checkout; the "real trades" they are set against are made up from those same prices |

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
- **The price chart draws on a canvas** with the colours it read when it was made: `PricesPage.tsx` gives it
  the theme as its `key`, so another theme makes another chart. The Recharts charts read CSS variables and
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
- Components in use: `advanced-data-table`, `alert`, `animated-tabs`, `badge`, `button`,
  `calendar`, `date-picker`, `drag-to-confirm`, `form`, `loader`, `not-found`, `settle-modal`, `sidebar`,
  `skeleton`, `smooth-accordion`, `spring-select`, `stepper`, `stretch-switch`, `velocity-toast`.
- `src/lib/motion-tokens.ts`, `src/lib/utils.ts` and `src/styles/tokens.css` come from it too.

Changes made to the copied files:

- `advanced-data-table.tsx`: new `selectable` prop (default `true`) to hide the row checkboxes of tables
  that have no bulk action; `defaultHidden` on a column (shown once picked in the column list); `emptyTitle` /
  `emptyDescription` on `AdvancedDataTable`, and "Clear filters" only when a search or a filter is active;
  cells padded 20 px instead of 24, so that the seven columns of the closed positions fit a 1440 px screen;
  `initialSearch` and `initialExpandedRowIds`, for a link that lands on one row found and opened (an order
  from its position, a position from its order: `?find=<id>`).
- `settle-modal.tsx`: rendered in `document.body` (a dialog opened from a table cell took the cell's
  alignment and wrapping); focus moves into the dialog, stays in it and returns to what opened it.
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

The candles of the Prices page are drawn by [Lightweight Charts](https://github.com/tradingview/lightweight-charts)
5.2.1 (`lightweight-charts`, Apache License 2.0). Recharts stays for the other pages. It is loaded with the
Prices page only.

- **Its licence asks for a notice and a link on the page that shows the charts.** The notice is
  "TradingView Lightweight Charts™ Copyright (с) 2025 TradingView, Inc. https://www.tradingview.com/".
  `ChartAttribution.tsx` writes it under the chart, on both pages that show one, and the chart keeps the
  library's own logo (`layout.attributionLogo`). Keep both if a page is reworked, and on any new one.
- **`features/prices/PriceChart.tsx` is the only file that imports it.** It is given data that is ready to
  draw and draws it. Everything that can be wrong without a canvas (times, holes, markers, intervals) is in
  `chartData.ts`, which is plain functions with tests.
- **Two pages show it**: the Prices page, and a day of a backtest (`features/backtests/BacktestDayPage.tsx`),
  which gives it `overlays`: lines over the prices, on their scale (the kernel, the VWAP of the session, its
  band). A line is told by more than its colour: the kernel takes the colour of its direction at each
  point, the VWAP is thick, its band thin and dashed. `features/prices/ChartAttribution.tsx` is the licence
  notice: it goes on every page where `PriceChart` goes.
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
