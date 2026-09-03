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
        surface: 'rgb(var(--color-surface) / <alpha-value>)',
        raised: 'rgb(var(--color-raised) / <alpha-value>)',
        ink: 'rgb(var(--color-ink) / <alpha-value>)',
        muted: 'rgb(var(--color-muted) / <alpha-value>)',
        line: 'rgb(var(--color-line) / <alpha-value>)',
        accent: 'rgb(var(--color-accent) / <alpha-value>)',
        'accent-ink': 'rgb(var(--color-accent-ink) / <alpha-value>)',
        // Allergen chips are a compliance surface: high contrast, not decoration.
        'allergen-contains': 'rgb(var(--color-allergen-contains) / <alpha-value>)',
        'allergen-may': 'rgb(var(--color-allergen-may) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
      minHeight: { touch: '44px' },
      minWidth: { touch: '44px' },
    },
  },
  plugins: [],
};
