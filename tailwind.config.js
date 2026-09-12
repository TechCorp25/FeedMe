/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./app/templates/**/*.html', './app/static/js/**/*.js'],
  // Class names the templates compose at render time — `flash--{{ category }}`,
  // `status-stamp--{{ order.status.value }}`, `card--{{ kind }}` — never
  // appear whole in a scanned file, so the extractor cannot see them and the
  // build drops the rules. `.flash--error` was being dropped exactly this
  // way: an error flash shipped styled as an ordinary one. Listing them here
  // is the fix; the alternative is composing the whole class name in Jinja,
  // which moves the same trap one step further from where it is declared.
  safelist: [
    'flash--success',
    'flash--error',
    'flash--info',
    'flash--message',
    'card--component',
    'card--dish',
    'status-stamp--placed',
    'status-stamp--confirmed',
    'status-stamp--prepping',
    'status-stamp--ready',
    'status-stamp--collected',
    'status-stamp--delivered',
    'status-stamp--cancelled',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      screens: {
        // Small-phone tier below Tailwind's defaults (03-FRONTEND.md).
        xs: '360px',
        // Phone rotated: short viewport is a real target, not an edge case.
        landscapeShort: { raw: '(orientation: landscape) and (max-height: 480px)' },
      },
      colors: {
        // Butcher's Label palette. Declared as RGB channels so Tailwind's
        // alpha modifiers keep working; the values themselves live in
        // tailwind.css and are the only place a raw colour appears.
        //
        // The neutrals carry the kraft ground the system is built on. The
        // four hues above them each have one job, so a colour is never
        // decoration: `accent` is what you can do, `saffron` is what it
        // costs, `berry` is a way in to the catalogue, and `stamp` is the
        // allergen declaration. The three signal hues below them say how
        // something went, and are never used for anything else.
        ground: 'rgb(var(--color-ground) / <alpha-value>)',
        surface: 'rgb(var(--color-surface) / <alpha-value>)',
        // A recessed panel: filters, table heads, the bands that group a
        // page without boxing it.
        sunken: 'rgb(var(--color-sunken) / <alpha-value>)',
        ink: 'rgb(var(--color-ink) / <alpha-value>)',
        'ink-muted': 'rgb(var(--color-ink-muted) / <alpha-value>)',
        line: 'rgb(var(--color-line) / <alpha-value>)',
        // Actions, links and the active state.
        accent: 'rgb(var(--color-accent) / <alpha-value>)',
        'accent-ink': 'rgb(var(--color-accent-ink) / <alpha-value>)',
        'accent-soft': 'rgb(var(--color-accent-soft) / <alpha-value>)',
        // Money, and the marks that count something.
        saffron: 'rgb(var(--color-saffron) / <alpha-value>)',
        'saffron-soft': 'rgb(var(--color-saffron-soft) / <alpha-value>)',
        // The menus and the taste vocabulary: how a customer gets in.
        berry: 'rgb(var(--color-berry) / <alpha-value>)',
        'berry-soft': 'rgb(var(--color-berry-soft) / <alpha-value>)',
        // The stamped mark: allergen declarations and batch marks share one
        // ink, so a compliance surface reads as part of the label language.
        // It is the declaration and nothing else — an error now has its own
        // colour, so a failed sign-in can never be mistaken for a warning
        // about what is in the food.
        stamp: 'rgb(var(--color-stamp) / <alpha-value>)',
        'stamp-soft': 'rgb(var(--color-stamp-soft) / <alpha-value>)',
        // Signals. Always paired with the word that says the same thing.
        success: 'rgb(var(--color-success) / <alpha-value>)',
        'success-soft': 'rgb(var(--color-success-soft) / <alpha-value>)',
        danger: 'rgb(var(--color-danger) / <alpha-value>)',
        'danger-soft': 'rgb(var(--color-danger-soft) / <alpha-value>)',
        info: 'rgb(var(--color-info) / <alpha-value>)',
        'info-soft': 'rgb(var(--color-info-soft) / <alpha-value>)',
      },
      fontFamily: {
        // Condensed and stamped: names, headings, labels, buttons only.
        display: ['"Big Shoulders Display"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        // Every run of reading text.
        serif: ['"Source Serif 4"', 'Georgia', 'Cambria', 'serif'],
      },
      fontSize: {
        // Perfect fourth (1.333) from a 16px base: poster-like headings over
        // quieter reading text.
        xs: ['0.75rem', { lineHeight: '1.5' }],
        sm: ['0.875rem', { lineHeight: '1.6' }],
        base: ['1rem', { lineHeight: '1.6' }],
        lg: ['1.333rem', { lineHeight: '1.4' }],
        xl: ['1.777rem', { lineHeight: '1.2' }],
        '2xl': ['2.369rem', { lineHeight: '1.1' }],
        '3xl': ['3.157rem', { lineHeight: '1.05' }],
        '4xl': ['4.209rem', { lineHeight: '1.0' }],
      },
      spacing: { 18: '4.5rem', 22: '5.5rem' },
      // Near-zero throughout: the stamped label is boxy. The pill is the one
      // exception, so a taste chip never reads as an allergen stamp.
      borderRadius: { DEFAULT: '2px', sm: '1px', md: '3px', lg: '4px', pill: '999px' },
      maxWidth: { screen: '1280px' },
      minHeight: { touch: '44px' },
      minWidth: { touch: '44px' },
    },
  },
  plugins: [],
};
