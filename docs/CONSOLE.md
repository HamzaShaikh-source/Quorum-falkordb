# The console and the landing page

## The landing page (`#/`)
- Designed on a crisp editorial paper stock (`#f3f5f2`).
- Features a **live interactive hero**, scroll-spy problem statement, and an interactive **Click-to-Break Playground** allowing judges to inject bad facts and watch traditional RAG break live in their browser.
- Displays the **Defense Carousel**, console feature gallery, live proof metrics, and a 1-click **"Watch the 90-second story"** button that jumps directly into the Incident Command Room and starts Autopilot.

## The console (`#/app`)
- Designed as a printed field manual: ink on paper with one mint accent. Every tab is labeled and every status chip is in plain words.
- **Interactive Cytoscape DAG Canvas:** Real-time visualization of `Fact`, `Conclusion`, `Action`, and `Handoff` nodes with layered hierarchical layout.
- **Quarantine Red Wave:** Live animated ripple coloring poisoned nodes crimson by causal hop depth.
- **Prompt Diff Drawer:** Strikethrough comparison showing exact tokens stripped from agent memory.
- **Action Ledger & Handoff Board:** Live tracking of concurrent claim races, lease countdowns, and human review queues.
- **Time-Travel Slider:** Drag backwards in time to reconstruct the exact graph state at any millisecond (`as_of`).
- **AI at work (Overview):** a live feed of everything connected agents are reading, writing or being refused, each step with a plain-language purpose, so you can see exactly what the AI is using the memory for.
- **Guide (press `G`):** what the AI does here (read from the hub's tool catalog), a searchable **A to Z glossary** of every term, and the shortcuts. Each glossary entry links to the screen where you can see it.
- **Show Cypher Drawer:** Live inspect the exact parameterised Cypher statements executed for every operation.

---

## The console beyond the graph

- **Team** manages plan, teams, seats, members and links on the server, plus **Connect with teammates**: hubs visible on your network, requests waiting for your approval (with a permission level), and connections. Each member gets their own token (shown once, stored as a hash); presence comes from their real calls. Facts saved with `scope="team"` stay private to the team until a link shares them.
- **Connect** shows a real MCP handshake, paste-ready setup for Claude Code, Claude Desktop, Cursor, VS Code, Windsurf, Antigravity and any MCP client, a **paste-into-your-AI prompt**, the 28 tools read from the hub, and every real tool call (who, what, tokens used against a top-8 retriever).
- **Context** shows the memory as a mind map for people and as a compact map plus packed answer for agents, with live token numbers measured by the backend.
- **Activity** is the audit log in plain sentences (who did what, when, what it cost), filterable, with a CSV export.

Everything the console does is also available from your AI chat: see [`USER_GUIDE.md`](USER_GUIDE.md).
