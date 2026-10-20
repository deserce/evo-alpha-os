import assert from 'node:assert/strict';
import test from 'node:test';

import { createPortfolioClientRequestId } from './portfolio-client-request-id.ts';


test('uses randomUUID when the browser provides it', () => {
  assert.equal(
    createPortfolioClientRequestId({
      crypto: { randomUUID: () => 'uuid-from-browser' },
    }),
    'uuid-from-browser',
  );
});

test('falls back to getRandomValues when randomUUID is unavailable', () => {
  const requestId = createPortfolioClientRequestId({
    crypto: {
      getRandomValues: (values) => {
        values.set([0x12, 0x34, 0xab, 0xcd]);
        return values;
      },
    },
    now: () => 1_722_000_000_000,
  });

  assert.equal(
    requestId,
    'portfolio-1722000000000-1234abcd000000000000000000000000',
  );
});

test('still returns a bounded id when Web Crypto is absent', () => {
  const requestId = createPortfolioClientRequestId({
    crypto: undefined,
    now: () => 1_722_000_000_000,
    random: () => 0.25,
  });

  assert.match(requestId, /^portfolio-1722000000000-[a-z0-9]+$/);
  assert.ok(requestId.length <= 200);
});
