import { describe, expect, it } from 'vitest';
import { safeUrl } from './safeUrl';

describe('safeUrl', () => {
  it('allows http and https links', () => {
    expect(safeUrl('https://acme.example/policy')).toBe('https://acme.example/policy');
    expect(safeUrl('http://localhost:8000/doc')).toBe('http://localhost:8000/doc');
  });
  it('rejects script and data links', () => {
    expect(safeUrl('javascript:alert(1)')).toBeNull();
    expect(safeUrl('JaVaScRiPt:alert(1)')).toBeNull();
    expect(safeUrl('data:text/html;base64,AAAA')).toBeNull();
    expect(safeUrl('vbscript:x')).toBeNull();
  });
  it('rejects empty values', () => {
    expect(safeUrl('')).toBeNull();
    expect(safeUrl(undefined)).toBeNull();
  });
});
