import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  // 相对路径：Vercel（根域名）和 GitHub Pages（子路径 /bishe/）都能用
  base: './',
  plugins: [vue()],
})
