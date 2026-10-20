interface CryptoLike {
  randomUUID?: () => string;
  getRandomValues?: (values: Uint8Array) => Uint8Array;
}

interface RequestIdOptions {
  crypto?: CryptoLike;
  now?: () => number;
  random?: () => number;
}

export function createPortfolioClientRequestId(
  options: RequestIdOptions = {},
): string {
  const cryptoProvider = Object.prototype.hasOwnProperty.call(options, 'crypto')
    ? options.crypto
    : globalThis.crypto;
  if (typeof cryptoProvider?.randomUUID === 'function') {
    return cryptoProvider.randomUUID();
  }

  const timestamp = (options.now ?? Date.now)();
  if (typeof cryptoProvider?.getRandomValues === 'function') {
    const values = cryptoProvider.getRandomValues(new Uint8Array(16));
    const entropy = Array.from(values)
      .map((value) => value.toString(16).padStart(2, '0'))
      .join('');
    return `portfolio-${timestamp}-${entropy}`;
  }

  const entropy = (options.random ?? Math.random)()
    .toString(36)
    .slice(2, 18);
  return `portfolio-${timestamp}-${entropy || 'fallback'}`;
}
