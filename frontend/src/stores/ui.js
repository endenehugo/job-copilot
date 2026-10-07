import { defineStore } from 'pinia'

/** 纯界面状态（与业务数据无关） */
export const useUiStore = defineStore('ui', {
  state: () => ({
    analysisDrawerOpen: false,
  }),
  actions: {
    openDrawer() {
      this.analysisDrawerOpen = true
    },
    toggleDrawer() {
      this.analysisDrawerOpen = !this.analysisDrawerOpen
    },
  },
})
