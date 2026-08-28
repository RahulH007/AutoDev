import type { Config } from "tailwindcss";

/**
 * Monochrome by construction.
 *
 * The console has one ramp and no hue. The semantic scales below (violet,
 * emerald, amber, red, blue) are deliberately redefined onto that same ramp so
 * the pages written before this direction land monochrome without being
 * rewritten — and so no future `text-emerald-400` can quietly reintroduce
 * colour. Status is carried by icon, fill and weight instead; see StatusDot.
 *
 * The ramps are not identical. Failures sit higher on the ramp than successes
 * because in a single-hue system contrast is the only loudness available, and
 * a failed run should still be the thing your eye lands on first.
 */

const ink = {
  0: "#ffffff",
  25: "#fbfbfc",
  50: "#f6f6f8",
  100: "#ededf0",
  200: "#dededf",
  300: "#c3c3cb",
  400: "#9a9aa5",
  500: "#74747f",
  600: "#55555e",
  700: "#3c3c44",
  800: "#26262c",
  850: "#1b1b20",
  900: "#131317",
  925: "#0d0d10",
  950: "#08080a",
};

/** Quiet: informational, no alarm. */
const quiet = {
  200: ink[300],
  300: ink[400],
  400: ink[400],
  500: ink[500],
  600: ink[600],
  700: ink[700],
  800: ink[800],
  900: ink[900],
  950: ink[950],
};

/** Loud: the top of the ramp, reserved for things that need attention. */
const loud = {
  200: ink[50],
  300: ink[100],
  400: ink[200],
  500: ink[400],
  600: ink[500],
  700: ink[600],
  800: ink[700],
  900: ink[800],
  950: ink[900],
};

const config: Config = {
  darkMode: "class",
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        ink,
        brand: quiet,
        // Redefined onto the neutral ramp — see the note above.
        violet: quiet,
        emerald: quiet,
        blue: quiet,
        amber: loud,
        red: loud,
        rose: loud,
        surface: {
          DEFAULT: ink[925],
          50: ink[850],
          100: ink[900],
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        display: ["Archivo", "Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      letterSpacing: {
        tightest: "-0.035em",
      },
      backgroundImage: {
        "gradient-radial": "radial-gradient(var(--tw-gradient-stops))",
        "grid-pattern":
          "linear-gradient(rgba(128,128,140,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(128,128,140,0.06) 1px, transparent 1px)",
      },
      backgroundSize: {
        "grid-sm": "32px 32px",
      },
      animation: {
        "float-slow": "float 8s ease-in-out infinite",
        "float-slower": "float 12s ease-in-out infinite reverse",
        "pulse-glow": "pulseGlow 3s ease-in-out infinite",
        "fade-up": "fadeUp 0.5s cubic-bezier(0.22, 1, 0.36, 1) forwards",
        shimmer: "shimmer 2.5s linear infinite",
        "border-spin": "borderSpin 4s linear infinite",
        "track-sweep": "trackSweep 3.2s ease-in-out infinite",
      },
      keyframes: {
        float: {
          "0%, 100%": { transform: "translateY(0px) scale(1)" },
          "50%": { transform: "translateY(-24px) scale(1.03)" },
        },
        pulseGlow: {
          "0%, 100%": { opacity: "0.4", transform: "scale(1)" },
          "50%": { opacity: "0.7", transform: "scale(1.05)" },
        },
        fadeUp: {
          "0%": { opacity: "0", transform: "translateY(12px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        borderSpin: {
          "0%": { transform: "rotate(0deg)" },
          "100%": { transform: "rotate(360deg)" },
        },
        trackSweep: {
          "0%, 100%": { opacity: "0.28" },
          "50%": { opacity: "0.85" },
        },
      },
      boxShadow: {
        glow: "0 0 40px rgba(0, 0, 0, 0.35)",
        "glow-sm": "0 0 20px rgba(0, 0, 0, 0.25)",
        "glow-blue": "0 0 40px rgba(0, 0, 0, 0.25)",
        card: "0 1px 1px rgba(0,0,0,0.35), 0 4px 14px rgba(0,0,0,0.22)",
        lift: "0 1px 2px rgba(0,0,0,0.28), 0 10px 30px rgba(0,0,0,0.30)",
      },
    },
  },
  plugins: [],
};

export default config;
