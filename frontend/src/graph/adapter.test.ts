import { describe, expect, it } from 'vitest';
import { wrapNodeLabel } from './adapter';

describe('wrapNodeLabel', () => {
  it('handles empty or whitespace strings', () => {
    expect(wrapNodeLabel('')).toBe('');
    expect(wrapNodeLabel('   ')).toBe('');
  });

  it('keeps short labels on a single line', () => {
    expect(wrapNodeLabel('Short label')).toBe('Short label');
  });

  it('breaks words at whitespace when fitting within maxLineLen', () => {
    const wrapped = wrapNodeLabel('Alpha beta gamma delta', 14, 3);
    const lines = wrapped.split('\n');
    expect(lines.length).toBeLessThanOrEqual(3);
    for (const line of lines) {
      expect(line.length).toBeLessThanOrEqual(14);
    }
  });

  it('breaks long tokens at punctuation marks like underscores and dots', () => {
    const wrapped = wrapNodeLabel('CipherDOM_Hackering2.0 (1).pptx', 14, 3);
    const lines = wrapped.split('\n');
    expect(lines.length).toBeLessThanOrEqual(3);
    for (const line of lines) {
      expect(line.length).toBeLessThanOrEqual(14);
    }
    expect(wrapped).toContain('CipherDOM_');
  });

  it('truncates with ellipsis when text exceeds maxLines', () => {
    const wrapped = wrapNodeLabel('One two three four five six seven eight nine ten', 10, 2);
    const lines = wrapped.split('\n');
    expect(lines.length).toBe(2);
    expect(lines[1]?.endsWith('...')).toBe(true);
  });
});
