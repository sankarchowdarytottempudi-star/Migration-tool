# UI build prompt — TraceIT · Migration Factory (authoritative)

Design source of truth: `design/migration-factory/` in this repo (Main / Playground / Knowledge /
NewMigration / SignIn `.dc.html` + `canvas.json`). Backend contract: `docs/HANDOFF-TRACEIT.md` §3.
Implement exactly as below.

---

OBJECTIVE
Implement the approved "TraceIT · Migration Factory" UI in React + TypeScript, matching the design reference pixel-for-pixel in Light, Dark and Auto themes. Wire it to the existing Migration Tool backend, then build, test, commit and push to main.

DESIGN SOURCE (source of truth)
The design folder is `design/migration-factory/` (already committed at the repo root). It contains:
- Main.dc.html: workspace overview + API pipeline detail
- Playground.dc.html: API Playground
- Knowledge.dc.html: Knowledge base
- NewMigration.dc.html: 3-step New migration wizard
- SignIn.dc.html: admin token sign-in
- canvas.json: screen index
Each file is HTML with inline styles, a <helmet><style> block holding every design token, class and @keyframes, and a small JS class whose renderVals() holds the screen's data and behaviour. Read all five files before writing code. Port the exact values: colours, spacing, radii, font sizes, copy, keyframes, easings and durations. Do not invent new visuals. Keep the design/ folder as the permanent reference.

STEP 0: DETECT WHERE YOU ARE (decide, don't ask)
- If this repo is TraceIT (pnpm workspace with a Next.js 15 App Router web app and packages/tokens): build the feature inside the TraceIT web app as route group app/(app)/migrations/ with these routes: /migrations (overview), /migrations/playground, /migrations/knowledge, /migrations/new. Use the existing TraceIT sidebar/shell, adding a "Migrations" item with a NEW badge and the four sub-items exactly as in the design. Sign-in stays TraceIT's existing Better Auth. Do not build SignIn.dc.html; reuse only its visual polish if the TraceIT login lacks it.
- Otherwise (this is the Migration Tool repo): create apps/web as Vite + React 19 + TypeScript + React Router, with all five screens including SignIn, served by the existing backend's static hosting or its own container. Keep the old UI reachable at /legacy until this one ships, then delete it in a follow-up commit.
- Either way, the existing codebase's conventions win: package manager, lint config, folder style, and its styling system (if it uses Tailwind, map the tokens below into its theme; if not, use CSS Modules + tokens.css).

DECISIONS ALREADY MADE
- Tokens: create migration-factory/theme/tokens.css with the exact CSS custom properties from the design's .mf block (light) and .mf:not(.t-light) block (dark): --bg --side --surface --surface2 --surface3 --border --border2 --text --muted --faint --accent #B45309 --accentHover --accentText --accentSoft --accentGlow --ok --okSoft --warn --warnSoft --bad --badSoft --sys/--proc/--exp (+Soft) --term --termLine --shadow. Scope them by [data-theme="light"|"dark"] on <html>. If TraceIT's packages/tokens exists, add these as a "migrations" theme extension there instead of duplicating its shared values.
- Theme: ThemeProvider with mode "light"|"dark"|"auto", persisted to localStorage key tw-theme. Auto follows prefers-color-scheme live, via a matchMedia change listener. Put an inline pre-hydration script in the document head to set data-theme, so the wrong theme never flashes. Theme switches use document.startViewTransition with the circular clip-path reveal from the design (::view-transition-new(root) → mfReveal, 0.6s, cubic-bezier(.16,1,.3,1)). Fall back to a plain 0.45s colour crossfade.
- Fonts: Inter (UI), Manrope 600/700/800 (h1, h2, big numbers, logo wordmark), JetBrains Mono (ids, paths, logs, code). Self-host them with @fontsource packages. NO Google Fonts CDN: TraceIT runs on closed shipboard networks.
- Icons: lucide-react, stroke 1.8–2.2, matching the design's inline SVGs. The TraceIT logo is the wave path "M3 18c3-9 6-12 9-6s6 3 9-6" in var(--accent), drawn on load (stroke-dasharray 40 animation), next to the Manrope 800 wordmark "Trace" + "IT" in accent. Make it one <TraceItLogo/> component so the official SVG can be dropped in later.
- Motion: use the "motion" package (Framer Motion) for page/section entrance (opacity + y16 + scale .985 + blur 4px → none, 0.7s, ease [.16,1,.3,1], 60ms stagger), the tab underline and sidebar active bar (shared layoutId, spring), the accordion open/close (height auto) and list re-ordering on filter. Use CSS keyframes for loops: live ping, spinner, shimmer, caret blink. Port these exactly from the design: mfGrow for progress bars, mfRing for the completion ring, the primary-button light sweep (::before), card hover lift (-4px + shadow), API item hover (translateX 3px), and the KPI count-up (rAF, 1.4s, easeOutQuart). Everything must honour prefers-reduced-motion: disable all animation and jump count-ups to their final value.
- Data: TanStack Query. Build a typed API client in migration-factory/api/ against the EXISTING backend endpoints (see docs/HANDOFF-TRACEIT.md §3): APIs list, API detail and phases, run docs+scaffold, run tests, deploy, delete, documents download, feedback submit, knowledge base list, playground proxy, admin token check. Do NOT change any backend endpoint or response shape. Poll the API detail every 2s while any step is "running", otherwise every 30s. If the backend exposes SSE/websocket progress, use that instead of polling.
- Status derivation (pure functions, unit-tested): phase state = done | warn (complete but has a skipped or warning step) | run | pend, exactly as in Main.dc.html renderVals(). An API whose phases are all complete but has skipped gates shows the amber "Deployed with N quality gates skipped" banner with a "Review gates" action that opens the Pipeline tab and expands Testing + Deployment. Show deployment step warnings as "Done · warning" chips with a one-line clean summary, never raw docker/kubectl output; the raw log goes behind a "View log" disclosure.
- Security (fix now): remove every hard-coded token from the frontend, including the Playground's "Bearer e2e-secret-token-123" default. The Playground Authorization header shows a masked reference (Bearer ••••) and is injected server-side by the existing playground proxy, from server config/env. (The backend already supports this via MT_DEFAULT_BEARER; keep the request contract.) Keep the admin token in sessionStorage, never localStorage, and clear it on Lock.
- Destructive action: Delete sits apart from Run tests / Deploy and opens a confirm dialog that requires typing the API name. Never use window.confirm.
- Accessibility: real <button>/<a>/<label>. Tabs use role="tablist" with arrow-key navigation. Accordions use aria-expanded. Icon-only buttons get aria-label. Focus ring is 3px var(--accent). Status is always icon + word, never colour alone. Text contrast ≥ 4.5:1 in both themes.
- Responsive: sidebar 264px. Below 760px it collapses into a top bar with a sheet menu. Content max-width 1440px. The pipeline stepper scrolls horizontally on small screens. Tables scroll inside their own box.
- States: every data view gets skeletons (shimmer), empty states (copy from the design), error states with retry, and optimistic feedback on actions (button spinner + toast "Tests started", etc.).

SCREENS (match the design files)
1. Overview (Main.dc.html): page header (Workspace eyebrow, Manrope h1, subtitle, "Pipeline workers online" ping, New migration primary). 4 KPI cards with count-up. API list card: filter input, All / Deployed / In progress chips, API items with layer badge, animated bar, status and %. Detail card: breadcrumb, name + status pill, meta chips (service id, layer, source→target, effort), completion ring, actions row, gates banner, endpoint grid (copy button showing a "Copied" state for 1.6s, base path, NodePort, Try it → Playground with that API preselected). Tabs: Pipeline (7-node stepper + accordion phases with step rows), Documents (5 cards; Download disabled until generated), Reviewer feedback (3-way scope segmented control with the help text, form, empty state), Activity (terminal log, colour-coded tags, blinking caret; streams new lines).
2. API Playground (Playground.dc.html): deployed API list, method select colour-coded by verb, path input with base prefix, Send with loading state, Headers/Body/Params tabs, headers table with Source column (Secret / Preset / Lesson; correlationId auto-generated per request), body editor with line numbers, response panel showing real status, time, size, pretty JSON with syntax colouring, and a copy button.
3. Knowledge base (Knowledge.dc.html): 4-step learning-loop strip, scope chips (All / Functional / Workspace / API) with counts, sections with lesson cards (scope badge, Applied state, mistakes list, "Applies to", Edit lesson) and empty states.
4. New migration (NewMigration.dc.html): 3-step wizard with an animated progress rail. Step 1: drag-and-drop .zip, upload progress, technology auto-detected via the backend. Step 2: display name, service id, layer cards, target technology, switches for "Run documents + scaffold immediately" and "Apply Knowledge base lessons" (live count from the API). Step 3: summary grid + 7-phase preview. "Start migration" creates the migration and routes to its Overview detail.
5. Sign in (SignIn.dc.html, standalone mode only): split layout, wave logo draw, phase checklist animation, token field with show/hide, Verifying… state, inline error "That token was not accepted…", theme switch.

ACCEPTANCE CRITERIA
- Build, lint and typecheck pass with zero errors.
- Unit tests (Vitest + Testing Library) cover: ThemeProvider (light/dark/auto + system change), phase/gate status derivation, API client parsing, the Playground masks tokens, Delete requires typed confirmation.
- Playwright smoke test opens every route in light AND dark, asserts there are no console errors, and runs @axe-core/playwright with zero serious/critical violations. Under reduced motion, no element has a running animation.
- No hard-coded secrets: grep for "Bearer " and "token-" in src returns nothing.
- Visual parity: for each route, take a 1440px screenshot in both themes into runs/migration-ui/ and compare it against the design file opened in a browser. Fix any spacing, colour or type drift before committing.
- No calls to fonts.googleapis.com or any CDN at runtime.

RULES FOR THIS REPO
Merge origin/main if needed. Never force-push and never rebase. Do not deploy, do not touch production data, and do not change backend API contracts. Pushing to main is the end of this task; deployment stays owner-only.

Work fully autonomously. Do not ask questions. Where something is ambiguous, make the industry-standard choice consistent with the decisions above and the design files, and note it in the commit message. Run build, lint, typecheck, unit tests and Playwright. If anything fails, fix it and re-run until everything is green. Then commit with conventional-commit messages (feat(migrations): …) and push to main. Report back only: which mode you detected, what was built, the decisions you took, test results (counts), the screenshot paths, and the commit hash(es).
