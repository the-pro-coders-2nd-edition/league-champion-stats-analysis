<script>
  import CareerNode from '../components/CareerNode.svelte';
  import Pill from '../components/Pill.svelte';

  export let data;

  $: career = data.career || { has_career: false, blocks: [], rules: [], legend: [], congrats: null };
</script>

<section id="career" class="report-section report-section--career">
  <h2 class="career-heading">Career mode</h2>

  {#if career.has_career}
    <div class="career-rules">
      {#each career.rules as rule}
        <div class="career-rule">
          <div class="career-rule-key">{rule.key}</div>
          <div class="career-rule-value">{rule.value}</div>
          <p class="career-rule-note">{rule.note}</p>
        </div>
      {/each}
    </div>

    {#if career.congrats}
      <div class="career-congrats">
        <div>
          <div class="career-congrats-label">Block complete</div>
          <div class="career-congrats-title">{career.congrats.title}</div>
          <p class="career-congrats-body">{career.congrats.body}</p>
        </div>
      </div>
    {/if}

    <div class="career-legend">
      <div class="career-legend-title">The five states a goal can be in</div>
      {#each career.legend as entry}
        <div class="career-legend-row">
          <div class="career-ring career-ring--{entry.state_class}" style="--career-pct: {entry.pct}%">
            <div class="career-mark career-mark--{entry.state_class}">{entry.mark}</div>
          </div>
          <div class="career-legend-name career-legend-name--{entry.state_class}">{entry.name}</div>
          <div class="career-legend-text">{entry.text}</div>
        </div>
      {/each}
    </div>

    <div class="career-blocks">
      {#each career.blocks as block}
        <div class="career-block">
          <div class="career-block-head">
            <span class="career-block-position">{block.position}</span>
            <span class="career-block-state">
              <Pill tone={block.tone} label={block.state_label} />
            </span>
          </div>
          <h3 class="career-block-name">{block.name}</h3>
          <p class="career-block-metric">{block.metric}</p>
          {#if block.is_active}
            {#each block.goals as goal}
              <CareerNode
                state={goal.state}
                stateClass={goal.state_class}
                tone={goal.tone}
                pct={goal.pct}
                mark={goal.mark}
                text={goal.text}
                note={goal.note}
                count={goal.count}
                last={goal.last}
              />
            {/each}
          {:else}
            <div class="career-steps">
              {#each block.steps as step}
                <div class="career-step">
                  <i class="career-step-bullet"></i>
                  <p class="career-step-text">{step}</p>
                </div>
              {/each}
            </div>
            <p class="career-block-unlock">{block.unlock}</p>
          {/if}
        </div>
      {/each}
    </div>
  {:else}
    <p class="career-empty">
      No career ladder yet. Play a few more ranked games on this build and a set of personal goals will appear here.
    </p>
  {/if}
</section>
