import { useEffect, useRef, type CSSProperties, type ReactNode } from 'react';
import { registerParallax } from './motion';

interface Props {
  speed: number;
  className?: string;
  innerClassName?: string;
  style?: CSSProperties;
  children?: ReactNode;
}

/** The outer box stays put and is measured; the inner box drifts with scroll. Negative speeds move against the scroll. */
export function Parallax({ speed, className, innerClassName, style, children }: Props) {
  const outer = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!outer.current || !inner.current) return;
    return registerParallax(outer.current, inner.current, speed);
  }, [speed]);
  return (
    <div ref={outer} className={className} style={style}>
      <div ref={inner} className={innerClassName} style={{ willChange: 'transform' }}>
        {children}
      </div>
    </div>
  );
}
