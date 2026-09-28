/**
 * 前端静态检查：找出模板里引用但 setup 中未定义的标识符。
 *
 * 为什么需要它：Vue 模板里用了未定义的变量时，编译期不报错，
 * 只在运行时抛 "xxx is not a function"，并让整块内容消失 ——
 * 这是这次「指派界面整个不见了」的根因，排查花了很久。
 * 把它固化成脚本，以后 npm run build 前跑一次即可提前发现。
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from 'vue/compiler-sfc'
import path from 'node:path'

const BUILTIN = new Set(['$router', '$route', '$emit', '$slots', '$attrs', '$props', '$refs', '$el'])

function walk(dir) {
  const out = []
  for (const e of readdirSync(dir)) {
    const p = path.join(dir, e)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (e.endsWith('.vue')) out.push(p)
  }
  return out
}

let bad = 0
for (const file of walk('src')) {
  const src = readFileSync(file, 'utf-8')
  const { descriptor, errors } = parse(src, { filename: file })
  if (errors.length) { console.log(`${file}: SFC 解析错误`); bad++; continue }
  let script
  try { script = compileScript(descriptor, { id: 'x' }) }
  catch (e) { console.log(`${file}: script 编译失败 ${String(e).slice(0,120)}`); bad++; continue }

  const tpl = compileTemplate({
    source: descriptor.template.content, filename: file, id: 'x',
    compilerOptions: { bindingMetadata: script.bindings, prefixIdentifiers: true },
  })
  if (tpl.errors.length) {
    console.log(`${file}: 模板编译错误`)
    tpl.errors.forEach(e => console.log('   ', String(e.message || e).slice(0,150)))
    bad++
    continue
  }
  const unknown = [...new Set(
    (tpl.code.match(/_ctx\.([A-Za-z_$][\w$]*)/g) || []).map(s => s.replace('_ctx.', ''))
  )].filter(n => !BUILTIN.has(n))
  if (unknown.length) {
    console.log(`${file}: 模板引用了未定义的标识符 → ${unknown.join(', ')}`)
    bad++
  }
}
console.log(bad ? `\n>>> ${bad} 个文件有问题` : '>>> 全部通过：无未定义引用')
process.exit(bad ? 1 : 0)
