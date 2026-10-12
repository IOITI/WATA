# WATA Admin interface

The React interface of the `admin` service. It is built into `../src/admin/static/`, which the admin FastAPI
application (`../src/admin/app.py`) serves and which ships with `./src` in the deployment package. Nothing is
built on the server.

React 19, Vite, TypeScript, Tailwind CSS 4, [EasyUI](https://easyui.site) components, TanStack Query,
React Router, Recharts.

## Working on it

Node.js is required, in the version of `.nvmrc` (`nvm use`).

```bash
npm ci
npm run dev:api     # the real admin application over fake data, on 127.0.0.1:8082
npm run dev         # Vite dev server; /api is proxied to the server above
```

`npm run dev:api` needs the Python dependencies of the repository. It prints the password to sign in with,
keeps everything in memory and in a temporary directory, and never talks to Saxo, PostgreSQL or RabbitMQ.
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
| `src/styles/` | `tokens.css` (EasyUI, unchanged), `theme.css` (WATA additions), `index.css` (Tailwind mapping) |
| `dev/fake_api.py` | The fake-data server |

The interface is dark only: several EasyUI components hardcode dark colours.

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
  cells padded 20 px instead of 24, so that the seven columns of the closed positions fit a 1440 px screen.
- `settle-modal.tsx`: rendered in `document.body` (a dialog opened from a table cell took the cell's
  alignment and wrapping); focus moves into the dialog, stays in it and returns to what opened it.
- `drag-to-confirm.tsx`: the hidden fallback button is out of the Tab order (it was a stop nobody could see,
  on which Enter confirmed); ArrowRight no longer confirms, only Enter and Space.
- `velocity-toast.tsx`: `wrapperClassName`, to lift the toast above the bar at the bottom of the configuration page.
- `form.tsx`, `animated-tabs.tsx`, `smooth-accordion.tsx`: the literal grey `#6B6B6B` (3.6:1 on the dark
  surface) replaced by `text-muted-foreground`; hints at 12 px instead of 11.
- `not-found.tsx`: import of `./button` in lower case, as the file is named (the original casing only
  resolves on case-insensitive file systems).

Two rules of `src/styles/` apply to every component, copied or not: the keyboard focus ring (`index.css`,
outside any layer so that it wins over the components' `outline-none`) and the raised `--text-muted` /
`--text-subtle` of the dark palette (`theme.css`; `tokens.css` stays the unchanged copy).

The copied components are excluded from the linter (`.oxlintrc.json`) to keep them close to their source.
Read a component before adding it: some carry demo behaviour (`activity-feed` has a simulation mode that
adds made-up events) or site-specific imports, and none may load anything from another origin.

Chart colours are in `theme.css`. The profit and loss pair was checked for colour-blind separation and
lightness against the dark card surface; check a new pair the same way before changing it.
