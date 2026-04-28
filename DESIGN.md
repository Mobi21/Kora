---
version: alpha
name: Kora Life OS
description: "A calm, local-first Electron app for believable day planning, reality confirmation, day repair, and shame-safe life management."
colors:
  primary: "#2F6F68"
  on-primary: "#FFFFFF"
  primary-container: "#D8F0EA"
  on-primary-container: "#123B36"
  secondary: "#3E5F8A"
  on-secondary: "#FFFFFF"
  secondary-container: "#DFE9F7"
  on-secondary-container: "#172D48"
  tertiary: "#7A5A8E"
  on-tertiary: "#FFFFFF"
  tertiary-container: "#F0E3F6"
  on-tertiary-container: "#352042"
  background: "#F7F4EE"
  on-background: "#22211F"
  surface: "#FFFCF6"
  surface-dim: "#EEE8DD"
  surface-bright: "#FFFFFF"
  surface-container-lowest: "#FFFFFF"
  surface-container-low: "#FBF7F0"
  surface-container: "#F4EEE5"
  surface-container-high: "#ECE4D8"
  surface-container-highest: "#E2D8CA"
  on-surface: "#22211F"
  on-surface-variant: "#625D54"
  outline: "#8C8376"
  outline-variant: "#D0C6B8"
  inverse-surface: "#302E2B"
  inverse-on-surface: "#F5EFE6"
  surface-tint: "#2F6F68"
  success: "#2E7D5B"
  on-success: "#FFFFFF"
  success-container: "#D9F0E2"
  on-success-container: "#123A29"
  warning: "#9A6517"
  on-warning: "#FFFFFF"
  warning-container: "#F7E4BF"
  on-warning-container: "#432B06"
  attention: "#B35A3C"
  on-attention: "#FFFFFF"
  attention-container: "#F8DED5"
  on-attention-container: "#4A1E11"
  quiet: "#6F7885"
  on-quiet: "#FFFFFF"
  quiet-container: "#E5E9EF"
  on-quiet-container: "#242A32"
  stabilization: "#5B4E7A"
  on-stabilization: "#FFFFFF"
  stabilization-container: "#E8E0F3"
  on-stabilization-container: "#2A203D"
  error: "#B3261E"
  on-error: "#FFFFFF"
  error-container: "#F9DEDC"
  on-error-container: "#410E0B"
  focus-ring: "#3E7E78"
typography:
  display-lg:
    fontFamily: "Inter"
    fontSize: 40px
    fontWeight: 650
    lineHeight: 48px
    letterSpacing: 0em
  headline-lg:
    fontFamily: "Inter"
    fontSize: 30px
    fontWeight: 650
    lineHeight: 38px
    letterSpacing: 0em
  headline-md:
    fontFamily: "Inter"
    fontSize: 24px
    fontWeight: 650
    lineHeight: 32px
    letterSpacing: 0em
  title-lg:
    fontFamily: "Inter"
    fontSize: 20px
    fontWeight: 620
    lineHeight: 28px
    letterSpacing: 0em
  title-md:
    fontFamily: "Inter"
    fontSize: 17px
    fontWeight: 620
    lineHeight: 24px
    letterSpacing: 0em
  body-lg:
    fontFamily: "Inter"
    fontSize: 17px
    fontWeight: 400
    lineHeight: 28px
    letterSpacing: 0em
  body-md:
    fontFamily: "Inter"
    fontSize: 15px
    fontWeight: 400
    lineHeight: 24px
    letterSpacing: 0em
  body-sm:
    fontFamily: "Inter"
    fontSize: 13px
    fontWeight: 400
    lineHeight: 20px
    letterSpacing: 0em
  label-lg:
    fontFamily: "Inter"
    fontSize: 14px
    fontWeight: 650
    lineHeight: 20px
    letterSpacing: 0em
  label-md:
    fontFamily: "Inter"
    fontSize: 12px
    fontWeight: 650
    lineHeight: 16px
    letterSpacing: 0.02em
  label-sm:
    fontFamily: "Inter"
    fontSize: 11px
    fontWeight: 650
    lineHeight: 14px
    letterSpacing: 0.03em
  data-md:
    fontFamily: "SF Mono"
    fontSize: 13px
    fontWeight: 500
    lineHeight: 18px
    letterSpacing: 0em
rounded:
  none: 0px
  xs: 3px
  sm: 6px
  DEFAULT: 8px
  md: 10px
  lg: 12px
  xl: 16px
  full: 9999px
spacing:
  unit: 4px
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
  xxl: 32px
  xxxl: 48px
  desktop-margin: 24px
  desktop-gutter: 16px
  panel-padding: 16px
  card-padding: 14px
  control-height-sm: 32px
  control-height-md: 40px
  control-height-lg: 48px
  sidebar-width: 280px
  agenda-width: 520px
  inspector-width: 360px
shadows:
  none: "0 0 0 rgba(0, 0, 0, 0)"
  hairline: "0 0 0 1px rgba(34, 33, 31, 0.06)"
  sm: "0 1px 2px rgba(34, 33, 31, 0.06)"
  md: "0 8px 24px rgba(34, 33, 31, 0.08)"
  lg: "0 18px 48px rgba(34, 33, 31, 0.10)"
  focus: "0 0 0 3px rgba(47, 111, 104, 0.24)"
elevation:
  level-0:
    backgroundColor: "{colors.background}"
    shadow: "{shadows.none}"
  level-1:
    backgroundColor: "{colors.surface}"
    shadow: "{shadows.hairline}"
  level-2:
    backgroundColor: "{colors.surface-container-low}"
    shadow: "{shadows.sm}"
  level-3:
    backgroundColor: "{colors.surface-container-lowest}"
    shadow: "{shadows.md}"
  level-4:
    backgroundColor: "{colors.surface-container-lowest}"
    shadow: "{shadows.lg}"
motion:
  instant: "80ms ease-out"
  fast: "140ms ease-out"
  base: "200ms ease-out"
  calm: "280ms cubic-bezier(0.2, 0, 0, 1)"
  repair: "360ms cubic-bezier(0.2, 0, 0, 1)"
  reduced: "0ms linear"
components:
  app-shell:
    backgroundColor: "{colors.background}"
    textColor: "{colors.on-background}"
    typography: "{typography.body-md}"
    rounded: "{rounded.none}"
    padding: "{spacing.desktop-margin}"
  navigation-sidebar:
    backgroundColor: "{colors.surface-container}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.lg}"
    padding: "{spacing.lg}"
    width: "{spacing.sidebar-width}"
  main-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.lg}"
    padding: "{spacing.panel-padding}"
  agenda-card:
    backgroundColor: "{colors.surface-container-lowest}"
    textColor: "{colors.on-surface}"
    typography: "{typography.body-md}"
    rounded: "{rounded.lg}"
    padding: "{spacing.card-padding}"
  agenda-card-active:
    backgroundColor: "{colors.primary-container}"
    textColor: "{colors.on-primary-container}"
    rounded: "{rounded.lg}"
    padding: "{spacing.card-padding}"
  protected-life-card:
    backgroundColor: "{colors.success-container}"
    textColor: "{colors.on-success-container}"
    rounded: "{rounded.lg}"
    padding: "{spacing.card-padding}"
  repair-card:
    backgroundColor: "{colors.warning-container}"
    textColor: "{colors.on-warning-container}"
    rounded: "{rounded.lg}"
    padding: "{spacing.card-padding}"
  stabilization-card:
    backgroundColor: "{colors.stabilization-container}"
    textColor: "{colors.on-stabilization-container}"
    rounded: "{rounded.lg}"
    padding: "{spacing.card-padding}"
  nudge-card:
    backgroundColor: "{colors.secondary-container}"
    textColor: "{colors.on-secondary-container}"
    rounded: "{rounded.lg}"
    padding: "{spacing.lg}"
  chat-user:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.body-md}"
    rounded: "{rounded.xl}"
    padding: "{spacing.md}"
  chat-kora:
    backgroundColor: "{colors.surface-container-low}"
    textColor: "{colors.on-surface}"
    typography: "{typography.body-md}"
    rounded: "{rounded.xl}"
    padding: "{spacing.md}"
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label-lg}"
    rounded: "{rounded.DEFAULT}"
    height: "{spacing.control-height-md}"
    padding: "0 16px"
  button-primary-hover:
    backgroundColor: "{colors.on-primary-container}"
    textColor: "{colors.on-primary}"
  button-secondary:
    backgroundColor: "{colors.surface-container}"
    textColor: "{colors.on-surface}"
    typography: "{typography.label-lg}"
    rounded: "{rounded.DEFAULT}"
    height: "{spacing.control-height-md}"
    padding: "0 14px"
  button-quiet:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.on-surface-variant}"
    typography: "{typography.label-lg}"
    rounded: "{rounded.DEFAULT}"
    height: "{spacing.control-height-md}"
    padding: "0 12px"
  confirmation-chip-done:
    backgroundColor: "{colors.success-container}"
    textColor: "{colors.on-success-container}"
    typography: "{typography.label-md}"
    rounded: "{rounded.full}"
    height: "{spacing.control-height-sm}"
    padding: "0 12px"
  confirmation-chip-partial:
    backgroundColor: "{colors.warning-container}"
    textColor: "{colors.on-warning-container}"
    typography: "{typography.label-md}"
    rounded: "{rounded.full}"
    height: "{spacing.control-height-sm}"
    padding: "0 12px"
  confirmation-chip-blocked:
    backgroundColor: "{colors.attention-container}"
    textColor: "{colors.on-attention-container}"
    typography: "{typography.label-md}"
    rounded: "{rounded.full}"
    height: "{spacing.control-height-sm}"
    padding: "0 12px"
  input-field:
    backgroundColor: "{colors.surface-container-lowest}"
    textColor: "{colors.on-surface}"
    typography: "{typography.body-md}"
    rounded: "{rounded.DEFAULT}"
    height: "{spacing.control-height-lg}"
    padding: "0 14px"
  load-meter-light:
    backgroundColor: "{colors.success-container}"
    textColor: "{colors.on-success-container}"
    typography: "{typography.label-md}"
    rounded: "{rounded.full}"
    padding: "4px 10px"
  load-meter-high:
    backgroundColor: "{colors.warning-container}"
    textColor: "{colors.on-warning-container}"
    typography: "{typography.label-md}"
    rounded: "{rounded.full}"
    padding: "4px 10px"
  load-meter-overloaded:
    backgroundColor: "{colors.attention-container}"
    textColor: "{colors.on-attention-container}"
    typography: "{typography.label-md}"
    rounded: "{rounded.full}"
    padding: "4px 10px"
  load-meter-stabilization:
    backgroundColor: "{colors.stabilization-container}"
    textColor: "{colors.on-stabilization-container}"
    typography: "{typography.label-md}"
    rounded: "{rounded.full}"
    padding: "4px 10px"
---

## Overview

Kora Life OS should feel like a calm desktop companion for a person trying to keep one real day understandable. The visual identity is quiet, grounded, and concrete: more like a trusted local workspace than a productivity dashboard or therapy-branded wellness app.

The core emotional promise is: the day can change without becoming a moral failure. The interface should make plan, reality, repair, and tomorrow handoff visible without turning them into scores, streaks, or performance theater. It should support executive dysfunction, overload, avoidance, sensory load, social load, low energy, and recovery needs with clear structure and low-friction controls.

The target surface is an Electron app. It should feel native to the desktop: steady panels, clear keyboard focus, fast local interactions, conservative animation, visible state changes, and no cloud-product gloss. Kora can be warm and human, but the UI should stay practical and nonintrusive.

## Colors

The palette uses soft paper surfaces, graphite text, and muted support colors. It should avoid harsh white, pure black, neon accents, gamified progress colors, and saturated productivity-dashboard blues.

- **Primary teal (#2F6F68):** Kora's main action color. Use it for the current recommendation, selected state, primary confirmation, and one highest-priority action per region.
- **Secondary blue (#3E5F8A):** Use for preparation, context packs, scheduling, and trust-oriented administrative surfaces.
- **Tertiary violet (#7A5A8E):** Use sparingly for support modes, personal patterns, and reflective or stabilization-adjacent content.
- **Paper neutrals:** Warm off-white and muted stone surfaces create a local, readable workspace that is softer than a white SaaS dashboard.
- **Semantic states:** Success means "confirmed reality," warning means "needs repair," attention means "overload or friction," and stabilization means "scope is intentionally reduced."

Semantic colors must describe the state of the day, not judge the user. Avoid red for ordinary missed tasks. Reserve error colors for destructive failures, safety boundaries, invalid input, or data integrity problems.

## Typography

Use Inter as the default product face because it is readable, neutral, and comfortable in dense desktop surfaces. Typography should be compact enough for daily planning but not cramped. Large display type should be rare and reserved for the current day, current mode, or a major review outcome.

Headlines are semi-bold, not heavy. Body text uses generous line height to make explanations and nudge reasons easy to scan when the user is tired or anxious. Labels are clear and mostly sentence case; avoid shouting. Monospace is reserved for timestamps, local IDs, diagnostics, or audit-style metadata that the user explicitly opens.

The writing style inside the UI should be direct and shame-safe. Prefer "Lunch is unconfirmed" over "You missed lunch." Prefer "The plan is too heavy" over "You are behind."

## Layout

The default desktop layout is a three-zone workspace:

1. A stable navigation or mode rail for Today, Calendar, Review, Settings, and support modes.
2. A central daily agenda where the current believable plan lives.
3. A contextual side panel for Kora's reasoning, nudge details, repair proposals, or "why this changed" explanations.

The central agenda is the product's anchor. It should show must-do obligations, protected life-maintenance items, current load, next best action, open confirmations, and what Kora already moved, deferred, or suppressed.

Use an 8px rhythm with 4px micro-adjustments. Group information by state and action, not by database object. The user should be able to answer these questions at a glance: what matters now, what actually happened, what is uncertain, what changed, and what can wait.

Density should adapt to load. In normal mode, Kora can show a full day with secondary context. In high-load or stabilization mode, reduce visible choices, increase vertical spacing, and foreground only essentials, tiny next actions, and recovery.

## Elevation & Depth

Depth should come from tonal layering, fine outlines, and soft shadows. This is a desktop workspace, not a glassmorphic scene. Use elevation to clarify which layer owns the current decision:

- Base: app background and long-lived workspace areas.
- Level 1: normal panels and agenda regions.
- Level 2: day cards, list items, and quiet nudge cards.
- Level 3: active repair proposals, confirmation prompts, drawers, and focused editor surfaces.
- Level 4: modal decisions that need explicit user consent.

Shadows should be subtle and low-contrast. Avoid dramatic floating cards, blurred backdrops, glowing halos, or decorative gradients. Focus states should be unmistakable and accessible, using the focus-ring color and a non-color affordance where possible.

Motion should be calm and explanatory. Repairs can animate gently from old placement to new placement so the user sees what changed. Avoid celebratory confetti, aggressive bouncing, or animations that imply failure when an item moves, slips, or gets dropped.

## Shapes

Kora uses modest radii. Cards and panels use 10px to 12px corners; buttons and inputs use 8px; small chips use pill shapes only when they behave like compact status or action controls.

The shape language should feel organized and steady. Do not over-round the entire app into toy-like softness. Avoid nested cards. Use full-width sections, list rows, dividers, and panels before reaching for more containers.

## Components

### App Shell

The shell should be stable and local-feeling. Navigation should not compete with the agenda. The app should never open on a marketing hero, empty dashboard, or generic chat-only screen. It should open on the user's current day or a first-run path that produces today's first believable plan quickly.

### Daily Agenda

Agenda items should clearly distinguish hard events, flexible tasks, protected life-maintenance items, support blocks, recovery blocks, and open confirmations. Each item should show its current relationship to reality: planned, started, done, partial, skipped, blocked, moved, cancelled, or unknown.

### Quick Confirmation Controls

Confirmation controls are first-class. Done, started, partial, skipped, blocked, snooze, move, cancel, and no longer matters should be available without forcing a chat turn. These controls must feel like neutral state updates, not productivity scoring.

### Load Meter

The load meter shows burden, not achievement. It should expose the current band and the top reasons behind it. Avoid circular scores, streak metaphors, or fitness-style gamification. The most important action is correction: the user must be able to say the load assessment is wrong.

### Repair Cards

Repair cards explain what changed and why. They should present one recommended repair, the reason trail, affected items, and clear choices: apply, adjust, defer, or reject. Destructive, external, socially visible, or low-confidence repairs must ask first.

### Nudge Cards

Nudges are actionable cards with a visible reason. Every nudge should include the suggested action, snooze, wrong, too much, and stop-this-type controls. A nudge that cannot explain why now should not be visually promoted.

### Chat

Chat remains important, but it should not be the whole product. Kora messages should be narrow, concrete, and action-aware. When the user is scattered, messages should get shorter and offer defaults. Chat bubbles should avoid high contrast unless they are the user's own current message or a critical safety boundary.

### Review

Daily and weekly review screens should separate done, partially done, blocked, unrealistic, rescheduled, dropped, and still important. Reviews should make the plan smarter without making the user feel judged.

## Do's and Don'ts

- Do design around one believable day, not a universal productivity command center.
- Do make plan-vs-reality visible and correctable.
- Do use calm semantic color to show state without blame.
- Do reduce choices and density when load is high.
- Do make Kora's reasoning inspectable: why now, why this, what changed, what was suppressed.
- Do prioritize local-first trust: visible sync, visible memory, visible correction, visible delete/export controls.
- Don't use the old coding-assistant or generic task-manager visual model as the product center.
- Don't use streaks, points, trophies, red missed-task states, or shame-coded completion charts.
- Don't treat empty calendar gaps as automatically usable work time.
- Don't make every surface a card; use panels, rows, sections, and clear hierarchy.
- Don't hide repair actions behind chat-only flows.
- Don't use decorative gradients, glass effects, or oversized hero layouts for the core app.
- Don't silently imply diagnosis. Use user-selected support profiles and support modes.
- Don't make crisis or safety states look like ordinary productivity nudges.
