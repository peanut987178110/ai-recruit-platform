<template>
  <div>
    <div class="card">
      <div class="card-head">
        <span class="card-title">面试日程</span>
        <span class="card-desc">面试官仅可见被指派给自己的场次</span>
        <VButton size="sm" icon="⟳" class="ml-auto" :loading="loading" @click="load">刷新</VButton>
      </div>

      <VState :items="rows" :loading="loading" title="暂无面试安排" icon="◷"
              desc="在候选人复核页点击「采纳」后，可直接安排面试；系统会按简历与能力模型的差集生成分层题目。">
        <table class="table">
          <thead>
            <tr>
              <th>候选人</th>
              <th>岗位</th>
              <th style="width: 90px">轮次</th>
              <th style="width: 110px">面试官</th>
              <th style="width: 80px">时长</th>
              <th style="width: 90px">题目</th>
              <th style="width: 110px">状态</th>
              <th style="width: 160px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in rows" :key="s.id">
              <td class="bold">{{ s.candidate_name }}</td>
              <td class="small">{{ s.position_name }}</td>
              <td><span class="tag tag-gray">{{ s.round_name }}</span></td>
              <td class="small">{{ s.interviewer || '未指派' }}</td>
              <td class="small">{{ s.plan_minutes }} 分钟</td>
              <td class="num">{{ s.question_count || '—' }}</td>
              <td>
                <span class="tag" :class="s.status === '已完成' ? 'tag-ok' : 'tag-blue'">{{ s.status }}</span>
              </td>
              <td>
                <VButton size="sm" variant="primary" @click="$router.push(`/interview/${s.id}`)">
                  {{ s.question_count ? '查看准备' : '生成题目' }}
                </VButton>
                <VButton v-if="s.has_report" size="sm" style="margin-left: 4px"
                         @click="$router.push(`/interview/${s.id}/report`)">报告</VButton>
              </td>
            </tr>
          </tbody>
        </table>
      </VState>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import VButton from '../components/VButton.vue'
import VState from '../components/VState.vue'
import { interviewApi } from '../api'
import { toast } from '../components/toast'

const rows = ref<any[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try { rows.value = await interviewApi.schedules() } catch (e) { toast.err(e) } finally { loading.value = false }
}

onMounted(load)
</script>
