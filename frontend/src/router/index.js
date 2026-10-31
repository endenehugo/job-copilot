import { createRouter, createWebHistory } from 'vue-router'
import Workspace from '../views/Workspace.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'workspace', component: Workspace },
    {
      // 运维监控页：不进侧栏导航（展示站主界面不出现运维概念），
      // 管理员从侧栏底部头像菜单进入；页面内自检角色，非管理员显示登录卡
      path: '/admin',
      name: 'admin-stats',
      component: () => import('../views/AdminStatsView.vue'),
    },
  ],
})
