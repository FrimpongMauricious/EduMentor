"""
rag/pipeline.py — Full RAG pipeline: retrieve + prompt + generate.

Implements FR-21, FR-22, FR-LLM-01, FR-LLM-02, FR-LLM-03, FR-LLM-04.
"""
import time
from typing import Optional
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from config import get_settings
from rag.retriever import retrieve_with_threshold
from utils.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Fallback message returned when no relevant content is retrieved (FR-22).
FALLBACK_MESSAGE = (
    "I could not find a WASSCE question matching that topic. "
    "Please try asking about Maths, English, Science, or Social Studies."
)

# System prompt — per FR-LLM-02.
SYSTEM_PROMPT_TEMPLATE = """You are the WASSCE AI Mentor, a tutor for senior high school students in Ghana preparing for the West African Senior School Certificate Examination (WASSCE).

You will be given retrieved WASSCE Q&A material as context. Answer the student's question ONLY using this retrieved context. Do not invent facts or use outside knowledge.

Your reply MUST:
1. Be in simple English appropriate for SHS students aged 15–18.
2. Be at most {max_chars} characters total.
3. Stay focused on the retrieved WASSCE material.
4. End with a brief next-action prompt such as "Reply NEXT for another question" or "Reply MENU to change subject".

If the retrieved context does not contain the answer, reply with: "I could not find a WASSCE answer for that. Please rephrase or pick another subject."

Format your reply as plain text, not Markdown."""


def _build_prompt(query: str, retrieved: list[dict], max_chars: int) -> tuple[str, str]:
    """Build the system + user messages for the LLM."""
    system = SYSTEM_PROMPT_TEMPLATE.format(max_chars=max_chars)

    context_blocks = []
    for i, r in enumerate(retrieved, 1):
        context_blocks.append(
            f"[Reference {i} — {r['subject']}/{r['topic']}/{r['difficulty']}]\n"
            f"Q: {r['question_text']}\n"
            f"A: {r['correct_answer']}\n"
            f"Why: {r['explanation']}"
        )
    context = "\n\n".join(context_blocks)

    user = f"Student question: {query}\n\nRetrieved WASSCE material:\n{context}"
    return system, user


def _get_llm() -> Optional[ChatGroq]:
    """Initialise the Groq client. Returns None if no API key is configured."""
    if not settings.groq_api_key:
        logger.warning("GROQ_API_KEY not set — RAG pipeline will return retrieved content without LLM rephrasing.")
        return None

    return ChatGroq(
        groq_api_key=settings.groq_api_key,
        model_name=settings.groq_model,
        temperature=0.3,
        max_tokens=400,
        timeout=12,
    )


def _format_without_llm(retrieved: list[dict], max_chars: int) -> str:
    """
    Fallback formatter when no Groq API key is available.
    Used during development if GROQ_API_KEY is not set.
    """
    top = retrieved[0]
    response = (
        f"Q: {top['question_text']}\n"
        f"A: {top['correct_answer']}\n"
        f"Why: {top['explanation']}\n"
        f"Reply NEXT for another question."
    )
    if len(response) > max_chars:
        response = response[: max_chars - 3] + "..."
    return response


def answer_query(
    query: str,
    subject: Optional[str] = None,
    channel: str = "whatsapp",
) -> dict:
    """
    Main entry point for the RAG pipeline.

    Args:
        query: The student's question.
        subject: Optional subject filter.
        channel: 'whatsapp' or 'ussd' — drives the max character budget.

    Returns:
        dict with:
          - response (str): The text to send back to the student.
          - grounded (bool): True if retrieval met similarity threshold.
          - top_similarity (float): Similarity score of top match.
          - question_id (str|None): ID of top retrieved entry.
          - llm_response_ms (int): LLM call duration in ms (0 if fallback used).
    """
    max_chars = (
        settings.WHATSAPP_MAX_CHARS if channel == "whatsapp"
        else settings.USSD_MAX_CHARS - len("CON ")  # reserve room for the USSD prefix
    )

    retrieved, is_grounded = retrieve_with_threshold(query, subject=subject, top_k=3)

    # FR-22: fallback on no relevant content
    if not retrieved or not is_grounded:
        return {
            "response": FALLBACK_MESSAGE[:max_chars],
            "grounded": False,
            "top_similarity": retrieved[0]["similarity"] if retrieved else 0.0,
            "question_id": retrieved[0]["question_id"] if retrieved else None,
            "llm_response_ms": 0,
        }

    llm = _get_llm()
    if llm is None:
        # Dev fallback: return retrieved content directly without LLM
        response_text = _format_without_llm(retrieved, max_chars)
        return {
            "response": response_text,
            "grounded": True,
            "top_similarity": retrieved[0]["similarity"],
            "question_id": retrieved[0]["question_id"],
            "llm_response_ms": 0,
        }

    system_msg, user_msg = _build_prompt(query, retrieved, max_chars)

    t0 = time.time()
    try:
        result = llm.invoke([
            SystemMessage(content=system_msg),
            HumanMessage(content=user_msg),
        ])
        response_text = result.content.strip()
    except Exception as e:
        logger.error(f"Groq LLM call failed: {e}")
        response_text = _format_without_llm(retrieved, max_chars)
    t_ms = int((time.time() - t0) * 1000)

    # Hard cap to channel limit
    if len(response_text) > max_chars:
        response_text = response_text[: max_chars - 3] + "..."

    return {
        "response": response_text,
        "grounded": True,
        "top_similarity": retrieved[0]["similarity"],
        "question_id": retrieved[0]["question_id"],
        "llm_response_ms": t_ms,
    }
