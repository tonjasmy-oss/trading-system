/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'nofx-gold': {
          DEFAULT: '#F0B90B',
          dim: 'rgba(240, 185, 11, 0.1)',
          glow: 'rgba(240, 185, 11, 0.5)',
          highlight: '#FFD700',
        },
        'nofx-bg': {
          DEFAULT: '#0B0E11',
          deeper: '#050709',
          lighter: '#0E1217',
        },
        'nofx-accent': '#00F0FF',
        'nofx-text': {
          DEFAULT: '#EAECEF',
          main: '#EAECEF',
          muted: '#848E9C',
        },
        'nofx-success': '#0ECB81',
        'nofx-danger': '#F6465D',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui'],
        mono: ['JetBrains Mono', 'Menlo', 'Monaco', 'Courier New', 'monospace'],
      },
      backgroundImage: {
        'grid-pattern': "linear-gradient(to right, #1f2937 1px, transparent 1px), linear-gradient(to bottom, #1f2937 1px, transparent 1px)",
      },
      animation: {
        'pulse-slow': 'pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'float': 'float 6s ease-in-out infinite',
        'shimmer': 'shimmer 2s linear infinite',
      },
      keyframes: {
        float: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-10px)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
      boxShadow: {
        'neon': '0 0 5px #F0B90B, 0 0 20px rgba(240, 185, 11, 0.2)',
        'neon-blue': '0 0 5px #00F0FF, 0 0 20px rgba(0, 240, 255, 0.2)',
      },
    },
  },
  plugins: [],
}