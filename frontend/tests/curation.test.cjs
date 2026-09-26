const { test } = require('node:test');
const assert = require('node:assert/strict');
const { esc, messages, label, models, pairButtons, createReviewController } = require('../curation.js');

test('all evidence and label strings are escaped before HTML rendering', () => {
  assert.equal(esc('<img onerror="attack">'), '&lt;img onerror=&quot;attack&quot;&gt;');
  const target = {
    messages: [
      { role: 'user', content: '<script>private</script>' },
      { role: 'assistant', content: 'An answer' },
    ],
  };
  assert.match(messages(target), /&lt;script&gt;/);
  assert.doesNotMatch(messages(target), /<script>/);
  assert.doesNotMatch(messages(target, true), /An answer/);
  assert.match(messages({ messages: [] }, true), /No recorded user input/);
});

test('status, coverage failures and removal controls are explicit', () => {
  assert.match(
    label({ label: { status: 'inferred', category: 'coding' }, disposition: 'removed_duplicate' }),
    /inferred.*removed_duplicate/,
  );
  const html = models({
    results: [
      {
        configuration: { model: 'small', reasoning_effort: 'low', execution: 'independent' },
        status: 'changed_input',
        result: { category: 'coding', reason: '<b>old</b>' },
      },
    ],
  });
  assert.match(html, /changed_input/);
  assert.match(html, /Excluded from comparison/);
  assert.doesNotMatch(html, /<b>old/);
  assert.match(pairButtons(true), /Keep both/);
  assert.match(pairButtons(true), /Remove left turn/);
  assert.doesNotMatch(pairButtons(false), /data-action="remove/);
});

function harness(handler) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id))
      elements.set(id, { innerHTML: '', textContent: '', value: '', checked: false, hidden: false });
    return elements.get(id);
  };
  const document = { getElementById: element, querySelectorAll: () => [] };
  const workspace = {
    revision: 4,
    turns: 2,
    labels: { inferred: 2 },
    removed: 0,
    incomplete_coverage: 0,
    recipe: { reference_configuration: {} },
    taxonomy: { categories: [{ id: 'coding', label: 'Coding' }] },
    similarity: {
      threshold: 0.85,
      provider: 'remote',
      model: 'custom/model',
      base_url: 'http://example.test/v1',
      reported_model: 'deployed-model',
      server_truncation: 'unknown',
    },
  };
  const requests = [];
  const controller = createReviewController({
    document,
    request: async (path, body) => {
      requests.push({ path, body });
      if (path === '/api/workspace') return workspace;
      if (path.startsWith('/api/turns?') || path.startsWith('/api/pairs?')) return { items: [], total: 0 };
      return handler(path, body);
    },
  });
  return { controller, element, requests };
}

function turn(overrides = {}) {
  return {
    revision: 4,
    label: { status: 'inferred', category: 'coding', reason: 'Synthetic reason' },
    disposition: 'active',
    comparison: 'agreement',
    coverage_complete: true,
    results: [],
    target: { messages: [{ role: 'user', content: 'Synthetic input' }] },
    ...overrides,
  };
}

test('a failed evidence load clears the prior form and prevents saving its decision', async () => {
  const { controller, element, requests } = harness(async (path) => {
    if (path === '/api/turns/old') return turn();
    throw Error('Unavailable');
  });
  await controller.start();
  await controller.select({ id: 'old' });
  assert.match(element('detail').innerHTML, /Save verified label/);
  await assert.rejects(controller.select({ id: 'new' }), /Unavailable/);
  assert.doesNotMatch(element('detail').innerHTML, /Save verified label/);
  await assert.rejects(controller.decide('verify'), /Select an item/);
  assert.ok(requests.every((request) => request.path !== '/api/decisions'));
});

test('duplicate review presents full evidence and submits the inspected revision', async () => {
  const longAnswer = 'Complete assistant answer. '.repeat(500) + 'END OF LEFT ANSWER';
  const left = {
    revision: 4,
    label: { status: 'inferred', category: 'coding', reason: 'Reference explanation' },
    disposition: 'active',
    target: {
      original_codex_session_id: 'left-session',
      original_codex_turn_id: 'left-turn',
      messages: [
        { role: 'user', content: 'The same request' },
        { role: 'assistant', content: longAnswer },
        { role: 'tool', content: 'Recorded tool output <script>alert(1)</script>' },
      ],
    },
    results: [
      {
        configuration: { model: 'model-left', reasoning_effort: 'high', execution: 'batched' },
        status: 'available',
        result: { category: 'coding', reason: 'Left classifier explanation' },
      },
    ],
    context_status: 'available',
    context: { messages: [{ role: 'user', content: 'Earlier context' }] },
    history: [],
  };
  const right = {
    ...left,
    label: {
      status: 'verified',
      category: 'research',
      reason: 'Human explanation',
      reviewer: 'Reviewer',
      at: '2026-09-26T00:00:00Z',
    },
    target: {
      ...left.target,
      original_codex_session_id: 'right-session',
      original_codex_turn_id: 'right-turn',
      messages: [
        { role: 'user', content: 'The same request' },
        { role: 'assistant', content: 'A different right answer' },
      ],
    },
    results: [
      {
        configuration: { model: 'model-right', reasoning_effort: 'low', execution: 'independent' },
        status: 'invalid_result',
        result: { category: 'research', reason: 'Right classifier explanation' },
      },
    ],
  };
  const { controller, element, requests } = harness(async (path) => {
    if (path === '/api/decisions') return {};
    return path.endsWith('/left') ? left : right;
  });
  await controller.setQueue('pairs');
  await controller.select({ id: 'pair', left: 'left', right: 'right', score: 1, decision: 'pending' });
  assert.deepEqual(
    requests.filter((request) => request.path.startsWith('/api/turns/')).map((request) => request.path),
    ['/api/turns/left', '/api/turns/right'],
  );
  const html = element('detail').innerHTML;
  for (const text of [
    longAnswer,
    'A different right answer',
    'Earlier context',
    'Left classifier explanation',
    'Right classifier explanation',
    'left-session',
    'right-session',
    'verified',
    'inferred',
    'invalid_result',
    'Human explanation',
    'custom/model',
    'deployed-model',
    'server truncation is unknown',
    'Keep both',
    'Remove left turn',
    'Remove right turn',
  ]) {
    assert.ok(html.includes(text), `Missing evidence or decision control: ${text.slice(0, 60)}`);
  }
  assert.match(html, /Similarity uses only the user inputs/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  element('reviewer').value = ' Reviewer ';
  element('reason').value = ' Both results are useful ';
  await controller.decide('keep_both');
  assert.deepEqual(requests.find((request) => request.path === '/api/decisions').body, {
    revision: 4,
    action: 'keep_both',
    target: 'pair',
    reviewer: 'Reviewer',
    reason: 'Both results are useful',
  });
  assert.match(element('notice').textContent, /Decision saved/);
});

test('mixed revisions prevent an actionable duplicate review', async () => {
  const { controller, element, requests } = harness(async (path) =>
    turn({ revision: path.endsWith('/left') ? 4 : 5 }),
  );
  await controller.setQueue('pairs');
  await assert.rejects(controller.select({ id: 'pair', left: 'left', right: 'right' }), /Workspace changed/);
  assert.doesNotMatch(element('detail').innerHTML, /data-action/);
  await assert.rejects(controller.decide('remove_left'), /Select an item/);
  assert.ok(requests.every((request) => request.path !== '/api/decisions'));
});

test('label decisions validate required fields and preserve evidence after a rejected save', async () => {
  const { controller, element, requests } = harness(async (path) => {
    if (path === '/api/decisions') throw Error('Workspace changed');
    return turn();
  });
  await controller.start();
  await controller.select({ id: 'target' });
  await assert.rejects(controller.decide('verify'), /reviewer name and a reason/);
  element('reviewer').value = 'Reviewer';
  element('reason').value = 'Checked the evidence';
  await assert.rejects(controller.decide('verify'), /Choose a category/);
  element('category').value = 'coding';
  await assert.rejects(controller.decide('verify'), /Workspace changed/);
  assert.match(element('detail').innerHTML, /Synthetic input/);
  assert.deepEqual(requests.find((request) => request.path === '/api/decisions').body, {
    revision: 4,
    action: 'verify',
    target: 'target',
    reviewer: 'Reviewer',
    reason: 'Checked the evidence',
    category: 'coding',
  });
  assert.equal(element('notice').textContent, '');
});

test('embedding scores are cosine values, never duplicate probabilities or lexical percentages', () => {
  const { similarityScore } = require('../curation.js');
  assert.equal(similarityScore(0.91234), '0.912 cosine similarity');
  assert.equal(similarityScore(-0.125), '-0.125 cosine similarity');
  assert.equal(similarityScore(0.9), '0.900 cosine similarity');
});

test('embedding details show local pins and escaped remote provenance without a revision', () => {
  const { embeddingDetails } = require('../curation.js');
  assert.match(
    embeddingDetails({ provider: 'local', model: 'org/model', revision: 'abcdef123456' + 'a'.repeat(28) }),
    /local · abcdef123456/,
  );
  const html = embeddingDetails({ provider: 'remote', model: '<b>model</b>', base_url: 'http://host/v1' });
  assert.match(html, /&lt;b&gt;model&lt;\/b&gt;/);
  assert.match(html, /not reported/);
  assert.match(html, /server truncation is unknown/);
});
