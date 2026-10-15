// 1. Validates every mock fixture and SSE event against the zod schemas and contracts/events.schema.json.
// 2. If QUORUM_BACKEND is set, diffs the live /openapi.json against contracts/openapi.snapshot.json.
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';

const fixtures = spawnSync('npx', ['vitest', 'run', 'src/test/contract.test.ts'], { stdio: 'inherit', shell: true });
if (fixtures.status !== 0) process.exit(fixtures.status ?? 1);

const backend = process.env.QUORUM_BACKEND;
const backendSnapshot = new URL('../../contracts/openapi.json', import.meta.url);
const snapshotPath = existsSync(backendSnapshot) ? backendSnapshot : new URL('../../contracts/openapi.snapshot.json', import.meta.url);
if (!backend) {
  console.log('\nLive drift check skipped: set QUORUM_BACKEND (for example http://localhost:8000) to diff /openapi.json.');
  process.exit(0);
}

const live = await fetch(`${backend}/openapi.json`).then((r) => r.json());
if (!existsSync(snapshotPath)) {
  writeFileSync(snapshotPath, JSON.stringify(live, null, 2));
  console.log('No snapshot yet. Wrote contracts/openapi.snapshot.json from the live backend. Review and commit it.');
  process.exit(0);
}

const snap = JSON.parse(readFileSync(snapshotPath, 'utf8'));
const paths = (o) => Object.keys(o.paths ?? {}).sort();
const schemas = (o) => Object.keys(o.components?.schemas ?? {}).sort();
const stable = (v) => JSON.stringify(v, (_k, x) => (x && typeof x === 'object' && !Array.isArray(x) ? Object.fromEntries(Object.entries(x).sort()) : x));
const drift = [];
for (const [label, a, b] of [
  ['paths', paths(snap), paths(live)],
  ['schemas', schemas(snap), schemas(live)],
]) {
  const missing = a.filter((x) => !b.includes(x));
  const added = b.filter((x) => !a.includes(x));
  if (missing.length) drift.push(`${label} removed from backend: ${missing.join(', ')}`);
  if (added.length) drift.push(`${label} new in backend: ${added.join(', ')}`);
}
for (const name of schemas(snap).filter((n) => schemas(live).includes(n))) {
  if (stable(snap.components.schemas[name]) !== stable(live.components.schemas[name])) drift.push(`schema changed: ${name}`);
}
if (drift.length) {
  console.error('CONTRACT DRIFT\n' + drift.map((d) => ' - ' + d).join('\n'));
  process.exit(1);
}
console.log('Live OpenAPI matches the snapshot.');
