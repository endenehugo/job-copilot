<script setup>
// 统一图标层：24×24 网格、1.7 描边、currentColor。
// Element Plus 的图标是实心风格，放进侧栏会显得比截图厚重，因此自绘一套描边图标。
const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 18 },
  strokeWidth: { type: [Number, String], default: 1.7 },
})

const PATHS = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  panel:
    '<rect x="3" y="3" width="18" height="18" rx="2.5"/><path d="M9 3v18"/>',
  more: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20.5 20.5l-4.2-4.2"/>',
  newChat:
    '<path d="M11 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/><path d="M18.4 3.6a2 2 0 0 1 2.8 2.8L12 15.6l-3.6.8.8-3.6Z"/>',
  target:
    '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.4"/>',
  chart: '<path d="M6 20v-5M12 20V8M18 20V4"/>',
  pen: '<path d="M12 20h9"/><path d="M16.5 3.6a2 2 0 0 1 2.9 2.8L7.8 18l-4 1 1-4Z"/>',
  folder:
    '<path d="M3 7.5A2 2 0 0 1 5 5.5h3.6a2 2 0 0 1 1.6.8l.9 1.2H19a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
  library:
    '<path d="M12 5.5S10 3.5 6.5 3.5H3v15h3.5C10 18.5 12 20 12 20s2-1.5 5.5-1.5H21v-15h-3.5C14 3.5 12 5.5 12 5.5Z"/><path d="M12 5.5V20"/>',
  image:
    '<rect x="3" y="3" width="18" height="18" rx="2.5"/><circle cx="8.8" cy="8.8" r="1.5"/><path d="M21 15.5l-4.8-4.8L6 21"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/>',
  file: '<path d="M14 2.5H6.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8Z"/><path d="M14 2.5V8h5.5"/>',
  arrowUp: '<path d="M12 19V5"/><path d="M5.5 11.5L12 5l6.5 6.5"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="2"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2.5"/><path d="M5.5 15H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v.5"/>',
  check: '<path d="M20 6.5L9.2 17.3 4 12.1"/>',
  close: '<path d="M18 6L6 18M6 6l12 12"/>',
  trash:
    '<path d="M3.5 6.5h17"/><path d="M9 6.5V4.5h6v2"/><path d="M18.5 6.5l-1 13.5h-11l-1-13.5"/>',
  logout:
    '<path d="M9.5 21H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.5"/><path d="M16 16.5l4.5-4.5L16 7.5"/><path d="M20.5 12H9.5"/>',
  user: '<circle cx="12" cy="8.5" r="4"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/>',
  chevronRight: '<path d="M9.5 18l6-6-6-6"/>',
  chevronDown: '<path d="M6 9.5l6 6 6-6"/>',
  sparkle:
    '<path d="M12 3.5l1.7 4.6 4.8 1.7-4.8 1.7L12 16.1l-1.7-4.6-4.8-1.7 4.8-1.7Z"/><path d="M18.5 16.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8Z"/>',
  refresh: '<path d="M20.5 12a8.5 8.5 0 1 1-2.6-6.1"/><path d="M20.5 4v5h-5"/>',
  warning: '<path d="M12 3.8L21 19.5H3Z"/><path d="M12 9.5v4.2"/><circle cx="12" cy="16.6" r="0.9"/>',
  pin: '<path d="M8.8 4.2h6.4a1 1 0 0 1 1 1.1l-.5 4 2.7 2.7a.55.55 0 0 1-.4.95H6a.55.55 0 0 1-.4-.95l2.7-2.7-.5-4a1 1 0 0 1 1-1.1Z"/><path d="M12 12.9v7.3"/>',
}
</script>

<template>
  <svg
    class="app-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
    v-html="PATHS[name] || ''"
  />
</template>

<style scoped>
.app-icon {
  display: block;
  flex: none;
}
</style>
