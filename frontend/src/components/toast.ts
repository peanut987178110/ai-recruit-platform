/** 全局轻提示。用队列而非单例，避免连续操作时后面的提示把前面的顶掉。 */
import { ref } from 'vue'

export interface Toast {
  id: number
  text: string
  kind: 'info' | 'ok' | 'warn' | 'danger'
  detail?: string
}

export const toasts = ref<Toast[]>([])
let seq = 0

function push(text: string, kind: Toast['kind'] = 'info', detail = '', ms = 3600) {
  const id = ++seq
  toasts.value.push({ id, text, kind, detail })
  setTimeout(() => {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }, ms)
}

export const toast = {
  info: (t: string, d = '') => push(t, 'info', d),
  ok: (t: string, d = '') => push(t, 'ok', d),
  warn: (t: string, d = '') => push(t, 'warn', d, 5000),
  danger: (t: string, d = '') => push(t, 'danger', d, 6000),
  /** 统一处理接口异常，把后端的可读错误直接展示给用户 */
  err: (e: unknown, fallback = '操作失败') => {
    const msg = (e as { friendly?: string })?.friendly
      || (e as Error)?.message || fallback
    push(msg, 'danger', '', 6500)
  },
}
