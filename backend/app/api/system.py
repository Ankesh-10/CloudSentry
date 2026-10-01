from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.app.auth import actor_label, get_current_user, require_operator
from backend.app.services import runtime_config
from backend.app.services.audit_logger import AuditLogger

router = APIRouter()


class SystemConfigUpdate(BaseModel):
    value: str = Field(max_length=64)


class SystemConfigBody(BaseModel):
    key: str = Field(max_length=64)
    value: str = Field(max_length=64)


@router.get("/health")
async def detailed_health():
    from backend.app.services.health import collect_health
    return await collect_health(detailed=True)


@router.get("/")
@router.get("/config")
def get_system_configs():
    runtime_config.refresh_from_db()
    return runtime_config.snapshot()


def _set(key: str, value: str, user: dict):
    actor = actor_label(user)
    try:
        result = runtime_config.request_change(key, value, actor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception:
        raise HTTPException(status_code=503, detail="Failed to persist configuration; change not applied")
    parsed = result["value"]
    if result["status"] == "pending_confirmation":
        AuditLogger().log_action(
            event_type="config_change_requested",
            actor=actor,
            request_params={"key": key, "value": str(parsed)},
            message=f"{key}={parsed} requested; awaiting a second operator",
        )
        return JSONResponse(status_code=202, content={"status": "pending_confirmation", "key": key,
                                                      "value": parsed, "expires_at": result["expires_at"]})
    params = {"key": key, "value": str(parsed)}
    if result.get("requested_by"):
        params["requested_by"] = result["requested_by"]
    AuditLogger().log_action(
        event_type="config_changed",
        actor=actor,
        request_params=params,
        message=f"{key} set to {parsed}",
    )
    return {"status": "success", "key": key, "value": parsed}


@router.patch("/config")
def update_system_config_body(payload: SystemConfigBody, user: dict = Depends(require_operator)):
    return _set(payload.key, payload.value, user)


# Any authenticated user may stop automation: stopping is always the safe
# direction, and it must not wait for an operator to be found.
@router.post("/emergency-stop")
def emergency_stop(user: dict = Depends(get_current_user)):
    flags, persisted = runtime_config.emergency_stop()
    AuditLogger().log_action(
        event_type="emergency_stop",
        actor=actor_label(user),
        response_status="persisted" if persisted else "in_memory_only",
        message="GLOBAL_AUTOMATION_ENABLED set to false via emergency stop",
    )
    return {"automation_enabled": False, "persisted": persisted, "config": flags}


@router.patch("/{key}")
def update_system_config(payload: SystemConfigUpdate, key: str = Path(max_length=64),
                         user: dict = Depends(require_operator)):
    return _set(key, payload.value, user)
