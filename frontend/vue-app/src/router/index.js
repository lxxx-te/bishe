import { createRouter, createWebHistory } from 'vue-router'
import OnboardingView from '../views/OnboardingView.vue'
import EventStreamView from '../views/EventStreamView.vue'
import DigestView from '../views/DigestView.vue'
import RagChatView from '../views/RagChatView.vue'

const routes = [
  { path: '/', redirect: '/onboarding' },
  { path: '/onboarding', name: 'Onboarding', component: OnboardingView },
  { path: '/events', name: 'Events', component: EventStreamView },
  { path: '/digest', name: 'Digest', component: DigestView },
  { path: '/chat', name: 'Chat', component: RagChatView },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
