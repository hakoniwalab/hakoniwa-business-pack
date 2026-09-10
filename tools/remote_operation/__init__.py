"""Safe, state-machine-based coordination between Recipe hosts."""

from .protocol import (
    ProtocolError,
    decode_message,
    encode_message,
    make_message,
    validate_message,
    validate_transition,
)

__all__ = [
    "ProtocolError",
    "decode_message",
    "encode_message",
    "make_message",
    "validate_message",
    "validate_transition",
]
