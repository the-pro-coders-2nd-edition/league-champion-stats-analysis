<script lang="ts">
  export let anchor: string;
  export let title: string;
</script>

<a class="section-pulse-fix" href="#{anchor}">
  <span class="section-pulse-fix-kicker">Try this</span>
  <span class="section-pulse-fix-title">{title}</span>
</a>

<style>
  :global(.section-pulse-fix) {
    display: flex; flex-direction: column; gap: 6px;
    padding: 12px 14px; border-radius: 12px;
    border: 1px solid rgba(124, 108, 240, 0.28);
    background: linear-gradient(180deg, rgba(124, 108, 240, 0.10), rgba(30, 34, 48, 0.35));
    color: var(--color-text); text-decoration: none;
    transition: border-color .15s, transform .15s, background .15s;
  }
  :global(.section-pulse-fix:hover) {
    border-color: var(--color-accent);
    background: rgba(124, 108, 240, 0.12);
    transform: translateX(2px);
  }
  :global(.section-pulse-fix-kicker) {
    font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
    color: var(--color-accent);
  }
  :global(.section-pulse-fix-title) { font-size: 13px; font-weight: 600; line-height: 1.4; }
</style>
