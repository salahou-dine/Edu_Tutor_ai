/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Palette « Zyricon » : violet profond, verre dépoli, accent lumineux.
        ink: "#ece9f4",
        muted: "#9a93ab",
        accent: {
          DEFAULT: "#8b5cf6",
          soft: "#a78bfa",
        },
        surface: {
          DEFAULT: "rgba(255,255,255,0.04)",
          hover: "rgba(255,255,255,0.08)",
          border: "rgba(255,255,255,0.09)",
        },
      },
      borderRadius: {
        xl2: "1.1rem",
      },
    },
  },
  plugins: [],
};
