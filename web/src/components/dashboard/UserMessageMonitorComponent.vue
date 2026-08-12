<template>
  <div class="user-message-monitor">
    <div class="monitor-header">
      <h3 class="monitor-title">用户问题监控</h3>
      <div class="monitor-filters">
        <a-input
          v-model:value="filters.keyword"
          placeholder="搜索问题关键词"
          allow-clear
          style="width: 200px"
          @pressEnter="loadData"
          @change="onKeywordChange"
        />
        <a-select
          v-model:value="filters.feedback"
          placeholder="满意度"
          allow-clear
          style="width: 120px"
          @change="loadData"
        >
          <a-select-option value="like">👍 点赞</a-select-option>
          <a-select-option value="dislike">👎 点踩</a-select-option>
        </a-select>
        <a-button @click="loadData">查询</a-button>
      </div>
    </div>

    <a-table
      :columns="columns"
      :data-source="tableData"
      :pagination="pagination"
      :loading="loading"
      row-key="id"
      size="small"
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
            <span class="text-cell">{{ record.answer || '—' }}</span>
          </a-tooltip>
        </template>
        <template v-else-if="column.key === 'feedback'">
          <span v-if="record.feedback === 'like'" style="color: #52c41a">👍</span>
          <span v-else-if="record.feedback === 'dislike'" style="color: #ff4d4f">👎</span>
          <span v-else style="color: #d9d9d9">—</span>
        </template>
      </template>
    </a-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { dashboardApi } from '@/apis/dashboard_api'

const loading = ref(false)
const tableData = ref([])

const filters = ref({
  keyword: '',
  feedback: undefined
})

const pagination = ref({
  current: 1,
  pageSize: 20,
  total: 0,
  showSizeChanger: true,
  showTotal: (total) => `共 ${total} 条`
})

const columns = [
  { title: '用户', dataIndex: 'username', key: 'username', width: 100 },
  { title: '问题', dataIndex: 'question', key: 'question', ellipsis: true },
  { title: 'Agent 回答', dataIndex: 'answer', key: 'answer', ellipsis: true, width: 300 },
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
    // API 没返回总数，用当前条数粗估
    pagination.value.total = data.length < pagination.value.pageSize
      ? (pagination.value.current - 1) * pagination.value.pageSize + data.length
      : (pagination.value.current + 1) * pagination.value.pageSize
  } catch (error) {
    console.error('加载用户问题列表失败:', error)
    message.error('加载用户问题列表失败')
  } finally {
    loading.value = false
  }
}

const onTableChange = (pag) => {
  pagination.value.current = pag.current
  pagination.value.pageSize = pag.pageSize
  loadData()
}

let keywordTimer = null
const onKeywordChange = () => {
  clearTimeout(keywordTimer)
  keywordTimer = setTimeout(() => {
    pagination.value.current = 1
    loadData()
  }, 300)
}

onMounted(() => {
  loadData()
})
</script>

<style scoped lang="less">
.user-message-monitor {
  margin-top: 24px;

  .monitor-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;

    .monitor-title {
      font-size: 16px;
      font-weight: 600;
      color: var(--gray-1000, #1b2421);
      margin: 0;
    }

    .monitor-filters {
      display: flex;
      gap: 8px;
    }
  }

  .text-cell {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: normal;
    word-break: break-all;
  }
}
</style>
