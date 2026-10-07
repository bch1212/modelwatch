import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

const endpointId = 'db36fd13-738c-4600-8ed7-7ef2af87f049';
const binary = new URL('../dist/index.js', import.meta.url).pathname;

test('MCP create_spec matches the API contract and health advertises its real shape', async (t) => {
  const requests = [];
  const api = createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    requests.push({ method: req.method, url: req.url, body: body ? JSON.parse(body) : null });
    res.setHeader('content-type', 'application/json');
    if (req.method === 'POST' && req.url === `/api/specs?endpoint_id=${endpointId}` &&
        requests.at(-1).body.input_text === 'Probe prompt') {
      res.writeHead(201);
      res.end(JSON.stringify({ id: 'new-spec' }));
    } else if (req.method === 'GET' && req.url === '/api/dashboard/health') {
      res.end(JSON.stringify([{ spec_id: 'new-spec', status: 'green' }]));
    } else {
      res.writeHead(422);
      res.end(JSON.stringify({ detail: 'API contract mismatch' }));
    }
  });
  api.listen(0, '127.0.0.1');
  await new Promise((resolve) => api.once('listening', resolve));
  t.after(() => api.close());

  const client = new Client({ name: 'modelwatch-contract-test', version: '1.0.0' });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [binary],
    env: { ...process.env, MODELWATCH_API_KEY: `mw_${'a'.repeat(43)}`,
      MODELWATCH_API_BASE: `http://127.0.0.1:${api.address().port}` },
  });
  t.after(async () => client.close());
  await client.connect(transport);
  const tools = (await client.listTools()).tools;
  assert.match(tools.find((tool) => tool.name === 'get_health').description, /per-spec health/i);
  assert.equal(tools.find((tool) => tool.name === 'create_spec').inputSchema.properties.threshold, undefined);
  assert.equal(tools.find((tool) => tool.name === 'create_endpoint').inputSchema.properties.base_url, undefined);

  const created = await client.callTool({ name: 'create_spec', arguments: {
    endpoint_id: endpointId, name: 'canary', prompt: 'Probe prompt',
    frequency: 'weekly', semantic_threshold: 0.8,
  } });
  assert.equal(JSON.parse(created.content[0].text).id, 'new-spec');
  assert.deepEqual(requests[0], {
    method: 'POST', url: `/api/specs?endpoint_id=${endpointId}`,
    body: { name: 'canary', input_text: 'Probe prompt', schedule: 'weekly', semantic_threshold: 0.8 },
  });
  const defaulted = await client.callTool({ name: 'create_spec', arguments: {
    endpoint_id: endpointId, name: 'default canary', prompt: 'Probe prompt',
  } });
  assert.equal(JSON.parse(defaulted.content[0].text).id, 'new-spec');
  assert.deepEqual(requests[1].body, { name: 'default canary', input_text: 'Probe prompt', schedule: 'daily' });
  const health = await client.callTool({ name: 'get_health', arguments: {} });
  assert.deepEqual(JSON.parse(health.content[0].text), [{ spec_id: 'new-spec', status: 'green' }]);
});
