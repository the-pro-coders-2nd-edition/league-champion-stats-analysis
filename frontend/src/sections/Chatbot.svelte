<script>
  export let data = {};
  export let sendMessage;

  const CONSENT_STORAGE_KEY = 'chatbotConsentAccepted';
  const STARTER_PROMPTS = [
    'What should I focus on to win more games?',
    'How did my last game go?',
    'How do I compare to players at my rank?',
  ];

  let isOpen = false;
  let consentChecked = false;
  let consentAccepted = false;
  try {
    consentAccepted = localStorage.getItem(CONSENT_STORAGE_KEY) === '1';
  } catch (err) {
    // Private mode: consent lives for this page only.
  }

  let history = [];
  let inputValue = '';
  let sending = false;
  let inputEl;

  $: available = !!data.chat_endpoint;
  $: reportRef = data.chat_report_ref || '';

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function inlineMarkdown(line) {
    return line
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>');
  }

  function renderMarkdown(text) {
    const lines = escapeHtml(text).split('\n');
    let html = '';
    let i = 0;
    while (i < lines.length) {
      if (/^\s*[-*]\s+/.test(lines[i])) {
        const items = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          items.push('<li>' + inlineMarkdown(lines[i].replace(/^\s*[-*]\s+/, '')) + '</li>');
          i++;
        }
        html += '<ul>' + items.join('') + '</ul>';
        continue;
      }
      if (lines[i].trim() === '') {
        i++;
        continue;
      }
      const paragraph = [inlineMarkdown(lines[i])];
      i++;
      while (i < lines.length && lines[i].trim() !== '' && !/^\s*[-*]\s+/.test(lines[i])) {
        paragraph.push(inlineMarkdown(lines[i]));
        i++;
      }
      html += '<p>' + paragraph.join('<br>') + '</p>';
    }
    return html;
  }

  function openPanel() {
    isOpen = true;
    if (consentAccepted && inputEl) inputEl.focus();
  }

  function closePanel() {
    isOpen = false;
  }

  function acceptConsent() {
    consentAccepted = true;
    try {
      localStorage.setItem(CONSENT_STORAGE_KEY, '1');
    } catch (err) {
      // Private mode: consent lives for this page only.
    }
    if (inputEl) inputEl.focus();
  }

  async function sendText(text) {
    if (!text || sending) return;
    history = [...history, { role: 'user', parts: [{ text }] }];
    inputValue = '';
    sending = true;
    try {
      const replyText = await sendMessage(reportRef, history);
      if (!replyText) throw new Error('The assistant returned an empty response.');
      history = [...history, { role: 'model', parts: [{ text: replyText }] }];
    } catch (err) {
      history = [...history, { role: 'error', parts: [{ text: err.message || 'Something went wrong.' }] }];
    } finally {
      sending = false;
      if (inputEl) inputEl.focus();
    }
  }

  function handleSubmit(event) {
    event.preventDefault();
    const text = inputValue.trim();
    if (!text || !consentAccepted) return;
    sendText(text);
  }

  function handleStarter(prompt) {
    sendText(prompt);
  }
</script>

<button id="chatbot-toggle" class="chatbot-toggle" aria-label="Ask about your stats" title="Ask coach" on:click={() => (isOpen ? closePanel() : openPanel())}>
  <iconify-icon icon="lucide:message-circle" aria-hidden="true"></iconify-icon>
</button>
<aside id="chatbot-panel" class="chatbot-panel{isOpen ? ' is-open' : ''}" aria-hidden={!isOpen}>
  <div id="chatbot-resize-handle" class="chatbot-resize-handle"></div>
  <div class="chatbot-header">
    <h2>Ask about your stats</h2>
    <button id="chatbot-close" class="chatbot-close" aria-label="Close" on:click={closePanel}>✕</button>
  </div>
  {#if !consentAccepted}
    <div id="chatbot-consent" class="chatbot-consent">
      <p>This chat is powered by <strong>Google Gemini</strong> (free tier). Google may
      use the messages you send here to improve their models. Don't paste anything you
      wouldn't want used that way.</p>
      <label>
        <input type="checkbox" id="chatbot-consent-checkbox" bind:checked={consentChecked}>
        I understand and want to continue.
      </label>
      <button id="chatbot-start-btn" class="chatbot-start-btn" disabled={!consentChecked} on:click={acceptConsent}>Start chatting</button>
    </div>
  {:else if !available}
    <div id="chatbot-no-key" class="chatbot-no-key">
      <p>This report was generated without a Gemini API key configured, so the chatbot
      isn't available. Set <code>GEMINI_API_KEY</code> and regenerate the report to
      enable it.</p>
    </div>
  {:else}
    <div id="chatbot-chat" class="chatbot-chat">
      <div id="chatbot-messages" class="chatbot-messages">
        {#if !history.length}
          <div class="chatbot-starters">
            <p class="chatbot-starters-label">Try asking:</p>
            {#each STARTER_PROMPTS as prompt}
              <button type="button" class="chatbot-starter" on:click={() => handleStarter(prompt)}>{prompt}</button>
            {/each}
          </div>
        {/if}
        {#each history as message}
          <div class="chatbot-bubble chatbot-bubble--{message.role}">
            {#if message.role === 'model'}
              {@html renderMarkdown(message.parts[0].text)}
            {:else}
              {message.parts[0].text}
            {/if}
          </div>
        {/each}
        {#if sending}
          <div class="chatbot-bubble chatbot-bubble--loading"><span></span><span></span><span></span></div>
        {/if}
      </div>
      <form id="chatbot-form" class="chatbot-form" on:submit={handleSubmit}>
        <input id="chatbot-input" type="text" placeholder="Ask about your stats…" autocomplete="off" bind:value={inputValue} bind:this={inputEl} disabled={sending}>
        <button id="chatbot-send" type="submit" disabled={sending}>Send</button>
      </form>
    </div>
  {/if}
</aside>
