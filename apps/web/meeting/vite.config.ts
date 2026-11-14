import { readFileSync } from 'node:fs'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

/**
 * demo/验证阶段允许把 LiveKit key pair 放进 public/config.json。
 * 默认构建遇到 client 模式的 secret 时直接失败；demo 构建必须显式
 * ALLOW_CLIENT_TOKEN=1，避免把 secret 无意打进 dist。
 */
function guardClientTokenConfig(): Plugin {
  return {
    name: 'guard-client-token-config',
    apply: 'build',
    buildStart() {
      const url = new URL('./public/config.json', import.meta.url)
      try {
        const cfg = JSON.parse(readFileSync(url, 'utf8')) as {
          tokenMode?: string
          apiKey?: string
          apiSecret?: string
        }
        if (!cfg.apiSecret) return
        if (cfg.tokenMode === 'server') {
          this.error('tokenMode=server must not contain apiKey/apiSecret.')
        }
        if (process.env.ALLOW_CLIENT_TOKEN === '1') {
          this.warn('Building with an embedded LiveKit API secret (demo only).')
          return
        }
        this.error(
          'public/config.json contains a client-mode API secret. ' +
            'Set ALLOW_CLIENT_TOKEN=1 to build the demo bundle, or remove the file.',
        )
      } catch (err) {
        if ((err as { code?: string }).code === 'ENOENT') return
        throw err
      }
    },
  }
}

export default defineConfig({
  base: '/meeting/',
  plugins: [react(), guardClientTokenConfig()],
  server: {
    port: 5174,
    // 不需要 /api 代理：token 在浏览器生成，LiveKit 走 WSS 直连
  },
  build: {
    outDir: 'dist',
    // Huawei Browser 14 is Chromium 99. Without an explicit CSS target,
    // Lightning CSS minifies media queries to Media Queries Level 4 range
    // syntax (e.g. `(width<=640px)`), which Chromium 99 parses as `not all`
    // and silently drops. Keep CSS output compatible with our oldest
    // supported engine.
    cssTarget: ['chrome99', 'edge99', 'firefox115', 'safari16.4', 'ios16.4'],
  },
})
