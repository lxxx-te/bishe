import axios from 'axios'

export const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
})

const CATEGORIES = ['政治', '经济', '文化', '社会', '科技', '国际', '体育', '其他']

export function getCategories() {
  return CATEGORIES
}

export async function saveUserProfile(userId, tags) {
  const res = await api.post('/user/profile', { user_id: userId, interest_tags: tags })
  return res.data
}

export async function fetchEvents(limit = 20, offset = 0, category = null) {
  const params = { limit, offset }
  if (category) params.category = category
  const res = await api.get('/events', { params })
  return res.data
}

export async function fetchEventReports(eventId, limit = 20, offset = 0) {
  const res = await api.get('/reports', {
    params: { event_id: eventId, limit, offset },
  })
  return res.data
}

export async function fetchDigest(date = null, days = 1) {
  const params = { days }
  if (date) params.date = date
  const res = await api.get('/digest', { params })
  return res.data
}
