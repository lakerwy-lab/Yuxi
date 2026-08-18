<template>
  <div class="dingtalk-bot-settings-section">
    <div class="section-title">钉钉机器人配置</div>
    <p class="section-description">
      配置钉钉企业内部机器人 Stream 模式。client_id 与 client_secret 复用登录用的 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，无需在此填写。
    </p>
    <div class="option-list">
      <section v-for="option in configOptions" :key="option.key" class="option-card">
        <header class="option-header">
          <div>
            <h4>{{ option.name }}</h4>
            <p>{{ option.description }}</p>
          </div>
          <div class="option-actions" :class="{ editing: editingKey === option.key }">
            <template v-if="editingKey === option.key">
              <a-button size="small" :disabled="savingOption === option.key" @click="cancelEditing">
                取消
              </a-button>
              <a-button
                type="primary"
                size="small"
                class="save-button"
                :loading="savingOption === option.key"
                @click="saveOption(option)"
              >
                保存
              </a-button>
            </template>
            <a-button
              v-else
              size="small"
              :disabled="Boolean(editingKey)"
              @click="startEditing(option)"
            >
              编辑
            </a-button>
          </div>
        </header>
        <div v-if="editingKey === option.key" class="option-fields">
          <label v-for="field in option.params.fields" :key="field.key" class="option-field">
            <span class="setting-label">{{ field.label }}</span>
            <a-input-password
              v-if="field.sensitive"
              v-model:value="draftValue[field.key]"
              :placeholder="field.environment"
              autocomplete="new-password"
            />
            <a-input
              v-else
              v-model:value="draftValue[field.key]"
              :placeholder="field.placeholder || field.environment"
              allow-clear
            />
            <small>
              {{ field.sensitive ? '留空并保存会清除数据库中的值。' : field.help }}
            </small>
          </label>
        </div>
        <div v-else class="option-fields option-values">
          <div v-for="field in option.params.fields" :key="field.key" class="option-field">
            <span class="setting-label">{{ field.label }}</span>
            <a-input
              :value="getFieldDisplay(option, field)"
              :class="{ 'masked-value': field.sensitive }"
              disabled
            />
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { configOptionsApi } from '@/apis/system_api'

const BOT_OPTION_KEYS = new Set(['dingtalk_bot_opts'])
const configOptions = ref([])
const editingKey = ref('')
const draftValue = ref({})
const savingOption = ref('')

const loadConfigOptions = async () => {
  try {
    const data = await configOptionsApi.getOptions()
    configOptions.value = (data.options || [])
      .filter((option) => BOT_OPTION_KEYS.has(option.key))
      .map((option) => ({
        ...option,
        value: { ...(option.value || {}) },
        sensitive_state: { ...(option.sensitive_state || {}) }
      }))
  } catch (error) {
    message.error(error.message || '加载钉钉机器人配置失败')
  }
}

const startEditing = (option) => {
  editingKey.value = option.key
  draftValue.value = { ...(option.value || {}) }
}

const cancelEditing = () => {
  editingKey.value = ''
  draftValue.value = {}
}

const getFieldDisplay = (option, field) => {
  if (!field.sensitive) {
    return option.value?.[field.key] || `读取 ${field.environment}`
  }
  const state = option.sensitive_state?.[field.key]
  if (state?.source === 'database') return state.preview
  if (state?.source === 'environment') return `已通过 ${field.environment} 配置`
  return '未配置'
}

const saveOption = async (option) => {
  savingOption.value = option.key
  try {
    const data = await configOptionsApi.updateOption(option.key, draftValue.value)
    Object.assign(option, data.option, {
      value: { ...(data.option.value || {}) },
      sensitive_state: { ...(data.option.sensitive_state || {}) }
    })
    cancelEditing()
    message.success('配置已保存')
  } catch (error) {
    message.error(error.message || '保存配置失败')
  } finally {
    savingOption.value = ''
  }
}

onMounted(loadConfigOptions)
</script>

<style lang="less" scoped>
@import '@/assets/css/base.css';

.dingtalk-bot-settings-section {
  color: var(--gray-900);
}

.section-title {
  margin: 0 0 10px;
  color: var(--gray-900);
  font-size: 15px;
  font-weight: 600;
}

.section-description {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.option-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 12px;
}

.option-card {
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.option-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;

  h4,
  p {
    margin: 0;
  }

  h4 {
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 500;
  }

  p {
    margin-top: 3px;
    color: var(--color-text-secondary);
    font-size: 12px;
  }
}

.option-actions {
  display: flex;
  flex: none;
  gap: 8px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s ease;
}

.option-card:hover .option-actions,
.option-card:focus-within .option-actions,
.option-actions.editing {
  opacity: 1;
  pointer-events: auto;
}

.save-button {
  border-color: var(--gray-900);
  background: var(--gray-900);

  &:hover,
  &:focus {
    border-color: var(--gray-700);
    background: var(--gray-700);
  }
}

.option-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 16px;
}

.option-field {
  display: flex;
  flex-direction: column;
  gap: 6px;

  &:only-child {
    grid-column: 1 / -1;
  }

  small {
    color: var(--color-text-secondary);
    font-size: 12px;
    line-height: 1.5;
  }
}

.setting-label {
  color: var(--gray-700);
  font-size: 13px;
  font-weight: 500;
}

.masked-value {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  letter-spacing: 0.02em;
}

@media (max-width: 680px) {
  .option-fields {
    grid-template-columns: 1fr;
  }

  .option-field:only-child {
    grid-column: auto;
  }
}
</style>
