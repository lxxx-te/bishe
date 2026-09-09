<template>
  <article class="event-item" @click="expanded = !expanded">
    <p class="event-summary">{{ event.merged_summary || '（无摘要）' }}</p>
    <div class="event-meta">
      <span class="event-category">{{ event.category || '未分类' }}</span>
      <span class="sep">·</span>
      <span class="event-breadth" :class="{ multi: (event.source_breadth || 0) >= 2 }">{{ event.source_breadth || 0 }} 家媒体</span>
      <span class="sep">·</span>
      <time>{{ formatTime(event.event_publish_time) }}</time>
      <span class="hint">{{ expanded ? '收起来源' : '来源报道' }}</span>
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
      <p v-if="(event.keywords || []).length" class="kw-line">关键词：{{ (event.keywords || []).join(' / ') }}</p>
    </div>
  </article>
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
  if (isNaN(d)) return iso
  return `${d.getMonth() + 1}月${d.getDate()}日`
}
</script>

<style scoped>
.event-item {
  padding: 16px 2px;
  border-bottom: 1px solid var(--line);
  cursor: pointer;
  transition: background 0.12s;
}
.event-item:hover {
  background: rgba(30, 58, 95, 0.04);
}
.event-summary {
  margin: 0 0 8px;
  line-height: 1.75;
  font-size: 14.5px;
  color: var(--ink);
}
.event-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 7px;
  font-size: 12.5px;
  color: var(--muted);
}
.sep {
  color: var(--faint);
}
.event-category {
  color: var(--accent);
  font-weight: 600;
  letter-spacing: 0.02em;
}
.event-breadth.multi {
  color: var(--accent);
  font-weight: 600;
}
.hint {
  margin-left: auto;
  color: var(--faint);
  transition: color 0.15s;
}
.event-item:hover .hint {
  color: var(--ink-strong);
}
.detail {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--line-strong);
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
.kw-line {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--faint);
}
</style>
