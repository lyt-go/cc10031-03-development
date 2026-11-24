import { createRouter, createWebHistory } from 'vue-router'

import ArchiveRecallView from '@/views/ArchiveRecallView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/archive-recall', name: 'archive-recall', component: ArchiveRecallView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
