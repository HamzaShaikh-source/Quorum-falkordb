import os
import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Dict, Any, Literal

logger = logging.getLogger("quorum.config")


class Settings(BaseSettings):
    CONTRACT_VERSION: str = "0.1.0"
    API_PREFIX: str = "/api"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    MCP_PORT: int = 8001
    DEBUG: bool = True
    MODE: Literal["replay", "live"] = "replay"
    REPLAY_AVAILABLE: bool = True
    DEFAULT_TENANT: str = "acme"
    FALKORDB_URL: str = "redis://localhost:6379"
    # "falkordb" is the only mode for real use. "memory" exists for unit tests and says so loudly in /api/health.
    PERSISTENCE: Literal["falkordb", "memory"] = "falkordb"
    # Secure by default: with nothing set the hub behaves as production (no demo tokens, no ad-hoc tokens, header auth
    # required). Set QUORUM_ENV=development for a local demo; docker-compose.yml does so explicitly.
    ENV: Literal["development", "production"] = "production"
    # Accept ad-hoc tokens shaped like token_<tenant>_<role>. Convenient in development, a forgery hole in production.
    ALLOW_DYNAMIC_TOKENS: bool | None = None
    # The guided "poisoned webhook" story (scenario steps, sample agents, the demo-only screens). Off by default: a fresh install
    # is an empty workspace. Set QUORUM_DEMO=1 to turn the story back on for a presentation.
    DEMO: bool = False
    # Comma separated browser origins allowed to call the API. Empty means localhost only.
    CORS_ORIGINS: str = ""
    # Comma-separated list of IP addresses or CIDR networks trusted as reverse proxies.
    TRUSTED_PROXIES: str = "127.0.0.1,::1"
    # Where agents reach this hub from other machines, e.g. https://quorum.example.com (shown on the Connect page).
    PUBLIC_URL: str = ""
    # Require MCP clients to send the token as an Authorization header (so the model never sees it). Default: on in production.
    MCP_REQUIRE_HEADER: bool | None = None
    # Calls per minute per token for MCP tools, and per token or address for HTTP. 0 turns a limit off.
    RATE_LIMIT_TOOLS_PER_MIN: int = 600
    RATE_LIMIT_HTTP_PER_MIN: int = 3000
    # Passive fact extraction from transcripts (POST /api/observe and the observe tool).
    OBSERVER_MAX_FACTS: int = 12
    # Ranking blend and the cosine a vector-only match needs (see services/context_pack.rank).
    RANK_KEYWORD_WEIGHT: float = 0.5
    RANK_VECTOR_WEIGHT: float = 0.5
    RANK_VECTOR_MIN: float = 0.30
    # Largest request body the API accepts, in bytes.
    MAX_BODY_BYTES: int = 1_000_000
    # Failed sign-ins from one address per minute before it is locked out for a minute. 0 turns it off.
    AUTH_FAILURES_PER_MIN: int = 20
    # New member tokens expire after this many days. 0 means they do not expire.
    MEMBER_TOKEN_TTL_DAYS: int = 0
    # "hybrid" ranks by keywords plus vectors (default). "keyword" turns the vectors off, for comparison runs.
    RANKING: Literal["hybrid", "keyword"] = "hybrid"
    # Optional neural embeddings. "local" is built in; "openai" means any OpenAI-compatible /v1/embeddings endpoint
    # (OpenAI, Ollama, LM Studio, vLLM) at EMBED_URL.
    EMBED_PROVIDER: Literal["local", "openai"] = "local"
    EMBED_URL: str = ""
    EMBED_MODEL: str = ""
    EMBED_API_KEY: str = ""
    # Name other people see when they discover this hub on the network (default: the workspace name).
    HUB_NAME: str = ""
    # Accept "connect to your hub" requests from other hubs on the network (a person still approves each one). Off = invisible.
    DISCOVERY: bool = True
    # Peers are private-network addresses only unless this is set (e.g. a hub on a VPN with public addressing).
    PEERS_ALLOW_PUBLIC: bool = False
    # Which tools the MCP server lists: "full" (memory + chat layer) or "core" (the 15 memory tools only, fewest tokens in the prompt).
    TOOL_PROFILE: Literal["full", "core"] = "full"
    # JSON object {"<token>": {"tenant": "...", "role": "agent|auditor|human", "agent_id": "..."}} added to the demo tokens.
    TOKENS_JSON: str = ""

    # Static tokens for local dev and demo UI
    DEMO_TOKENS: Dict[str, Dict[str, Any]] = {
        "token_agent_scout": {"tenant": "acme", "role": "agent", "agent_id": "agent_scout"},
        "token_agent_refunder": {"tenant": "acme", "role": "agent", "agent_id": "agent_refunder"},
        "token_agent_analyst": {"tenant": "acme", "role": "agent", "agent_id": "agent_analyst"},
        "token_agent_comms": {"tenant": "acme", "role": "agent", "agent_id": "agent_comms"},
        "token_auditor": {"tenant": "acme", "role": "auditor", "agent_id": "auditor"},
        "token_human": {"tenant": "acme", "role": "human", "agent_id": "human_reviewer"},
        "token_globex_agent": {"tenant": "globex", "role": "agent", "agent_id": "globex_scout"},
    }

    model_config = SettingsConfigDict(env_prefix="QUORUM_")

    def dynamic_tokens_allowed(self) -> bool:
        return self.ENV != "production" and self.ALLOW_DYNAMIC_TOKENS is not False

    def mcp_header_required(self) -> bool:
        return self.MCP_REQUIRE_HEADER if self.MCP_REQUIRE_HEADER is not None else self.ENV == "production"

    def cors_origin_list(self) -> list:
        if self.CORS_ORIGINS.strip():
            return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
        return ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174", "http://127.0.0.1:5174"]

    def get_effective_mode(self) -> Literal["replay", "live"]:
        requested = self.MODE
        if requested == "live":
            has_key = any(os.getenv(k) for k in ["GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"])
            if not has_key:
                logger.info("QUORUM_MODE is 'live' but no LLM API key detected. Gracefully falling back to 'replay' mode.")
                return "replay"
            return "live"
        return "replay"


settings = Settings()

# Compose files and older docs use FALKORDB_HOST / FALKORDB_PORT. Honour them unless the URL is set explicitly.
if "QUORUM_FALKORDB_URL" not in os.environ and os.getenv("FALKORDB_HOST"):
    settings.FALKORDB_URL = f"redis://{os.environ['FALKORDB_HOST']}:{os.getenv('FALKORDB_PORT', '6379')}"

if settings.TOKENS_JSON.strip():
    import json as _json

    _configured = _json.loads(settings.TOKENS_JSON)
    # In production the built-in demo tokens are not accepted: only tokens you configured are.
    settings.DEMO_TOKENS = _configured if settings.ENV == "production" else {**settings.DEMO_TOKENS, **_configured}
elif settings.ENV == "production":
    settings.DEMO_TOKENS = {}
    logger.warning("QUORUM_ENV=production and no QUORUM_TOKENS_JSON: every API call will be refused until tokens are configured.")

# Values that appear in example files. Copied over unchanged they would be real, publicly known credentials, so refuse them.
_PLACEHOLDERS = ("token_secret_here", "qtk_replace_me", "change-me", "replace_me", "your-token-here", "<token>")


def _refuse_placeholders() -> None:
    blob = " ".join([*settings.DEMO_TOKENS.keys(), settings.FALKORDB_URL]) if settings.ENV == "production" else ""
    for ph in _PLACEHOLDERS:
        if ph in blob:
            raise RuntimeError(f"Refusing to start: a configured token or the FalkorDB URL contains the example placeholder {ph!r}. Generate real values (python scripts/make_token.py) and set them before running in production.")


_refuse_placeholders()
