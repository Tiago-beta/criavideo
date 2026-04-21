"""
Persona Router - persistent realistic persona image profiles.
"""

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import get_settings
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
settings = get_settings()

_ALLOWED_REFERENCE_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
_REFERENCE_IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class CreatePersonaProfileRequest(BaseModel):
    persona_type: str
    name: str = ""
    attributes: dict | None = None
    set_default: bool = False


class SetDefaultRequest(BaseModel):
    profile_id: int = Field(gt=0)


def _parse_attributes_json(attributes_json: str) -> dict:
    raw = str(attributes_json or "").strip()
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="attributes_json invalido")

    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="attributes_json deve ser um objeto JSON")

    return parsed


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


@router.post("/profiles/from-reference")
async def create_profile_from_reference(
    persona_type: str = Form(...),
    name: str = Form(""),
    attributes_json: str = Form("{}"),
    set_default: bool = Form(False),
    reference_image: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    normalized_type = normalize_persona_type(persona_type)
    attrs = normalize_persona_attributes(normalized_type, _parse_attributes_json(attributes_json))

    content_type = str(reference_image.content_type or "").lower().strip()
    if content_type and content_type not in _ALLOWED_REFERENCE_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Formato de imagem nao suportado")

    content = await reference_image.read()
    if not content:
        raise HTTPException(status_code=400, detail="Envie uma imagem de referencia valida")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Imagem muito grande (max 10MB)")

    reference_dir = Path(settings.media_dir) / "personas" / str(current_user["id"]) / "_reference_uploads"
    reference_dir.mkdir(parents=True, exist_ok=True)

    suffix = _REFERENCE_IMAGE_EXTENSIONS.get(content_type, "")
    if not suffix:
        inferred = Path(reference_image.filename or "").suffix.lower()
        suffix = inferred if inferred in {".jpg", ".jpeg", ".png", ".webp"} else ".png"

    reference_path = reference_dir / f"reference_{uuid.uuid4().hex[:12]}{suffix}"
    reference_path.write_bytes(content)

    try:
        profile = await create_persona_profile(
            db=db,
            user_id=current_user["id"],
            persona_type=normalized_type,
            name=name,
            attributes=attrs,
            set_default=set_default,
            reference_image_path=str(reference_path),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao criar persona com referencia: {exc}")
    finally:
        try:
            reference_path.unlink(missing_ok=True)
        except Exception:
            pass

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
