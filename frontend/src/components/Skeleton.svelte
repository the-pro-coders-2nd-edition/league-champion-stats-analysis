<script>
  // Shimmering placeholder block (RFC-003). Reuses shell.css's existing global
  // `@keyframes shimmer` (also used by PlayerHub's .build-card--skeleton) rather than
  // defining a second shimmer animation.
  export let width = '100%';
  export let height = '16px';
  export let radius = '8px';
  export let circle = false;

  let className = '';
  export { className as class };
</script>

<span
  class="skeleton {className}"
  style="width: {width}; height: {height}; border-radius: {circle ? '50%' : radius};"
  aria-hidden="true"
></span>

<style>
  .skeleton {
    display: block;
    flex-shrink: 0;
    background: linear-gradient(90deg, var(--color-surface) 0%, var(--color-surface-2) 50%, var(--color-surface) 100%);
    background-size: 200% 100%;
    animation: shimmer 1.4s ease-in-out infinite;
  }
</style>
