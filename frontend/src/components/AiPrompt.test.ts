import { describe, expect, it } from 'vitest';
import { aiPrompt } from './AiPrompt';

describe('the AI setup prompt', () => {
  it('names the hub, the token header and every chat command in order', () => {
    const p = aiPrompt('http://192.168.1.20:8000/', 'qtk_abc');
    expect(p).toContain('url: http://192.168.1.20:8000/mcp');
    expect(p).toContain('Authorization: Bearer qtk_abc');
    const order = ['quorum_help', 'quorum_start', 'quorum_discover', 'quorum_connect', 'quorum_inbox', 'quorum_respond', 'quorum_status', 'quorum_manage'].map((c) => p.indexOf(c));
    expect(order.every((i) => i > 0)).toBe(true);
    expect(order.slice(0, 3)).toEqual([...order.slice(0, 3)].sort((a, b) => a - b));
  });
  it('uses a visible placeholder, never a real-looking token, when none is given', () => {
    expect(aiPrompt('http://h')).toContain('<YOUR TOKEN>');
  });
});
