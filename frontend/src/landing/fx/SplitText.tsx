import { createElement, type CSSProperties } from 'react';
import { useInView } from '../hooks';

interface Props {
  text: string;
  as?: 'h1' | 'h2' | 'h3' | 'p';
  className?: string;
  /** Start straight away instead of waiting for the element to scroll into view. */
  now?: boolean;
  step?: number;
}

/** Each word rises out of a mask, one after another. The full text stays available to assistive tech. */
export function SplitText({ text, as = 'h2', className, now = false, step = 55 }: Props) {
  const [ref, seen] = useInView<HTMLElement>(0.25);
  const on = now || seen;
  const words = text.split(' ');
  return createElement(
    as,
    { ref, className: `${className ?? ''} split${on ? ' on' : ''}`, 'aria-label': text },
    words.map((w, i) => (
      <span key={`${w}-${i}`} className="split-w" aria-hidden="true">
        <span className="split-i" style={{ transitionDelay: `${i * step}ms` } as CSSProperties}>
          {w}
        </span>
        {i < words.length - 1 ? ' ' : ''}
      </span>
    )),
  );
}
