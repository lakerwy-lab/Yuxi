<template>
  <a-card class="monitor-card" :bordered="false">
    <template #title>
      <span class="card-title">用户问题监控</span>
    </template>
    <template #extra>
      <div class="filter-bar">
        <a-input
          v-model:value="filters.keyword"
          placeholder="搜索问题关键词"
          allow-clear
          style="width: 200px"
          @press-enter="onSearch"
          @change="onKeywordChange"
        />
        <a-select
          v-model:value="filters.feedback"
          placeholder="满意度"
          allow-clear
          style="width: 120px"
          @change="onSearch"
        >
          <a-select-option value="like">👍 点赞</a-select-option>
          <a-select-option value="dislike">👎 点踩</a-select-option>
        </a-select>
      </div>
    </template>

    <a-table
      :columns="columns"
      :data-source="tableData"
      :loading="loading"
      :pagination="pagination"
      row-key="id"
      size="small"
      :scroll="{ y: 400 }"
      @change="onTableChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'question'">
          <a-tooltip :title="record.question">
            <span class="text-cell">{{ record.question }}</span>
          </a-tooltip>
        </template>
        <template v-else-if="column.key === 'answer'">
          <a-tooltip :title="record.answer">
            <span class="text-cell text-cell-muted">{{ record.answer || '—' }}</span>
          </a-tooltip>
        </template>
        <template v-else-if="column.key === 'feedback'">
          <span v-if="record.feedback === 'like'" class="feedback-like">👍</span>
          <span v-else-if="record.feedback === 'dislike'" class="feedback-dislike">👎</span>
          <span v-else class="feedback-none">—</span>
        </template>
        <template v-else-if="column.key === 'created_at'">
          <span class="time-cell">{{ record.created_at }}</span>
        </template>
      </template>
    </a-table>
  </a-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { dashboardApi } from '@/apis/dashboard_api'

const loading = ref(false)
const tableData = ref([])
const total = ref(0)

const filters = ref({
  keyword: '',
  feedback: undefined
})

const pagination = ref({
  current: 1,
  pageSize: 20,
  total: 0,
  showSizeChanger: true,
  showTotal: (t) => `共 ${t} 条`
})

const columns = [
  { title: '用户', dataIndex: 'username', key: 'username', width: 100 },
  { title: '问题', dataIndex: 'question', key: 'question', ellipsis: true, width: 280 },
  { title: 'Agent 回答', dataIndex: 'answer', key: 'answer', ellipsis: true, width: 320 },
  { title: '满意', key: 'feedback', width: 60, align: 'center' },
  { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 160 }
]

const loadData = async () => {
  loading.value = true
  try {
    const data = await dashboardApi.getUserMessages({
      keyword: filters.value.keyword || undefined,
      feedback: filters.value.feedback || undefined,
      page: pagination.value.current,
      page_size: pagination.value.pageSize
    })
    tableData.value = data
    // API 没返回总数，按当前页是否满页估算
    const fullPage = data.length === pagination.value.pageSize
    total.value = fullPage
      ? pagination.value.current * pagination.value.pageSize + 1
      : (pagination.value.current - 1) * pagination.value.pageSize + data.length
    pagination.value.total = total.value
  } catch (error) {
    console.error('加载用户问题列表失败:', error)
    message.error('加载用户问题列表失败')
  } finally {
    loading.value = false
  }
}

const onSearch = () => {
  pagination.value.current = 1
  loadData()
}

const onTableChange = (pag) => {
  pagination.value.current = pag.current
  pagination.value.pageSize = pag.pageSize
  loadData()
}

let keywordTimer = null
const onKeywordChange = () => {
  clearTimeout(keywordTimer)
  keywordTimer = setTimeout(onSearch, 300)
}

onMounted(() => {
  loadData()
})
</script>

<style scoped lang="less">
.monitor-card {
  margin-top: 24px;
  background-color: var(--gray-0, #ffffff);
  border-radius: 8px;

  .card-title {
    font-size: 16px;
    font-weight: 600;
    color: var(--gray-1000, #1b2421);
  }

  .filter-bar {
    display: flex;
    gap: 8px;
  }

  .text-cell {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: normal;
    word-break: break-all;
    color: var(--gray-900, #202123);
  }

  .text-cell-muted {
    color: var(--gray-500, #909090);
  }

  .feedback-like {
    color: var(--color-success-600, #52c41a);
  }

  .feedback-dislike {
    color: var(--color-error-600, #ff4d4f);
  }

  .feedback-none {
    color: var(--gray-200, #e7e7e4);
  }

  .time-cell {
    color: var(--gray-500, #909090);
    font-size: 13px;
  }
}
</style>
