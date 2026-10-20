import assert from 'node:assert/strict';
import test from 'node:test';
import { arrangePriceLabels } from '../src/components/stock/stockHintAxisLayout.ts';
test('接近价格错开但保留真实坐标',()=>{
 const labels=arrangePriceLabels([{y:100},{y:102},{y:103}],20,200);
 assert.deepEqual(labels.map(l=>l.y),[100,102,103]);
 assert.ok(labels.every((l,i)=>!i||l.top-labels[i-1].top>=18));
});
test('边缘标签仍在主图范围内',()=>{
 const labels=arrangePriceLabels([{y:195},{y:196},{y:197}],20,200);
 assert.ok(labels.every(l=>l.top>=20&&l.top<=200));
 assert.ok(labels.every((l,i)=>!i||l.top-labels[i-1].top>=18));
});
test('异常、图外、有界输入',()=>{
 assert.deepEqual(arrangePriceLabels([{y:NaN},{y:400}],20,200),[]);
 assert.deepEqual(arrangePriceLabels([{y:100}],200,20),[]);
 assert.equal(arrangePriceLabels(Array.from({length:100},()=>({y:100})),20,250).length,12);
});
