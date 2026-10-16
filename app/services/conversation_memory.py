"""按会话编号管理查询历史。"""

from app.entities.conversation import SuccessfulTurn


class ConversationMemoryStore:
    """保存会话成功记录，并管理当前话题的历史起点。"""

    def __init__(self):
        self._histories: dict[str, list[SuccessfulTurn]] = {}
        self._topic_starts: dict[str, int] = {}

    def get_history(self, session_id: str) -> list[SuccessfulTurn]:
        """读取当前话题的成功记录，返回列表副本。"""
        history = self._histories.get(session_id, [])
        start = self._topic_starts.get(session_id, 0)
        return history[start:]

    def start_new_topic(self, session_id: str) -> None:
        """以现有成功记录的末尾作为新话题起点。"""
        self._topic_starts[session_id] = len(
            self._histories.get(session_id, [])
        )

    def append_success(self, session_id: str, turn: SuccessfulTurn) -> None:
        """将一轮成功查询追加到所属会话。"""
        if session_id not in self._histories:
            self._histories[session_id] = []

        self._histories[session_id].append(turn)


conversation_memory_store = ConversationMemoryStore()
