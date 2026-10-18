import { useState } from 'react';
import { useStore } from '../state/store';
import { copyText } from '../workspace/clipboard';
import { Icon } from './Icon';
import './network.css';

/** The prompt that does the rest of the setup inside the person's own AI. setup.py prints the same text with their real token filled in. */
export function aiPrompt(origin: string, token = '<YOUR TOKEN>'): string {
  const mcp = `${origin.replace(/\/+$/, '')}/mcp`;
  return [
    'You are setting up Quorum, my shared memory for AI agents and teammates. Do these steps in order and keep your replies short.',
    '',
    '1. Connect yourself to my Quorum hub (an MCP server over HTTP):',
    `   url: ${mcp}`,
    `   header: Authorization: Bearer ${token}`,
    `   - If you are Claude Code, run: claude mcp add --transport http quorum ${mcp} --header "Authorization: Bearer ${token}"`,
    '   - If you can edit your own MCP settings, add it. If you cannot, tell me the one thing I must do and wait.',
    '2. Call quorum_help and show me every command with what it does, in plain words.',
    '3. Call quorum_start and ask me its questions one at a time, then call it again with my answers.',
    '4. Call quorum_discover and tell me who else on my network runs Quorum. If I name someone, call quorum_connect. When anyone asks to join me, tell me and ask whether to approve (quorum_inbox, quorum_respond).',
    '5. Call quorum_status and explain what is remembered, what will be remembered and what is shared with whom.',
    '6. Call quorum_guide with topic=install and your client name (claude-code, cursor, vscode, windsurf, antigravity, claude-desktop, chatgpt), and write the working agreement it returns into the file it names, so every future session follows it. Never write my token into that file.',
    '7. Tell me in three lines how to use Quorum from here, and that quorum_guide explains any topic (start, remember, ask, derive, correct, team, connect, coordinate, time, people, console, troubleshoot) whenever I ask.',
    '',
    'From now on: call retrieve_context with format=compact before answering about our work, save durable facts with remember_fact or observe, and quarantine a wrong fact instead of deleting it. I manage teams and permission levels by asking you (quorum_manage).',
  ].join('\n');
}

export function AiPrompt() {
  const toast = useStore((s) => s.pushToast);
  const [open, setOpen] = useState(false);
  const text = aiPrompt(window.location.origin);
  return (
    <section className="card wide ai-prompt" aria-label="Set up from your AI">
      <div className="nw-head">
        <h2>Prefer your AI chat to this console?</h2>
        <p>Paste one prompt into Claude, Cursor, VS Code, Antigravity or any MCP-capable AI. It connects itself, lists every command, asks you a few questions, looks for teammates on your network and shows what is remembered. Replace the token placeholder with yours (the setup script prints the prompt with it filled in).</p>
      </div>
      <div className="tm-issued-actions">
        <button className="btn primary" onClick={() => void copyText(text).then((ok) => toast(ok ? 'Prompt copied. Paste it into your AI.' : 'Copy was blocked by the browser', ok ? 'good' : 'warn'))}>
          <Icon name="copy" size={13} /> Copy the prompt
        </button>
        <button className="btn quiet" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? 'Hide' : 'Read it first'}
        </button>
      </div>
      {open && <pre className="block-doc mono ai-prompt-text">{text}</pre>}
    </section>
  );
}
