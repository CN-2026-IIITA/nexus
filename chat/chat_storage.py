import threading
from typing import List, Dict

class ChatStorage:
    def __init__(self):
        self._lock = threading.Lock()
        # Maps peer_id -> List of message dictionaries
        self.history: Dict[str, List[dict]] = {}

    def store_message(self, peer_id: str, content: str, timestamp: float, is_sent: bool = False) -> None:
        with self._lock:
            if peer_id not in self.history:
                self.history[peer_id] = []
            
            self.history[peer_id].append({
                "content": content,
                "timestamp": timestamp,
                "is_sent": is_sent
            })

    def get_history(self, peer_id: str) -> List[dict]:
        with self._lock:
            return list(self.history.get(peer_id, []))
