<template>
  <section class="page" data-module="oog">
    <header class="page-head">
      <div>
        <h2>超限箱管理管理</h2>
        <p class="page-desc">维护超限箱，围绕超限箱号、箱型尺寸、超限方向、超限尺寸做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记超限箱</button>
        <button class="btn" type="button" @click="exportRows">导出超限箱管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无超限箱管理数据，可先登记超限箱</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条超限箱管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/oog'
const columns = ["超限箱号", "箱型尺寸", "超限方向", "超限尺寸", "专用吊具", "堆放区域", "绑扎方案", "超限状态", "判定依据"]
const actions = ["确认超限", "安排作业", "确认装机"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = ["超限箱号", "箱型尺寸", "超限方向"]

// 作业看板：超限箱数不从静态配置读，统一由明细实时重算。判定超限=明细里
// 判为一般/严重超限的箱子；其余三张卡片按作业状态统计，与列表完全同源。
const stats = computed(() => {
  const oversize = allRows.value.filter((row) => row['超限状态'] === '一般超限' || row['超限状态'] === '严重超限').length
  const countByStatus = (status: string) => allRows.value.filter((row) => row.status === status).length
  return [
    { label: '判定超限', value: oversize },
    { label: '待确认超限', value: countByStatus('待确认') },
    { label: '作业中超限', value: countByStatus('作业中') },
    { label: '已装机超限', value: countByStatus('已装机') },
  ]
})
// 统计口径用全量明细：列表页有分页，单独拉一份 size=200（接口允许上限）做看板重算。
const allRows = ref<Row[]>([])

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '超限箱登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('超限箱管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '超限箱管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  // 看板统计与页面明细并行拉取，结论都来自后端同一份判据。
  const [filtered, all] = await Promise.all([
    request(`${ENDPOINT}?${query}`),
    request(`${ENDPOINT}?page=1&size=200`),
  ])
  if (!filtered.ok || !all.ok) {
    errorMessage.value = '超限箱列表读取失败'
    return
  }
  const payload = await filtered.json()
  const overviewPayload = await all.json()
  rows.value = payload.items ?? []
  total.value = payload.total ?? rows.value.length
  allRows.value = overviewPayload.items ?? []
}

onMounted(reload)
</script>
