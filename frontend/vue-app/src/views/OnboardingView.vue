<template>
  <div class="onboarding">
    <div class="hero">
      <p class="eyebrow">MULTI-SOURCE NEWS AGGREGATION</p>
      <h1>多源新闻<br />事件聚合</h1>
      <p class="subtitle">
        把多家媒体报道聚合成一个「事件」，做多源事实互证与可溯源问答。
      </p>
    </div>

    <div class="panel">
      <h2>选择你感兴趣的类别</h2>
      <p class="hint">最多选 3 个，用于事件流预过滤和 RAG 问答</p>

      <div class="tags">
        <button
          v-for="cat in categories"
          :key="cat"
          class="tag-btn"
          :class="{ selected: selected.includes(cat) }"
          :disabled="!selected.includes(cat) && selected.length >= 3"
          @click="toggle(cat)"
        >
          {{ cat }}
        </button>
      </div>

      <div class="actions">
        <button class="primary" :disabled="selected.length === 0" @click="save">
          进入系统
        </button>
        <button class="secondary" @click="skip">跳过，先看全部</button>
      </div>

      <p v-if="msg" class="msg">{{ msg }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getCategories, saveUserProfile } from '../api.js'

const router = useRouter()
const categories = getCategories()
const selected = ref([])
const msg = ref('')

onMounted(() => {
  const saved = localStorage.getItem('interest_tags')
  if (saved) {
    try {
      selected.value = JSON.parse(saved)
    } catch {}
  }
})

function toggle(cat) {
  if (selected.value.includes(cat)) {
    selected.value = selected.value.filter((c) => c !== cat)
  } else if (selected.value.length < 3) {
    selected.value.push(cat)
  }
}

async function save() {
  await persist()
}

async function skip() {
  selected.value = []
  await persist()
}

async function persist() {
  const userId = localStorage.getItem('user_id') || crypto.randomUUID()
  localStorage.setItem('user_id', userId)
  localStorage.setItem('interest_tags', JSON.stringify(selected.value))

  try {
    await saveUserProfile(userId, selected.value)
    router.push('/events')
  } catch (e) {
    msg.value = '保存失败：' + (e.response?.data?.detail || e.message)
  }
}
</script>

<style scoped>
.onboarding {
  max-width: 640px;
  margin: 0 auto;
  padding: clamp(48px, 10vh, 96px) clamp(20px, 6vw, 48px) 64px;
}
.hero {
  margin-bottom: 48px;
}
.eyebrow {
  margin: 0 0 16px;
  font-size: 12px;
  letter-spacing: 0.28em;
  color: var(--accent-ink);
  font-weight: 600;
}
.hero h1 {
  margin: 0 0 20px;
  font-family: var(--serif);
  font-size: clamp(2.6rem, 8vw, 4.2rem);
  line-height: 1.12;
  letter-spacing: -0.01em;
  color: var(--ink-strong);
}
.subtitle {
  margin: 0;
  color: var(--muted);
  font-size: 15px;
  line-height: 1.8;
  max-width: 36em;
}
.panel {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: clamp(24px, 5vw, 36px);
}
.panel h2 {
  margin: 0 0 6px;
  font-family: var(--serif);
  font-size: 20px;
  color: var(--ink-strong);
}
.hint {
  color: var(--faint);
  font-size: 13px;
  margin: 0 0 24px;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 28px;
}
.tag-btn {
  padding: 9px 20px;
  border: 1px solid var(--line-strong);
  background: var(--surface);
  color: var(--ink-soft);
  cursor: pointer;
  transition: all 0.15s;
  font-size: 14px;
  border-radius: var(--radius-sm);
}
.tag-btn:hover:not(:disabled) {
  border-color: var(--ink-strong);
  color: var(--ink-strong);
  box-shadow: var(--shadow-sm);
}
.tag-btn.selected {
  background: var(--ink-strong);
  color: #fff;
  border-color: var(--ink-strong);
}
.tag-btn:disabled:not(.selected) {
  opacity: 0.4;
  cursor: not-allowed;
}
.actions {
  display: flex;
  gap: 12px;
}
.actions button {
  flex: 1;
  padding: 13px 0;
  font-size: 15px;
  cursor: pointer;
  border-radius: var(--radius-sm);
}
.primary {
  border: none;
  background: var(--ink-strong);
  color: #fff;
  transition: background 0.15s, box-shadow 0.15s;
}
.primary:hover:not(:disabled) {
  background: var(--ink-strong-hover);
  box-shadow: var(--shadow-md);
}
.primary:disabled {
  background: var(--faint);
  cursor: not-allowed;
}
.secondary {
  border: 1px solid var(--line-strong);
  background: var(--surface);
  color: var(--ink-soft);
  transition: all 0.15s;
}
.secondary:hover {
  border-color: var(--ink-strong);
  color: var(--ink-strong);
  box-shadow: var(--shadow-sm);
}
.msg {
  margin-top: 16px;
  color: #dc2626;
  text-align: center;
  font-size: 14px;
}
</style>