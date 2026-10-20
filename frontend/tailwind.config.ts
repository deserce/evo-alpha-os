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
        // 品牌色
        brand: {
          blue: '#2f7dff',
          'blue-light': '#56d8ff',
          'blue-dark': '#1d4ed8',
        },

        // 功能色
        success: '#28e070',    // 涨/盈利/看多（绿）
        danger: '#ff4f6d',     // 跌/亏损/看空（红）
        warning: '#ffb020',    // 警告（黄）
        info: '#8ea0bd',       // 中性（灰蓝）

        // 背景色
        bg: {
          primary: '#020713',       // 页面背景
          secondary: '#07111f',     // 卡片背景
          tertiary: '#0d1a2c',      // 输入框背景
          hover: '#12233a',         // 悬停背景
        },

        // 文本色
        text: {
          primary: '#d8e4f7',     // 主要文本
          secondary: '#8ea0bd',   // 次要文本
          tertiary: '#5e718f',    // 辅助文本
          disabled: '#3e526f',    // 禁用文本
        },

        // 边框色
        border: {
          primary: '#18304d',   // 主边框
          secondary: '#10233a', // 次边框
          accent: '#2f7dff',    // 强调边框
        },

        // 股票涨跌色（金融特有）
        up: '#ff4f6d',          // 上涨/涨停（红）
        down: '#28e070',        // 下跌/跌停（绿）
        flat: '#8ea0bd',        // 平盘（灰）
      },

      fontSize: {
        'xs': ['10px', { lineHeight: '14px' }],
        'sm': ['11px', { lineHeight: '16px' }],
        'base': ['12px', { lineHeight: '18px' }],
        'md': ['13px', { lineHeight: '20px' }],
        'lg': ['14px', { lineHeight: '22px' }],
        'xl': ['16px', { lineHeight: '24px' }],
        '2xl': ['20px', { lineHeight: '28px' }],
        '3xl': ['24px', { lineHeight: '32px' }],
        '4xl': ['32px', { lineHeight: '40px' }],
      },

      fontWeight: {
        'light': '300',
        'normal': '400',
        'medium': '500',
        'bold': '600',
        'black': '700',
      },

      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
        mono: ['SF Mono', 'Monaco', 'Cascadia Code', 'Roboto Mono', 'monospace'],
      },

      spacing: {
        '0': '0',
        '1': '4px',
        '2': '8px',
        '3': '12px',
        '4': '16px',
        '5': '20px',
        '6': '24px',
        '8': '32px',
        '10': '40px',
        '12': '48px',
        '16': '64px',
      },

      borderRadius: {
        'sm': '4px',
        'md': '6px',
        'lg': '8px',
        'xl': '12px',
        '2xl': '16px',
        'full': '9999px',
      },

      boxShadow: {
        'sm': '0 1px 2px rgba(0, 0, 0, 0.05)',
        'md': '0 4px 6px rgba(0, 0, 0, 0.1)',
        'lg': '0 10px 15px rgba(0, 0, 0, 0.1)',
        'xl': '0 20px 25px rgba(0, 0, 0, 0.15)',
      },

      screens: {
        'sm': '640px',
        'md': '768px',
        'lg': '1024px',
        'xl': '1280px',
        '2xl': '1536px',
        '3xl': '1920px',
      },

      maxWidth: {
        '8xl': '88rem',
        '9xl': '96rem',
        '10xl': '128rem',
        'screen-xl': '1920px',
      },

      backgroundImage: {
        "gradient-radial": "radial-gradient(var(--tw-gradient-stops))",
        "gradient-conic": "conic-gradient(from 180deg at 50% 50%, var(--tw-gradient-stops))",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
