"""
fsm/ussd_pagination.py — USSD text pagination helpers.

USSD screens are limited to ~182 characters per response (including the
"CON " / "END " prefix). Long questions with MCQ options get truncated.
These helpers break long responses into 150-character chunks delivered
on demand via the '99' command.

Only called for channel="ussd". WhatsApp always receives the full text.
"""


def paginate_ussd(full_text: str, session: dict, chunk_size: int = 150) -> str:
    """
    If full_text fits within chunk_size, return it unchanged and clear any
    stale pagination state from session.

    If full_text exceeds chunk_size, store the full text and current offset
    in session, then return the first chunk with a '99. More' hint appended.

    Args:
        full_text:  Complete response text to potentially paginate.
        session:    Mutable dict used to store pagination state across requests.
        chunk_size: Maximum characters per chunk (default 150).

    Returns:
        The (possibly truncated) text to send to the student.
    """
    if len(full_text) <= chunk_size:
        session.pop("ussd_full_text", None)
        session.pop("ussd_text_offset", None)
        return full_text

    session["ussd_full_text"] = full_text
    session["ussd_text_offset"] = chunk_size

    return full_text[:chunk_size] + "\n99. More"


def get_next_ussd_chunk(session: dict, chunk_size: int = 150) -> str:
    """
    Return the next chunk of previously stored paginated text.

    Reads 'ussd_full_text' and 'ussd_text_offset' from session.
    If the remaining text fits in one chunk, clears pagination state.
    Otherwise advances the offset and appends '99. More'.

    Args:
        session:    Mutable dict holding pagination state.
        chunk_size: Maximum characters per chunk (default 150).

    Returns:
        The next text chunk to send to the student.
    """
    full_text = session.get("ussd_full_text", "")
    offset = session.get("ussd_text_offset", 0)

    remaining = full_text[offset:]

    if len(remaining) <= chunk_size:
        session.pop("ussd_full_text", None)
        session.pop("ussd_text_offset", None)
        return remaining

    session["ussd_text_offset"] = offset + chunk_size
    return remaining[:chunk_size] + "\n99. More"


def has_pending_pagination(session: dict) -> bool:
    """
    Return True if there is unread paginated text waiting in session.

    A non-zero offset means the first chunk was already sent and more
    text is waiting. An offset of 0 with text present would mean we
    haven't sent anything yet, which should not happen in normal flow.
    """
    return "ussd_full_text" in session and session.get("ussd_text_offset", 0) > 0
