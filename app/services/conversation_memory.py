"""按会话编号管理查询历史。"""

from app.entities.conversation import SuccessfulTurn


class ConversationMemoryStore:
    """保存不同会话的成功查询记录。"""

    def __init__(self):
        self._histories: dict[str, list[SuccessfulTurn]] = {}
    def get_history(self, session_id: str) -> list[SuccessfulTurn]:
        """读取指定会话的成功记录，返回列表副本。"""
        return self._histories.get(session_id, []).copy()
    def append_success(self, session_id: str, turn: SuccessfulTurn) -> None:
        """将一轮成功查询追加到所属会话。"""
        if session_id not in self._histories:
            self._histories[session_id] = []

        self._histories[session_id].append(turn)

conversation_memory_store = ConversationMemoryStore()
