/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./app/templates/**/*.html', './app/static/js/**/*.js'],
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
        ground: 'rgb(var(--color-ground) / <alpha-value>)',
        surface: 'rgb(var(--color-surface) / <alpha-value>)',
        ink: 'rgb(var(--color-ink) / <alpha-value>)',
        'ink-muted': 'rgb(var(--color-ink-muted) / <alpha-value>)',
        line: 'rgb(var(--color-line) / <alpha-value>)',
        accent: 'rgb(var(--color-accent) / <alpha-value>)',
        'accent-ink': 'rgb(var(--color-accent-ink) / <alpha-value>)',
        // The stamped mark: allergen declarations and batch marks share one
        // ink, so a compliance surface reads as part of the label language.
        stamp: 'rgb(var(--color-stamp) / <alpha-value>)',
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
