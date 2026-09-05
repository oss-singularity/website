import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdtemp, mkdir, writeFile, symlink, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { once } from 'node:events';

const script = fileURLToPath(new URL('../dev-server.mjs', import.meta.url));

test('development server requires an explicit local-mode flag', async () => {
  const child = spawn(process.execPath, [script], { stdio: ['ignore', 'pipe', 'pipe'] });
  const [status] = await once(child, 'exit');
  assert.equal(status, 1);
});

test('loopback server serves the build and real SQLite API without exposing private files', async (t) => {
  const root = await mkdtemp(join(tmpdir(), 'commons-server-test-'));
  const dist = join(root, 'dist');
  await mkdir(join(dist, 'workshop'), { recursive: true });
  await writeFile(join(dist, 'workshop', 'index.html'), '<!doctype html><title>Local Workshop</title>');
  await writeFile(join(root, 'private.txt'), 'PRIVATE TEST VALUE');
  await writeFile(join(dist, '.secret'), 'SECRET TEST VALUE');
  await writeFile(join(dist, 'source.map'), 'DISALLOWED TEST VALUE');
  await writeFile(join(dist, 'oversized.txt'), Buffer.alloc(1_048_577));
  await symlink(join(root, 'private.txt'), join(dist, 'linked.txt'));
  await symlink(root, join(dist, 'linked-directory'));
  const reservation = createServer();
  reservation.listen(0, '127.0.0.1');
  await once(reservation, 'listening');
  const port = reservation.address().port;
  await new Promise((resolve) => reservation.close(resolve));
  const admin = 'disposable_local_admin_for_integration_test_only';
  const database = join(root, 'db', 'local.sqlite');
  const child = spawn(process.execPath, [script, '--dev'], {
    env: { ...process.env, COMMONS_DEV_PORT: String(port), COMMONS_DEV_DIST: dist, COMMONS_DEV_DB: database, COMMONS_DEV_ADMIN_TOKEN: admin },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  t.after(async () => {
    if (child.exitCode === null) {
      const exit = once(child, 'exit');
      child.kill('SIGTERM');
      await exit;
    }
    await rm(root, { recursive: true, force: true });
  });
  let output = '';
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Development server did not start.')), 5000);
    child.stdout.on('data', (chunk) => {
      output += chunk.toString();
      if (output.includes('Commons development server:')) { clearTimeout(timeout); resolve(); }
    });
    child.once('exit', (code) => { clearTimeout(timeout); reject(new Error(`Development server exited: ${code}`)); });
  });
  const origin = `http://127.0.0.1:${port}`;
  assert.ok(!output.includes(admin));
  const page = await fetch(`${origin}/workshop/`);
  assert.equal(page.status, 200);
  assert.match(await page.text(), /Local Workshop/);
  for (const path of ['/linked.txt', '/linked-directory/private.txt', '/oversized.txt', '/.secret', '/source.map', '/%2e%2e/private.txt', '/missing.html']) {
    const result = await fetch(`${origin}${path}`);
    assert.equal(result.status, 404, path);
    assert.ok(!(await result.text()).includes('PRIVATE TEST VALUE'));
  }
  assert.equal((await stat(database)).mode & 0o777, 0o600);
  const submitted = await fetch(`${origin}/api/v1/proposals`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Origin: origin },
    body: JSON.stringify({ kind: 'project', title: 'Actual local integration', summary: 'A real local database round trip for the development server.' }),
  });
  assert.equal(submitted.status, 202);
  const receipt = await submitted.json();
  const own = await fetch(`${origin}${receipt.poll_url}`, { headers: { Authorization: `Bearer ${receipt.receipt_token}` } });
  assert.equal((await own.json()).status, 'pending');
  const publish = await fetch(`${origin}/api/v1/admin/proposals/${receipt.id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${admin}` },
    body: JSON.stringify({ status: 'published' }),
  });
  assert.equal(publish.status, 200);
  const feed = await fetch(`${origin}/api/v1/contributions`);
  assert.equal((await feed.json()).items[0].id, receipt.id);
  const denied = await fetch(`${origin}/api/v1/proposals`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Origin: 'https://cross-site.invalid' },
    body: '{}',
  });
  assert.equal(denied.status, 403);
});
