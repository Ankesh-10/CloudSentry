from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from backend.app.config import settings

security = HTTPBearer()

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Verifies the JWT token from Supabase in the Authorization header.
    """
    token = credentials.credentials
    try:
        # Supabase uses HS256 with the JWT secret
        payload = jwt.decode(
            token, 
            settings.SUPABASE_JWT_SECRET, 
            algorithms=["HS256"],
            options={"verify_aud": False}
        )
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication credentials: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

def get_current_user(payload: dict = Depends(verify_token)):
    """
    Extracts the user from the verified JWT payload.
    """
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User ID not found in token")
    return {"id": user_id, "email": payload.get("email"), "role": payload.get("role")}
