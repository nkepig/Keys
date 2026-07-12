# Key Console Design System

## 1. Atmosphere & Identity

A quiet utility surface that gets out of the way. The signature is strict information restraint: one upload action and one two-column usage list.

## 2. Color

| Role | Token | Value | Usage |
|---|---|---|---|
| Canvas | `--canvas` | `#f7f7f5` | Page background |
| Surface | `--surface` | `#ffffff` | Input and usage list |
| Text | `--text` | `#20201e` | Primary copy |
| Muted | `--muted` | `#77746f` | Helper text |
| Border | `--border` | `#e5e3df` | Quiet separation |
| Accent | `--accent` | `#1769d2` | Primary action and focus |
| Success | `--success` | `#237a42` | Upload confirmation |
| Error | `--error` | `#b54435` | Actionable errors |

## 3. Typography

- Primary: system sans-serif with Chinese platform fallbacks.
- Mono: platform monospace for masked keys and numeric usage.
- H1: 24px / 650 / 1.25. Body: 14px / 400 / 1.6. Labels: 13px / 500.

## 4. Spacing & Layout

- Base unit: 4px. Content width: 720px.
- Page padding: 24px mobile, 40px desktop.
- Vertical sections: 40px. Controls: 12px gap.

## 5. Components

### Multiline upload
- **Structure**: visible label, textarea, single primary button, inline feedback.
- **States**: default, focus, disabled/loading, success, error.
- **Accessibility**: native label association, visible focus, 44px button target.

### Usage list
- **Structure**: heading and a two-column table (`Key`, `用量`).
- **States**: loading, empty, error, populated.
- **Accessibility**: semantic table with numeric alignment and masked keys.

## 6. Motion & Interaction

- 150ms color and border transitions only. Spinner rotation is the sole continuous motion.
- Respect `prefers-reduced-motion`.

## 7. Depth & Surface

- Borders-only. No shadows, gradients, decorative icons, badges, cards, filters, pagination controls, or metadata.
