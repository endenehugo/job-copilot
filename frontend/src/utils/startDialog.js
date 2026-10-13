import { reactive } from 'vue'

// 发起任务对话框的全局状态：空态卡片、Composer、树快捷操作共用一个入口。
// 用模块级 reactive 单例，避免跨组件 prop 钻透。
export const startDialog = reactive({
  visible: false,
  tab: 'interview', // interview | analysis | rewrite
})

export function openStartDialog(tab = 'interview') {
  startDialog.tab = tab
  startDialog.visible = true
}
