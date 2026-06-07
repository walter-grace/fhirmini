"""Wire codecs for TCP message framing. MLLP is the healthcare default."""

VT, FS, CR = 0x0B, 0x1C, 0x0D  # MLLP: <VT> ... <FS><CR>


def mllp_wrap(payload: bytes) -> bytes:
    return bytes([VT]) + payload + bytes([FS, CR])


def mllp_extract(buffer: bytearray):
    """Pull complete MLLP frames out of a streaming buffer.
    Returns (list_of_message_bytes, leftover_buffer)."""
    msgs = []
    while True:
        try:
            start = buffer.index(VT)
        except ValueError:
            return msgs, bytearray()          # no frame start yet; drop noise
        end = buffer.find(bytes([FS, CR]), start + 1)
        if end == -1:
            return msgs, buffer[start:]        # partial frame; keep for next read
        msgs.append(bytes(buffer[start + 1:end]))
        buffer = bytearray(buffer[end + 2:])


def raw_newline_extract(buffer: bytearray):
    """Newline-delimited raw framing."""
    msgs, parts = [], buffer.split(b"\n")
    leftover = parts.pop()                     # last element is the incomplete tail
    for p in parts:
        if p.strip():
            msgs.append(p.rstrip(b"\r"))
    return msgs, bytearray(leftover)


def length_prefix_extract(buffer: bytearray):
    """4-byte big-endian length prefix framing."""
    msgs = []
    while len(buffer) >= 4:
        n = int.from_bytes(buffer[:4], "big")
        if len(buffer) < 4 + n:
            break
        msgs.append(bytes(buffer[4:4 + n]))
        buffer = bytearray(buffer[4 + n:])
    return msgs, buffer


EXTRACTORS = {
    "mllp": mllp_extract,
    "raw-newline": raw_newline_extract,
    "raw-lenprefix": length_prefix_extract,
}
