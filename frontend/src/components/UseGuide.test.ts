import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import guide from '../../../backend/app/services/guide.json';
import { UseGuide } from './UseGuide';

describe('the console use guide', () => {
  const html = renderToStaticMarkup(createElement(UseGuide));
  it('has one tab per guide topic and shows the first topic open', () => {
    expect((html.match(/role="tab"/g) ?? []).length).toBe(guide.topics.length);
    for (const t of guide.topics) expect(html).toContain(t.title);
    expect(html).toContain((guide.topics[0]?.steps[0] ?? '').slice(0, 25));
  });
  it('tells the person how to teach their AI the working agreement', () => {
    expect(html).toContain('topic install');
  });
});
