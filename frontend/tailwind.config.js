/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        teal: {
          950: "#123f3d",
          900: "#0b4f50",
        },
        mint: {
          100: "#e8f3ef",
        },
        canvas: "#f7f8f4",
        warm: "#9c3d2c",
      },
    },
  },
  plugins: [],
};
