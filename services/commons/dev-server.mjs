import { createServer } from 'node:http';
import { randomBytes } from 'node:crypto';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, realpathSync, statSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Readable } from 'node:stream';
import worker from './worker.mjs';
import { SQLiteD1 } from './local-d1.mjs';

if (process.argv.slice(2).join(' ') !== '--dev') {
  process.stderr.write('Local development only. Start explicitly with: node services/commons/dev-server.mjs --dev\n');
  process.exit(1);
}

const port = Number(process.env.COMMONS_DEV_PORT || 4198);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('COMMONS_DEV_PORT must be between 1024 and 65535.');
const origin = `http://127.0.0.1:${port}`;
const dist = realpathSync(process.env.COMMONS_DEV_DIST || fileURLToPath(new URL('../../dist/', import.meta.url)));
const database = process.env.COMMONS_DEV_DB
  ? resolve(process.env.COMMONS_DEV_DB)
  : join(mkdtempSync(join(tmpdir(), 'oss-singularity-commons-')), 'commons.sqlite');
if (!existsSync(dirname(database))) mkdirSync(dirname(database), { recursive: true, mode: 0o700 });
const env = {
  DB: new SQLiteD1(database), PUBLIC_ORIGIN: origin,
  ADMIN_TOKEN: process.env.COMMONS_DEV_ADMIN_TOKEN || randomBytes(48).toString('base64url'),
  IP_HMAC_SECRET: randomBytes(48).toString('base64url'),
};
chmodSync(database, 0o600);

const types = new Map([
  ['.html', 'text/html; charset=utf-8'], ['.css', 'text/css; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'], ['.json', 'application/json; charset=utf-8'],
  ['.svg', 'image/svg+xml'], ['.png', 'image/png'], ['.webp', 'image/webp'],
  ['.jpg', 'image/jpeg'], ['.jpeg', 'image/jpeg'], ['.ico', 'image/x-icon'],
  ['.txt', 'text/plain; charset=utf-8'], ['.xml', 'application/xml; charset=utf-8'],
  ['.woff2', 'font/woff2'],
]);

const server = createServer(async (incoming, outgoing) => {
  try {
    const target = new URL(incoming.url, origin);
    if (incoming.headers.host !== `127.0.0.1:${port}` || target.origin !== origin) {
      outgoing.writeHead(403, { 'Content-Type': 'text/plain', 'Cache-Control': 'no-store' });
      outgoing.end('Local origin required.');
      return;
    }
    if (target.pathname.startsWith('/api/')) {
      const headers = new Headers();
      for (const [key, value] of Object.entries(incoming.headers)) {
        if (value !== undefined) headers.set(key, Array.isArray(value) ? value.join(', ') : value);
      }
      // A loopback development request stands in for Cloudflare's trusted edge IP.
      headers.set('cf-connecting-ip', '127.0.0.1');
      const hasBody = !['GET', 'HEAD'].includes(incoming.method);
      const request = new Request(target, {
        method: incoming.method, headers,
        ...(hasBody ? { body: Readable.toWeb(incoming), duplex: 'half' } : {}),
      });
      const result = await worker.fetch(request, env);
      outgoing.writeHead(result.status, Object.fromEntries(result.headers));
      outgoing.end(Buffer.from(await result.arrayBuffer()));
      return;
    }
    if (!['GET', 'HEAD'].includes(incoming.method)) {
      outgoing.writeHead(405, { Allow: 'GET, HEAD', 'Cache-Control': 'no-store' });
      outgoing.end();
      return;
    }
    const pathname = decodeURIComponent(target.pathname);
    if (pathname.includes('\\') || pathname.includes('\0') || pathname.split('/').some((part) => part.startsWith('.'))) throw new Error('Disallowed path.');
    let candidate = resolve(dist, `.${pathname}`);
    if (statSync(candidate).isDirectory()) candidate = join(candidate, 'index.html');
    const path = realpathSync(candidate);
    const contentType = types.get(extname(path));
    if (!path.startsWith(`${dist}${sep}`) || !contentType || !statSync(path).isFile()) throw new Error('Disallowed path.');
    const content = await readFile(path);
    outgoing.writeHead(200, { 'Content-Type': contentType, 'Content-Length': content.length, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-Robots-Tag': 'noindex, nofollow' });
    outgoing.end(incoming.method === 'HEAD' ? undefined : content);
  } catch {
    if (!outgoing.headersSent) outgoing.writeHead(404, { 'Content-Type': 'text/plain', 'Cache-Control': 'no-store' });
    outgoing.end('Not found.');
  }
});

const maintenance = setInterval(() => {
  worker.scheduled({}, env).catch(() => process.stderr.write('Local cleanup failed.\n'));
}, 60 * 60 * 1000);
maintenance.unref();
server.listen(port, '127.0.0.1', () => {
  process.stdout.write(`Commons development server: ${origin}/workshop/\nLocal SQLite: ${database}\n`);
});
server.on('error', () => {
  process.stderr.write('Could not start the local development server. Check the port and paths.\n');
  env.DB.sqlite.close();
  process.exit(1);
});
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    clearInterval(maintenance);
    server.close(() => { env.DB.sqlite.close(); process.exit(0); });
  });
}
