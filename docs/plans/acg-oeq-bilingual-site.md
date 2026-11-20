# Bilingual site (English + Vietnamese) Implementation Plan

> **For agentic workers:** Use subagents when appropriate to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `site/index.html` shows English or Vietnamese, picked with a header switcher, in one file.

**Architecture:** Every user-visible string becomes a pair of adjacent `<span lang="en">` / `<span lang="vi">`
inside the existing element (no duplicated `li`, `p`, `details` or anchors: the CSS keys on `:last-child` and the
test counts the install links). CSS hides the inactive language, keyed on `<html lang>`; the switcher buttons
carry `data-lang` and are exempt. Attributes (`alt`, `aria-label`, meta description) carry `data-vi-<attr>` and the
script swaps them; `document.title` and the script's own strings come from a small table. A head one-liner applies
the saved choice before first paint. No JS: English, switcher hidden. `tools/make_site.py` is unchanged.

**Tech Stack:** static HTML/CSS/JS, `tools/make_site.py` tokens, plain `unittest`.

---

### Task 1: Page

**Files:** Modify `site/index.html`

- [ ] CSS: `html[lang="vi"] [lang="en"]:not([data-lang]), html[lang="en"] [lang="vi"]:not([data-lang]) { display: none; }`,
      switcher styles; header wraps on a phone.
- [ ] Head: `try` read of `localStorage['ambientcg-browser-lang']`, only `en`/`vi`, sets `<html lang>`.
- [ ] Switcher in the header nav (`hidden` until the script runs): English | Tiếng Việt, `aria-pressed`.
- [ ] Vietnamese pair for every string: skip link, nav, hero, meta list, drag button, hint, unofficial notice,
      figcaptions, install cards, warning, Using it, cards, troubleshooting, About, footer. Blender UI labels and
      the add-on's English messages stay in English.
- [ ] `data-vi-alt` on both images, `data-vi-aria-label` on the nav and the release list, `data-vi-content` on the
      meta description.
- [ ] Script: `setLang` (html lang, title, attributes, `aria-pressed`, save in try/catch); hint and Copy button
      write each language's span instead of `textContent` of the whole element.

### Task 2: Test

**Files:** Modify `tests/test_site.py`

- [ ] `test_the_page_is_in_english_and_vietnamese`: the built page has a Vietnamese string, both switcher buttons,
      no `{{`/`}}`, and as many `<span lang="en">` as `<span lang="vi">`.

## Verification

- `python -I -m unittest discover tests` → OK.
- `powershell -ExecutionPolicy Bypass -File build.ps1` → `Built ... index.html`.
- Serve `dist` with `python -m http.server <free port> --directory dist` (started detached, stopped by PID).
  In a browser: no visible text outside the active language except names/tokens/URLs; Vietnamese survives a
  reload (lang, title, alt); screenshots at ~360 px and desktop width.
- Commit: `feat: make the landing page bilingual, English and Vietnamese`.
