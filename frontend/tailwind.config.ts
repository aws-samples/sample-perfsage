import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        perfsage: {
          bg: "#0f172a",
          card: "#1e293b",
          border: "#334155",
          accent: "#3b82f6",
          success: "#22c55e",
          error: "#ef4444",
          warning: "#f59e0b",
        },
      },
    },
  },
  plugins: [],
};

export default config;
