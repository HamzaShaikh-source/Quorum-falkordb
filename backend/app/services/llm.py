import os
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger("quorum.llm")


class LLMAdapter:
    """
    Thin LLM adapter supporting Gemini, OpenAI, Anthropic, or graceful zero-key fallback.
    Selected via QUORUM_LLM_PROVIDER and QUORUM_LLM_MODEL.
    """
    def __init__(self):
        self.provider = os.getenv("QUORUM_LLM_PROVIDER", "").lower()
        self.model = os.getenv("QUORUM_LLM_MODEL", "")
        self.active_provider = self._detect_provider()

    def _detect_provider(self) -> str:
        # Check explicit selection first
        if self.provider == "gemini" and (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
            return "gemini"
        elif self.provider == "openai" and os.getenv("OPENAI_API_KEY"):
            return "openai"
        elif self.provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"

        # Auto-detect if provider not set
        if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            return "gemini"
        elif os.getenv("OPENAI_API_KEY"):
            return "openai"
        elif os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"

        return "none"

    @property
    def is_available(self) -> bool:
        return self.active_provider != "none"

    def generate_session_summary(self, session_id: str, messages: List[Dict[str, Any]], cached_summary: Optional[str] = None) -> str:
        """
        Generates session summary.
        - If cached_summary is present (or in replay mode), returns it.
        - If live mode with valid provider, calls LLM.
        - Deterministic fallback: byte-identical string without timestamps or random IDs.
        """
        if cached_summary:
            return cached_summary

        if not self.is_available:
            # Deterministic fallback without timestamps or random numbers
            msg_count = len(messages)
            topics = set()
            for m in messages:
                text = m.get("text", "")
                if "refund" in text.lower():
                    topics.add("refunds")
                if "policy" in text.lower():
                    topics.add("policy")
                if "email" in text.lower():
                    topics.add("notifications")
            topic_str = ", ".join(sorted(topics)) if topics else "general inquiries"
            return f"Session {session_id}: Processed {msg_count} messages relating to {topic_str}."

        # Live LLM call
        try:
            prompt = f"Summarize these {len(messages)} agent conversation messages in 1-2 factual sentences:\n"
            for m in messages:
                prompt += f"- {m.get('sender', 'agent')}: {m.get('text', '')}\n"

            if self.active_provider == "gemini":
                from google import genai
                client = genai.Client()
                model_name = self.model or "gemini-2.5-flash"
                resp = client.models.generate_content(model=model_name, contents=prompt)
                return (resp.text or "").strip()
            elif self.active_provider == "openai":
                import openai
                oai = openai.OpenAI()
                model_name = self.model or "gpt-4o-mini"
                oresp = oai.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=100,
                )
                return (oresp.choices[0].message.content or "").strip()
            elif self.active_provider == "anthropic":
                import anthropic
                client = anthropic.Anthropic()
                model_name = self.model or "claude-3-5-haiku-latest"
                resp = client.messages.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=100,
                )
                return resp.content[0].text.strip()
        except Exception as e:
            logger.warning(f"Live LLM call failed ({e}); using deterministic fallback.")

        # Fallback if call fails
        msg_count = len(messages)
        return f"Session {session_id}: Processed {msg_count} messages."


llm_adapter = LLMAdapter()
