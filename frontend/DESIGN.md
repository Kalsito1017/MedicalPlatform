# MediCore — frontend design system

This document records the visual redesign of the buildless vanilla frontend to
match the mockups in `design/` (`doctorpanel1.png`, `doctorpanel2.png`,
`AdminPanel.png`, `TechAdminPanel.png`). One product family name is used
everywhere in the UI chrome: **MediCore — Clinical Platform** (the mockups show
four different product names; that was deliberately ignored).

Everything here lives in `frontend/assets/css/styles.css` (design tokens as CSS
custom properties) and the HTML/JS page modules. No build step, no external
resources of any kind.

---

## 0. De-marking decision (owner, 2026-10-30)

The product surface stops advertising its data origin. All visible demo
labelling is removed from every page and JS-rendered string: "Synthetic demo
data" badges/pills, the "(демо)" name-suffix displays, demo banners and footer
demo lines, "(synthetic demo)" `<title>`/meta wording, the analytics subtitle
copy, and the login "what this is / is not" section. All disclaimer text is
removed entirely: "not a diagnosis or treatment recommendation", "not
clinically validated / not a medical device / not for clinical use",
"INFORMATIONAL ONLY" pills, "AI-generated insights for clinical review …"
subtitles, quick-action caveats, and the About dialog's disclaimer content
(the dialog is now a plain **About** panel: product name/version + issue-
reporting contact; the `data-open-about` hook is unchanged). Exactly one
content-origin label remains: the small **"AI-generated"** badge on assistant
answers (provenance of AI output, not a disclaimer).

Internal documentation stays honest: `docs/API.md`, the backend, and the repo
docs continue to record that the data is synthetic. The API shapes follow the
same decision — the `ai/ask` response is `{answer, used_tools, citations,
model, ai_generated}` (no `disclaimer` key) and clinical payloads carry no
`provenance` key. The frontend codes to those shapes and gracefully ignores
any legacy extra keys without rendering them. `tests/smoke.mjs` enforces the
surface rule with a static hygiene check ("product surface carries no
de-marked labelling or disclaimer copy"); its banned fragments are assembled
from string pieces so tree-wide sweep greps stay clean, and the pattern is
word-bounded so "demographics" terminology is deliberately not matched.

---

## 1. Design tokens

### Colour (light theme; WCAG-AA checked pairings)

| Token | Value | Use |
|---|---|---|
| `--color-canvas` | `#f8fafc` | page background (slate canvas) |
| `--color-canvas-alt` | `#f1f5f9` | subtle fills, user chat bubble, table heads |
| `--color-surface` | `#ffffff` | cards, sidebar, topbar |
| `--color-text` | `#0f172a` | headings |
| `--color-text-body` | `#475569` | body copy |
| `--color-text-muted` | `#64748b` | meta, hints (4.7:1 on white) |
| `--color-border` / `--color-border-strong` | `#e2e8f0` / `#cbd5e1` | card and control borders |
| `--color-primary` (+hover `#1d4ed8`) | `#2563eb` | links, active nav, primary buttons |
| `--color-primary-soft` | `#eff6ff` | active nav pill, chips, info cards |
| `--color-teal` / `--color-teal-strong` | `#0f766e` / `#0d9488` | teal accents, avatars, AI badge |
| `--color-success` / `--color-success-text` | `#16a34a` / `#15803d` | positive badges (text tone passes AA on its soft fill) |
| `--color-danger` / `--color-danger-text` | `#dc2626` / `#b91c1c` | destructive actions, alert badges |
| `--color-warn` / `--color-warn-text` | `#d97706` / `#b45309` | warnings, "shown once" alerts |
| `--gradient-brand` | teal `#0f766e` → blue `#2563eb` | brand mark, AI assistant header, accent button |

Dark mode (`prefers-color-scheme: dark`) swaps the token values only; the same
components restyle automatically.

### Typography

System stack only (CSP: no webfonts): `ui-sans-serif, system-ui, -apple-system,
"Segoe UI", Roboto, Arial, sans-serif`; `ui-monospace…` for MRNs, request IDs
and generated passwords. Scale: `--text-xs 0.75rem` … `--text-3xl 2.125rem`,
headings 700 weight, tight leading.

### Spacing, radii, elevation

- Spacing scale (4px base): `--space-1 … --space-7` (`0.25rem … 3rem`).
- Radii: `--radius-sm 6px` (small controls), `--radius-md 10px` (buttons,
  inputs), `--radius-lg 12px`, `--radius-xl 16px` (cards, dialogs),
  `--radius-full` (pills, avatars, page circles).
- Shadows: soft and low-contrast — `--shadow-1` (cards), `--shadow-2` (hover),
  `--shadow-3` (dialogs).

## 2. Component vocabulary

- **App shell** — white sidebar (brand + icon nav + "Need help?" card) with a
  subtle right border; sticky topbar with role chip and user block (initials
  avatar + display name + role); light slate content canvas, `max-width 75rem`.
  Collapses below 960px to a stacked layout where the `#nav-toggle` "Menu"
  button expands `#site-nav` (the pre-existing toggle behaviour, unchanged).
- **Buttons** — 40px height (`.btn`), radius 10px: `btn-primary` (solid blue),
  `btn-accent` (brand gradient), `btn-secondary` (white + border), `btn-danger`,
  `btn-ghost`, `btn-page`/`btn-icon` (pagination circles). Hover/active/focus
  states on all; `:focus-visible` ring is global.
- **Badges / pills** — rounded-full, tinted background + saturated text:
  `badge--ok/warn/alert/neutral/info/ai` (status + the "AI-generated" label;
  see "De-marking decision" above) and
  `badge--role-doctor/nurse/clinical_admin` (the AdminPanel mockup's
  coloured role tags).
- **Role mapping** — three roles only (owner decision, 2026-10-26; the
  `tech_admin` role and its `badge--role-tech_admin` styling were removed):
  `doctor` → "Doctor", `nurse` → "Nurse", `clinical_admin` → "Admin"
  (`ui.js` `ROLE_LABELS`). The mockup's "TechAdmin" panels are historical
  reference only; admin functionality lives in `admin.html` and is visible
  only to holders of `staff:read`/`staff:manage`/`role:assign` (the Admin
  role) — UI hiding is never the control, the API enforces it.
- **Patient cards** (dashboard "recent patients" + patient search) — initials
  avatar, bold name, `MRN: …` (mono), `Born <date> • <sex>` meta, chevron.
  Whole card is the patient link.
- **Patient header card** — large initials avatar, name (`h1`),
  `MRN:` + working **Copy MRN** button (real clipboard API with an honest
  fallback message), meta line.
- **Icon tabs** — Summary | Labs | Prescriptions | Encounters | AI Patient
  with blue underline; full ARIA tabs pattern (roving tabindex, arrow keys,
  Home/End).
- **Lab Results Summary** — in the Labs tab, Graphify renders one chart card
  per test in a responsive grid. Each card has its own Y scale and unit, a
  green recorded-reference band, a target note, value labels on the points,
  hover/focus readouts, and a visually hidden data table. The full paginated
  lab table remains below the summary. The Labs tab and its Graphify module
  load on first activation, so the default Summary view does not download the
  chart code or lab data.
- **Tables** — staff/labs/prescriptions/audit; scroll container + horizontal
  scroll shadow, card stacking under 620px via `data-label`.
- **AI Patient tab** — the fifth patient-record tab, after Encounters. It
  carries the gradient header ("AI Patient"), a patient context line
  (name + MRN), chat bubbles (user right/gray, assistant left/white with the
  "AI-generated" badge, source chips, citations, model + timestamp),
  patient-filtered history, and the composer with send button. The old
  right-hand AI rail is gone; the tab is full width. The patient-filtered
  history request starts on first open (or immediately before the first ask),
  not during the initial Summary page load.
- **Print / Save as PDF** — patient-header action that lazily imports
  `print-record.js`, fetches the complete demographics, summary, encounter,
  laboratory and prescription record through the authenticated API, renders a
  dedicated `#print-record` document with text nodes only, and opens the native
  browser print dialog. `print.css` supplies the A4 layout; the normal screen
  interface is unaffected. Any section load failure blocks printing and offers
  a retry.
- **AI Assistant page** (`ai.html`, nav item "AI Assistant" with the sparkles
  icon, shown only with `patient:read`) — the general-lookup companion to the
  AI Patient tab: same `ai-card` vocabulary at page scale (gradient header) with
  a chat transcript (`#ai-response-region`) that keeps the conversation of the
  current page view, suggested-question chips that fill the composer (plain
  text shortcuts — real queries, no automation), and an "Ask about a patient"
  link to the real patient list (`patients.html` → `patient.html?id=…`; a
  detail page needs a concrete record id, so the list is the honest entry).
  Composer: send + cancel + 2000-char counter. All patient-panel states kept:
  loading, cancel, 503 unavailable, 429 countdown, `describeApiError` errors
  incl. the network/offline banner. It posts `{ question }` (general lookup
  mode — no `patient_id`); the AI Patient tab keeps posting
  `{ patient_id, question }` unchanged.
  Citation links: `patient` citations always link to `patient.html?id=…`;
  `encounter`/`lab_result`/`prescription` citations link to the patient record
  only when the same answer cites exactly one patient (unambiguous
  attribution), otherwise they stay plain record references — never invented.
  Questions/answers are persisted in the per-user server-side conversation
  history and rendered in the page DOM; never in web storage or logs.
- **Landing / sign-in page** (`index.html`) — full-viewport dark hero with a
  CSP-safe ambient clinical background: a local, heavily blurred care-team
  photograph (`assets/images/doctor-background.webp` with a PNG fallback,
  decorative/aria-hidden) under layered gradients and soft light streaks (no
  external image requests),
  MediCore brand lockup, large
  "MediCore — Clinical Platform" headline, value proposition, and a translucent
  sign-in card with email/password field icons, an accessible show/hide
  password button, a full-width primary Sign in action, and an honest
  "Role-based access with server-side audit logging" line. Footer:
  "About MediCore" + "Report an issue". The page intentionally uses a dark
  treatment in both app themes.
- **Dialogs** — head icon + title, optional `confirm-details` definition rows
  (the AdminPanel confirmation modal), Cancel + solid confirm; one-time password
  modal with the "shown once" warning; About dialog. All via the existing
  `ui.confirmDialog` / `ui.openDialog` / `ui.infoDialog`.
- **Pagination** — "Showing X–Y of Z" + prev/next chevrons (visually-hidden
  labels) + numbered page circles.
- **States** — heart-loader loading placeholders, empty/error state panels, global banner
  tones, form fields with hint/error/aria-invalid styling.

## 3. Mockup → feature mapping decisions

Real surfaces only (project rule: every control must work, no invented data):

| Mockup concept | Mapped to | Notes |
|---|---|---|
| Sidebar brand + nav + "Need help?" | `app-shell` sidebar; nav items derived from real permissions (Dashboard; Patients for `patient:read`; Admin for `staff:read/manage/role:assign`) | "Need help?" is wired to the real About dialog + issue `mailto:` |
| Role chip + user block | topbar role chip (real `roles`) + initials avatar | job titles do not exist in the API; the user block subtitle shows the real role label |
| "Welcome back, Dr. Mitchell" | dashboard `Welcome back, {display_name}` + role badges | first-name-only greeting would misreport the real display name |
| Patient card grid | dashboard recent patients (`GET /patients?limit=8`) + patient search results | see "omitted" rows for the card contents |
| Quick actions row | "Quick actions" tiles — only real destinations (Patients, Admin) | |
| Patient header (MRN copy, meta) | patient header card | see "omitted" for right-side meta fields |
| Tabs Summary/Labs/Prescriptions/Encounters | real tabbed panels backed by the real sections of the summary/labs/prescriptions data; the AI Patient tab is the fifth tab | |
| Labs table (value, unit, ref range, status, collected) | labs table; reference range from `ref_low`/`ref_high`, status badge from the API `status` field; per-test Graphify summary cards above it | status text is the raw API value, never invented "Normal/High" labels; chart flags come from the API `flag` field |
| AI Assistant rail | AI Patient tab over the existing `patient.js` AI flow | all current states kept: loading, cancel, 503 unavailable, 429 rate limit, errors, citations, permission gating; the panel is scoped to the patient in the URL |
| Sidebar "AI Assistant" nav item (`doctorpanel1.png`) | `ai.html` + `assets/js/ai-assistant.js`, nav item derived from `patient:read` | the mockup's "AI Insights" tab and quick-action tile are the same surface; only this one nav item is real |
| Staff management table + role pills + confirmation modal + pagination | admin staff table (avatar + name/email stack, role pills, Active/Inactive, actions), `ui.confirmDialog` with detail rows, page circles | |
| TechAdmin audit log (mockup label; `tech_admin` was removed as a role) | admin "Audit events" table (when/actor/action/resource/outcome/request id) with coloured outcome badges | |

### Deliberately NOT built from the mockups (and why)

- **Demo data banner** ("Synthetic demo data — not for clinical use" header
  banner in `doctorpanel1.png`) — the owner removed the persistent header banner
  (PLAN.md, 2026-10-26) and, on 2026-10-30, all remaining visible origin
  labelling and disclaimer copy with it (see "De-marking decision" above):
  no demo/data-origin disclosure footer or banner remains on the product
  surface; About/Report links live in the sidebar help card (and the compact
  bottom-left landing footer). Synthetic provenance is documented only in the
  internal docs.
- **Appointments, Messages, Orders, Results, Reports, Settings, Population
  Health, AI Insights, Alerts nav items** — no such features or endpoints exist;
  every nav item must lead to a working surface. (The mockups' "AI Assistant"
  item is real and built; "AI Insights" is that same AI surface under a
  different mockup label and is not a separate page.)
- **"New Patient / Order Labs / Schedule Visit" quick actions** — no such
  features exist in the API.
- **⌘K search chip and dashboard search box** — no keyboard-shortcut search is
  implemented; the real search lives on the Patients page and is API-backed
  (`?q=`).
- **Patient card "Last visit" line** — the API returns no visit data on patient
  records. Cards show real fields only (name, MRN, date of birth, sex).
- **Patient header right-side meta fields (PCP, insurance, last encounter,
  "View Profile")** — not returned by the API (deliberate
  minimalism); only real fields render.
- **Lab value colouring / "High ↑" arrows** — the API `status` field is a result
  status (e.g. `final`), not an interpretation; deriving "High/Low" would invent
  data. The badge shows the real status, tinted by its category.
- **Admin toolbar search box, role/status dropdown filters, "3 columns" toggle,
  date picker, notifications bell, a data-reset action** — the staff API accepts
  pagination only; a search box would be a fake control. The filter-less table
  is shown honestly.
- **TechAdmin stat cards (System Status / Database / Services / Storage,
  sparklines)** — there is no system-metrics endpoint; those stats would be
  invented.
- **Row action icons as the only affordance (edit/lock/kebab)** — real actions
  are labelled buttons (Grant role, Revoke …, Activate/Deactivate) so they are
  unambiguous and keyboard/screen-reader friendly.
- **Photo avatars** — no images from the web are allowed (CSP); coloured
  initials circles are used instead, exactly as the fallback style of the
  mockups.

## 4. Keeping the CSP hash untouched

`frontend/_headers` pins `script-src 'self' 'sha256-FCyl4aml0FXN3LiS6Gywzo8SYVs85ZhLR8JLqUxTX78='`.
That hash is the SHA-256 of the **byte-exact** inline bootstrap block that every
page carries in `<head>`:

```html
  <script>
    /* Deployment override — see assets/js/config.js. Example:
       window.__APP_CONFIG__ = { API_BASE_URL: "https://<backend-host>/api/v1" }; */
    window.__APP_CONFIG__ = window.__APP_CONFIG__ || {};
  </script>
```

Rules when editing any page:

1. Never change this block — not one character, not the whitespace, not the
   comment. `scripts/security_check.py` (section 6/6) recomputes the hash of
   every inline `<script>` in `frontend/**/*.html` and fails on drift.
2. Add **no other inline `<script>`** — application code goes in
   `assets/js/*.js` (loaded with `type="module"`).
3. No inline `style="…"` attributes and no `el.style.x = …` DOM writes: CSP
   `style-src 'self'` allows only the stylesheet. Dynamic values use predefined
   CSS classes (e.g. `avatar--3`, `badge--role-nurse`). `tests/smoke.mjs` now
   enforces this for all HTML and JS.
4. No external `src`/`href`/fonts/icons — enforced by `tests/smoke.mjs` and
   `scripts/security_check.py`.

## 5. Accessibility & responsiveness

- Semantic heading order per page (`h1` → `h2` → `h3`); labelled inputs;
  `aria-live` status regions for async results; focus moves to mutation
  outcomes (e.g. `#staff-status`); visible focus rings; skip link on every page.
- Tabs implement the ARIA tabs pattern with keyboard support; dialogs use native
  `<dialog>` focus trapping.
- `prefers-reduced-motion` disables all animation/transition.
- Breakpoints: 960px (sidebar collapse + mobile nav), 620px (tables stack into
  cards), 480px (detail grids stack). The landing page stacks its hero and
  sign-in card below 960px. Verified down to 360px by code review of the
  grid/flex rules — pixel-level fidelity to the mockups is reviewed against the
  PNGs, not screenshot-tested (no browser in the build environment).

## 6. UI/UX pattern library (owner list items 1–20)

Everything below lives in the same buildless system: `styles.css` classes and
small ES modules (`ui.js`, `cache.js`, `toast.js`, `command-menu.js`). No
inline `style` attributes, no `el.style.*` or `setProperty()` writes — dynamic
appearance always goes through predefined classes/attributes (enforced by
`tests/smoke.mjs`).

### 1. Heart loader (the app-wide loading indicator)

**The anatomical heart is THE loading indicator of the platform** (owner request
2026-10-27): `ui.heartLoader({ size, label })` renders `assets/images/heart-loader.png`
(local asset, transparent background — deliberately **no plate** behind the
image, so it reads as intentional on both canvases) as
`.heart-loader > img.heart-loader__img` (decorative: `alt=""`,
`aria-hidden="true"` — the surrounding region owns the `role="status"`
announcement) plus an optional visible caption `.heart-loader__label` (pass
`label: ''` when the surrounding state panel already carries the status text).

**Sizing rule** (class-based only): `sm` ≈ 28px wide for compact regions/rows,
`md` ≈ 48px (default) for every list/table/section load, `lg` ≈ 72px for the
**AI answer region**. `ui.renderLoading(container, { label, rows, variant,
wrapperTag })` keeps its signature for all existing call sites and renders the
**md** heart on a centred `.loading-block` surface plate (rows and cards
alike; `rows` is accepted in name only now that the loader is a single
indicator). The placeholder is announced (`role="status"`, label text) and
replaced atomically by the real renderer.

**The heartbeat** is the signature look: a gentle "lub-dub" —
`@keyframes heart-beat` 0% `scale(1)` → 12% `scale(1.12)` → 24% `scale(1)` →
36% `scale(1.06)` → 48% `scale(1)`, `1.5s ease-in-out infinite`, pure
transform, **no fill-mode** (interruptible/re-targetable, DESIGN.md item 11),
soft enough for a clinical product. A soft danger-hue drop shadow
(`--heart-glow`, token overridden per theme) lifts the heart off both canvases.
`prefers-reduced-motion`: the global rule freezes the pulse to a static image.

**Wiring map (old visual → heart)**: shimmer skeleton rows/cards
(`ui.renderLoading`) → centred md heart on `.loading-block`; hero blur slabs
(`ui.renderBlurPlaceholder`/`blurPlaceholderNode`) → md/lg heart inside the
kept `blur-placeholder` contract (see item 8); the assistant emoji thinking
ticker (`emojiLoadingNode`) → lg heart in the answer region's state panel
(favicon avatar + "Consulting the assistant…" status text kept); static pre-JS
page fallbacks (dashboard/patients/patient/admin/analytics HTML) → the same
heart markup. Button pending states keep their disabled + label pattern; the
SWR "Updating…" `.sync-chip` stays as-is (background refresh indicator, not a
full loader).

**Retired visuals (dead code deleted, proven by `tests/smoke.mjs`)**: the
shimmer skeleton CSS/markup and the **emoji thinking ticker** (`.ai-emoji-loader`,
`emoji-pulse`, `emojiLoadingNode`) — the emoji ticker was a previous owner
request and was superseded by the heart loader on **2026-10-27**. Covered async
regions: patient list cards, patient detail sections
(demographics/summary/labs/prescriptions/encounters), AI conversation history
(both the page and the AI Patient tab), the staff table, the audit table, the
analytics regions, dashboard recent patients, and both LLM answer regions.

### 2. Optimistic updates (admin staff table)
`admin.js` mutation flow: confirm dialog → **optimistic row update** (grant /
revoke / activate-deactivate apply to the row instantly) → API call →
reconcile with the fresh `StaffUserOut`, or **roll the row back to the last
server truth** and show an error toast built from `api.describeApiError`
(plus the existing `#staff-status` announcement and focus move). Mutations
`cache.invalidate('/staff')` so list pages revalidate.

### 3. Command menu (⌘K / Ctrl+K)
`command-menu.js` + `.command-menu` dialog (native `<dialog>`: focus trap for
free). Sources: permission-derived navigation (`auth.navItemsFor`), bounded
"Go to patient …" entries (first 50 records from `GET /patients?limit=50`, a
note points to the Patients page when there are more; lazily fetched on first
open, only with `patient:read`), contextual page actions (patient page:
"Summarize this patient", "Ask AI about this patient", the patient suggestion
chips; AI page: the suggestion chips fill the composer), and a real
"Keyboard shortcuts" entry. Fuzzy-ish filtering (subsequence scoring), typed
filter, ↑/↓ + Enter + Esc, `aria-activedescendant` listbox semantics, empty
state "No matches", focus restored to the opener on close. Navigation rows
show action labels only (no `.html` destination hints); patient rows keep
their MRN hint. At mobile widths (≤620px) the palette becomes a bottom sheet
with swipe-to-dismiss (see "Swipe-to-dismiss sheets" below).

### 4. Shortcut hints (kbd chips)
`.kbd` chips render **real** keybindings only: the ⌘K chip in the topbar
"Search & commands" trigger (every app-shell page), the ⌘K/Ctrl+K note in the
Patients search hint, and the composer hints (`Enter` / `Shift+Enter`) on the
AI page and the AI Patient tab. The command-menu footer shows the compact
"↑↓ navigate · ↵ open · esc close" hint; the "Keyboard shortcuts" command
lists every keybinding in the app (nothing invented).

### 5. Undo toasts
`toast.js` + `.toast-region` (bottom-right, polite `aria-live`, auto-dismiss
6s, working action button + close button). Applied to the reversible
high-impact admin actions: **deactivate account** → toast "Account deactivated"
with **Undo** (= PATCH `is_active: true`, optimistic, "Restored" on success /
error toast on failure) and **revoke role** → toast "Role removed" with
**Undo** (= re-grant via `POST /roles`, same contract).
**Undo vs confirm (documented decision):** the pre-action `confirmDialog` is
kept for *all three* mutations (grant role, revoke role, deactivate) as belt
and braces on top of the undo toast — the confirm carries the consequence
copy (session revocation, self-escalation) and the existing behaviour
contracts assert it; the undo toast is the second, post-action safety net.
"Clear history" keeps its confirm dialog and deliberately has **no fake
Undo** — it is not reversible.

### 6. Stale-while-revalidate
`cache.js` — in-memory only (Map), keyed by request URL (path + query), 30s
TTL, **per-tab/per-page-view lifetime** (never web storage; `auth.logout()`
clears it and every page load starts cold). Within a page view, repeat loads
(paging back, re-running a search, revisiting a section) render the cached
payload **instantly** and revalidate in the background; the DOM re-renders
only when the fresh payload differs, with a tiny `.sync-chip` "Updating…"
indicator in the meantime. A failed background revalidation never replaces
working content with an error state. First visits are unchanged (heart loader →
data, or the existing error/empty states). Applied to: patients list, patient
detail sections, staff list, audit list, and both AI history lists. Note:
because the app is multi-page (full loads), the cache cannot survive a real
cross-page navigation — the win is within-page re-visits; a cross-page cache
would require web storage, which is banned for clinical data. The sole
non-clinical storage item is the theme preference (`medicore-theme`).

### 13. Hit slop
`.hit-slop` (documented shared class): an invisible `::after` pseudo-element
extends the effective touch target of small/icon buttons to at least 44×44px
**without changing their visual size**. Applied to: the AI send plane
(`.ai-send`), pagination arrows, Copy MRN / Copy password, dialog action
buttons (confirm/close), admin row actions (Grant/Revoke/Activate/Deactivate),
banner Dismiss, toast Undo/Close, and chip action buttons. The app has no
chip-close (×) affordance — chips are plain labels or whole buttons — so there
was nothing to cover there; suggestion chips get the class anyway.

### 14. Safe-area insets
`padding: max(<base>, env(safe-area-inset-*))` on the topbar (top/left/right),
the sidebar foot and mobile nav list (bottom), the AI composer (bottom), the
compact landing footer and the toast region (bottom) so nothing sits under
notches or home indicators.

### 15. Autofill semantics (no OTP surface)
Item 15 = **autofill correctness**. Tokens: sign-in email
`autocomplete="email"` + password `current-password`; staff-create form `email`
(→ `#create-email`), `name` (→ `#create-name`), `new-password`
(→ `#create-password`); search inputs and the role `<select>` keep
`autocomplete="off"` (they are filters, not identity fields). **The app has no
OTP / one-time-code surface**, so the `one-time-code` token is deliberately
unused anywhere (and `tests/smoke.mjs` fails if any page claims it).

### 16. Tabular numbers
`.tabular-nums` utility + `font-variant-numeric: tabular-nums` applied to
`.table` (labs, prescriptions, staff, audit), `.pagination__summary`
("Showing X–Y of Z"), `.char-counter`, and `.state-panel__meta` (request IDs).
The app has no stat-card surface (no metrics endpoint), so there are no
stat-like numbers beyond these.

### 17. Balanced headlines
`text-wrap: balance` on `h1`, `h2`, card/panel/state titles and quick-link
titles. Unsupported browsers wrap normally (pure progressive enhancement).

### 18. Continuous corners
`corner-shape: squircle` on cards, panels, dialogs, the command menu, buttons,
badges, form controls, state panels, toasts and tiles. Browsers without
support keep the existing `border-radius` values unchanged (no regression).

### 20. Safe triangle (edge-swipe zone)
Interpretation: on touch-width layouts (≤960px) the left screen edge is
reserved as the back-swipe/edge-gesture zone — content keeps a ≥22px left
gutter (`.container`, `.topbar`) so no interactive control's hit area
(including `.hit-slop` pseudo-elements) overlaps the edge and fires
accidentally during an edge swipe. The sidebar "Menu" nav toggle is the
deliberate edge control and is exempt.

## 7. Wave UI: what the browser must still verify

The build environment has no browser; layout, focus rings, screen-reader
output, dark-mode contrast of the new components, `corner-shape` rendering in
a supporting browser, and real notch behaviour (safe-area insets) are
code-reviewed and test-covered for behaviour only — see the final report's
browser checklist.

## 8. Wave UI-B pattern library (owner list items 7–12, 19)

Same buildless system as section 6: `styles.css` classes plus small ES modules
(`ui.js`, `sheet.js`, `overlay-history.js`, `prefetch.js`, `api.js`,
`toast.js`). No inline styles anywhere — with exactly one documented exception
(see "Swipe-to-dismiss sheets"), no external resources, no clinical/session
web storage (only the theme preference), no console logging.

### 7. Pre-rendering and intent prefetch

- **Module warm-up.** Every page declares static `<link rel="modulepreload">`
  hints for its shared graph (`ui.js`, `api.js`, `cache.js`, `auth.js`,
  `command-menu.js` on shell pages). On idle (`requestIdleCallback`, with a
  `setTimeout` fallback — `prefetch.onIdle`) each page additionally warms the
  **next-likely page modules** for its navigation flow (login → dashboard;
  dashboard/patients → patient; patient ↔ ai; admin → patients) by injecting
  the same link hints (`prefetch.warmModules`). Link hints fetch+parse without
  **executing** the module — a dynamic `import()` would RUN the page module, so
  it is deliberately not used.
- **Hover-intent / focus-intent prefetch.** Patient cards
  (`ui.renderPatientCard` stamps `data-prefetch-patient`) and "Go to patient"
  command-menu entries (plus arrow-key selection in the palette) warm the
  patient-detail API data through `cache.js` after a ~150ms intent debounce
  (`prefetch.wirePatientIntent`, `prefetch.warmPatientRecord`) — a stray mouse
  pass never fires a request.
- **Code-asset caching.** HTML, JS and CSS are served with
  `Cache-Control: no-cache` so a normal reload revalidates and picks up a
  buildless fix; images keep a bounded `public, max-age=3600` cache. Shell
  entry modules and the shared `command-menu.js` import carry a one-time
  `?v=YYYYMMDD…` cache-busting token when a change must bypass an already
  cached copy (there are no hashed filenames to rotate).
- **Privacy stance.** Permission-gated: nothing is prefetched unless the
  signed-in `/auth/me` payload includes `patient:read` (never beyond the
  authenticated user's permissions; the backend still re-checks every request).
  In-memory only: warmed responses live in `cache.js`'s per-document Map —
  never web storage, never logged. Cache-TTL respecting: a fresh cache entry is
  not re-fetched. Anonymous pages get zero prefetches.
- **Honest scope limit.** The app is multi-page: a full cross-page navigation
  starts a new document, so the in-memory cache cannot carry over — the warm-up
  pays off for within-page revisits (tabs, back/forward within a page view,
  re-opened sections) and for module parse time on the next navigation.
  Persisting prefetched clinical payloads across documents would require web
  storage (banned for clinical data) or a service worker (out of scope).

### 8. Blur placeholders (hero loading regions → heart loader)

HERO-ish async regions — the **patient header card** (`#demographics-region`),
the **AI answer turn** (AI Patient tab and AI page), and the **dashboard
welcome** region — use `ui.renderBlurPlaceholder` / `ui.blurPlaceholderNode`
and settle through the ready-transition contract: the container carries
`.blur-region.is-loading` while pending and `ui.markBlurReady` flips
`.blur-region.is-ready`, so the real content sharpens in via the `blur-in`
opacity/filter animation — class toggles only, never a style write.
`prefers-reduced-motion`: instant swap (the global rule kills the animation).

**Visual (2026-10-27; simplified 2026-11-09):** the placeholder is the
**heart loader** alone (item 1) — md heart for `hero`/`welcome`, lg heart for
the `answer` variant. The assistant avatar/favicon (the blue plus mark) is
deliberately not shown beside the heart in any loading state. The former
blurred gradient slabs (`.blur-block*`) are deleted as superseded dead code.

**Naming decision (documented per owner instruction):** the `blur*` function
and class names (`blurPlaceholderNode`, `renderBlurPlaceholder`,
`markBlurReady`, `.blur-region`, `.blur-placeholder`, `blur-in`) are
**kept deliberately** — they are the DOM contract callers (`patient.js`,
`dashboard.js`, `ai-assistant.js`) and the render suite bind to, renaming them
would churn four modules and the tests for cosmetics only, and the arrival
`blur-in` sharpen-in is still literally what happens on ready. The *visuals*
changed; the loading → content → ready semantics did not. Hero regions stay
distinct from `ui.renderLoading` (item 1) not by shape but by contract: hero
regions transition in with `is-ready`, list/table loads swap atomically.

### 9. View transitions

`ui.withViewTransition(update)` wraps `document.startViewTransition` with a
feature test and a graceful fallback: no API (or `prefers-reduced-motion`) →
the update runs immediately and the return value mimics the `ViewTransition`
shape. Applied to the same-document swaps where they read best: patient detail
**tab switches** (`patient.js`), history **expand/collapse** (`ai-history.js`),
**command-menu open/close** (`command-menu.js`), and every **dialog open/close**
(`ui.js` `closeDialog`). Stable `view-transition-name`s live in `styles.css` on
the main content regions — `.patient-main` (`vt-record-panels`),
`.command-menu` (`vt-command-menu`), `.dialog` (`vt-dialog-surface`) — each
unique per visible element. The shared dialog host re-uses ONE element for
confirm/password/about dialogs, so its old/new snapshots deliberately skip the
default cross-fade (`::view-transition-old/new(vt-dialog-surface)
{ animation: none }`) which would ghost one body over the other; the record
panels keep the default cross-fade for tab swaps. Cross-page navigations
(document loads) are outside the API's reach in a buildless multi-page app and
stay instant. Theme-agnostic: nothing is theme-coupled. Reduced motion:
`ui.withViewTransition` opts out in JS and a CSS rule kills any
`::view-transition-*` animation as belt and braces. Because the close itself
can be deferred into the transition update callback, `ui.openDialog.close()`
returns a promise resolved by the native `close` event; callers that immediately
reuse the shared dialog (appointment detail → reschedule form, cancel
confirmation) must await it or wait for the dialog's own `close` event first;
the appointment flow does both so it also survives an older cached `ui.js`.
Dialogs also participate in overlay history: a replacement dialog opened while
the previous overlay's UI close is still consuming its history entry defers
its `pushState` until that self-back's `popstate` settles, so a pending
`history.back()` can never navigate to the previous page. The appointment flow
adds a popstate/timeout fallback for browsers holding an older cached
`overlay-history.js`.

### 10. Swipe-to-dismiss sheets

On touch widths (≤620px) the overlay surfaces — `confirmDialog`, `openDialog`
(including the generated-password modal) and the command menu — present as
**bottom sheets** (`.dialog`/`.command-menu` fixed to the bottom edge with a
top radius; the palette drops its former full-screen treatment). A grab handle
(`.sheet-grabber`) is the drag surface, so scrolling the palette list or typing
is never hijacked. Pointer Events with a touch-events fallback; vertical drags
only (a horizontal-first gesture cancels). The sheet follows the finger while
dragging; releasing **≥120px** down dismisses it with the **exact Esc/cancel
semantics — never confirming** (a confirm dialog resolves `false`), sliding out
via `.is-closing` first. Below the threshold it springs back.

**Sanctioned style-write exception (lead pre-authorization, wave UI-B):**
finger-following requires a running pixel offset and the ban on
`el.style`/`setProperty` writes has no attribute-based encoding for one, so
`sheet.js` — and only `sheet.js` — may write
`sheet.style.setProperty('--sheet-dy', …)` **on the sheet element only**,
consumed by `styles.css` as `transform: translateY(var(--sheet-dy, 0px))`.
Everything else (drag state, closing state) is plain classes. This is the only
style write in the entire app; `tests/smoke.mjs` allowlists exactly this
file/property (with an allowlist comment) and fails if anything else slips in.

### 11. Interruptible animations

Audit of every transition/animation in `styles.css`: all motion is either a
short keyframe (≤260ms, **no fill-mode**, end state = base state), a looping
decorative keyframe (heart-beat: 1.5s, **no fill-mode**, no state coupling),
or a pure transition on transform/opacity/colour — so every animation is
re-targetable mid-flight and can never lock state or block input:

- toasts: `toast-in` entrance keyframe + `.is-closing` exit transition
  (removal after 160ms; instant under reduced motion);
- dialogs/command menu: `overlay-in` entrance keyframe; close is a view
  transition / instant swap;
- heart loader: the `heart-beat` lub-dub loop is decorative and has no state
  coupling — it is simply removed with its region when the content arrives;
- sync chip: `fade-in`, class-toggled on/off (rapid toggling settles on one
  chip — tested);
- accordion history expand/collapse: `reveal-in` on the detail (discardable
  mid-flight; re-expanding replays it);
- tab indicator: 120ms colour/border transition (naturally re-targetable);
- hover states (buttons, nav links, cards, chips): 120ms colour transitions;
- hero loading regions: `blur-in` on arrival (class-toggled).

`prefers-reduced-motion` parity: the global `animation: none; transition: none`
rule covers all of the above, plus the view-transition pseudo kill. State-
coupled class toggling is exercised in `tests/render-smoke.mjs` ("rapid
toggling" scenario) — including interrupted sheet drags settling exactly once
and mid-entrance toast dismissal.

### 12. Predictive back

Every overlay — command menu, dialogs/sheets (incl. the generated-password
modal), expanded history entries, and the mobile nav drawer — participates in
browser history (`overlay-history.js`): opening pushes one `history.pushState`
entry (URL untouched — `?id=` deep links and `patients.html?q=` state are never
modified), and **Back dismisses the topmost overlay first** (LIFO unwinding)
with its exit transition instead of leaving the page. UI closes (Esc, scrim,
buttons, swipe) consume their own history entry via `history.back()`, and
self-inflicted popstates are swallowed so a close can never cascade. With no
overlays open, Back behaves exactly as before. `history.back()` is only called
for entries the module actually pushed (safe in sandboxed frames where
`pushState` throws).

**What "predictive" means here (precise):**

- **True:** Back-dismisses-the-topmost-overlay with the exit transition;
  popstate-driven state; LIFO multi-overlay unwinding; gesture-linked preview
  where the platform exposes one — the swipe-to-dismiss sheet follows the
  finger and previews its own dismissal before committing past the threshold.
- **Adapted (not possible in a buildless web app):** true gesture-scrubbed
  back previews (Android's predictive back / iOS interactive pop) are not
  exposed to vanilla JS — no browser API reports gesture progress to the page,
  and there is no View Transition equivalent for the history traversal. The
  exit transition therefore plays on dismissal rather than tracking the
  gesture; the sheet drag is the closest gesture-linked preview available.

### 19. Idempotency keys

- **Frontend (`api.js`).** Every mutating request (POST/PATCH/DELETE) carries
  `Idempotency-Key: <crypto.randomUUID()>` (random fallback for older
  engines). The key is a random token — it never encodes or contains clinical
  data or anything user-authored (verified in tests). A retry of the SAME
  logical request reuses its key (pass `idempotencyKey` through the request
  options); a NEW request always gets a fresh key. GET/HEAD/OPTIONS never send
  the header.
- **Backend (`backend/app/api/idempotency.py` + `admin.idempotency_record`).**
  Honoured on the two POSTs whose double-apply would hurt — `POST /staff` and
  `POST /ai/ask`. Scope is (user_id, endpoint, key); the first successful
  response is recorded and replayed verbatim within a 24h retention window
  with `Idempotency-Replayed: true` (no second account, no second LLM call, no
  second history row, no second audit event). Same key + different payload →
  `409 conflict` (documented choice: it is a state conflict with the consumed
  key, not a malformed body), detected via a SHA-256 request fingerprint — the
  payload itself is never stored. Missing header = unchanged behaviour.
  Concurrency is decided by the unique constraint (the second insert loses and
  is served as a replay-after-read). Expired rows are pruned opportunistically.
  Secrets never land in the record: the staff one-time generated password is
  stored/replayed as `null` (shown exactly once). Full semantics in
  `docs/API.md` ("Idempotent mutations").

### 19b. Platform-neutral shortcut chip (UI-A follow-up)

The ⌘K chip is no longer macOS-literal: `ui.commandShortcutLabel()` renders
"⌘K" on Apple platforms, "Ctrl K" elsewhere, and "Ctrl/⌘ K" when the platform
cannot be determined (from `navigator.userAgentData.platform` →
`navigator.platform` → `navigator.userAgent`). The static HTML default is the
neutral "Ctrl/⌘ K" and `data-command-shortcut` chips are rewritten at chrome
init. The "Keyboard shortcuts" dialog continues to list both real bindings.

## 9. Wave UI-C: Responsive (phone / TV / reduced)

One system, three regimes: **phone** (≤620px, primary target 360–430px),
**TV / large displays** (≥1600px, with a 4K step at ≥2560px), and **reduced**
conditions (320px windows, 200% browser zoom ≈ half viewport width,
`prefers-reduced-motion`). Enforced statically by `tests/smoke.mjs` and
structurally by `tests/render-smoke.mjs` (the DOM shim has no layout — real
layout is on the browser checklist below).

### Phone (≤620px)

- **No horizontal overflow anywhere.** Wide data (staff/labs/prescriptions
  tables, AI Markdown answer tables) scrolls horizontally inside labelled,
  focusable scroll containers (`.table-scroll`, `.ai-md-table-scroll`); at
  ≤620px `.table-stack` turns data tables into labelled cards
  (`td::before { content: attr(data-label) }`). Every auto-fit grid clamps its
  tracks with `minmax(min(<track>, 100%), 1fr)` (`minmax(min(` in
  styles.css) so a 320px viewport can never overflow a track minimum, even
  nested inside padded panels at 200% zoom.
- **Fluid display type.** `--text-2xl`/`--text-3xl` use `clamp()` between the
  phone and desktop values so headlines scale down gracefully instead of
  overflowing at 360px.
- **Long strings wrap.** Bulgarian compound names
  ("Александър-Константин Димитров-Стоянов") and long MRNs wrap via
  `overflow-wrap: anywhere` on every name/MRN surface (`.patient-card__name`,
  `.patient-header__main h1`, `.user-chip__name`, `.cell-stack__primary`,
  `.command-menu__option-title/-hint`, `.patient-header__meta .mono`) plus the
  generic `.text-break` utility (applied to the patient heading in
  `patient.js`). Truncation is reserved for one-line meta rows
  (`.ai-history__question`).
- **Thumb reach & targets.** Primary actions stay where the thumb is (AI
  composer at the card bottom, list/toolbars inline); every small/icon control
  carries `.hit-slop` (≥44px effective target without visual change).
- **Safe areas.** All pages declare `viewport-fit=cover`; the topbar,
  composer, footer, nav, toast region, and the bottom sheets
  (`.dialog`/`.command-menu` at ≤620px) pad with `env(safe-area-inset-*)` so
  nothing hides under the notch or home indicator. The ≤960px safe-triangle
  left gutter (≥22px) keeps hit areas out of the back-swipe edge.

### TV / large displays (≥1600px, 4K step ≥2560px)

- **Root-token scaling only.** Each step is a single `:root` override of
  `--text-*`, `--space-*`, `--container-max`, `--sidebar-width`, and
  `--focus-width` — no per-element magic numbers, so every component scales
  coherently (cards, tables, pills, chat surfaces, charts). The content column
  grows 75rem → 94rem (~1500px) → 120rem (~1920px) so cards/tables use more of
  the display while prose keeps readable line lengths (62ch caps on `.lede`,
  `.state-panel__text`).
- **Focus at distance.** `--focus-width` (3px base → 5px → 6px) drives every
  focus ring (`:focus-visible`, scroll regions, disclosures, status regions),
  keeping outlines thick enough to see from a couch.

### Reduced (320px, 200% zoom, reduced-motion)

- 320px/zoom reflow is the same no-overflow contract as phone (WCAG 1.4.10):
  grids clamp with `minmax(min(`, long text wraps, controls never clip.
- `prefers-reduced-motion: reduce` kills `animation`, `transition`, and
  `scroll-behavior` globally (`!important`): the heart pulse freezes to a
  static image, view transitions are instant, sheets snap, toasts pop. The one
  dynamic style write (`--sheet-dy`, sheet.js) still
  tracks the finger 1:1; only its settle transition is removed.

### Keyboard / remote parity (also the TV remote model)

- Everything is operable by Tab/arrows/Enter/Esc: command palette (type
  filters, ↑/↓ move, Enter runs, Esc closes), ARIA tabs (←/→/Home/End),
  native `<dialog>` focus trapping + Esc, buttons/links everywhere.
- The palette follows its selection: after each arrow move, `renderList()`
  calls `scrollIntoView({ block: 'nearest', inline: 'nearest' })` on the **new**
  active option (`#command-menu-option-<index>`), so long filtered lists keep
  the highlight visible without scrolling when it is already in view (instant,
  reduced-motion-safe). Covered by the render-smoke arrow-follow scenario
  (domshim records every `scrollIntoView` call).
- Every horizontal/vertical scroll container is a labelled, focusable region
  (`role="region"`, `aria-label`, `tabindex="0"`): the app's `.table-scroll`
  containers, the AI answer `.ai-md-table-scroll` (constant attributes only —
  the untrusted renderer never takes attribute names/values from answer text),
  and the `.ai-chat` conversation regions. Arrow keys/page keys scroll a
  focused region on keyboards and TV remotes.
- No mouse-only affordances: hover-intent prefetch has a focus-intent twin
  (`focusin`, prefetch.js); palette patient entries warm on arrow-focus too
  (`warmActiveCommand`); hover styles are decorative-only; swipe-to-dismiss
  sheets always have Esc/Back equivalents.

### Browser checklist (what tests cannot verify)

Real-device passes the lead should run: **heart loader sweep** (pulse rhythm
feel — the lub-dub must read gentle, not frantic; heart at sm/md/lg sizes in
compact rows, list/section loads, and the LLM answer region; both themes — the
transparent PNG must have no white box and the `--heart-glow` shadow must look
intentional on light *and* dark canvases; reduced-motion = static heart);
320/360/430px overflow sweep on every
page (incl. the long-name seed patient with the extra-long MRN); 200% zoom reflow (WCAG 1.4.10);
1920/4K token steps + focus-ring thickness at viewing distance; notch/home
indicator insets with `viewport-fit=cover`; reduced-motion pass (static heart,
no pulse, instant transitions); keyboard/remote walk of palette, tabs, dialogs,
and scroll regions.

## 10. Motion system (2026-11-07)

Shared tokens in `:root`: `--motion-fast: 160ms`, `--motion-base: 200ms`,
`--motion-exit: 160ms` (80% of base), `--motion-stagger: 30ms`,
`--ease-out` (entrances), `--ease-in-out` (moves/settles). Only transform,
opacity and filter are animated; the one layout transition is the mobile nav
disclosure (`grid-template-rows`), isolated to that control. The global
`prefers-reduced-motion` rule disables all animation/transition and view
transitions.

| Principle | Implementation |
|---|---|
| Ease-out entrances | `toast-in`, `overlay-in`, `command-menu-in`, `sheet-in`, `blur-in`, `row-in`, `settle-in` |
| Drop-downs 150–250ms | Mobile `#site-nav` disclosure at 200ms; command palette at 200ms. Native `<select>` options are browser-owned and not animated. |
| 30ms list stagger | `.patient-cards`, `.table tbody tr`, `.lab-chart`, `.bar-row`, `.kpi-tile` (six steps, then capped at 180ms) |
| Press scale 0.97 | `.btn`, `.tab`, `.chip--action`, `.ai-send`; large patient cards use 0.985 |
| 12% knob stretch | Existing heart loader beat (`scale(1.12)`) and Graphify point focus/hover (`scale(1.12)`) |
| Details settle | `.confirm-details > *`, `.ai-answer details[open] > *` |
| Arcs for object movement | Deliberately not added: there is no free-moving object, and the only drag surface (sheets) must track the finger vertically. An arc would break direct manipulation. |
| Success pulse | `.toast--success` dot pulse; admin restore actions use `tone: 'success'` |
| Ease-in-out moves | Tab underline, sheet settle, view-transition exits |
| Popovers start at 0.95 | `overlay-in` and `command-menu-in` start at `scale(0.95)` |
| Enter from trigger | Command palette enters from the topbar with `transform-origin: top center`; mobile nav from its top edge; sheets from the bottom |
| Exit to trigger | Palette view-transition old snapshot exits toward the top; sheets/toasts exit downward |
| No bounce | Only ease-out/ease-in-out curves; no overshoot in everyday UI |
| No tween while held | `:active` controls set `transition-duration: 0ms`; sheet drag sets `transition: none` |
| Exits 20% shorter | `--motion-exit` for panels/sheets; toast exit 144ms vs 180ms entrance |
| Overlap old/new | Explicit `vt-record-panels` old/new view-transition animations |
| Masks for reveals | Active tab underline uses `mask-image` + `scaleX` reveal |
| Icon crossfade + blur | Password show/hide icons crossfade with `opacity`/`filter: blur(3px)` |
| Rubber band | `sheet.js` applies diminishing resistance above the sheet's resting position, then snaps back |
| Reuse same layout | Animations are transform/opacity/filter only; shared tokens/keyframes; no panel markup swap |

## 11. Appointments and voice scheduling (2026-11-09)

**Page:** `appointments.html` + `appointments.js`, sharing the existing shell,
command palette, tokens, and dialog/sheet primitives. The nav item appears only
with `appointment:read`; the New/Cancel controls only with
`appointment:write`; Availability only with
`appointment:manage_availability`. Hiding is UX only — the backend returns 403
for a nurse and for a doctor naming another doctor.

**Calendar views.** Month (6×7 grid, Monday-first), week (seven day columns),
day (timeline + working hours + available-slot buttons), and upcoming (next 30
days grouped by clinic-local date). The month/week grids scroll horizontally
under a labelled scroll region on narrow screens; the day/upcoming lists stack.
Every appointment chip/row opens the same detail dialog. Month cells show at
most three chips plus a `+N more` control that switches to day view.

**Clinic time.** Every display uses `Intl.DateTimeFormat` with
`timeZone: 'Europe/Sofia'`, not the browser's zone. Date keys are handled as
calendar dates (UTC-noon objects) so month arithmetic is DST-safe. Form input
wall time is converted to UTC with a round-trip offset check before it is sent;
an impossible/ambiguous local time surfaces the backend's controlled error.

**Cancelled entries.** Cancellation retains the scheduling row and audit
record, but the calendar is a working view: a cancelled entry remains visible
for **5 minutes** after cancellation and is then omitted from list responses
and from the open page by a local timer. An **Admin** also gets **Remove from
calendar** in a cancelled appointment's detail dialog (confirmed action); this
sets `calendar_removed_at` and hides it immediately. The record still exists —
the detail endpoint can return it, and audit history is unchanged. Doctors can
cancel but do not get the immediate admin removal action.

**Doctor scope.** A doctor sees only their own calendar and no doctor picker.
An Admin sees a persistent doctor selector; appointment list/slots/
availability/interpret requests carry `doctor_id`. The selected doctor is shown
in the header context line. A **Nurse** gets the read-only clinic-wide calendar
(`appointment:read` only): the same month/week/day/upcoming views and detail
dialog, but New/Reschedule/Cancel, Availability, the voice panel, the doctor
picker, and booking slots are absent. The header says “Read-only clinic
calendar”. The backend independently enforces every write as `403`.

**Manual scheduling.** The form combines a bounded patient search
(`/patients?q=&limit=8`), a date input, live slot buttons, duration, visit
type, and notes. The visit type is a 19-entry catalogue
(`visit-types.js`) with a suggested duration per type; choosing a type updates
the duration field. Choosing **Other** reveals a required custom visit-type
input, stored in the existing bounded `appointment_type` string. Editing
(reschedule) reuses the form and highlights the current start even though the
slots endpoint marks it `booked`. Buttons stay disabled while a request is in
flight; `409` shows the server's conflict message and refreshes slots.
Creation is a normal explicit `POST /appointments`.

**Voice panel.** Progressive enhancement, never a requirement:
- `getUserMedia`/`MediaRecorder` are feature-detected; if unavailable the
  panel says so and the manual transcript textarea remains.
- A single explicit Start/Stop button, a 60-second auto-stop, and a visible
  timer. Tracks are stopped on stop/clear/page-hide. There is no background
  listening and no audio object URL.
- Audio stays in a memory-only Blob until the user presses **Transcribe**.
  Upload/retry states are plain status text; there is no persistent recording.
- The transcript textarea is editable and capped at 2000 chars; manual typing
  enables the same **Propose appointment** action.
- The proposal card is explicitly labelled **AI-generated** and shows patient
  (or candidate buttons), date/time, duration, type, notes, missing fields, and
  warnings. Its action is **Review & create**, which only prefills the normal
  form. The final **Create appointment** button is the explicit confirmation;
  the proposal itself never writes.

**Empty/error states.** No doctors (Admin), no slots, no upcoming
appointments, past dates, unavailable speech service, 403, 429, 409, and
network failures all have honest states. The page never invents availability or
patient matches.

**CSP and accessibility.** The page uses no inline scripts, styles, or event
handlers; the only inline script is the hashed `__APP_CONFIG__` bootstrap.
All controls are native buttons/inputs with labels; view buttons use
`aria-pressed`; calendar cells expose readable appointment names; status text
uses polite live regions; dialogs use the existing focus-trapping
`ui.openDialog`. Motion is inherited from the shared system and disabled under
`prefers-reduced-motion`.

**Not verified here:** real microphone permission prompts, codec selection,
and actual recording/transcription accuracy require a browser and a running
whisper.cpp service; the automated suite covers DOM flows with a stubbed
speech service and tests FFmpeg conversion separately.

## 12. Doctor portraits and footer placement (2026-11-09)

- Doctor identities use one of four local demo portraits in
  `assets/images/doctors/` (`avatarForUser` is gated on the `doctor` role).
  The top-bar identity, dashboard session card, admin staff rows, and
  appointment detail doctor row show the portrait; nurses/admins keep the
  initials avatar. The files are placeholder demo images (see the folder
  README); replace them with approved staff photos before real use.
- The repeated full-width `.site-footer` bar was removed from every page. The
  About / Report an issue links remain in the bottom-left sidebar help card;
  the landing page keeps a compact, left-aligned footer outside the hero card
  rather than a full-width bar.

## 13. Theme system — Light / Dark / System (2026-11-09)

**Selection.** `data-theme="light|dark"` is set on `<html>`; `data-theme-mode`
keeps the literal choice (`light`, `dark`, or `system`). The classic
`assets/js/theme-init.js` runs synchronously in `<head>` **before the
stylesheet**, reads `localStorage["medicore-theme"]`, resolves System against
`prefers-color-scheme`, and applies the attributes pre-paint. Default with no
stored value is **System**.

**Switcher.** `theme.js` mounts a three-button segmented control
(`Light` / `Dark` / `System`) with SVG icons, `aria-label`, native `title`
tooltips, `aria-pressed`, and keyboard focus. It is injected into the top-bar
user area on every authenticated page and into the landing header. Clicking a
mode updates the root attributes instantly, persists the choice, and re-renders
the pressed state. While in System, a `matchMedia('(prefers-color-scheme: dark)')`
listener updates the theme live when the OS changes. The only stored value is
the non-clinical preference key `medicore-theme`.

**Tokens.** Existing variable names remain unchanged; `:root` is the Light set,
`:root[data-theme='dark']` is the Dark set, and a
`@media (prefers-color-scheme: dark) { :root:not([data-theme]) { … } }` block is
the no-JS/system fallback. Key values:

| Token | Light | Dark |
|---|---|---|
| `--color-canvas` | `#f8fafc` | `#0b1220` |
| `--color-canvas-alt` | `#f1f5f9` | `#111a2b` |
| `--color-surface` | `#ffffff` | `#131c2e` |
| `--color-text` | `#0f172a` | `#f8fafc` |
| `--color-text-body` | `#334155` | `#cbd5e1` |
| `--color-text-muted` | `#526173` | `#a8b8cc` |
| `--color-border-strong` | `#cbd5e1` | `#465a78` |
| `--color-danger-text` | `#b91c1c` | `#fca5a5` |
| `--color-warn-text` | `#b45309` | `#fcd34d` |
| `--color-action` | `#2563eb` | `#2563eb` |
| `--color-action-contrast` | `#ffffff` | `#ffffff` |

Primary action buttons use the same accessible blue (`--color-action`) in both
themes. `color-scheme` follows the applied theme so native date/time controls
and scrollbars match.

**Readability.** Secondary/placeholder text uses the raised muted token; the
Voice scheduling status is a left-border callout with tone variants
(info/success/warn/error), the capture timer is a high-contrast pill, and the
transcript textarea has a larger line-height, padding, and minimum height.
`tests/smoke.mjs` computes WCAG contrast for primary/body/muted/error/warn/
action pairs in both token sets (AA minimum, primary text 7:1).

**Landing exception.** The landing hero keeps its dark brand treatment, but the
sign-in card, inputs, placeholders, helper text, secure/note copy, and footer
switch to the Light token surface when Light is selected.

**No-JS behavior.** Without JavaScript, the media-query fallback follows the OS.
With JavaScript, the stored/system choice wins and updates live.

## 14. Error states and custom 404 (2026-11-09)

- Missing browser navigations serve `404.html` with HTTP **404** (not the old
  index fallback). API/JSON clients keep the frozen `404 not_found` envelope.
- The 404 page uses the same theme bootstrap, tokens, brand, theme switcher,
  About dialog, and compact bottom-left footer treatment as the rest of the app.
- Two local SVG illustrations share the MediCore palette:
  - `assets/images/error-404.svg` — medical chart/clipboard with a missing page.
  - `assets/images/error-connection.svg` — medical monitor with a disconnected
    heartbeat.
- `ui.renderErrorState` shows the 404 illustration for `404`, the connection
  illustration for network failures (`status 0`) and 5xx responses, and no
  illustration for authorization/validation errors. The art sits beside the
  message on wide screens and stacks on narrow screens; it is decorative
  (`alt=""`/`aria-hidden`) because the text carries the meaning.
- Images are local, CSP-safe (`img-src 'self'`), and theme-aware through the
  surrounding token surfaces.

## 15. Analytics dashboard (2026-11-09)

- The admin-only analytics page now has a shared **Reporting period** card:
  Last 7 / 30 / 90 days, This year, and Custom range. Presets apply on change;
  Custom applies on **Apply**; **Reset** returns to the 30-day default. The
  selected period and generated-at timestamp stay visible. Invalid or
  reversed custom dates are explained inline and never fetch.
- One `GET /api/v1/analytics/summary` request per filter change feeds every
  section. The request carries explicit inclusive UTC `start`/`end` dates so
  the browser and server agree on the window. Clinical/audit daily series are
  UTC; appointment day/hour/weekday series use the clinic timezone because
  schedules are local. Appointment trend may include one partial local edge
  day and always sums to the appointment total.
- Sections: range-aware KPI cards with previous-period comparisons; patients
  and encounters; appointments (status donut, volume/completed/cancelled/
  no-show trend, visit type, weekday, busiest hours, recorded-hours
  utilization); laboratory classification/denominator/trends; prescriptions;
  Access & Security Activity; AI & Voice Assistant; Data Quality & System
  Health; service configuration; compact staff accounts.
- Visual rules: KPI cards for headline values, line charts for time series,
  horizontal bars for comparisons, and a small status donut for the closed
  appointment-status vocabulary. Charts reuse `graphify.js` or the same local
  SVG conventions (`role="img"`, `aria-label`, visually-hidden data table).
  Geometry and donut dash lengths are attributes, never inline styles.
- Honesty rules: abnormal percentages show their evaluable denominator;
  unclassified results are never normal; prior-period percentage is null when
  the previous period has no events; AI latency, voice metrics and API error
  rates are shown as **Not available** with their reasons because the source
  data is not persisted. Provider availability is marked **not monitored**.
- Security: the page remains Admin-only in navigation and the backend answers
  `403` for Doctor/Nurse direct API calls. No patient identifiers, raw audit
  metadata, questions/answers, audio or transcripts are rendered. No
  third-party analytics or trackers.
- Responsive/accessibility: filter controls wrap, panels collapse to one
  column under 720px, charts scale with the card, hidden tables mirror every
  chart, focusable bar/line marks keep keyboard readouts, and existing
  reduced-motion/token/contrast rules apply. Empty, loading and error states
  are explicit.
- CSV/print analytics was deferred: it needs its own authorization, audit and
  aggregate-redaction review rather than reusing the patient print path.

## 16. Internal messages (redesigned 2026-11-11)

- The **Messages** sidebar item appears for Doctor/Admin only, after AI
  Assistant and before Admin. Nurse has no navigation item and receives a
  friendly denial on a direct page visit; every message endpoint re-checks
  `message:read`/`message:write`.
- The Messages content area is a dark, two-pane professional inbox: tabs with
  real unread counts, a server-backed search field, compact message cards with
  initials, subject, preview, relative timestamp and unread dot, plus a spacious
  reading panel with sender/recipient header, full timestamp, safe pre-wrapped
  body and Reply/New message actions.
- The header count (`N messages in your inbox`, `N unread`) comes from the
  caller-private `GET /messages/counts` endpoint; search uses bounded
  server-side `q` filtering and never downloads the mailbox for browser search.
- Selecting an inbox message marks it read through the recipient-only endpoint.
  On mobile the list becomes the first view and selection opens a full-width
  reading view with a Back to inbox action; desktop keeps both panes.
- **New message** opens a compose dialog with active Doctor/Admin recipients
  including role labels. The caller cannot message themselves; subject is
  optional (≤200 chars), body is required (≤5000 chars). No attachments,
  external delivery, or patient payload fields. Errors preserve the compose
  input and sending is guarded against duplicate submission.
- Only the caller's own inbox/sent items are returned. Message subjects/bodies
  are never written to the audit trail; audit records only sender/recipient
  metadata and the message resource id.

## 17. Patient creation (2026-11-09)

- The Patients page header exposes **Add patient** only when the signed-in
  payload includes `patient:write` (Doctor/Admin; Nurse never receives it).
  The command palette on that page also offers **Add patient** under the same
  permission gate.
- The modal form collects given name, family name, optional date of birth,
  sex (closed vocabulary) and optional blood type (closed ABO/Rh vocabulary).
  Names are free UTF-8 text; validation strips whitespace and requires both
  name parts. The date cannot be in the future. Input is preserved if the
  request fails.
- The backend assigns the MRN (`MRN-…`) and always stores provenance
  `synthetic`; neither is trusted from the client. On success the browser opens
  the new patient's record (`patient.html?id=…`).
- Creation is a normal CSRF-protected, audited `POST /api/v1/patients`
  (`patient.create`); the backend re-checks `patient:write` regardless of the
  button visibility.
- The patient header exposes **Remove patient** only to Admin
  (`patient:delete`). It confirms with the patient name/identifier, performs a
  soft delete through `DELETE /api/v1/patients/{id}`, clears patient caches and
  returns to the patient list. Doctor/Nurse never see the control; the backend
  enforces the permission. The row and audit history remain internally.
- The appointment patient picker reuses the same shared form inline. When a
  search returns no matches and the signed-in Doctor/Admin holds
  `patient:write`, it offers **Add new patient**; creation selects the new
  patient in the open appointment form without closing or losing the chosen
  date/time. Nurse never sees the fallback.

## 18. System Logs chat (2026-11-10)

- **Role and tone:** Admin-only monitoring page, presented as a normal
  messaging-style group conversation. It uses the app's light/dark tokens
  rather than a fixed terminal theme; severity colors remain legible through
  bordered bubbles and badges.
- **Status first:** connection badge is Connected / Reconnecting /
  Disconnected, with last-connected and last-event times. It never says LIVE
  while disconnected. Counters are backend values for the whole selected
  filtered period, not the visible rows; the note above the chat says so.
- **Message:** incoming chat bubble with a local sender avatar (doctor portrait
  when the demo directory has one, otherwise colored initials), sender name and
  role, event type, description, timestamp with milliseconds in
  `CLINIC_TIMEZONE`, severity badge, and safe metadata chips. INFO is
  neutral/blue, SUCCESS green, WARNING amber, ERROR red, SECURITY violet.
  Events without an actor render as centered system notices. Each message is a
  focusable button that opens a safe field/metadata detail dialog; values are
  inserted as text, never HTML.
- **Controls:** severity/category/period/custom-range/actor/event-type/request
  ID/outcome filters and metadata search; Pause/Resume; Auto-scroll; Copy
  visible; bounded CSV export; Clear View. Manual upward scrolling disables
  forced follow and shows “N new events — Resume”. Clear View only clears the
  browser display; it never deletes backend records.
- **Reading order and motion:** DOM events are inserted chronologically by
  `(timestamp, event_id)`, keeping equal-timestamp order deterministic, with
  day separators and restrained arrival animation. There is no looping
  animation; the feed respects reduced motion by design.
- **Responsive/accessibility:** controls wrap, the chat feed becomes the scroll
  region with a visible focus outline, message bubbles are keyboard-operable,
  and the empty state is announced through the status region. Export
  neutralizes spreadsheet formulas in text fields.
- **Hard boundary:** read-only. No command input, shell, SQL console, file or
  environment browser exists, and the footer states the boundary explicitly.


## 19. Patient vitals and observed outcomes (2026-11-11)

- **Summary additions:** the summary now opens with Last visit, latest Vitals
  (weight, BP, heart rate, SpO2), and an “Observed treatment response” preview
  alongside the existing problems/labs/medications blocks. A response shows a
  medication, baseline → current value, delta and an Improved/Worsened/Stable/
  Monitoring/Awaiting-data badge.
- **Outcomes tab:** a new patient-record tab combines BP, weight and heart-rate
  line charts with the full observed-response list and recent visits. Charts
  reuse Graphify’s accessible line primitive: legend toggles, keyboard-focusable
  points, readout, and a visually hidden table. The tab loads only when opened.
- **Clinical wording:** values are derived from recorded synthetic labs and
  vitals. The UI says “observed change” and explicitly avoids claiming that a
  medication caused the change or that it is treatment advice.
- **Data growth:** the seed gains six additional visits per patient, repeated
  key-lab panels, explicit baseline/follow-up points around mapped
  prescriptions, and per-visit BP/HR/temperature/SpO2. Existing seeded rows are
  preserved; new rows use a separate longitudinal ID namespace.
