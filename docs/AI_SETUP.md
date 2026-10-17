# Set up Quorum from your AI chat

You do the download and one command. Your AI does the rest, and you never need the console.

## 1. Download and run

Needs Docker Desktop (running) and Python 3.

```bash
python scripts/setup.py          # Windows: double-click setup.bat   macOS/Linux: ./setup.sh
```

It writes `.env` with strong random secrets, starts FalkorDB, the hub and the console with production rules (token sign-in, FalkorDB password, no guessable tokens), starts the LAN beacon so teammates running Quorum can find you, then prints a prompt and copies it to your clipboard. The first run builds images, so allow a few minutes. Running it again reuses everything it made.

Options: `--name Maya --team "Support Desk"`, `--no-beacon` (do not announce this hub), `--add-to-claude-code` (also runs `claude mcp add` for you), `--reset` (new secrets). Port or address conflicts: set `QUORUM_API_PORT`, `QUORUM_UI_PORT`; setup picks a free Docker subnet by itself.

## 2. Paste the prompt into your AI

The prompt (also saved as `AI_SETUP_PROMPT.txt`, which contains your token: do not share it) tells the AI to:

1. connect itself to your hub (`quorum` MCP server, header `Authorization: Bearer <token>`),
2. call **`quorum_help`**: every command and what it does,
3. call **`quorum_start`**: a five-question survey, asked one question at a time: how many people, team name, what you use it for, save facts from chats automatically or not, look for teammates on the network,
4. call **`quorum_discover`**: who else on your Wi-Fi or LAN runs Quorum, so you can say "connect me to Maya's Quorum",
5. call **`quorum_status`**: what is remembered, what will be remembered, what is shared with whom.

| Your AI | How it connects | Notes |
| --- | --- | --- |
| Claude Code | `claude mcp add --transport http quorum <url>/mcp --header "Authorization: Bearer <token>"` | The prompt includes this exact line. |
| Cursor, Windsurf, VS Code | MCP settings: `url` plus an `Authorization` header | The Connect page prints the snippet for each. |
| Antigravity | `agy mcp add --header …` (see `docs/MCP.md`) | Verified end to end. |
| Claude Desktop | stdio through `mcp/bridge.py` | Token in `QUORUM_MCP_TOKEN`, never in an argument. |
| ChatGPT | HTTPS only; cannot send a header, so the token is a tool argument the model sees | Use a private hub. |

If your AI cannot edit its own MCP settings it tells you the one thing to do, then continues.

## 3. Everything else happens in the chat

| You say | The AI calls | What happens |
| --- | --- | --- |
| "What can you do?" | `quorum_help` | The command list. |
| "Set us up" | `quorum_start` | The survey, then team, seats and capture preference are set. |
| "Who's on my network?" | `quorum_discover` | Hubs visible by name. |
| "Connect me to Maya's Quorum" | `quorum_connect` | Maya's admin gets a notice in the chat they are already in. |
| (Maya) "Anyone asking to join?" | `quorum_inbox`, `quorum_respond` | Maya approves and picks Contributor, Reviewer or Admin. You receive your access automatically; no token is copied by hand. |
| "What do they know about refunds?" | `peer_retrieve` | You read their shared memory within the level they granted. |
| "Add Priya to Support as a contributor" | `quorum_manage` | A member and a token (shown once, with ready-to-paste settings). |
| "Make Priya a reviewer", "pause the link between Priya and Quinn", "new token for Priya" | `quorum_manage` | Applied on their next call. |
| "What's remembered and shared?" | `quorum_status` | Counts, what is captured, who sees what, who is connected, tokens saved. |

An admin is told about waiting requests in the chat itself: any tool result carries one line, `N connection request(s) waiting for you`, while one is open. The console shows the same on the Team page, with a toast when one arrives.

## How connecting stays safe

- Discovery lists names and addresses only; the beacon never carries a token. A packet is ignored unless the address it advertises belongs to the machine it came from.
- Peers must be on a private network (loopback, link-local or RFC 1918) unless you set `QUORUM_PEERS_ALLOW_PUBLIC`.
- A request creates a pending item. Nothing is granted until an admin approves, at a level they choose, and Admin is never the default.
- The token is minted at the moment the requester collects it, once; the approving hub never stores it in plaintext. Collecting needs a secret only the requester holds.
- The public handshake is rate limited (12 a minute per address), capped at 25 open requests, and off entirely with `QUORUM_DISCOVERY=0`.
- Connected peers read through their member token, so team scope, links and permission levels apply exactly as for a local member.

## Fewer tokens in your prompt

Measured from a live hub (characters / 4): the 15 memory tools cost your client about 2,000 tokens of tool descriptions, and the 10 chat tools add about 1,300. Set `QUORUM_TOOL_PROFILE=core` to list only the 17 memory and coordination tools once setup is done and keep the 1,300.
