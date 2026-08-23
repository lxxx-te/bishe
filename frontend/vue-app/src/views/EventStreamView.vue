<template>
  <div class="event-stream">
    <header class="stream-header">
      <div>
        <p class="eyebrow">EVENT STREAM</p>
        <h2>事件流</h2>
        <p class="subtitle">共 {{ total }} 个事件 · 多源报道自动聚合</p>
      </div>
      <router-link to="/chat" class="chat-link">去问问题 <span aria-hidden="true">→</span></router-link>
    </header>

    <div class="toolbar">
      <input
        v-model="search"
        class="search-input"
        placeholder="搜索事件关键词…"
        @input="applyFilters"
      />
      <select v-model="sortBy" @change="applyFilters">
        <option value="breadth">按多源度</option>
        <option value="time">按时间</option>
      </select>
      <select v-model="selectedCategory" @change="applyFilters">
        <option value="">全部类别</option>
        <option v-for="cat in categories" :key="cat" :value="cat">{{ cat }}</option>
      </select>
    </div>

    <div v-if="loading" class="status">加载中…</div>
    <div v-else-if="error" class="status error">{{ error }}</div>

    <EventCard
      v-for="ev in filteredEvents"
      :key="ev.id"
      :event="ev"
    />

    <div v-if="!loading && filteredEvents.length === 0" class="status">没有匹配事件</div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { getCategories, fetchEvents } from '../api.js'
import EventCard from '../components/EventCard.vue'

const categories = getCategories()
const search = ref('')
const sortBy = ref('breadth')
const selectedCategory = ref('')
const events = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref('')

async function loadEvents() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetchEvents(100, 0, selectedCategory.value || null)
    events.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    error.value = '加载失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  // client-side filtering after load
}

const filteredEvents = computed(() => {
  let list = events.value

  if (search.value.trim()) {
    const q = search.value.trim().toLowerCase()
    list = list.filter((ev) =>
      (ev.merged_summary || '').toLowerCase().includes(q) ||
      (ev.keywords || []).some((k) => k.toLowerCase().includes(q))
    )
  }

  if (sortBy.value === 'time') {
    list = [...list].sort((a, b) => {
      const ta = new Date(a.event_publish_time || 0).getTime()
      const tb = new Date(b.event_publish_time || 0).getTime()
      return tb - ta
    })
  } else {
    list = [...list].sort((a, b) => (b.source_breadth || 0) - (a.source_breadth || 0))
  }

  return list
})

onMounted(loadEvents)
</script>

<style scoped>
.event-stream {
  max-width: 720px;
  margin: 0 auto;
  padding: clamp(32px, 6vh, 56px) clamp(20px, 6vw, 48px) 64px;
}
.stream-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 28px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 28px;
}
.eyebrow {
  margin: 0 0 8px;
  font-size: 11px;
  letter-spacing: 0.26em;
  color: var(--faint);
  font-weight: 600;
}
.stream-header h2 {
  margin: 0 0 6px;
  font-family: var(--serif);
  font-size: clamp(1.9rem, 4.5vw, 2.6rem);
  line-height: 1.15;
  color: var(--ink-strong);
}
.subtitle {
  margin: 0;
  color: var(--muted);
  font-size: 14px;
}
.chat-link {
  flex-shrink: 0;
  margin-top: 4px;
  color: var(--ink-strong);
  border: 1px solid var(--ink-strong);
  padding: 8px 16px;
  text-decoration: none;
  font-size: 14px;
  transition: all 0.15s;
  border-radius: var(--radius-sm);
}
.chat-link:hover {
  background: var(--ink-strong);
  color: #fff;
  box-shadow: var(--shadow-md);
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 28px;
  align-items: center;
}
.search-input {
  flex: 1;
  min-width: 180px;
  padding: 9px 12px;
  border: 1px solid var(--line-strong);
  background: var(--surface);
  border-radius: var(--radius-sm);
  font-size: 14px;
  color: var(--ink);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.search-input:focus {
  outline: none;
  border-color: var(--ink-strong);
  box-shadow: 0 0 0 3px rgba(30, 58, 95, 0.1);
}
.toolbar select {
  padding: 9px 10px;
  border: 1px solid var(--line-strong);
  background: var(--surface);
  border-radius: var(--radius-sm);
  font-size: 14px;
  color: var(--ink-soft);
  cursor: pointer;
}
.status {
  text-align: center;
  color: var(--muted);
  padding: 48px 0;
}
.status.error {
  color: #dc2626;
}
</style>