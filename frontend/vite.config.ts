import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

declare const process: { env: Record<string, string | undefined> }

// Cloudflare Pages sets CF_PAGES=1. Without VITE_API_URL the built app would call
// http://localhost:8000, so stop the build there instead of shipping a broken site.
if (process.env.CF_PAGES && !process.env.VITE_API_URL) {
  throw new Error('Set VITE_API_URL (the Render API address) in the Cloudflare Pages settings')
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
})
