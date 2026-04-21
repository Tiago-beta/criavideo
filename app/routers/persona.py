"""
Persona Router - persistent realistic persona image profiles.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.services.persona_image import (
    PERSONA_LABELS,
    PERSONA_TYPES,
    default_persona_attributes,
    normalize_persona_attributes,
    normalize_persona_type,
)
from app.services.persona_registry import (
    create_persona_profile,
    delete_persona_profile,
    list_all_personas,
    list_persona_profiles,
    serialize_persona_profile,
    set_default_persona,
)

router = APIRouter(prefix="/api/persona", tags=["persona"])


class CreatePersonaProfileRequest(BaseModel):
    persona_type: str
    name: str = ""
    attributes: dict | None = None
    set_default: bool = False


class SetDefaultRequest(BaseModel):
    profile_id: int = Field(gt=0)


@router.get("/types")
async def get_persona_types() -> dict:
    return {
        "types": [
            {
                "key": key,
                "label": PERSONA_LABELS.get(key, key.title()),
                "default_attributes": default_persona_attributes(key),
            }
            for key in PERSONA_TYPES
        ]
    }


@router.get("/profiles")
async def get_persona_profiles(
    persona_type: str,
    ensure_default: bool = False,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    normalized_type = normalize_persona_type(persona_type)
    profiles = await list_persona_profiles(
        db=db,
        user_id=current_user["id"],
        persona_type=normalized_type,
        ensure_default=ensure_default,
    )
    return {
        "persona_type": normalized_type,
        "profiles": [serialize_persona_profile(profile) for profile in profiles],
    }


@router.get("/profiles/all")
async def get_all_persona_profiles(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    profiles = await list_all_personas(db=db, user_id=current_user["id"])
    grouped: dict[str, list] = {key: [] for key in PERSONA_TYPES}
    for profile in profiles:
        grouped.setdefault(profile.persona_type, []).append(serialize_persona_profile(profile))

    return {
        "profiles_by_type": grouped,
        "types": [
            {
                "key": key,
                "label": PERSONA_LABELS.get(key, key.title()),
                "default_attributes": default_persona_attributes(key),
            }
            for key in PERSONA_TYPES
        ],
    }


@router.post("/profiles")
async def create_profile(
    payload: CreatePersonaProfileRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    normalized_type = normalize_persona_type(payload.persona_type)
    attrs = normalize_persona_attributes(normalized_type, payload.attributes)

    try:
        profile = await create_persona_profile(
            db=db,
            user_id=current_user["id"],
            persona_type=normalized_type,
            name=payload.name,
            attributes=attrs,
            set_default=payload.set_default,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao criar persona: {exc}")

    return {
        "profile": serialize_persona_profile(profile),
        "message": "Persona criada com sucesso",
    }


@router.post("/profiles/default")
async def set_profile_default(
    payload: SetDefaultRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        profile = await set_default_persona(
            db=db,
            user_id=current_user["id"],
            profile_id=payload.profile_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "profile": serialize_persona_profile(profile),
        "message": "Persona padrao atualizada",
    }


@router.delete("/profiles/{profile_id}")
async def delete_profile(
    profile_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if profile_id <= 0:
        raise HTTPException(status_code=400, detail="profile_id invalido")

    try:
        result = await delete_persona_profile(
            db=db,
            user_id=current_user["id"],
            profile_id=profile_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "ok": True,
        **result,
        "message": "Persona removida",
    }
