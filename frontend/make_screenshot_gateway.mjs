/**
 * 重拍「模型网关配置」页面截图，去掉内部信息。
 *
 * 原图 docs/shot-gateway.png 把三样东西拍进了像素里，文本替换修不掉：
 *   1. 网关地址输入框里的内部域名；
 *   2. 底部「配置文件位置」里的本机绝对路径（含 Windows 账号名）；
 *   3. 该路径里的内部构建目录名。
 *
 * 做法：登录后打开该页面，把地址输入框改成占位值（只改显示，绝不点保存），
 * 再用与页面同色的遮罩盖住底部路径行。截图覆盖 docs/shot-gateway.png。
 *
 * 用 Node 而非 Python 写，是因为 playwright 已经是前端的 devDependency，
 * 不必为一个截图脚本再往 backend/requirements.txt 里加依赖。
 *
 * 用法（在 frontend 目录下）：
 *     node ../scripts/make_screenshot_gateway.mjs
 * 前置：backend 跑在 127.0.0.1:8000，前端已构建。
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')

const BASE = process.argv[2] || 'http://127.0.0.1:8000'
const OUT = process.argv[3] || resolve(ROOT, 'docs/shot-gateway.png')
const PLACEHOLDER_URL = 'https://your-gateway.example.com'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1500, height: 980 } })

try {
  // ---------- 登录 ----------
  await page.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
  await page.evaluate(() => localStorage.clear())
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  await page.locator('input[autocomplete=username]').fill('admin')
  await page.locator('input[autocomplete=current-password]').fill('123456')
  await page.locator('button', { hasText: '登录' }).last().click()
  await page.waitForTimeout(3000)

  // ---------- 阈值与参数 → 模型网关 ----------
  await page.goto(`${BASE}/#/settings`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(2000)
  await page.locator('button.tab', { hasText: '模型网关' }).click()
  await page.waitForTimeout(1500)

  // 1) 替换网关地址的显示值。
  //    这里只改 DOM，绝不点「保存并生效」—— 那会把占位地址写进真实 .env。
  const urlMasked = await page.evaluate((ph) => {
    const inputs = [...document.querySelectorAll('input.input')]
    const box = inputs.find((i) => i.type !== 'password' && /https?:/.test(i.value))
    if (!box) return false
    box.value = ph
    return true
  }, PLACEHOLDER_URL)

  if (!urlMasked) {
    console.error('  [错误] 没找到已填值的网关地址输入框，未生成截图。')
    process.exitCode = 3
  } else {
    // 2) 盖住底部「配置文件位置」行 —— 它含本机绝对路径与账号名，
    //    比域名更直接，必须一并遮掉。
    const pathMasked = await page.evaluate(() => {
      // 文本是「配置文件位置：<span>…</span><br>令牌签名密钥…」，
      // 落在 <div class="tiny muted"> 里。必须精确选中这个容器：
      // 若退而匹配任意 div，外层大容器的 textContent 也含这段文字，
      // 会把整页盖住（实测就是这样，截图变成纯黑色）。
      // 所以先找 .tiny，再校验它确实只承载这行提示。
      const line = [...document.querySelectorAll('div.tiny, div.muted')].find((el) => {
        const t = el.textContent || ''
        return /配置文件位置/.test(t) && t.length < 200
      })
      if (!line) return false
      const r = line.getBoundingClientRect()
      if (r.width < 20 || r.height < 8 || r.height > 120) return false
      const cover = document.createElement('div')
      cover.style.cssText = [
        'position:fixed',
        `left:${r.left - 4}px`,
        `top:${r.top - 4}px`,
        `width:${r.width + 8}px`,
        `height:${r.height + 8}px`,
        'background:var(--bg-0,#fff)',
        'z-index:9999',
      ].join(';')
      document.body.appendChild(cover)
      return true
    })

    if (!pathMasked) {
      console.error('  [错误] 没找到「配置文件位置」行，未生成截图。')
      console.error('  页面结构可能已变，请更新选择器后重试。')
      process.exitCode = 3
    } else {
      await page.waitForTimeout(400)
      mkdirSync(dirname(OUT), { recursive: true })
      await page.screenshot({ path: OUT })
      console.log(`  已重拍：${OUT}`)
      console.log(`    网关地址显示为占位值 ${PLACEHOLDER_URL}`)
      console.log('    配置文件路径行已遮盖')
    }
  }
} catch (e) {
  console.error(`  [失败] ${e?.message || e}`)
  process.exitCode = 1
} finally {
  await browser.close()
}
