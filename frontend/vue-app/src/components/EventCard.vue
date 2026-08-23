<template>
  <div class="event-card" @click="expanded = !expanded">
    <div class="event-header">
      <span class="event-category">{{ event.category || '未分类' }}</span>
      <span class="event-time">{{ formatTime(event.event_publish_time) }}</span>
      <span class="event-breadth">{{ event.source_breadth || 0 }} 家媒体报道</span>
    </div>

    <p class="event-summary">{{ event.merged_summary || '（无摘要）' }}</p>

    <div class="keywords">
      <span v-for="kw in (event.keywords || []).slice(0, 5)" :key="kw" class="keyword">#{{ kw }}</span>
    </div>

    <div v-if="expanded" class="detail" @click.stop>
      <h4>来源报道</h4>
      <ul class="report-list">
        <li v-for="report in event.reports" :key="report.id">
          <span class="report-site">{{ report.source_site }}</span>
          <a :href="report.original_url" target="_blank" rel="noopener">
            {{ report.title }}
          </a>
        </li>
      </ul>
    </div>

    <div class="hint">{{ expanded ? '收起' : '查看来源报道' }}<span class="chevron" aria-hidden="true">{{ expanded ? '−' : '+' }}</span></div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  event: { type: Object, required: true },
})

const expanded = ref(false)

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return isNaN(d) ? iso : d.toLocaleDateString('zh-CN')
}
</script>

<style scoped>
.event-card {
  border: 1px solid var(--line);
  padding: clamp(18px, 4vw, 26px);
  margin-bottom: 14px;
  background: var(--surface);
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
}
.event-card:hover {
  border-color: var(--line-strong);
  box-shadow: var(--shadow-md);
}
.event-header {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
  font-size: 12.5px;
}
.event-category {
  background: var(--ink-strong);
  color: #fff;
  padding: 2px 10px;
  font-weight: 600;
  letter-spacing: 0.06em;
  border-radius: var(--radius-sm);
}
.event-time {
  color: var(--muted);
}
.event-breadth {
  color: var(--accent-ink);
  font-weight: 600;
}
.event-summary {
  margin: 0 0 12px;
  line-height: 1.8;
  font-size: 15px;
  color: var(--ink);
}
.keywords {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 4px;
}
.keyword {
  font-size: 12px;
  color: var(--faint);
}
.detail {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
  cursor: default;
}
.detail h4 {
  margin: 0 0 10px;
  font-size: 13px;
  letter-spacing: 0.06em;
  color: var(--ink-soft);
  font-weight: 600;
}
.report-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.report-list li {
  margin-bottom: 10px;
  font-size: 13.5px;
  line-height: 1.6;
}
.report-site {
  display: inline-block;
  min-width: 88px;
  margin-right: 10px;
  font-size: 12px;
  color: var(--muted);
  font-weight: 600;
}
.report-list a {
  color: var(--ink-strong);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: border-color 0.15s;
}
.report-list a:hover {
  border-color: var(--ink-strong);
}
.hint {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--faint);
  letter-spacing: 0.04em;
  transition: color 0.15s;
}
.event-card:hover .hint {
  color: var(--ink-strong);
}
.chevron {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: 1px solid var(--line-strong);
  font-size: 12px;
  line-height: 1;
  border-radius: 6px;
}
</style>