const MAX_BODY = 8192;
const DAY = 86_400_000;
const encoder = new TextEncoder();

export class ApiError extends Error {
  constructor(status, code, message, field, retry) {
    super(message);
    Object.assign(this, { status, code, field, retry });
  }
}

export function response(value, status = 200, extra = {}) {
  return new Response(status === 204 ? null : JSON.stringify(value), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      'CDN-Cache-Control': 'no-store',
      'X-Robots-Tag': 'noindex, nofollow',
      'X-Content-Type-Options': 'nosniff',
      'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'",
      'Referrer-Policy': 'no-referrer',
      ...extra,
    },
  });
}

export function invalid(message, field) {
  throw new ApiError(400, 'invalid_request', message, field);
}

export async function readJson(request, allowed) {
  if (request.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json') {
    throw new ApiError(415, 'unsupported_media_type', 'Send application/json.');
  }
  if (request.headers.has('content-encoding')) {
    throw new ApiError(415, 'unsupported_media_type', 'Compressed request bodies are not supported.');
  }
  const declared = request.headers.get('content-length');
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > MAX_BODY)) {
    throw new ApiError(413, 'body_too_large', 'The request body must be at most 8192 bytes.');
  }
  const reader = request.body?.getReader();
  if (!reader) invalid('A JSON object is required.');
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > MAX_BODY) {
        await reader.cancel();
        throw new ApiError(413, 'body_too_large', 'The request body must be at most 8192 bytes.');
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  let value;
  try {
    value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch {
    invalid('A valid UTF-8 JSON object is required.');
  }
  if (!value || Array.isArray(value) || typeof value !== 'object') invalid('A JSON object is required.');
  if (Object.keys(value).some((key) => !allowed.includes(key))) invalid('The request contains an unsupported field.');
  return value;
}

export function textField(value, name, min, max) {
  if (typeof value !== 'string') invalid(`${name} must be text.`, name);
  const cleaned = value.trim();
  const size = [...cleaned].length;
  if (size < min || size > max) invalid(`${name} must contain ${min} to ${max} characters.`, name);
  if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(cleaned)) invalid(`${name} contains unsupported control characters.`, name);
  return cleaned;
}

export function identifier(value, name) {
  if (typeof value !== 'string' || !/^[a-z0-9][a-z0-9-]{0,79}$/.test(value)) invalid(`${name} must be a valid identifier.`, name);
  return value;
}

export function hex(bytes) {
  return [...new Uint8Array(bytes)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

export async function digest(value) {
  return hex(await crypto.subtle.digest('SHA-256', encoder.encode(value)));
}

export function equalHash(a, b) {
  if (a.length !== 64 || b.length !== 64) return false;
  let difference = 0;
  for (let index = 0; index < 64; index++) difference |= a.charCodeAt(index) ^ b.charCodeAt(index);
  return difference === 0;
}

export function bearer(request) {
  const value = request.headers.get('authorization') || '';
  if (!/^Bearer [A-Za-z0-9_-]{32,256}$/.test(value)) throw new ApiError(401, 'unauthorized', 'A valid Bearer token is required.');
  return value.slice(7);
}

export async function requireAdmin(request, env) {
  if (typeof env.ADMIN_TOKEN !== 'string' || !/^[A-Za-z0-9_-]{32,256}$/.test(env.ADMIN_TOKEN)) {
    throw new ApiError(503, 'service_unavailable', 'Moderation is temporarily unavailable.');
  }
  if (!equalHash(await digest(bearer(request)), await digest(env.ADMIN_TOKEN))) {
    throw new ApiError(401, 'unauthorized', 'A valid Bearer token is required.');
  }
}

export async function rateKeys(ip, secret, now) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const hash = async (period) => hex(await crypto.subtle.sign('HMAC', key, encoder.encode(`${period}\n${ip}`)));
  return Promise.all([hash(`hour:${Math.floor(now / 3_600_000)}`), hash(`day:${Math.floor(now / DAY)}`)]);
}


export function randomToken() {
  return btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))
    .replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
}
