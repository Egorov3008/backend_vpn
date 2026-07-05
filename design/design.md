# User Profile Page — Design Specification

## Project
`/home/admin/platform/backend` — backend for VPN/Telegram service.

## Page Goal
A client-facing profile page where the logged-in Telegram/web user can manage VPN keys and view their referral link.

## Data Sources (backend API)

| Field | Source | Endpoint / Schema |
|---|---|---|
| `tg_id`, `username`, `first_name`, `is_admin`, `is_blocked`, `created_at` | `UserResponse` | `GET /api/v1/users/{tg_id}` (`backend/app/schemas/users.py`) |
| VPN keys list | `KeyDTO` / `KeyDetailResponse` | `GET /api/v1/keys/?tg_id={tg_id}` (`backend/api/v1/keys.py`) |
| referral link | `ReferralLinkDTO` | `GET /api/v1/admin/referrals/links/{tg_id}` (`bot/api/backend_client.py`) |
| extend/delete key | key actions | `POST /api/v1/keys/{email}/renew` / `DELETE /api/v1/keys/{email}` (to be implemented) |

## Layout

### Desktop (1280×900 frame)
1. **Header bar** — 72 px height, glassmorphism background (`backdrop-filter: blur`), bottom border. Avatar with user initial, username, role label.
2. **Page title** — "My Profile".
3. **Subtitle** — "Manage your VPN keys and referral program."
4. **VPN Keys section** — card container with **accordion** items. Each key:
   - **Collapsed**: only key email is visible, with a chevron icon.
   - **Expanded**: progress bar showing time remaining, plus action buttons **"Продлить"** and **"Удалить ключ"**.
5. **Referral Program section** — card with referral link and "Copy link" primary button.

### Mobile (375×900 frame)
Same sections, single column. Accordion buttons stack full-width on narrow screens.

## Visual Style

### Color tokens
Based on UI/UX Pro Max — SaaS General palette (trust blue + orange accent):
- `--background`: Light `#F8FAFC` / Dark `#0F172A`
- `--foreground`: Light `#1E293B` / Dark `#F8FAFC`
- `--card`: Light `#FFFFFF` / Dark `#111827`
- `--primary`: `#2563EB` / Dark `#3B82F6`
- `--primary-foreground`: `#FFFFFF`
- `--secondary`: `#E9EFF8` / Dark `#1E293B`
- `--muted`: `#F1F5F9` / Dark `#1E293B`
- `--muted-foreground`: `#64748B` / Dark `#94A3B8`
- `--border`: `#E2E8F0` / Dark `#334155`
- `--accent`: `#EA580C`
- `--accent-foreground`: `#FFFFFF`
- `--destructive`: `#DC2626` / Dark `#EF4444`
- `--success`: `#DCFCE7` / Dark `#14532D`
- `--success-foreground`: `#166534` / Dark `#86EFAC`

### Typography
- Primary UI font: `Inter`
- Monospace/code font: `JetBrains Mono`
- Page title: 34px/700, letter-spacing -0.02em
- Section title: 22px/700
- Key email: 15px/500, monospace
- Progress label: 13px/600 uppercase
- Button text: 14px/600

### Spacing & Radius
- Page padding: 40px desktop, 24px mobile
- Card radius: 16px (`--radius-m`)
- Inner accordion radius: 12px
- Button radius: 8px
- Accordion gap: 8px

## Components

### Header
```
[U] @username
    Client profile
```
Glassmorphism bar with blur, sticky at top.

### Accordion (collapsed)
```
user_1@example.com                       ▼
```
Only email + chevron.

### Accordion (expanded)
```
user_1@example.com                       ▲
Time remaining        14 days left
[████████████························]
[Продлить]  [Удалить ключ]
```
Progress bar fill is a gradient from primary blue to lighter blue. Buttons: primary orange "Продлить" and destructive outlined "Удалить ключ".

### Referral Card
```
Your referral link
https://t.me/bot?start=ref_ABC123          [Copy link]
```

## Interactions
- Clicking accordion header toggles expand/collapse with smooth `grid-template-rows` animation.
- Chevron rotates 180° when expanded.
- "Продлить" opens payment/extension flow (to be wired).
- "Удалить ключ" triggers confirmation then DELETE request (to be wired).
- "Copy link" copies referral URL to clipboard.
- Responsive breakpoints: 900px (tablet padding), 640px (mobile stack).

## Files
- `/home/admin/platform/backend/design/profile.html` — Live interactive HTML/CSS/JS prototype.
- `/home/admin/platform/backend/design/design.md` — This specification.
- `/home/admin/platform/backend/design/profile_preview_collapsed.png` — Static preview, all accordions collapsed.
- `/home/admin/platform/backend/design/profile_preview_expanded.png` — Static preview, first accordion expanded.

## Live Preview
Run a local server in `/home/admin/platform/backend/design/`:
```bash
python3 -m http.server 8765
```
Open: **http://localhost:8765/profile.html**

## Next Steps
1. Wire accordion actions to backend API endpoints.
2. Replace mock data with real key list from `/api/v1/keys/?tg_id={tg_id}`.
3. Add confirmation modal for "Удалить ключ".
4. Implement "Продлить" flow (tariff selection + payment).
