/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src-ui/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'Segoe UI', 'Microsoft YaHei UI', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
