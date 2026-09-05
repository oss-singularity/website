import test from 'node:test';
import assert from 'node:assert/strict';
import { digest } from '../security.mjs';

test('production digest matches fixed SHA-256 vectors and encodes all 32 bytes as lowercase hex', async () => {
  // Independent known results keep tests that reuse digest from merely agreeing
  // with a changed implementation. These are public messages, not credentials.
  const vectors = [
    ['', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'],
    ['abc', 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'],
    ['abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq', '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1'],
  ];
  for (const [message, expected] of vectors) {
    const actual = await digest(message);
    assert.equal(actual, expected);
    assert.match(actual, /^[a-f0-9]{64}$/);
  }
});
