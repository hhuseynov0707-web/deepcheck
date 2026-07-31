# UI Design System: Modern Premium Dark (Shadcn / Linear Style)

This document contains strict visual, structural, and behavioral rules. Claude must follow these guidelines strictly to replace the current heavy-border, toy-like appearance with a high-end, secure, and professional cybersecurity SaaS aesthetic.

---

## 1. Global Visual Restrictions (NEVER DO)
- **NO Heavy Borders:** Never use black lines or border-2/border-4 for layouts.
- **NO Harsh Color Backgrounds:** The background must never be pure white, pure yellow, or colorful.
- **NO Massive Shadows:** No cartoonish solid offset shadows (`shadow-[4px_...]`).
- **NO Sharp 90-Degree Corners:** Do not use `rounded-none`. Use precise, subtle rounding.
- **NO Emojis:** Absolutely zero emojis in the functional UI, status tags, or text.

---

## 2. Core Tailwind Tokens & Classes

### Base Theme & Backgrounds
- **Main App Background:** Deep, dark charcoal. Use `bg-[#09090b]` (Slate 950 variant).
- **Cards & Containers:** Slightly lighter dark surface. Use `bg-[#18181b]` (Zinc 900 variant).
- **Subtle Borders:** Containers must use a very thin, low-contrast border. Use `border border-zinc-800`.

### UI Component Rounding (Subtle & Clean)
- **Buttons, Cards, Inputs:** Must use exactly `rounded-lg` (8px) or `rounded-md` (6px). This creates a polished, engineering-grade finish.

### Color Palette for Security Metrics
- **Primary Text:** Stark white (`text-zinc-50`) for main highlights, titles, and active inputs.
- **Secondary/Muted Text:** Soft silver-gray (`text-zinc-400`) for labels, descriptions, and structural info.
- **Success State (e.g., Gerçek Kullanıcı):** Smooth emerald green accent. Use `bg-emerald-500/10 text-emerald-400 border border-emerald-500/20`.
- **Primary CTA Button:** Dark-mode contrast. Clean white background with deep black text (`bg-zinc-100 text-zinc-900`).

### Typography Hierarchy
- **Headings & Main Labels:** Strict clean sans-serif. Use `font-semibold tracking-tight text-zinc-50`.
- **Data, Response Times, Inputs:** Technical text must be monospaced for precise structural rendering. Use `font-mono text-zinc-300`.

---

## 3. Reference Component Templates

### Premium Primary Button (The "ONAYLA" Button)
```html
<button class="w-full bg-zinc-100 hover:bg-zinc-200 text-zinc-900 font-medium py-3 px-4 rounded-md transition-colors cursor-pointer text-sm tracking-wide font-sans shadow-sm">
  TL 2.038,80 Onayla
</button>
```

### Premium Security Content Card
```html
<div class="bg-[#18181b] border border-zinc-800 rounded-lg p-6 flex flex-col gap-4 shadow-xl">
  <div class="flex items-center justify-between">
    <h3 class="text-lg font-semibold tracking-tight text-zinc-50 uppercase">KART BİLGİLERİ</h3>
    <span class="bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-xs font-mono px-2 py-0.5 rounded-full font-bold">● GERÇEK KULLANICI 13.0</span>
  </div>
  <!-- Inner form items go here -->
</div>
```

### Premium Input Field
```html
<div class="flex flex-col gap-1.5">
  <label class="text-xs font-medium text-zinc-400 uppercase tracking-wider">Kart Numarası</label>
  <input type="text" class="w-full bg-[#09090b] text-zinc-100 border border-zinc-800 rounded-md p-3 font-mono text-sm tracking-widest focus:outline-none focus:border-zinc-700 transition-colors placeholder-zinc-600" placeholder="1234 5678 9012 3456" />
</div>
```

---

## 4. Layout & Interaction Rules
- **Micro-shadow Glow:** To create depth, cards can use a very soft zinc shadow: `shadow-2xl shadow-black/50`.
- **State Changes:** Hovering over fields or secondary actions should trigger a subtle border transition, changing from `border-zinc-800` to `border-zinc-700`. Keep it fast: `duration-200 ease-out`.
