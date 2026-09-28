<template>
  <div v-if="info" class="alert alert-warn" :class="{ 'mt-2': spaced }">
    <span class="alert-icon">⚠</span>
    <div>
      <b>{{ title }}</b>
      <div v-if="info.reason" class="mt-1">{{ info.reason }}</div>
      <div class="tiny muted mt-1">
        <template v-if="info.level">降级级别：{{ info.level }}<br /></template>
        <template v-if="info.model">模型：{{ info.model }}<br /></template>
        {{ hint }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 降级提示条。
 *
 * PRD 4.3 明令禁止静默降级：使用了备用模型或模板兜底时，界面必须标注，
 * 否则用户会把兜底结果当作正常结论采信。抽成一个组件，是因为这套系统里
 * 每个 AI 能力都会降级，分散写必然会有某处漏掉。
 */
withDefaults(defineProps<{
  info: { reason?: string; level?: string; model?: string } | null
  title?: string
  hint?: string
  spaced?: boolean
}>(), {
  title: '本结果走了降级路径',
  hint: '降级结果不应直接作为业务判断依据，请人工复核或稍后重试。',
  spaced: false,
})
</script>
