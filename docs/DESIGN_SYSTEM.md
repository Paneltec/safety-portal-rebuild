# Design system

Approved by the owner from the design preview on 15 November 2026. Original to this build; nothing is copied from the previous screens. Tokens live in `frontend/src/styles.css`.

## Direction

"Road-sign utilitarian": a dark graphite shell, a pale cool working surface, one strong signal colour for the main action, and condensed signage-style headings over plain body text. Dense but calm, for office administrators and field workers.

## Colour

| Token | Value | Use |
|---|---|---|
| `--ink` | `#16222E` | Menu, phone headers, grand-total rows |
| `--ground` | `#F3F5F7` | Page background |
| `--surface` | `#FFFFFF` | Cards, tables, inputs |
| `--text` / `--muted` | `#17212B` / `#4A5967` | Body text / secondary text (both pass 4.5:1 on white and on ground) |
| `--accent` | `#C2410C` | The one primary action per screen, active menu marker |
| `--link` | `#1B4B8F` | Links, informational chips, subtotal rows |
| ok / warn / bad | `#0B5D4A` / `#7A4A00` / `#9F1D12` on tinted backgrounds | Status |

The accent is a single token, so the organisation can change it. Payroll screens will use a teal accent (`#0F766E`) inside the same shell.

## Rules

- Status is never colour alone: every chip has a word, and an icon where it signals attention.
- Subtotal and total rows are labelled in words and stay readable in monochrome exports.
- Primary touch targets are at least 44px on phone screens; office controls are at least 40px.
- Every input has a visible label. Focus is always visible.
- Tables scroll sideways inside their card on narrow screens; the page itself does not.
- Empty, loading, denied, error and retry states are designed, not left blank. "No data" and "server unavailable" look different.
- A simulator or held message is labelled as such and never shown as a live success.
- Organisation name and logo come from settings. No host or platform branding is seeded.

## Type

Inter for text; Barlow Semi Condensed (600/700) for headings and large figures. Both are bundled with the app. Numbers in tables use tabular figures and right alignment.

## Dates and numbers

Dates display as `dd/mm/yyyy` and times in the organisation's time zone (default Australia/Hobart). Money is AUD with exact decimal arithmetic on the server.
