/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Palette « futuriste premium » : noir aubergine, surfaces presque
        // noires, bordures lavande fines, accents violet néon + bleu électrique.
        // Contraste volontairement faible (ambiance cinématographique).
        ink: "#d9d3e4",
        muted: "#8d8399",
        accent: {
          DEFAULT: "#9d6bff", // violet néon
          soft: "#bb9bff",
        },
        electric: {
          DEFAULT: "#4da3ff", // bleu électrique (accents secondaires)
          soft: "#8ec5ff",
        },
        surface: {
          DEFAULT: "rgba(15, 11, 21, 0.52)",   // presque noir, translucide
          hover: "rgba(157, 107, 255, 0.08)",  // effleurement violet discret
          border: "rgba(196, 181, 253, 0.14)", // lavande, fine
        },
      },
      borderRadius: {
        xl2: "1.1rem",
      },
    },
  },
  plugins: [],
};
