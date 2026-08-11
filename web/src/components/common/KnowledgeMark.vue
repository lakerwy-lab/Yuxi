<template>
  <svg
    aria-hidden="true"
    class="knowledge-mark"
    fill="none"
    :height="size"
    viewBox="0 0 24 24"
    :width="size"
  >
    <path
      d="M4.5 6.75h4.25A3.25 3.25 0 0 1 12 10v9.25a4.15 4.15 0 0 0-3.25-1.5H4.5v-11Z"
      stroke="currentColor"
      stroke-linejoin="round"
      stroke-width="1.65"
    />
    <path
      d="M19.5 8.25v9.5h-4.25a4.15 4.15 0 0 0-3.25 1.5V10a3.25 3.25 0 0 1 3.25-3.25h1"
      stroke="currentColor"
      stroke-linejoin="round"
      stroke-width="1.65"
    />
    <path
      d="M18.25 3.75v4M16.25 5.75h4"
      stroke="var(--brand-accent, #0f766e)"
      stroke-linecap="round"
      stroke-width="1.55"
    />
    <circle cx="18.25" cy="5.75" fill="var(--brand-accent, #0f766e)" r="0.65" />
  </svg>
</template>

<script setup>
defineProps({
  size: {
    type: [String, Number],
    default: 24
  }
})
</script>
