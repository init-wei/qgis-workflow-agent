const {test, before, after} = require('node:test')
const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const {chromium} = require('playwright')

const dist = path.resolve(__dirname, '../dist')
const task = 'task-a', session = 'session-a', other = 'session-b'
const source = 'shared/uploads/points.geojson'
const args = {name: '正确图层', path: source, layer_type: 'vector'}
const execution = {
  task_id: task, session_id: session, plan_revision: 2, status: 'success', confirmed: true,
  plan: {task_id: task, revision: 2, review_status: 'approved',
    tool_calls: [{step_id: 1, tool_name: 'add_layer', arguments: args}]},
  attempts: [{step_id: 1, tool_name: 'add_layer', tool_arguments: args, status: 'success',
    observation: {status: 'success', outcome: 'completed', data: {name: '正确图层', source}}}],
  artifacts: [{kind: 'layer', path: source, layer_id: 'layer-a', deliverable: true,
    spatial_validation: {status: 'passed', checks: [], facts: {}}}]
}
const history = {thread_id: session, input_query: '', chat_messages: [
  {role: 'user', content: '加载点图层'}, {role: 'assistant', content: '旧摘要中的名称：旧图层'}
], history: [], executions: [
  {task_id: 'older-task', plan_revision: 1, status: 'success', confirmed: true},
  {task_id: task, plan_revision: 2, status: 'success', confirmed: true}
]}
const emptyHistory = {thread_id: other, input_query: '', chat_messages: [
  {role: 'user', content: '会话 B 的消息'}
], history: [], executions: []}
const snapshot = id => ({thread_id: id, state: {task_plan: null}, pause: null,
  continuation: null, can_start_task: true, running: false})
const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return {promise, resolve}
}
async function bounded(promise) {
  let timer
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Expected request did not arrive')), 4000)
    })])
  } finally { clearTimeout(timer) }
}

let server, browser, origin
before(async () => {
  server = http.createServer((request, response) => {
    const pathname = new URL(request.url, origin).pathname
    const filename = path.resolve(dist, pathname === '/' ? 'index.html' : pathname.slice(1))
    if (!filename.startsWith(dist + path.sep) || !fs.existsSync(filename) || !fs.statSync(filename).isFile()) {
      response.writeHead(404).end(); return
    }
    const type = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}[path.extname(filename)]
    response.setHeader('Content-Type', type || 'application/octet-stream')
    fs.createReadStream(filename).pipe(response)
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  origin = `http://127.0.0.1:${server.address().port}`
  browser = await chromium.launch({headless: true})
})
after(async () => {
  if (browser) await browser.close()
  if (server) await new Promise(resolve => server.close(resolve))
})

async function scenario(t, options = {}) {
  const context = await browser.newContext({viewport: {width: 1450, height: 1000}})
  const failures = [], requests = []
  const gate = {started: deferred(), release: deferred(), sent: deferred(), hold: false}
  // Signal after XHR completion and the subsequent event-loop turn; the race test
  // waits on this instead of guessing how long the UI needs to process a response.
  await context.addInitScript(() => {
    window.__completedApiReads = []
    const original = XMLHttpRequest.prototype.open
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
      const endpoint = new URL(url, location.href).pathname
      this.addEventListener('loadend', () => setTimeout(() => window.__completedApiReads.push(endpoint), 0), {once: true})
      return original.call(this, method, url, ...rest)
    }
  })
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url())
    if (url.origin !== origin) { failures.push(`external: ${url.origin}`); await route.abort(); return }
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    requests.push(`${request.method()} ${url.pathname}`)
    if (request.method() !== 'GET') { failures.push(`write: ${request.method()} ${url.pathname}`); await route.abort(); return }
    let body, status = 200
    if (url.pathname === '/api/sessions') body = [
      {thread_id: session, name: '会话 A', created_at: '2026-01-01T00:00:00Z'},
      {thread_id: other, name: '会话 B', created_at: '2026-01-01T00:00:01Z'}]
    else if (url.pathname === '/api/files') body = {files: []}
    else if (url.pathname === '/api/qgis/status') body = {status: 'unknown', tools_count: 0}
    else if (url.pathname === `/api/sessions/${session}/history`) {
      status = options.historyError ? 503 : 200; body = options.empty ? {...history, executions: []} : history
    } else if (url.pathname === `/api/sessions/${other}/history`) body = emptyHistory
    else if (url.pathname === `/api/state/${session}`) body = snapshot(session)
    else if (url.pathname === `/api/state/${other}`) body = snapshot(other)
    else if (url.pathname === `/api/executions/${task}`) {
      if (gate.hold) {
        gate.hold = false; gate.started.resolve(); await gate.release.promise
        const stale = structuredClone(execution)
        stale.plan.tool_calls[0].arguments.name = '过期名称'
        stale.attempts[0].tool_arguments.name = '过期名称'
        stale.attempts[0].observation.data.name = '过期名称'
        await route.fulfill({status: 200, json: {execution: stale}}); gate.sent.resolve(); return
      }
      status = options.executionError ? 503 : 200
      body = {execution: options.record || execution}
    } else { failures.push(`unknown API: ${url.pathname}`); await route.abort(); return }
    await route.fulfill({status, json: body})
  })
  t.after(async () => {
    gate.release.resolve()
    await context.close()
    assert.deepEqual(failures, [])
  })
  const page = await context.newPage()
  page.setDefaultTimeout(4000)
  await page.goto(origin, {waitUntil: 'networkidle'})
  const select = label => page.locator('.session-item').filter({hasText: label}).click()
  return {page, select, requests, gate}
}

const card = page => page.locator('[aria-label="业务执行记录"]')
const textOfCard = async page => { await card(page).waitFor(); return card(page).innerText() }

test('finished session restores approved revision and recorded result', async t => {
  const {page, select, requests} = await scenario(t)
  await select('会话 A')
  const text = await textOfCard(page)
  for (const fact of ['task-a', 'r2', '已批准调用', '尝试与审核记录', 'add_layer', '正确图层',
    source, '空间验证：1 项通过', '空间验证通过', '用户确认：已确认成功']) assert.ok(text.includes(fact), fact)
  const approved = await card(page).getByText('已批准调用：', {exact: true}).locator('..').innerText()
  const attempts = await card(page).getByText('尝试与审核记录：', {exact: true}).locator('..').innerText()
  for (const fact of ['add_layer', '正确图层', source]) assert.ok(approved.includes(fact), `approved: ${fact}`)
  for (const fact of ['add_layer', '实际名称：正确图层', `实际来源：${source}`, '结果 completed'])
    assert.ok(attempts.includes(fact), `attempt: ${fact}`)
  assert.match(await page.getByRole('main').innerText(), /历史对话及摘要，仅供参考/)
  assert.equal(await page.getByRole('dialog', {name: /人工审核/}).isVisible(), false)
  assert.ok(requests.includes('GET /api/executions/task-a'))
  assert.ok(!requests.includes('GET /api/executions/older-task'))
  await page.reload({waitUntil: 'networkidle'}); await select('会话 A')
  assert.match(await textOfCard(page), /用户确认：已确认成功/)
})

test('empty, unavailable and mismatched records are not invented', async t => {
  const empty = await scenario(t, {empty: true})
  await empty.select('会话 A')
  await empty.page.getByText('旧摘要中的名称：旧图层').waitFor()
  await empty.page.waitForFunction(p => window.__completedApiReads.includes(p), `/api/state/${session}`)
  assert.equal(await card(empty.page).count(), 0)
  assert.ok(!empty.requests.includes('GET /api/executions/task-a'))

  const error = await scenario(t, {executionError: true})
  await error.select('会话 A')
  await error.page.getByText('无法读取业务执行记录，结果未知。', {exact: true}).waitFor()
  assert.equal(await card(error.page).count(), 0)

  const mismatch = await scenario(t, {record: {...execution, session_id: other}})
  await mismatch.select('会话 A')
  await mismatch.page.getByText('无法读取业务执行记录，结果未知。', {exact: true}).waitFor()
  assert.equal(await card(mismatch.page).count(), 0)

  const failedHistory = await scenario(t, {historyError: true})
  await failedHistory.select('会话 A')
  await failedHistory.page.getByText('无法读取会话历史，业务执行结果未知。', {exact: true}).waitFor()
  assert.equal(await card(failedHistory.page).count(), 0)
})

test('null and missing confirmations remain distinct; blocked attempt was not sent', async t => {
  const blocked = {...execution, status: 'failed', confirmed: null, artifacts: [], attempts: [
    {step_id: 1, tool_name: 'add_layer', tool_arguments: args, status: 'blocked',
      observation: {status: 'error', outcome: 'not_started'}}]}
  const a = await scenario(t, {record: blocked})
  await a.select('会话 A')
  const blockedText = await textOfCard(a.page)
  assert.match(blockedText, /用户确认：尚未确认/)
  assert.match(blockedText, /未发送/)
  assert.doesNotMatch(blockedText, /1\/1 项尝试或审核记录返回成功/)

  const readOnly = {...execution, plan: {...execution.plan,
    tool_calls: [{step_id: 1, tool_name: 'get_layers', arguments: {}}]}, artifacts: [], attempts: [
    {step_id: 1, tool_name: 'get_layers', tool_arguments: {}, status: 'success',
      observation: {status: 'success', outcome: null}}]}
  const read = await scenario(t, {record: readOnly})
  await read.select('会话 A')
  const readText = await textOfCard(read.page)
  assert.match(readText, /get_layers/)
  assert.match(readText, /1\/1 项尝试或审核记录返回成功/)
  assert.match(readText, /结果 未提供/)
  assert.doesNotMatch(readText, /结果未知|未发送/)

  const missing = structuredClone(execution)
  delete missing.confirmed; delete missing.attempts; delete missing.artifacts
  const b = await scenario(t, {record: missing})
  await b.select('会话 A')
  const missingText = await textOfCard(b.page)
  assert.match(missingText, /用户确认：确认状态未知/)
  assert.match(missingText, /记录未提供/)
  assert.doesNotMatch(missingText, /用户确认：尚未确认/)
})

test('a delayed A response cannot overwrite a newer A snapshot after visiting B', async t => {
  const {page, select, gate} = await scenario(t)
  gate.hold = true
  await select('会话 A'); await bounded(gate.started.promise)
  await select('会话 B'); await page.getByText('会话 B 的消息', {exact: true}).waitFor()
  assert.equal(await card(page).count(), 0)
  await select('会话 A')
  assert.match(await textOfCard(page), /正确图层/)
  const endpoint = `/api/executions/${task}`
  await page.waitForFunction(p => window.__completedApiReads.filter(x => x === p).length >= 1, endpoint)
  const beforeCount = await page.evaluate(p => window.__completedApiReads.filter(x => x === p).length, endpoint)
  gate.release.resolve(); await bounded(gate.sent.promise)
  await page.waitForFunction(([p, count]) => window.__completedApiReads.filter(x => x === p).length > count, [endpoint, beforeCount])
  const finalText = await textOfCard(page)
  assert.match(finalText, /正确图层/)
  assert.doesNotMatch(finalText, /过期名称/)
})
