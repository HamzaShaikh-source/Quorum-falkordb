import { describe, expect, it } from 'vitest';
import { addToast, MAX_TOASTS, ttlFor, type Toast } from './toasts';

let n = 0;
const id = () => ++n;

describe('toasts', () => {
  it('keeps problems on screen longer than plain notices', () => {
    expect(ttlFor('Saved', 'bad')).toBeGreaterThan(ttlFor('Saved', 'info'));
  });
  it('gives long messages more time, with a ceiling', () => {
    expect(ttlFor('x'.repeat(120), 'info')).toBeGreaterThan(ttlFor('short', 'info'));
    expect(ttlFor('x'.repeat(5000), 'bad')).toBe(14000);
  });
  it('counts an identical message instead of stacking it', () => {
    let list: Toast[] = [];
    list = addToast(list, 'Memory changed', 'info', id);
    list = addToast(list, 'Memory changed', 'info', id);
    list = addToast(list, 'Memory changed', 'info', id);
    expect(list).toHaveLength(1);
    expect(list[0]?.count).toBe(3);
  });
  it('treats the same text with a different tone as a different toast', () => {
    let list: Toast[] = [];
    list = addToast(list, 'Nope', 'info', id);
    list = addToast(list, 'Nope', 'bad', id);
    expect(list).toHaveLength(2);
  });
  it('never shows more than the maximum, dropping the oldest', () => {
    let list: Toast[] = [];
    for (let i = 0; i < MAX_TOASTS + 3; i++) list = addToast(list, `message ${i}`, 'info', id);
    expect(list).toHaveLength(MAX_TOASTS);
    expect(list[list.length - 1]?.text).toBe(`message ${MAX_TOASTS + 2}`);
  });
});
