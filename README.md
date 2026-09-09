# 舆情雷达 / futu-radar

Local import of the Claude Design project
[fd51d8b0-14cd-4cd8-900b-45e95ac4d314](https://claude.ai/design/p/fd51d8b0-14cd-4cd8-900b-45e95ac4d314?file=official-activity.dc.html).

The repo holds the same screens twice, on purpose:

| Directory | What it is |
| --- | --- |
| `design/` | Byte-exact mirror of the design project. Read-only reference — never hand-edit. |
| `app/` | React + Vite port of the five screens, built from that mirror. |
| `docs/` | Spec and client-confirmation markdown that shipped with the design project. |

## Running

```sh
cd app
npm install
npm run dev      # the React port          → http://localhost:5173
npm run design   # the untouched .dc.html  → http://localhost:5174
```

`npm run design` serves `../design` as static files, so the original design source
renders side by side with the port for comparison. `npm run build` emits `app/dist`.

The checkout currently lives on the `P:` DFS share, which needs two Vite settings that
`app/vite.env.js` turns on automatically — see the comment there. Expect a slow first
start (~40 s) and slow HMR: every module is read over SMB and the watcher has to poll.
Working from a local disk is noticeably faster and needs no special configuration.

## Routes

| Route | Design file | Status |
| --- | --- | --- |
| `/official` | `official-activity.dc.html` | ported |
| `/kol` | `kol-activity.dc.html` | ported |
| `/kol/detail?kol=…` | `kol-detail.dc.html` | ported |
| `/product?code=…&range=…` | `product-monitor.dc.html` | ported |
| `/sector` | `sector-overview.dc.html` | ported |

`/` and any unknown path redirect to `/official`.

## How the port maps onto the design source

A `.dc.html` file is an `<x-dc>` template plus a `class Component extends DCLogic`
script. `DCLogic` is a React class component without `render()`, so each screen ports
mechanically:

- `state`, lifecycle, helper methods and `renderVals()` are copied **verbatim** from the
  design source. Keeping them diffable against `design/*.dc.html` is the point — when the
  design changes, re-copy the method rather than re-deriving it.
- `render()` is the only new code: `{{ hole }}` → JSX, `<sc-if>` → `{cond && (…)}`,
  `<sc-for>` → `.map()` with a `key` (dc did not need keys; JSX does).
- `data-props` defaults become `static defaultProps`.

Supporting pieces:

- `app/src/lib/dc.js` — `s(css)` parses a design `style="…"` declaration string into a
  React style object; `hover(css)` / `focus(css)` mint a class backed by a real
  `:hover` / `:focus` rule, which is how `style-hover` / `style-focus` survive the move.
- `app/src/components/Shell.jsx` — header rows 1 and 2, identical across all five
  screens; each screen passes its own filter bar as `children`.
- `app/src/lib/routes.js` — maps design hrefs (`kol-detail.dc.html?kol=…`) to router
  paths; `app/src/components/DcLink.jsx` uses it so in-page links stay verbatim.
- `app/src/data/radar.js` — imports `design/radar-data.js` for its side effect and
  re-exports `window.RADAR`. The data layer is the mirror's file, unmodified.

`product-monitor` (1793 source lines) and `sector-overview` (1295) are large enough that
their templates are split into section components under `app/src/screens/productMonitor/`
and `app/src/screens/sectorOverview/`; `renderVals()` stays whole in each `index.jsx`
because the sections share values the design computes in one pass.

### Deliberate deviations

- **No async runtime wait.** The design polls for `window.RADAR` because `radar-data.js`
  loads as a `<script>`; the port imports it synchronously, so the poll is gone.
- **URL rewrites.** `product-monitor`'s `go()` writes `/product?code=…&range=…` through
  `history.replaceState`, which `BrowserRouter` does not observe — the address updates
  without a remount, matching the design's behaviour.
- **Dead code dropped.** Values `renderVals()` computes but no template reads
  (`domains`, `subItems`, `heatBg`, `negCats`, …) were left out; the design's own comments
  mark the modules they belonged to as merged away.
- **Keys.** `id` / `key` fields were added where a list had no stable identifier of its
  own: `riskRows` and `evidence` rows in `product-monitor`, and the treemap tiles, theme
  rows and risk rows in `sector-overview`.

### Known warnings

`npm run build` prints `@import must precede all other statements` from the design
system's `colors_and_type.css`, which puts a Google Fonts `@import` after an `@font-face`.
That import is invalid in the design too — browsers drop it there as well — so the mirror
is left untouched and CJK text renders from the bundled `NotoSans-Regular.ttf`.

## Re-importing from Claude Design

Fetch through the design MCP's `render_preview` serve URL with **`&raw=1`** appended;
without that flag the endpoint injects preview scaffolding into `.dc.html` and the copy is
no longer byte-exact. Verify each file's size after fetching. Serve URLs carry a
short-lived project-scoped token — keep them out of commits and shared text; link the
`claude.ai/design/…` project URL instead.
