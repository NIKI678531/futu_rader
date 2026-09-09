# CSOP Design System

A design system for **CSOP Asset Management Limited** (南方東英資產管理), the leading Hong Kong–headquartered ETF issuer and Greater China investment manager. This system codifies the brand's typography, colour, layout, voice and UI patterns into reusable design tokens, components and reference screens.

> CSOP is a regulated, institutional asset manager. The visual system trends **formal, restrained, and trust-first**. Avoid playful illustration, novelty type, or vivid accent colours — the brand's authority comes from disciplined repetition of a single sapphire-blue mark across dense, fact-heavy layouts.

---

## Company context

CSOP Asset Management Limited ("CSOP", Chinese: 南方東英) was founded in **January 2008** as the first offshore subsidiary of China Southern Asset Management — one of the largest mainland Chinese fund houses. It is headquartered in Two Exchange Square, Central, Hong Kong, with additional operations in Singapore and mainland China.

CSOP's core business is **ETFs** (Exchange-Traded Funds): it is the **second-largest ETF issuer in Hong Kong** by AUM and the **leading ETF issuer in Singapore** since 2018. The product shelf spans:

- **ETFs** — A-share, H-share, US equity, thematic (Hang Seng TECH, Nasdaq-100, cloud, smart driving, healthcare), fixed-income and money-market ETFs.
- **Leveraged & Inverse (L&I) products** — daily 2x, ‑1x, ‑2x exposures to mainstream indices.
- **Active mutual funds** — Greater China equity, China onshore bond, USD-denominated offshore bond.
- **Crypto futures ETFs** — the first Bitcoin and Ethereum futures ETFs ever listed in Hong Kong (3066.HK).
- **Cross-border firsts** — the world's largest Saudi Arabia ETF, and Singapore-Shenzhen / Singapore-Shanghai linked ETF pairs.

The customer base is institutional and sophisticated retail: **sovereign wealth funds, pensions, insurance companies, family offices, endowments, and Hong Kong / Singapore brokerage retail**.

### Audience

Two distinct audiences read CSOP surfaces:

1. **Institutional / professional investors** — read prospectus pages, KIIDs, fund factsheets, performance attribution. Expect data density and regulatory rigor.
2. **Retail HK / SG investors** — arrive via stock-ticker landing pages, CIES (Capital Investment Entrant Scheme) explainers, ETF education content. Expect a friendly hand-hold but still authoritative.

### Languages

CSOP operates trilingually:

- **English** — primary for institutional, global, and Singapore audiences.
- **繁體中文 (Traditional Chinese)** — Hong Kong retail.
- **简体中文 (Simplified Chinese)** — mainland and cross-border audiences.

The wordmark is always shown in its **bilingual lockup** ("CSOP ASSET MANAGEMENT  |  南方東英") on official channels.

---

## Sources

| Source | Type | Notes |
|---|---|---|
| `uploads/CSOP_Logo_Bilingual.png` | Asset, user-supplied | Primary bilingual lockup. PNG with transparency, 1772×472. Copied to `assets/logo-bilingual.png`. |
| Theme colour `#2361AD` | Token, user-supplied | Adopted as `--csop-blue-600` primary. |
| https://www.csopasset.com/en/home | Live site | English homepage. Public, but not directly fetchable from this environment — referenced via search summaries only. |
| https://www.csopasset.com/sg/about_overview.html | Live site | Singapore "About" page. |
| https://hk.linkedin.com/company/csop-asset-management | Live site | LinkedIn voice samples. |
| https://en.wikipedia.org/wiki/China_Southern_Asset_Management | Reference | Corporate history & milestones. |

> ⚠️ **Caveat:** No codebase, Figma, or production CSS was provided. Visual decisions below were calibrated from the supplied logo (sapphire blue, metallic silver swirl), the user-specified theme colour `#2361AD`, and tone samples extracted from public-web copy. Where uncertain, the system errs toward conservative institutional-finance conventions. **Please review and replace any approximations with production tokens / fonts when available.**

---

## Index

Top-level files at the project root:

| Path | Purpose |
|---|---|
| `README.md` | This file. Brand context, content + visual fundamentals, iconography. |
| `SKILL.md` | Cross-compatible Agent Skill front-matter — invoke this skill in Claude Code to design for CSOP. |
| `colors_and_type.css` | All design tokens (colours, type ramp, spacing, radii, shadows). Import this into any new file. |
| `assets/` | Logos, marks, and reusable brand imagery. |
| `fonts/` | Web-font fallbacks (Google Fonts substitutes — see Visual Foundations). |
| `preview/` | Per-token preview HTML cards used by the Design System tab. |
| `ui_kits/website/` | High-fidelity recreation of csopasset.com — homepage, fund detail, fund list. |

Components compiled into the design-system bundle (read via `window.CSOPDesignSystem_019e15`):

- `Header` — sticky bilingual global nav with language switcher and region menu.
- `Hero` — homepage hero wash module with featured-ETF callout and stat strip.
- `FundTable` — searchable, category-filtered ETF list with tabular numerics.
- `FundDetail` — single-fund product page: NAV card, performance chart, key facts, risk warning.
- `Footer` — sitemap, regulatory line, office address.
- `DisclaimerModal` — first-visit jurisdiction gate.

UI kits available:

- **`ui_kits/website/`** — public marketing + product website. Surfaces: global header, hero, fund-table, fund detail page, footer, disclaimer modal.

---

## CONTENT FUNDAMENTALS

CSOP's voice is **regulated-institutional Hong Kong English** — closer in register to HSBC, Hang Seng, or Allianz than to a fintech app. It is *not* casual, *not* playful, and *not* aspirational-lifestyle.

### Tone

- **Authoritative and restrained.** State facts; let scale and pedigree speak. Do not hype.
- **Bilingual-mind.** Sentences should be writable in parallel English / 繁體中文 — keep clauses short and avoid English idioms (e.g. "moving the needle", "low-hanging fruit") that translate badly.
- **Time-stamped.** Almost every quantitative claim ends with a source + "as of [date]". E.g. *"the total AUM of CSOP is over US$ 12.6 billion … (Source: CSOP as at 31 December 2022.)"*
- **Risk-aware.** Every product mention is followed, eventually, by *"investments may suffer losses"* or equivalent — never elide.

### Person

- Predominantly **third-person institutional** ("CSOP", "the Manager", "the Sub-Fund").
- **"We"** appears in About / leadership / culture pages and LinkedIn updates — but never on regulated product pages.
- **"You"** appears in retail-facing onboarding ("You have a right to be informed…", "If you wish to access…") and PICS / data-rights notices.

### Casing & punctuation

- **Title Case** for page H1s and section headers ("Investment Philosophy", "Our People", "Fund Information").
- **Sentence case** for body, captions, table headers.
- **ALL CAPS** reserved for: the wordmark "CSOP", ticker codes ("3033.HK", "9410"), and small-cap labels ("AUM", "ETF", "L&I", "NAV", "PIF").
- Currency: always with code — `USD 12.6 billion`, `HK$30 million`, `RMB`. Use `US$` only inside quotations.
- Ticker convention: `Fund Name (CODE.EX)` — e.g. *CSOP Hang Seng TECH Index ETF (3033.HK)*.

### What we do *not* do

- **No emoji** in product surfaces. LinkedIn occasionally uses 🌿 / 🎉 / 🌍 in social posts, but the website and app never do.
- **No exclamation marks** in product copy. (Allowed in social and event recaps.)
- **No first-name CTAs** ("Hey there", "Let's go"). Use *"Learn more"*, *"View details"*, *"Download factsheet"*.
- **No invented statistics.** Every number must be sourced — show the source line, even on hero modules.

### Voice samples

> "CSOP ETFs series comprehensively cover HK stocks, A-shares, thematic, fixed income and money market, providing investors with simple and transparent investment tools to fulfill their investment needs in an all-rounded manner."

> "The investment objective of CSOP Hang Seng Index ETF (the "Sub-Fund") is to provide investment results that, before deduction of fees and expenses, closely correspond to the performance of the Hang Seng Index (the "Underlying Index"). There is no assurance that the Sub-Fund will achieve its investment objective."

> "Firmly holding the philanthropic belief of 'In Hong Kong, For Hong Kong' in mind since its establishment, CSOP has always been committed to paying attention to the disadvantaged groups…"

> "Founded in 2008, CSOP was the first offshore entity set up by a regulated Chinese asset manager."

Notice: long compound sentences, parenthetical defined-terms in quotation marks, regulatory hedges, and date-pinned claims.

---

## VISUAL FOUNDATIONS

### Colour

The system pivots on **one colour: CSOP sapphire blue `#2361AD`**. Everything else is supporting structure.

- **Primary** — `--csop-blue-600 #2361AD` (the supplied theme colour, drawn from the wordmark).
- **Deepened** — `--csop-blue-800 #163E73` and `--csop-navy-900 #0E2A52` for headers, the global nav band, footer.
- **Tints** — pale-blue surfaces (`--csop-blue-50 #EAF1FB`, `--csop-blue-100 #D2E0F4`) for hero washes, hover backgrounds, table-row striping.
- **Silver / steel** — drawn from the logo's metallic swirl: `--csop-silver-400 #B7BFC9`, `--csop-silver-200 #D9DEE5`. Used for rules, borders, divider bands, and "metallic-swirl" hero accents.
- **Neutral ink** — `--ink-900 #1A1F2B` for body, `--ink-700 #4A5568`, `--ink-500 #6E7A8A`, `--ink-300 #B6BDC8`.
- **Surface** — `--paper #FFFFFF`, `--canvas #F6F8FB` (institutional off-white), `--canvas-alt #EEF2F7`.
- **Semantic** — green `#1F8A5B` for gains / approved, red `#C53030` for losses / risk warnings, amber `#C9A961` for awards / featured callouts. Note: red/green are **muted** — never the fluorescent finance-app red/green; this is institutional, not retail-trading.

No gradients on flat UI surfaces. Gradients appear only on **hero washes** and only as `--csop-navy-900 → --csop-blue-600` diagonals at 6–12° — never bluish-purple, never pastel.

### Typography

CSOP's working type system has three layers; pick by surface:

| Layer | Where it's used | Font |
|---|---|---|
| **Logo wordmark** | The bilingual lockup only — never in product copy. | **MHeiHK** *(no license purchased — do NOT typeset other content in this face).* |
| **External marketing / web** | Public website, PDFs, prospectus, slides for external audiences. | **Noto Sans** + **Noto Sans HK** *(freely licensed — the safest default; this is what `--font-sans` and `--font-cjk` resolve to first).* |
| **Internal docs / decks today** | Most internal PowerPoint, Word, Excel artefacts in use right now. | **Microsoft YaHei** + **Microsoft JhengHei** (CJK) + **Calibri** (Latin). |

- **Primary sans:** Noto Sans, falling through to Calibri / Microsoft YaHei / Microsoft JhengHei. Used for body, UI labels, table data, footnotes.
- **Display:** Noto Serif for editorial headlines and big-quote slides; or Noto Sans Semibold for utilitarian H1s — both are acceptable.
- **CJK pairing:** Noto Sans HK for Traditional Chinese on web; Microsoft JhengHei (繁) / YaHei (简) inside Office documents.
- **Mono:** JetBrains Mono → Consolas fallback, for ticker codes, ISINs, and aligned numeric tables.

Type tone:

- **H1 / Display** — Source Serif 4 Semibold, tight tracking (-0.01em), generous line-height (1.1).
- **H2 / H3** — Source Sans 3 Semibold, slight negative tracking on H2 only.
- **Body** — Source Sans 3 Regular 16/26.
- **Captions / disclaimers** — Source Sans 3 Regular 12/18, `--ink-500`.
- **Numerals** — use `font-variant-numeric: tabular-nums` everywhere financial data appears.

> ℹ️ **Font policy (per CSOP brand owner):**
> • The bilingual logo wordmark is set in **MHeiHK**, but the licence has not been purchased — **never typeset body or product copy in MHeiHK**.
> • For all **external-facing material**, prefer **Noto Sans / Noto Sans HK** — freely licensed, safe everywhere.
> • The **in-use stack today** across internal PowerPoint / Word / Excel is **Microsoft YaHei + Microsoft JhengHei + Calibri**. These are listed as fallbacks in `--font-sans` and `--font-cjk` so artefacts authored in Office still render correctly when opened on a corporate machine.

### Spacing & layout

- **8-point grid.** Tokens: `--space-1: 4px` through `--space-12: 96px`. The vast majority of UI uses 8 / 16 / 24 / 32 / 48.
- **Container widths:** content max-width is **1200 px** on marketing, **1320 px** on fund-data screens (tables need more room).
- **Vertical rhythm:** section padding is `64 px` desktop / `40 px` tablet / `32 px` mobile.
- **Fixed elements:** the global nav is **sticky, 72 px tall** with a 1 px silver bottom border; it shrinks to 56 px when scrolled. Footer is **never** sticky.

### Backgrounds

- **No hand-drawn illustration. No emoji cards. No textured paper.**
- **Hero washes** — solid `--csop-navy-900` or a low-angle `navy → blue-600` linear gradient. Optional sub-1% white noise grain to soften banding.
- **Full-bleed photography** — used sparingly on About / Leadership / ESG pages only. Style: cool / desaturated, ~80% saturation, slight blue cast (think HSBC Asia annual report). No warm tones.
- **Repeating motif** — the logo's **swirl** is occasionally used as a 25% opacity oversized watermark in the bottom-right of hero modules, never as a busy repeating pattern.

### Animation

- **Easing:** all transitions use `cubic-bezier(0.4, 0.0, 0.2, 1)` (Material-style standard easing). No bounces, no overshoots, no springs.
- **Duration:** `120 ms` for hover state changes; `200 ms` for menu open / dropdown; `320 ms` for modal in/out; `480 ms` max for hero entry fade-ups.
- **Fades and short upward translates** (`translateY(8px) → 0`) only. Never scale-in from 0.95+, never rotate.
- **Tickers and price displays** flash a 600 ms `--csop-blue-50` row highlight on update, never red/green chrome animation.

### Hover & press states

- **Primary button hover** — background darkens to `--csop-blue-800`.
- **Secondary / ghost hover** — background fills `--csop-blue-50`, border stays the same.
- **Link hover** — text shifts from `--csop-blue-600` → `--csop-blue-800`, underline appears (1 px, `text-underline-offset: 3px`).
- **Press / active** — buttons drop to `--csop-navy-900`; no scale-down (no "shrink on tap"). Add `inset 0 0 0 2px rgba(255,255,255,0.15)` for tactile feedback.
- **Focus visible** — 2 px `--csop-blue-600` outline with 2 px offset, never the browser default.

### Borders, shadows, elevation

- **Borders:** **1 px**, `--ink-200 #E4E8EE`, *not* coloured. The only coloured borders are focus rings and the primary-button border (which matches the fill).
- **Outer shadows** — extremely light. Standard card shadow is `0 1px 2px rgba(14, 42, 82, 0.04), 0 4px 12px rgba(14, 42, 82, 0.06)`. Never use big diffuse drop shadows.
- **Inner shadows / inset** — only on inputs in pressed/focus state, and on the global nav at scroll.
- **No "protection gradients"** — CSOP never overlays gradients on photography to lift text; instead, photography reserves a solid-colour copy zone.

### Capsules vs. cards

- **Capsules** (radius-pill, `border-radius: 9999px`) — used for **status badges** ("Listed", "L&I", "ESG", "New"), **tag chips** in fund cards, and **fund-ticker pills**. Background tinted to a 50-level surface.
- **Cards** — `border-radius: 8px`, 1 px silver border, soft shadow, white surface. Larger feature cards step up to `12px`. **Never 16px+ rounding** — that reads consumer-app, not institutional.

### Corner radii

| Token | Value | Use |
|---|---|---|
| `--radius-sm` | `4px` | Inputs, small buttons, tag chips |
| `--radius-md` | `8px` | Cards, panels, primary buttons |
| `--radius-lg` | `12px` | Hero cards, modals |
| `--radius-pill` | `9999px` | Badges, ticker pills, filter chips |

### Transparency & blur

- **Glassmorphism / heavy blur — no.** It implies consumer-fintech.
- **Light overlay** allowed: modal scrim is `rgba(14, 42, 82, 0.55)` solid (no blur).
- **Sticky nav at scroll**: white `rgba(255,255,255,0.96)` with 8 px backdrop blur — *only* place backdrop-blur is used.

### Imagery treatment

- **Cool palette.** Photography is colour-graded toward `--csop-blue-600` (subtle blue cast in shadows, neutral whites).
- **People photography** — professional / boardroom / Hong Kong skyline. Never stock-photo "smiling with laptop". Subjects look toward camera or off-screen with neutral expression. Slight grain (1–2%) acceptable; no heavy film effects.
- **Charts** — line/area, single sapphire-blue series on a white card. Secondary series in silver. No 3D, no shadows on bars.

---

## ICONOGRAPHY

CSOP's production site uses a mix of **PNG navigation glyphs and simple line SVGs**. We do not have access to the production icon set, so:

- **Substituted icon library:** **Lucide** (https://lucide.dev, MIT-licensed, ~1.5 px stroke). Loaded from `unpkg.com/lucide@latest` in UI-kit examples. **Flagged as a substitution** — please replace with CSOP's production iconography if available.
- **Icon style rules:**
  - Line, **1.5 px stroke**, rounded line-caps and line-joins.
  - **24 px** standard size on desktop UI; 20 px in dense table chrome; 16 px inside tag chips and inline with body text.
  - Icon colour inherits `--ink-700` by default; primary icons take `--csop-blue-600`.
  - **No filled / duotone / colored emoji icons.**
- **Emoji:** **never** in product. Allowed on social media (LinkedIn) only.
- **Unicode characters as icons:** allowed only for arrows in inline copy (`→`, `↗`), bullets (`·`, `•`), and the multiplication sign in L&I copy (`2× Long`, `-1× Inverse`).
- **Logo / brand mark usage:**
  - Primary: bilingual lockup `assets/logo-bilingual.png` on top-level nav and footer.
  - Mark-only swirl (icon detached from wordmark) for app icons, social avatars, and large hero watermarks. (Currently the bilingual PNG only — derive the swirl crop in production if needed.)
  - Minimum clear-space around the lockup is **the height of the "C" character** on all four sides.
  - **Never** recolour, recompose, drop-shadow, or place on a busy photographic background without a contrast plate.

---

## How to use this system

1. Import `colors_and_type.css` at the top of any new HTML you write for CSOP.
2. Pick tokens from the variables — don't reinvent shades.
3. For new components, **start from a UI-kit component** in `ui_kits/website/` and adapt — don't draw new chrome from scratch.
4. When in doubt: more whitespace, less colour, more numerals, fewer adjectives.
5. Always include a disclaimer or risk line on product surfaces.
