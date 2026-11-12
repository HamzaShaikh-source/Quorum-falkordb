import type { GraphEdge, GraphNode } from '../api/schemas';
import type { Graph } from './packer';

/** Small deterministic PRNG so the same size always yields the same graph (and the same numbers). */
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

const TOPICS = [
  ['Refund policy', 'refund window', 'refunds'],
  ['Shipping SLA', 'delivery window', 'shipments'],
  ['Warranty terms', 'warranty length', 'claims'],
  ['Escalation rota', 'on-call owner', 'escalations'],
  ['Pricing sheet', 'plan price', 'quotes'],
  ['Data retention', 'retention period', 'exports'],
  ['Onboarding guide', 'setup steps', 'accounts'],
  ['Security review', 'audit scope', 'approvals'],
] as const;

/**
 * A synthetic enterprise memory of roughly `size` nodes, shaped like the real one: sources feed facts, facts feed
 * conclusions, conclusions feed actions. Clearly synthetic; used only for the scale demo in the Context view.
 */
export function synthGraph(size: number, seed = 7): Graph {
  const r = rng(seed);
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  let e = 0;
  const base = { stale: false, status: 'HEALTHY' as const };
  const perTopic = Math.max(5, Math.round(size / TOPICS.length));
  TOPICS.forEach(([doc, fact, action], t) => {
    const src = `s_${t}`;
    nodes.push({ ...base, id: src, kind: 'source', label: `${doc} v${1 + Math.floor(r() * 4)}`, text: `Official ${doc.toLowerCase()} maintained by the ${action} team.` });
    const facts: string[] = [];
    const nFacts = Math.max(2, Math.round(perTopic * 0.4));
    for (let i = 0; i < nFacts; i++) {
      const id = `f_${t}_${i}`;
      const days = 7 + Math.floor(r() * 80);
      nodes.push({
        ...base,
        id,
        kind: 'fact',
        label: `${fact} ${i + 1}: ${days} days`,
        text: `Ticket thread ${1000 + Math.floor(r() * 9000)} states the ${fact} is ${days} days for ${action} in region ${['EU', 'US', 'APAC'][i % 3]}, confirmed in a customer email.`,
        key: fact.replace(' ', '_'),
        value: String(days),
        asserted_by: { id: `p${i % 5}`, name: ['Mira', 'Jon', 'Aisha', 'Leo', 'Sana'][i % 5]!, tier: 'established' },
      } as GraphNode);
      edges.push({ id: `e${e++}`, source: src, target: id, type: 'FROM_SOURCE' } as GraphEdge);
      facts.push(id);
    }
    const nCon = Math.max(1, Math.round(perTopic * 0.35));
    const cons: string[] = [];
    for (let i = 0; i < nCon; i++) {
      const id = `c_${t}_${i}`;
      nodes.push({
        ...base,
        id,
        kind: 'conclusion',
        label: `Standard ${action} rule ${i + 1}`,
        text: `Agents decided the standard ${action} rule ${i + 1} follows from the ${fact}; applies to open cases and new requests this quarter.`,
      } as GraphNode);
      for (const f of [facts[Math.floor(r() * facts.length)]!, facts[Math.floor(r() * facts.length)]!].filter((x, k, a) => a.indexOf(x) === k)) {
        edges.push({ id: `e${e++}`, source: f, target: id, type: 'LEADS_TO' } as GraphEdge);
      }
      cons.push(id);
    }
    const nAct = Math.max(1, Math.round(perTopic * 0.25));
    for (let i = 0; i < nAct; i++) {
      const id = `a_${t}_${i}`;
      nodes.push({ ...base, id, kind: 'action', label: `${action[0]!.toUpperCase()}${action.slice(1)} #${2000 + t * 100 + i}`, text: `Action ${id} executed under the ${action} rule.` } as GraphNode);
      edges.push({ id: `e${e++}`, source: cons[Math.floor(r() * cons.length)]!, target: id, type: 'LEADS_TO' } as GraphEdge);
    }
  });
  return { nodes, edges };
}
