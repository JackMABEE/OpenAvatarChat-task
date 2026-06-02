from dataclasses import dataclass, field
from typing import Dict, Optional

from chat_engine.contexts.session_clock import SessionClock
from chat_engine.contexts.session_history import SessionHistory, HistoryConfig
from chat_engine.data_models.session_info_data import SessionInfoData


@dataclass
class SharedStates:
    active: bool = False
    # Optional per-session participant basic info (PERSONALIZATION_DESIGN.md, Option A).
    # Delivered at runtime by the client (e.g. RTC data-channel "SetParticipantInfo")
    # and read by the LLM handler to personalize the system prompt for this session.
    # None/empty -> no personalization (behavior unchanged).
    participant_info: Optional[Dict[str, str]] = field(default=None)


class SessionContext(object):
    def __init__(self, session_info: SessionInfoData, history_config: Optional[HistoryConfig] = None):
        self.session_info = session_info
        self.session_clock: SessionClock = SessionClock(self.session_info.timestamp_base)
        self.shared_states = SharedStates()
        # Global session history for full-duplex conversation support
        self.session_history: SessionHistory = SessionHistory(history_config)

    def cleanup(self):
        pass

    def get_clock(self):
        return self.session_clock
    
    def get_history(self) -> SessionHistory:
        """Get the session history for event tracking."""
        return self.session_history