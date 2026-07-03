import type { Config } from 'tailwindcss'

export default {
  content: ['./frontend/renderer/index.html', './frontend/renderer/**/*.{ts,tsx}'],
  theme: {
    extend: {}
  },
  plugins: []
} satisfies Config
