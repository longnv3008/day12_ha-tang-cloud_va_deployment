"""
Authentication module — API Key verification.

Usage:
    from app.auth import verify_api_key

    @app.post("/ask")
    def ask(user_key: str = Depends(verify_api_key)):
        ...
"""
from fastapi import HTTPException, Security
from fastapi.security.api_key import APIKeyHeader


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """
    Verify X-API-Key header against AGENT_API_KEY env var.
    Returns the key on success; raises 401 on failure.
    """
    from app.config import settings  # lazy import avoids circular at module load
    if not api_key or api_key != settings.agent_api_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Include header: X-API-Key: <key>",
        )
    return api_key
