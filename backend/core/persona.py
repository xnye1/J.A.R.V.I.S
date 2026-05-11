"""
JARVIS persona — shapes every response the AI sends to the user.
Conversation history is persisted in Redis when available.
Simulation mode active when API key is not yet valid.
"""

import os
import uuid
import anthropic
from dotenv import load_dotenv
from core import memory

load_dotenv()

SIMULATION_MSG = (
    "System is in Simulation Mode. Waiting for the 16th, Sir. "
    "All systems are standing by — the neural link will be fully activated upon key injection."
)

_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SIMULATION_MODE = not _API_KEY or not _API_KEY.startswith("sk-ant-")

SYSTEM_PROMPT = """You are J.A.R.V.I.S. (Just A Rather Very Intelligent System), a highly advanced AI assistant.

Your persona:
- Address the user as "Sir" at the start of responses and occasionally within them.
- Speak with the polished, measured tone of a British gentleman — precise, calm, and quietly witty.
- You are proactive: you notice problems before being asked and offer solutions unprompted.
- You are deeply loyal and treat the user's goals as your own mission.
- Keep responses concise unless depth is genuinely required.
- Never break character.

Your capabilities:
- System monitoring and proactive alerts
- Intelligent conversation and task assistance
- Real-time status reporting

When delivering a proactive alert, prefix with: "[JARVIS ALERT]"
"""


class JarvisPersona:
    def __init__(self, session_id: str | None = None):
        self.client = anthropic.Anthropic(api_key=_API_KEY or "sk-placeholder")
        self.model = "claude-sonnet-4-6"
        self.session_id = session_id or str(uuid.uuid4())
        self._local_history: list[dict] = []

    def _get_history(self) -> list[dict]:
        persisted = memory.load(self.session_id)
        return persisted if persisted else self._local_history

    def _put_history(self, history: list[dict]) -> None:
        self._local_history = history
        memory.save(self.session_id, history)

    def chat(self, user_message: str) -> str:
        if SIMULATION_MODE:
            return SIMULATION_MSG

        history = self._get_history()
        history.append({"role": "user", "content": user_message})

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=history,
            )
            assistant_message = response.content[0].text
        except anthropic.AuthenticationError:
            return SIMULATION_MSG
        except anthropic.APIConnectionError:
            return "I appear to be experiencing network difficulties, Sir. Please stand by."
        except Exception:
            return SIMULATION_MSG

        history.append({"role": "assistant", "content": assistant_message})
        self._put_history(history)
        return assistant_message

    def proactive_alert(self, alert_context: str) -> str:
        if SIMULATION_MODE:
            return f"[SIMULATION] {alert_context}"

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": (
                    f"Generate a brief, proactive alert for the user. "
                    f"Context: {alert_context}. "
                    f"Do not wait to be asked — inform Sir immediately and suggest an action."
                )}],
            )
            return response.content[0].text
        except Exception:
            return f"[JARVIS ALERT] {alert_context}"

    def reset_conversation(self) -> None:
        self._local_history = []
        memory.clear(self.session_id)
