<template>
  <div class="digest-view">
    <header class="digest-header">
      <div>
        <p class="eyebrow">DAILY DIGEST</p>
        <h2>每日简报</h2>
      </div>
      <div class="controls">
        <input v-model="date" type="date" :max="maxDate" @change="load" />
        <select v-model="days" @change="load">
          <option :value="1">1 天</option>
          <option :value="3">3 天</option>
          <option :value="7">7 天</option>
        </select>
        <a
          class="rss-btn"
          :href="rssUrl"
          target="_blank"
          rel="noopener"
          title="订阅事件流 RSS"
        >RSS 订阅</a>
      </div>
    </header>

    <p v-if="date" class="digest-meta">
      {{ date }} 共 {{ count }} 条事件（多源优先，按报道数排序）
    </p>

    <div v-if="loading" class="status">加载中…</div>
    <div v-else-if="error" class="status error">{{ error }}</div>

    <div class="digest-list">
      <article v-for="(item, idx) in items" :key="item.event_id" class="digest-card">
        <span class="digest-index" aria-hidden="true">{{ String(idx + 1).padStart(2, '0') }}</span>
        <div class="digest-body">
          <div class="digest-top">
            <span class="digest-category">{{ item.category || '未分类' }}</span>
            <span class="digest-sources">{{ item.source_breadth }} 家媒体 · {{ (item.sources || []).join(' / ') }}</span>
          </div>
          <h3>
            <a v-if="item.link" :href="item.link" target="_blank" rel="noopener">{{ item.title }}</a>
            <span v-else>{{ item.title }}</span>
          </h3>
          <p class="digest-summary">{{ item.summary }}</p>
          <a class="digest-more" :href="`#/events`" @click.prevent="$router.push('/events')">查看事件流 →</a>
        </div>
      </article>
    </div>

    <div v-if="!loading && !error && items.length === 0" class="status">该日期区间内没有事件</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { fetchDigest } from '../api.js'

const date = ref('')
const days = ref(1)
const items = ref([])
const count = ref(0)
const loading = ref(false)
const error = ref('')

const today = new Date()
const maxDate = today.toISOString().slice(0, 10)

const rssUrl = computed(() => `http://localhost:8000/feed/events.rss?days=${days.value}`)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetchDigest(date.value || null, days.value)
    items.value = res.items || []
    count.value = res.count || 0
  } catch (e) {
    error.value = '加载失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.digest-view {
  max-width: 720px;
  margin: 0 auto;
  padding: clamp(32px, 6vh, 56px) clamp(20px, 6vw, 48px) 64px;
}
.digest-header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  margin-bottom: 8px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 24px;
}
.eyebrow {
  margin: 0 0 8px;
  font-size: 11px;
  letter-spacing: 0.26em;
  color: var(--faint);
  font-weight: 600;
}
.digest-header h2 {
  margin: 0;
  font-family: var(--serif);
  font-size: clamp(1.9rem, 4.5vw, 2.6rem);
  line-height: 1.15;
  color: var(--ink-strong);
}
.controls {
  display: flex;
  gap: 8px;
  align-items: center;
}
.controls input,
.controls select {
  padding: 8px 10px;
  border: 1px solid var(--line-strong);
  background: var(--surface);
  font-size: 13px;
  color: var(--ink-soft);
  border-radius: var(--radius-sm);
}
.rss-btn {
  background: var(--accent);
  color: #fff;
  padding: 8px 14px;
  font-size: 13px;
  text-decoration: none;
  font-weight: 600;
  letter-spacing: 0.03em;
  transition: background 0.15s, box-shadow 0.15s;
  border-radius: var(--radius-sm);
}
.rss-btn:hover {
  background: var(--accent-ink);
  box-shadow: var(--shadow-md);
}
.digest-meta {
  color: var(--muted);
  font-size: 13px;
  margin: 16px 0 20px;
}
.status {
  text-align: center;
  color: var(--muted);
  padding: 40px;
}
.status.error {
  color: #dc2626;
}
.digest-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.digest-card {
  display: flex;
  gap: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  padding: clamp(18px, 4vw, 24px);
  transition: border-color 0.2s, box-shadow 0.2s;
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
}
.digest-card:hover {
  border-color: var(--line-strong);
  box-shadow: var(--shadow-md);
}
.digest-index {
  font-family: var(--serif);
  font-size: 28px;
  color: var(--faint);
  line-height: 1;
  flex-shrink: 0;
}
.digest-body {
  flex: 1;
  min-width: 0;
}
.digest-top {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
  font-size: 12.5px;
}
.digest-category {
  background: var(--ink-strong);
  color: #fff;
  padding: 2px 10px;
  font-weight: 600;
  letter-spacing: 0.06em;
  border-radius: var(--radius-sm);
}
.digest-sources {
  color: var(--accent-ink);
  font-weight: 600;
}
.digest-card h3 {
  margin: 0 0 10px;
  font-size: 17px;
  line-height: 1.45;
  font-family: var(--serif);
}
.digest-card h3 a {
  color: var(--ink-strong);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: border-color 0.15s;
}
.digest-card h3 a:hover {
  border-color: var(--ink-strong);
}
.digest-summary {
  margin: 0 0 12px;
  line-height: 1.8;
  font-size: 14px;
  color: var(--ink-soft);
}
.digest-more {
  font-size: 13px;
  color: var(--ink-strong);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: border-color 0.15s;
}
.digest-more:hover {
  border-color: var(--ink-strong);
}
</style>