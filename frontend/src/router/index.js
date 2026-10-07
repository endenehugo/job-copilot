import { createRouter, createWebHistory } from 'vue-router'
import Workspace from '../views/Workspace.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [{ path: '/', name: 'workspace', component: Workspace }],
})
