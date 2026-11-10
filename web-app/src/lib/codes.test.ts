import { describe, expect, it } from 'vitest';
import { normalizeCode, prettyCode } from './codes';

describe('codes', () => {
  it('normalizeCode ignores case, dashes and spaces', () => {
    expect(normalizeCode('abcd-efgh')).toBe('ABCDEFGH');
    expect(normalizeCode(' ab cd  ef-gh ')).toBe('ABCDEFGH');
    expect(normalizeCode(undefined as unknown as string)).toBe('');
  });
  it('prettyCode shows 4 + 4 with a dash as people type', () => {
    expect(prettyCode('ab')).toBe('AB');
    expect(prettyCode('abcd')).toBe('ABCD');
    expect(prettyCode('abcde')).toBe('ABCD-E');
    expect(prettyCode('abcd-efgh-zzzz')).toBe('ABCD-EFGH');
  });
});
