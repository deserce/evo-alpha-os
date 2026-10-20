import test from 'node:test';
import assert from 'node:assert/strict';
import { selectAssistantHints } from '../src/lib/mrgcAssistant.ts';
const items = [{symbol:'300483',direction:'buy'}, {symbol:'601101',direction:'buy'}, {symbol:'000001',direction:'sell'}, {symbol:'000002',direction:'risk'}];
test('buy answers preserve the daily hints order and count', () => {
  assert.deepEqual(selectAssistantHints(items,{intent:'buy',filter:'buy',symbols:[]}).map(x=>x.symbol),['300483','601101']);
});
test('sell maps to the hints direction and empty categories stay empty', () => {
  assert.equal(selectAssistantHints(items,{intent:'exit',filter:'exit',symbols:[]})[0].symbol,'000001');
  assert.deepEqual(selectAssistantHints([],{intent:'risk',filter:'risk',symbols:[]}),[]);
});
test('stock lookup cannot return unrelated stocks and lists are bounded', () => {
  assert.deepEqual(selectAssistantHints(items,{intent:'stocks',filter:'all',symbols:['999999']}),[]);
  assert.equal(selectAssistantHints(Array(400).fill(items[0]),{intent:'summary',filter:'all',symbols:[]}).length,300);
});
