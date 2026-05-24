import logging
from typing import Dict, List
from fastapi import WebSocket

logger = logging.getLogger("app.core.websocket")


class ConnectionManager:
    """
    Manages active real-time WebSocket sessions for the platform.
    Allows targeting specific users for real-time notification push events.
    """

    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)
        logger.info(f"[WS Connect] User {user_id} registered new push connection. Active connections: {len(self.active_connections[user_id])}")

    def disconnect(self, websocket: WebSocket, user_id: int):
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
                logger.info(f"[WS Disconnect] User {user_id} disconnected a session.")
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    async def send_personal_message(self, message: dict, user_id: int):
        """Pushes a JSON message to all active WebSocket sessions for a specific user."""
        if user_id in self.active_connections:
            closed_sockets = []
            for ws in self.active_connections[user_id]:
                try:
                    await ws.send_json(message)
                except Exception as e:
                    logger.warning(f"[WS Push Error] Failed to send to socket for user {user_id}: {e}")
                    closed_sockets.append(ws)
            
            # Clean up dead sockets
            for dead_ws in closed_sockets:
                self.disconnect(dead_ws, user_id)

    async def broadcast(self, message: dict):
        """Pushes a JSON message to all active sessions across all users."""
        for user_id, sockets in list(self.active_connections.items()):
            closed_sockets = []
            for ws in sockets:
                try:
                    await ws.send_json(message)
                except Exception as e:
                    logger.warning(f"[WS Broadcast Error] Failed for user {user_id}: {e}")
                    closed_sockets.append(ws)
            
            # Clean up dead sockets
            for dead_ws in closed_sockets:
                self.disconnect(dead_ws, user_id)


# Global connection manager instance
manager = ConnectionManager()
