import bench from '../../../bench/retrieval_bench.json';
import ai from '../../../bench/ai_retrieval_summary.json';
import { fmt } from '../context/tokens';

const pc = (x: number) => `${Math.round(x * 100)}%`;

const ROWS: { key: 'direct' | 'para_known' | 'para_unknown'; label: string; detail: string }[] = [
  { key: 'direct', label: 'Same words as the fact', detail: '"What is the refund window in Chile?"' },
  { key: 'para_known', label: 'Different words the built-in vocabulary knows', detail: '"How long do shoppers in Chile have to get their money back?"' },
  { key: 'para_unknown', label: 'Different words it does not know', detail: '"Up to when may people in Chile ask for cash back?"' },
];

/**
 * Does the right fact come back? Read from bench/retrieval_bench.json (python bench/retrieval_bench.py): 336 facts,
 * 190 questions, keyword ranking against Quorum's keyword + vector ranking. The honest limit is the last row.
 */
export function RetrievalQuality() {
  const kw = bench.keyword_only;
  const hy = bench.hybrid;
  return (
    <div className="measured">
      <p className="measured-h">
        <b>Does the right fact come back?</b> {fmt(bench.facts)} facts across {bench.topics} topics, {fmt(bench.questions)} questions, scored on whether the right fact is the top result. Keyword ranking only, against Quorum&apos;s keyword plus vector ranking (
        {bench.embedding_provider === 'local' ? 'built-in local vectors, no model' : bench.embedding_provider}).
      </p>
      <div className="cn-table-wrap" tabIndex={0} role="region" aria-label="Retrieval quality by wording (scrolls sideways on a narrow screen)">
        <table className="measured-t">
          <thead>
            <tr>
              <th scope="col">How the question is worded</th>
              <th scope="col">Top result, keywords only</th>
              <th scope="col">Top result, with vectors</th>
              <th scope="col">Right fact in the top 3</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map((r) => (
              <tr key={r.key}>
                <th scope="row">
                  {r.label}
                  <small>{r.detail}</small>
                </th>
                <td>
                  <b className="num">{pc(kw[r.key]['recall@1'])}</b>
                </td>
                <td>
                  <b className="num">{pc(hy[r.key]['recall@1'])}</b>
                </td>
                <td>
                  <b className="num">
                    {pc(kw[r.key]['recall@3'])} → {pc(hy[r.key]['recall@3'])}
                  </b>
                </td>
              </tr>
            ))}
            <tr>
              <th scope="row">
                Questions the memory cannot answer
                <small>{bench.hybrid.none.questions} off-topic questions; the right answer is nothing</small>
              </th>
              <td>
                <b className="num">{pc(kw.none.clean_rate)} clean</b>
              </td>
              <td>
                <b className="num">{pc(hy.none.clean_rate)} clean</b>
              </td>
              <td>
                <small>a hit here is a shared word, not a guess</small>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="measured-h">
        <b>Real AIs, same memory.</b> Claude Sonnet and Claude Haiku each answered {ai.questions} questions from the {ai.facts}-fact memory through Quorum&apos;s MCP tools, once on a hub ranking with keywords only and once with keywords plus vectors. A
        correct answer has the exact number from the fact; an off-topic question must come back NOT FOUND.
      </p>
      <div className="cn-table-wrap" tabIndex={0} role="region" aria-label="Real AIs answering from the memory (scrolls sideways on a narrow screen)">
        <table className="measured-t">
          <thead>
            <tr>
              <th scope="col">Model</th>
              <th scope="col">Ranking</th>
              <th scope="col">Different words, known</th>
              <th scope="col">Different words, unknown</th>
              <th scope="col">Off-topic</th>
              <th scope="col">Total</th>
            </tr>
          </thead>
          <tbody>
            {ai.models.flatMap((m) =>
              (['keyword', 'hybrid'] as const).map((arm) => {
                const r = ai.summary[`${arm}/${m}` as keyof typeof ai.summary];
                return (
                  <tr key={`${arm}/${m}`}>
                    <th scope="row">{m}</th>
                    <td>{arm === 'keyword' ? 'keywords only' : 'keywords + vectors'}</td>
                    <td className="num">
                      {r.by_kind.para_known.correct}/{r.by_kind.para_known.questions}
                    </td>
                    <td className="num">
                      {r.by_kind.para_unknown.correct}/{r.by_kind.para_unknown.questions}
                    </td>
                    <td className="num">
                      {r.by_kind.none.correct}/{r.by_kind.none.questions}
                    </td>
                    <td>
                      <b className="num">
                        {r.correct}/{r.questions}
                      </b>
                    </td>
                  </tr>
                );
              }),
            )}
          </tbody>
        </table>
      </div>
      <p className="measured-f">{ai.note}</p>
      <p className="measured-f">{bench.caveat} Set QUORUM_EMBED_PROVIDER=openai with any OpenAI-compatible embeddings endpoint (including a local Ollama) for the last row.</p>
    </div>
  );
}
