import { ConnectSection, Savings, TeamsSection } from './Extra';
import { Cta, Defense, Footer, Gallery, Playground, Proof, Tracks } from './Sections';

/** Everything under the first two screens. Loaded as its own chunk once the reader is near it. */
export default function Below() {
  return (
    <>
      <Playground />
      <Defense />
      <Gallery />
      <Proof />
      <Savings />
      <ConnectSection />
      <TeamsSection />
      <Tracks />
      <Cta />
    </>
  );
}

export function BelowFooter() {
  return <Footer />;
}
