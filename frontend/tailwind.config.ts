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
        // 自定义金融黑系列
        panel: "#0d1117",     // 稍微亮一点的面板色
        sidebar: "#010409",   // 极深的侧边栏
        border: "#30363d",    // 标准边框色
        accent: {
          blue: "#2f81f7",
          green: "#238636",
          red: "#da3633",
          purple: "#8957e5",
        }
      },
      backgroundImage: {
        "gradient-radial": "radial-gradient(var(--tw-gradient-stops))",
        "gradient-conic": "conic-gradient(from 180deg at 50% 50%, var(--tw-gradient-stops))",
        "glass-gradient": "linear-gradient(180deg, rgba(255, 255, 255, 0.05) 0%, rgba(255, 255, 255, 0) 100%)",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
export default config;