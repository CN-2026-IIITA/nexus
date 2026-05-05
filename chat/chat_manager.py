import asyncio
import json
from typing import Tuple, Dict, Optional
from dataclasses import dataclass, field

from protocols import _BaseMessage

class ChatMsgType:
    CHAT_REQUEST = "CHAT_REQUEST"
    CHAT_MESSAGE = "CHAT_MESSAGE"
    CHAT_ACK     = "CHAT_ACK"

@dataclass
class ChatMessage(_BaseMessage):
    msg_type: str = ChatMsgType.CHAT_MESSAGE
    sender_id: str = ""
    receiver_id: str = ""
    timestamp: float = 0.0
    content: str = ""

@dataclass
class ChatRequest(_BaseMessage):
    msg_type: str = ChatMsgType.CHAT_REQUEST
    sender_id: str = ""
    receiver_id: str = ""
    timestamp: float = 0.0

@dataclass
class ChatAck(_BaseMessage):
    msg_type: str = ChatMsgType.CHAT_ACK
    ack_id: str = ""

def _decode_chat(data: bytes) -> Optional[_BaseMessage]:
    """Gracefully attempt to decode a chat packet."""
    try:
        d = json.loads(data.decode())
        mtype = d.get("msg_type")
        if mtype == ChatMsgType.CHAT_MESSAGE:
            return ChatMessage(**{k: v for k, v in d.items() if k in ChatMessage.__dataclass_fields__})
        elif mtype == ChatMsgType.CHAT_REQUEST:
            return ChatRequest(**{k: v for k, v in d.items() if k in ChatRequest.__dataclass_fields__})
        elif mtype == ChatMsgType.CHAT_ACK:
            return ChatAck(**{k: v for k, v in d.items() if k in ChatAck.__dataclass_fields__})
    except Exception:
        pass
    return None

class ChatManager:
    """Manages P2P chat messages by cleanly hooking into the existing node."""
    
    def __init__(self, node, storage):
        self.node = node
        self.storage = storage
        self._pending_acks: Dict[str, asyncio.Future] = {}
        
        # Intercept incoming datagrams dynamically (keeps changes modular)
        self._original_handle_datagram = self.node._handle_datagram
        self.node._handle_datagram = self._handle_datagram

    async def _handle_datagram(self, data: bytes, addr: Tuple[str, int]) -> None:
        """Route packet to chat handlers or fallback to the original network layer."""
        msg = _decode_chat(data)
        if msg:
            if msg.msg_type == ChatMsgType.CHAT_MESSAGE:
                self._handle_chat_message(msg, addr)
            elif msg.msg_type == ChatMsgType.CHAT_REQUEST:
                self._handle_chat_request(msg, addr)
            elif msg.msg_type == ChatMsgType.CHAT_ACK:
                self._handle_chat_ack(msg, addr)
            return
        
        # Fallback to existing routing/DHT logic
        await self._original_handle_datagram(data, addr)

    def _handle_chat_message(self, msg: ChatMessage, addr: Tuple[str, int]) -> None:
        print(f"\n[CHAT] {msg.sender_id[:8]}: {msg.content}")
        self.storage.store_message(msg.sender_id, msg.content, msg.timestamp)
        
        # Send ACK back using existing UDP transport
        ack = ChatAck(ack_id=msg.msg_id, sender_anr=self.node.anr.to_dict())
        self.node._transport.sendto(ack.to_bytes(), addr)

    def _handle_chat_request(self, msg: ChatRequest, addr: Tuple[str, int]) -> None:
        print(f"\n[CHAT] Peer {msg.sender_id[:8]} requested chat session.")
        ack = ChatAck(ack_id=msg.msg_id, sender_anr=self.node.anr.to_dict())
        self.node._transport.sendto(ack.to_bytes(), addr)

    def _handle_chat_ack(self, msg: ChatAck, addr: Tuple[str, int]) -> None:
        fut = self._pending_acks.get(msg.ack_id)
        if fut and not fut.done():
            fut.set_result(True)

    async def send_message(self, peer_id: str, content: str) -> bool:
        # Utilize existing Kademlia routing table to resolve the peer's address
        target_anr = self.node.routing_table.get_node(peer_id)
        if not target_anr:
            print(f"[CHAT] Error: Peer {peer_id[:8]} not found in routing table.")
            return False

        msg = ChatMessage(
            sender_id=self.node.anr.node_id,
            receiver_id=peer_id,
            timestamp=self.node.uptime_seconds,
            content=content,
            sender_anr=self.node.anr.to_dict()
        )
        
        fut = asyncio.get_event_loop().create_future()
        self._pending_acks[msg.msg_id] = fut
        
        try:
            # Send via existing transport layer
            self.node._transport.sendto(msg.to_bytes(), (target_anr.ip, target_anr.udp_port))
            
            # Await ACK non-blockingly
            await asyncio.wait_for(fut, timeout=5.0)
            self.storage.store_message(peer_id, content, msg.timestamp, is_sent=True)
            print(f"[CHAT] Delivered to {peer_id[:8]} ✓")
            return True
            
        except asyncio.TimeoutError:
            print(f"[CHAT] Failed to deliver message to {peer_id[:8]} (Timeout) ✗")
            return False
        finally:
            self._pending_acks.pop(msg.msg_id, None)
