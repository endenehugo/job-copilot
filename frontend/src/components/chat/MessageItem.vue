<script setup>
import { computed } from 'vue'
import { renderMarkdown } from '../../utils/markdown'
import SourcePanel from './SourcePanel.vue'

const props = defineProps({ message: { type: Object, required: true } })
const html = computed(() => renderMarkdown(props.message.content))
</script>

<template>
  <div class="message" :class="message.role">
    <div class="avatar">{{ message.role === 'user' ? '我' : 'AI' }}</div>
    <div class="bubble">
      <div v-html="html"></div>
      <SourcePanel
        v-if="message.role === 'assistant'"
        :sources="message.sources"
        :verification="message.verification"
      />
    </div>
  </div>
</template>
