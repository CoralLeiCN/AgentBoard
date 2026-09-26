/* Local review uses saved evidence only; all evidence text is escaped before rendering. */
const Curation = (() => {
  const esc = (value) =>
    String(value ?? '').replace(
      /[&<>"']/g,
      (ch) =>
        ({
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;',
        })[ch],
    );
  const badge = (text, kind = '') => `<span class="badge ${esc(kind)}">${esc(text)}</span>`;

  function messages(target, inputOnly = false) {
    const selected = (target?.messages || []).filter((message) => !inputOnly || message.role === 'user');
    if (!selected.length) {
      return `<p class="muted">${inputOnly ? 'No recorded user input.' : 'No recorded messages.'}</p>`;
    }
    return selected
      .map(
        (message) => `
      <div class="message">
        <strong>${esc(message.role)}</strong><pre>${esc(message.content)}</pre>
      </div>
    `,
      )
      .join('');
  }

  function label(row) {
    return (
      badge(row.label.status, row.label.status) +
      badge(row.label.category || 'No label') +
      badge(row.disposition, row.disposition)
    );
  }

  function reviewAttribution(row) {
    if (!row.label.reviewer) return '';
    return `<p class="muted">Reviewed by ${esc(row.label.reviewer)} · ${esc(row.label.at)}</p>`;
  }

  function models(row) {
    return row.results
      .map(
        (result) => `
      <div class="model">
        <strong>${esc(result.configuration.model)}</strong> ·
        ${esc(result.configuration.reasoning_effort)} · ${esc(result.configuration.execution)}
        ${badge(result.status)}
        <p>${esc(result.result?.category || 'No result')} · ${esc(result.result?.reason || 'No saved explanation')}</p>
        ${result.status !== 'available' ? '<p class="muted">Excluded from comparison and reference inference.</p>' : ''}
        <details>
          <summary>Saved result evidence</summary><pre>${esc(JSON.stringify(result, null, 2))}</pre>
        </details>
      </div>
    `,
      )
      .join('');
  }

  function predecessorContext(row) {
    return `
      <details>
        <summary>Predecessor context · ${esc(row.context_status)}</summary>
        ${row.context ? messages(row.context) : '<p>No predecessor evidence in this selected dataset.</p>'}
      </details>
    `;
  }

  function duplicateTurn(row, title) {
    const audit = {
      target: row.target,
      embedding: row.embedding,
      removal: row.removal,
      history: row.history,
    };
    return `
      <section aria-label="${esc(title)}">
        <h3>${esc(title)}</h3>${label(row)}<p>${esc(row.label.reason)}</p>${reviewAttribution(row)}
        <p class="muted">
          Session: ${esc(row.target.original_codex_session_id)}<br>
          Turn: ${esc(row.target.original_codex_turn_id)}
        </p>
        <h4>Full turn transcript</h4>${messages(row.target)}${predecessorContext(row)}
        <h4>Original classifier results</h4>${models(row)}
        <details><summary>Turn metadata and decision history</summary><pre>${esc(JSON.stringify(audit, null, 2))}</pre></details>
      </section>
    `;
  }

  const similarityScore = (score) => `${Number(score).toFixed(3)} cosine similarity`;

  function embeddingDetails(similarity) {
    const revision = similarity.revision ? ` · ${esc(similarity.revision.slice(0, 12))}` : '';
    const model = `<p class="muted">Embedding model: ${esc(similarity.model)} · ${esc(similarity.provider)}${revision}</p>`;
    if (similarity.provider !== 'remote') return model;
    return `${model}
      <p class="muted">
        Endpoint: ${esc(similarity.base_url)} · Server model: ${esc(similarity.reported_model || 'not reported')}.
        Full input sent; server truncation is unknown.
      </p>
    `;
  }

  function pairButtons(active) {
    if (!active) return '<p>Restore removed turns from the Removed queue before changing this pair.</p>';
    return `
      <button data-action="keep_both">Keep both</button>
      <button class="remove" data-action="remove_left">Remove left turn</button>
      <button class="remove" data-action="remove_right">Remove right turn</button>
      <button data-action="reopen_pair">Reopen pair</button>
    `;
  }

  function renderPairReview(item, left, right, similarity) {
    const audit = {
      decision: item.decision,
      review: item.review,
      left_hash: left.normalized_input_sha256,
      right_hash: right.normalized_input_sha256,
      similarity,
    };
    return `
      <h2>Duplicate input review</h2>
      <p>${similarityScore(item.score)} · threshold ${similarity.threshold}</p>${embeddingDetails(similarity)}
      <p class="muted">
        Compare the full turns and classifier results before deciding what to keep.
        Similarity uses only the user inputs. Removing a turn excludes it from the active view
        and keeps its labels and source evidence.
      </p>
      <div class="columns">${duplicateTurn(left, 'Left turn')}${duplicateTurn(right, 'Right turn')}</div>
      <details><summary>Pair decision and input fingerprints</summary><pre>${esc(JSON.stringify(audit, null, 2))}</pre></details>
      <div class="review-form">
        <label>Decision reason<textarea id="reason" maxlength="2000" placeholder="Explain why to keep or remove the turn"></textarea></label>
        <div class="actions">${pairButtons(left.disposition === 'active' && right.disposition === 'active')}</div>
      </div>
    `;
  }

  function renderLabelReview(row, categories) {
    const options = categories
      .map(
        (category) => `
      <option value="${esc(category.id)}"${category.id === row.label.category ? ' selected' : ''}>${esc(category.label)}</option>
    `,
      )
      .join('');
    const audit = {
      session: row.target.original_codex_session_id,
      turn: row.target.original_codex_turn_id,
      input_sha256: row.target.classification_input_sha256,
      removal: row.removal,
      history: row.history,
    };
    return `
      <h2>Review turn label</h2>${label(row)}<p>${esc(row.label.reason)}</p>${reviewAttribution(row)}
      <p class="muted">${esc(row.comparison)} · ${row.coverage_complete ? 'Complete classifier coverage' : 'Some classifier results are unavailable'}</p>
      <h3>Target turn</h3>${messages(row.target)}${predecessorContext(row)}
      <h3>Original classifier results</h3>${models(row)}
      <div class="review-form">
        <label>Verified category
          <select id="category">
            <option value="" disabled${row.label.category ? '' : ' selected'}>Choose a category</option>${options}
          </select>
        </label>
        <label>Review reason<textarea id="reason" maxlength="2000" placeholder="Explain your label decision"></textarea></label>
        <div class="actions">
          <button class="primary" data-action="verify">Save verified label</button>
          <button data-action="reopen_label">Reopen label review</button>
          ${row.disposition === 'removed_duplicate' ? '<button data-action="restore">Restore removed turn</button>' : ''}
        </div>
      </div>
      <details><summary>Identity, removal and decision history</summary><pre>${esc(JSON.stringify(audit, null, 2))}</pre></details>
    `;
  }

  function renderQueueItem(item, queue) {
    const content =
      queue === 'pairs'
        ? `
      <span>${similarityScore(item.score)} · ${esc(item.decision)}</span>
      <small>${esc(item.left_turn.input || '(empty input)')}</small>
      <small>${esc(item.right_turn.input || '(empty input)')}</small>
    `
        : `
      <span>${esc(item.input || '(no user input)')}</span>
      <small>${esc(item.label.status)} · ${esc(item.label.category || 'No label')} · ${esc(item.comparison)}</small>
    `;
    return `<button class="queue-item" data-item="${esc(item.id)}">${content}</button>`;
  }

  async function request(path, body) {
    const options = body
      ? {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }
      : {};
    const response = await fetch(path, options);
    const value = await response.json();
    if (!response.ok) {
      throw Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail));
    }
    return value;
  }

  function createReviewController({ document, request: send = request }) {
    const $ = (id) => document.getElementById(id);
    const pageSize = 30;
    let workspace;
    let queue = 'disagreements';
    let offset = 0;
    let total = 0;
    let selection = null;
    let selectedRevision = 0;
    let busy = false;

    async function run(action) {
      if (busy) return;
      busy = true;
      $('error').hidden = true;
      document.querySelectorAll('button').forEach((button) => {
        button.disabled = true;
      });
      try {
        await action();
      } catch (error) {
        $('error').textContent = error.message;
        $('error').hidden = false;
      } finally {
        busy = false;
        document.querySelectorAll('button').forEach((button) => {
          button.disabled = false;
        });
        pagination();
      }
    }

    function pagination() {
      $('previous').disabled = offset === 0;
      $('next').disabled = offset + pageSize >= total;
    }

    function renderSummary() {
      const counts = workspace;
      $('counts').textContent =
        `${counts.turns} turns · ${counts.labels.verified || 0} verified · ${counts.labels.inferred || 0} inferred · ${counts.labels.unlabeled || 0} unlabeled · ${counts.removed} removed`;
      $('settings').textContent = JSON.stringify(
        {
          reference: counts.recipe.reference_configuration,
          similarity: counts.similarity,
          coverage_gaps: counts.incomplete_coverage,
          revision: counts.revision,
        },
        null,
        2,
      );
    }

    async function load() {
      workspace = await send('/api/workspace');
      renderSummary();
      $('pair-options').hidden = queue !== 'pairs';
      const url =
        queue === 'pairs'
          ? `/api/pairs?include_reviewed=${$('include-reviewed').checked}&offset=${offset}`
          : `/api/turns?queue=${queue}&offset=${offset}`;
      const data = await send(url);
      total = data.total;
      if (offset >= total && offset > 0) {
        offset = Math.max(0, Math.floor((total - 1) / pageSize) * pageSize);
        return load();
      }
      $('queue-count').textContent = total
        ? `${offset + 1}–${Math.min(offset + pageSize, total)} of ${total}`
        : 'No items in this queue.';
      $('items').innerHTML = data.items.map((item) => renderQueueItem(item, queue)).join('');
      selection = null;
      $('detail').innerHTML = `<p class="empty">${
        total
          ? 'Select an item to inspect its evidence.'
          : 'Nothing to review in this queue. All turns remain available in the other queues.'
      }</p>`;
      document.querySelectorAll('[data-item]').forEach((button) => {
        button.onclick = () => run(() => select(data.items.find((item) => item.id === button.dataset.item)));
      });
      pagination();
    }

    async function select(item) {
      selection = null;
      $('detail').innerHTML = '<p class="empty">Loading evidence…</p>';
      if (queue === 'pairs') {
        const [left, right] = await Promise.all([
          send('/api/turns/' + item.left),
          send('/api/turns/' + item.right),
        ]);
        if (left.revision !== right.revision || left.revision !== workspace.revision) {
          throw Error('Workspace changed; refresh before reviewing this pair.');
        }
        selectedRevision = left.revision;
        $('detail').innerHTML = renderPairReview(item, left, right, workspace.similarity);
      } else {
        const row = await send('/api/turns/' + item.id);
        selectedRevision = row.revision;
        $('detail').innerHTML = renderLabelReview(row, workspace.taxonomy.categories);
      }
      selection = item;
      document.querySelectorAll('[data-action]').forEach((button) => {
        button.onclick = () => run(() => decide(button.dataset.action));
      });
    }

    async function decide(action) {
      if (!selection) throw Error('Select an item before saving.');
      const reviewer = $('reviewer').value.trim();
      const reason = $('reason').value.trim();
      if (!reviewer || !reason) throw Error('Enter your reviewer name and a reason before saving.');
      const decision = { revision: selectedRevision, action, target: selection.id, reviewer, reason };
      if (action === 'verify') {
        decision.category = $('category').value;
        if (!decision.category) throw Error('Choose a category to verify.');
      }
      await send('/api/decisions', decision);
      $('notice').textContent = 'Decision saved. Original evidence is preserved.';
      await load();
    }

    async function setQueue(nextQueue) {
      queue = nextQueue;
      offset = 0;
      document.querySelectorAll('[data-queue]').forEach((button) => {
        button.setAttribute('aria-pressed', String(button.dataset.queue === queue));
      });
      await load();
    }

    async function exportDataset() {
      const result = await send('/api/export', { revision: workspace.revision });
      $('notice').textContent =
        `Saved immutable dataset ${result.id} at review revision ${result.curation.revision}.`;
      $('downloads').innerHTML = ['all', 'active', 'workspace']
        .map(
          (view) => `
        <a href="/api/exports/${encodeURIComponent(result.id)}/${view}" download>${
          { all: 'All turns', active: 'Active turns', workspace: 'Full audit' }[view]
        }</a>
      `,
        )
        .join('');
    }

    async function start() {
      document.querySelectorAll('[data-queue]').forEach((button) => {
        button.onclick = () => run(() => setQueue(button.dataset.queue));
      });
      $('refresh').onclick = () => run(load);
      $('previous').onclick = () =>
        run(async () => {
          offset = Math.max(0, offset - pageSize);
          await load();
        });
      $('next').onclick = () =>
        run(async () => {
          offset += pageSize;
          await load();
        });
      $('include-reviewed').onchange = () =>
        run(async () => {
          offset = 0;
          await load();
        });
      $('export').onclick = () => run(exportDataset);
      await run(load);
    }

    return { start, select, decide, setQueue, refresh: load };
  }

  return {
    esc,
    messages,
    label,
    models,
    duplicateTurn,
    similarityScore,
    embeddingDetails,
    pairButtons,
    renderLabelReview,
    renderPairReview,
    createReviewController,
  };
})();

if (typeof module !== 'undefined') module.exports = Curation;
if (typeof document !== 'undefined') Curation.createReviewController({ document }).start();
