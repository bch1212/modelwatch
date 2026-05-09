"""Embedding service — uses the customer's OpenAI key to get embeddings.

Falls back gracefully if the customer only has an Anthropic key (no embeddings).
"""

from typing import Optional

import openai


async def get_embedding(
    text: str,
    api_key: str,
    model: str = "text-embedding-3-small",
) -> Optional[list[float]]:
    """Get embedding vector using customer's OpenAI API key.

    Returns None if the call fails (no OpenAI key, rate limit, etc.).
    The diff engine handles None gracefully.
    """
    try:
        client = openai.AsyncOpenAI(api_key=api_key)
        resp = await client.embeddings.create(
            model=model,
            input=text[:8000],  # trim to stay within token limits
        )
        return resp.data[0].embedding
    except Exception:
        return None
