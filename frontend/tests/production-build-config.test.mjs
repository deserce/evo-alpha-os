import assert from 'node:assert/strict';
import test from 'node:test';

async function loadConfig(distDir, cacheKey) {
  if (distDir === undefined) delete process.env.NEXT_DIST_DIR;
  else process.env.NEXT_DIST_DIR = distDir;
  return (await import(`../next.config.ts?case=${cacheKey}`)).default;
}

test('默认生产目录仍使用 Next.js 的 .next', async () => {
  const config = await loadConfig(undefined, 'default');
  assert.equal(config.distDir, undefined);
});

test('可使用独立目录预构建而不停止正在运行的前端', async () => {
  const config = await loadConfig('.next-stage', 'stage');
  assert.equal(config.distDir, '.next-stage');
});

test('空白预构建目录不覆盖默认配置', async () => {
  const config = await loadConfig('   ', 'blank');
  assert.equal(config.distDir, undefined);
});
