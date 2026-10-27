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
| `--color-teal` / `--color-teal-strong` | `#0f766e` / `#0d9488` | teal accents, avatars, demo pills |
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
  `badge--ok/warn/alert/neutral/info/synthetic` (status + provenance) and
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
  avatar, bold name, `MRN: …` (mono), `Born <date> • <sex>` meta, "Synthetic
  demo data" provenance badge, chevron. Whole card is the patient link.
- **Patient header card** — large initials avatar, name (`h1`), provenance pill,
  `MRN:` + working **Copy MRN** button (real clipboard API with an honest
  fallback message), meta line.
- **Icon tabs** — Summary | Labs | Prescriptions | Encounters with blue
  underline; full ARIA tabs pattern (roving tabindex, arrow keys, Home/End).
- **Tables** — staff/labs/prescriptions/audit; scroll container + horizontal
  scroll shadow, card stacking under 620px via `data-label`.
- **AI assistant rail** — gradient header (sparkles, "AI Assistant",
  "INFORMATIONAL ONLY" pill), subtitle disclaimer, chat bubbles (user right/gray,
  assistant left/white with badges, sources chips, citations, model + timestamp),
  composer with send button, warning-triangle disclaimer footer. Sticky on wide
  screens, moves below the panels under 1100px.
- **AI Assistant page** (`ai.html`, nav item "AI Assistant" with the sparkles
  icon, shown only with `patient:read`) — the general-lookup companion to the
  patient rail: same `ai-card` vocabulary at page scale (gradient header +
  "INFORMATIONAL ONLY" pill, subtitle disclaimer, warning-triangle footer) with
  a chat transcript (`#ai-response-region`) that keeps the conversation of the
  current page view, suggested-question chips that fill the composer (plain
  text shortcuts — real queries, no automation), and an "Ask about a patient"
  link to the real patient list (`patients.html` → `patient.html?id=…`; a
  detail page needs a concrete record id, so the list is the honest entry).
  Composer: send + cancel + 2000-char counter. All rail states kept: loading,
  cancel, 503 unavailable, 429 countdown, `describeApiError` errors incl. the
  network/offline banner. It posts `{ question }` (general lookup mode — no
  `patient_id`); the rail keeps posting `{ patient_id, question }` unchanged.
  Citation links: `patient` citations always link to `patient.html?id=…`;
  `encounter`/`lab_result`/`prescription` citations link to the patient record
  only when the same answer cites exactly one patient (unambiguous
  attribution), otherwise they stay plain record references — never invented.
  Questions/answers live only in the page DOM, never in web storage or logs.
- **Dialogs** — head icon + title, optional `confirm-details` definition rows
  (the AdminPanel confirmation modal), Cancel + solid confirm; one-time password
  modal with the "shown once" warning; About dialog. All via the existing
  `ui.confirmDialog` / `ui.openDialog` / `ui.infoDialog`.
- **Pagination** — "Showing X–Y of Z" + prev/next chevrons (visually-hidden
  labels) + numbered page circles.
- **States** — skeleton loading rows, empty/error state panels, global banner
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
| Patient header (DEMO PATIENT pill, MRN copy, meta) | patient header card | see "omitted" for right-side meta fields |
| Tabs Summary/Labs/Prescriptions/Encounters | real tabbed panels backed by the real sections of the summary/labs/prescriptions data | |
| Labs table (value, unit, ref range, status, collected) | labs table; reference range from `ref_low`/`ref_high`, status badge from the API `status` field | status text is the raw API value, never invented "Normal/High" labels |
| AI Assistant rail | right rail over the existing `patient.js` AI flow | all current states kept: loading, cancel, 503 unavailable, 429 rate limit, errors, citations, disclaimer, permission gating |
| Sidebar "AI Assistant" nav item (`doctorpanel1.png`) | `ai.html` + `assets/js/ai-assistant.js`, nav item derived from `patient:read` | the mockup's "AI Insights" tab and quick-action tile are the same surface; only this one nav item is real |
| Staff management table + role pills + confirmation modal + pagination | admin staff table (avatar + name/email stack, role pills, Active/Inactive, actions), `ui.confirmDialog` with detail rows, page circles | |
| TechAdmin audit log (mockup label; `tech_admin` was removed as a role) | admin "Audit events" table (when/actor/action/resource/outcome/request id) with coloured outcome badges | |

### Deliberately NOT built from the mockups (and why)

- **Demo data banner** ("Synthetic demo data — not for clinical use" header
  banner in `doctorpanel1.png`) — the owner removed the persistent header banner
  (PLAN.md, 2026-10-26). It is not re-added. Disclosure remains in the footer,
  the About dialog, per-record provenance badges, meta descriptions, the login
  "what this is / is not" rows and the dashboard disclosure card.
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
  records. Cards show real fields only (name, MRN, date of birth, sex,
  provenance).
- **Patient header right-side meta fields (PCP, insurance, last encounter,
  "View Profile")** — not returned by the API (deliberate synthetic-data
  minimalism); only real fields render.
- **Lab value colouring / "High ↑" arrows** — the API `status` field is a result
  status (e.g. `final`), not an interpretation; deriving "High/Low" would invent
  data. The badge shows the real status, tinted by its category.
- **Admin toolbar search box, role/status dropdown filters, "3 columns" toggle,
  date picker, notifications bell, "Clear Demo Data"** — the staff API accepts
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
- Breakpoints: 960px (sidebar collapse + mobile nav), 1100px (AI rail moves
  below the panels), 620px (tables stack into cards), 480px (detail grids
  stack). Verified down to 360px by code review of the grid/flex rules —
  pixel-level fidelity to the mockups is reviewed against the PNGs, not
  screenshot-tested (no browser in the build environment).

## 6. UI/UX pattern library (owner list items 1–20)

Everything below lives in the same buildless system: `styles.css` classes and
small ES modules (`ui.js`, `cache.js`, `toast.js`, `command-menu.js`). No
inline `style` attributes, no `el.style.*` or `setProperty()` writes — dynamic
appearance always goes through predefined classes/attributes (enforced by
`tests/smoke.mjs`).

### 1. Skeleton loaders
One shimmer component (`.skeleton-block`, `.skeleton-row`, `.skeleton-card`,
`.skeleton-bar--lg/md/sm/circle`) plus `ui.renderLoading(container,
{ label, rows, variant, wrapperTag })`. `variant: 'rows'` approximates table
rows; `variant: 'cards'` approximates patient cards (avatar circle + name/meta
bars). Under `prefers-reduced-motion` the shimmer sweep is dropped and static
grey blocks remain. The skeleton is announced (`role="status"`) and replaced
atomically by the real renderer. Covered async regions: patient list cards,
patient detail sections (demographics/summary/labs/prescriptions/encounters),
AI conversation history (both the page and the patient rail), the staff table,
the audit table, and dashboard recent patients. There is no analytics surface
in the demo (no endpoint exists), so there is no analytics placeholder to cover.

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
"Summarize this patient", "Ask AI about this patient", the rail suggestion
chips; AI page: the suggestion chips fill the composer), and a real
"Keyboard shortcuts" entry. Fuzzy-ish filtering (subsequence scoring), typed
filter, ↑/↓ + Enter + Esc, `aria-activedescendant` listbox semantics, empty
state "No matches", focus restored to the opener on close. At mobile widths
(≤620px) the palette becomes a full-width sheet.

### 4. Shortcut hints (kbd chips)
`.kbd` chips render **real** keybindings only: the ⌘K chip in the topbar
"Search & commands" trigger (every app-shell page), the ⌘K/Ctrl+K note in the
Patients search hint, and the composer hints (`Enter` / `Shift+Enter`) on the
AI page and the patient rail. The command-menu footer shows the compact
"↑↓ navigate · ↵ open · esc close" hint; the "Keyboard shortcuts" command
lists every keybinding in the demo (nothing invented).

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
working content with an error state. First visits are unchanged (skeleton →
data, or the existing error/empty states). Applied to: patients list, patient
detail sections, staff list, audit list, and both AI history lists. Note:
because the app is multi-page (full loads), the cache cannot survive a real
cross-page navigation — the win is within-page re-visits; a cross-page cache
would require web storage, which is banned for clinical data.

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
site footer and the toast region (bottom) so nothing sits under notches or
home indicators.

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
The demo has no stat-card surface (no metrics endpoint), so there are no
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
