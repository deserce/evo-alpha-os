import assert from 'node:assert/strict';
import test from 'node:test';
import { stockDetailReturn } from '../src/lib/stockDetailReturn.ts';

test('research return retains stock, historical cutoff and page', () => {
  const path = '/a/research?symbol=000001&date_to=2026-10-20&page=2';
  assert.equal(stockDetailReturn(path), path);
  assert.equal(stockDetailReturn('/a/research'), '/a/research');
  assert.equal(stockDetailReturn('/a/research?v=20261021'), '/a/research?v=20261021');
});
test('existing workspace returns remain unchanged', () => {
  assert.equal(stockDetailReturn('/a/strategy'), '/a/strategy');
  assert.equal(stockDetailReturn(null), '/a/watchlist');
});
test('external, malformed and unbounded return destinations are rejected', () => {
  for (const path of ['//evil.test/a/research', 'https://evil.test/a/research',
    '/a/research/../portfolio', '/a/research?symbol=foo', '/a/research?redirect=x',
    '/a/research#bad', `/a/research?q=${'x'.repeat(121)}`]) {
    assert.equal(stockDetailReturn(path), '/a/watchlist');
  }
});
