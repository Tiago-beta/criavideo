"""
Persona registry service.
Handles profile CRUD, default behavior and reference image resolution.
"""

import logging
import os
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import Image, ImageOps

from app.config import get_settings
from app.models import PersonaProfile
from app.services.persona_image import (
    PERSONA_LABELS,
    PERSONA_TYPES,
    build_default_persona_name,
    default_persona_attributes,
    generate_persona_image,
    normalize_persona_attributes,
    normalize_persona_type,
)

logger = logging.getLogger(__name__)
settings = get_settings()


def _media_url_from_path(path: str | None) -> str | None:
    if not path:
        return None
    media_prefix = settings.media_dir.rstrip("/")
    normalized = str(path)
    if normalized.startswith(media_prefix):
        return "/video/media" + normalized[len(media_prefix):]
    return None


def serialize_persona_profile(profile: PersonaProfile) -> dict:
    return {
        "id": profile.id,
        "persona_type": profile.persona_type,
        "persona_label": PERSONA_LABELS.get(profile.persona_type, profile.persona_type),
        "name": profile.name,
        "attributes": profile.attributes or {},
        "image_path": profile.image_path,
        "image_url": _media_url_from_path(profile.image_path),
        "is_default": bool(profile.is_default),
        "is_active": bool(profile.is_active),
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


async def _query_active_profiles(db: AsyncSession, user_id: int, persona_type: str) -> list[PersonaProfile]:
    result = await db.execute(
        select(PersonaProfile)
        .where(
            PersonaProfile.user_id == user_id,
            PersonaProfile.persona_type == persona_type,
            PersonaProfile.is_active == True,
        )
        .order_by(PersonaProfile.is_default.desc(), PersonaProfile.created_at.desc(), PersonaProfile.id.desc())
    )
    return list(result.scalars().all())


async def create_persona_profile(
    db: AsyncSession,
    user_id: int,
    persona_type: str,
    name: str = "",
    attributes: dict | None = None,
    set_default: bool = False,
) -> PersonaProfile:
    persona_type = normalize_persona_type(persona_type)
    attrs = normalize_persona_attributes(persona_type, attributes)

    generated = await generate_persona_image(
        user_id=user_id,
        persona_type=persona_type,
        attributes=attrs,
    )

    profiles = await _query_active_profiles(db, user_id, persona_type)
    has_any = len(profiles) > 0
    should_be_default = bool(set_default) or not has_any

    if should_be_default:
        await db.execute(
            update(PersonaProfile)
            .where(
                PersonaProfile.user_id == user_id,
                PersonaProfile.persona_type == persona_type,
            )
            .values(is_default=False)
        )

    cleaned_name = " ".join(str(name or "").split()).strip()
    if not cleaned_name:
        cleaned_name = build_default_persona_name(persona_type) if not has_any else f"{PERSONA_LABELS.get(persona_type, 'Persona')} {len(profiles) + 1}"

    profile = PersonaProfile(
        user_id=user_id,
        persona_type=persona_type,
        name=cleaned_name[:255],
        attributes=generated["attributes"],
        prompt_text=generated["prompt_text"],
        image_path=generated["image_path"],
        is_default=should_be_default,
        is_active=True,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    return profile


async def list_persona_profiles(
    db: AsyncSession,
    user_id: int,
    persona_type: str,
    ensure_default: bool = False,
) -> list[PersonaProfile]:
    persona_type = normalize_persona_type(persona_type)
    profiles = await _query_active_profiles(db, user_id, persona_type)

    if ensure_default and not profiles:
        created = await create_persona_profile(
            db=db,
            user_id=user_id,
            persona_type=persona_type,
            name=build_default_persona_name(persona_type),
            attributes=default_persona_attributes(persona_type),
            set_default=True,
        )
        return [created]

    if ensure_default and profiles and not any(p.is_default for p in profiles):
        await db.execute(
            update(PersonaProfile)
            .where(
                PersonaProfile.user_id == user_id,
                PersonaProfile.persona_type == persona_type,
            )
            .values(is_default=False)
        )
        profiles[0].is_default = True
        await db.commit()
        await db.refresh(profiles[0])

    return profiles


async def set_default_persona(db: AsyncSession, user_id: int, profile_id: int) -> PersonaProfile:
    profile = await db.get(PersonaProfile, profile_id)
    if not profile or profile.user_id != user_id or not profile.is_active:
        raise ValueError("Perfil de persona nao encontrado")

    await db.execute(
        update(PersonaProfile)
        .where(
            PersonaProfile.user_id == user_id,
            PersonaProfile.persona_type == profile.persona_type,
        )
        .values(is_default=False)
    )
    profile.is_default = True
    await db.commit()
    await db.refresh(profile)

    return profile


async def delete_persona_profile(db: AsyncSession, user_id: int, profile_id: int) -> dict:
    profile = await db.get(PersonaProfile, profile_id)
    if not profile or profile.user_id != user_id:
        raise ValueError("Perfil de persona nao encontrado")

    persona_type = profile.persona_type
    was_default = bool(profile.is_default)
    image_path = profile.image_path

    await db.delete(profile)
    await db.flush()

    replacement_default_id = 0
    if was_default:
        result = await db.execute(
            select(PersonaProfile)
            .where(
                PersonaProfile.user_id == user_id,
                PersonaProfile.persona_type == persona_type,
                PersonaProfile.is_active == True,
            )
            .order_by(PersonaProfile.created_at.desc(), PersonaProfile.id.desc())
        )
        replacement = result.scalars().first()
        if replacement:
            replacement.is_default = True
            replacement_default_id = int(replacement.id)

    await db.commit()

    try:
        if image_path and os.path.exists(image_path):
            Path(image_path).unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Failed to remove persona image file %s: %s", image_path, exc)

    return {
        "deleted": True,
        "replacement_default_id": replacement_default_id,
    }


async def resolve_persona_reference_image(
    db: AsyncSession,
    user_id: int,
    persona_type: str,
    persona_profile_id: int = 0,
    ensure_default: bool = True,
) -> tuple[PersonaProfile | None, str]:
    persona_type = normalize_persona_type(persona_type)
    profile: PersonaProfile | None = None

    if persona_profile_id:
        candidate = await db.get(PersonaProfile, int(persona_profile_id))
        if (
            candidate
            and candidate.user_id == user_id
            and candidate.is_active
            and candidate.persona_type == persona_type
        ):
            profile = candidate

    if not profile:
        result = await db.execute(
            select(PersonaProfile)
            .where(
                PersonaProfile.user_id == user_id,
                PersonaProfile.persona_type == persona_type,
                PersonaProfile.is_active == True,
                PersonaProfile.is_default == True,
            )
            .order_by(PersonaProfile.id.desc())
        )
        profile = result.scalars().first()

    if not profile:
        result = await db.execute(
            select(PersonaProfile)
            .where(
                PersonaProfile.user_id == user_id,
                PersonaProfile.persona_type == persona_type,
                PersonaProfile.is_active == True,
            )
            .order_by(PersonaProfile.created_at.desc(), PersonaProfile.id.desc())
        )
        profile = result.scalars().first()

    if not profile and ensure_default:
        profile = await create_persona_profile(
            db=db,
            user_id=user_id,
            persona_type=persona_type,
            name=build_default_persona_name(persona_type),
            attributes=default_persona_attributes(persona_type),
            set_default=True,
        )

    if not profile:
        return None, ""

    image_path = str(profile.image_path or "")
    if image_path and os.path.exists(image_path):
        return profile, image_path

    if ensure_default:
        regenerated = await generate_persona_image(
            user_id=user_id,
            persona_type=persona_type,
            attributes=profile.attributes or default_persona_attributes(persona_type),
        )
        profile.image_path = regenerated["image_path"]
        profile.prompt_text = regenerated["prompt_text"]
        profile.attributes = regenerated["attributes"]
        await db.commit()
        await db.refresh(profile)
        return profile, str(profile.image_path)

    return profile, ""


async def resolve_persona_reference_images(
    db: AsyncSession,
    user_id: int,
    persona_type: str,
    persona_profile_ids: list[int] | None = None,
    ensure_default: bool = False,
) -> tuple[list[PersonaProfile], list[str]]:
    persona_type = normalize_persona_type(persona_type)
    requested_ids: list[int] = []
    for raw in (persona_profile_ids or []):
        try:
            pid = int(raw)
        except Exception:
            continue
        if pid > 0 and pid not in requested_ids:
            requested_ids.append(pid)

    if not requested_ids:
        profile, image_path = await resolve_persona_reference_image(
            db=db,
            user_id=user_id,
            persona_type=persona_type,
            persona_profile_id=0,
            ensure_default=ensure_default,
        )
        if profile and image_path:
            return [profile], [image_path]
        return [], []

    result = await db.execute(
        select(PersonaProfile)
        .where(
            PersonaProfile.id.in_(requested_ids),
            PersonaProfile.user_id == user_id,
            PersonaProfile.persona_type == persona_type,
            PersonaProfile.is_active == True,
        )
    )
    profiles_by_id = {int(p.id): p for p in result.scalars().all()}

    profiles: list[PersonaProfile] = []
    image_paths: list[str] = []
    for pid in requested_ids:
        profile = profiles_by_id.get(pid)
        if not profile:
            continue

        image_path = str(profile.image_path or "")
        if image_path and os.path.exists(image_path):
            profiles.append(profile)
            image_paths.append(image_path)
            continue

        if not ensure_default:
            continue

        regenerated = await generate_persona_image(
            user_id=user_id,
            persona_type=persona_type,
            attributes=profile.attributes or default_persona_attributes(persona_type),
        )
        profile.image_path = regenerated["image_path"]
        profile.prompt_text = regenerated["prompt_text"]
        profile.attributes = regenerated["attributes"]
        await db.flush()

        refreshed_path = str(profile.image_path or "")
        if refreshed_path and os.path.exists(refreshed_path):
            profiles.append(profile)
            image_paths.append(refreshed_path)

    if ensure_default and not image_paths:
        profile, image_path = await resolve_persona_reference_image(
            db=db,
            user_id=user_id,
            persona_type=persona_type,
            persona_profile_id=0,
            ensure_default=True,
        )
        if profile and image_path:
            return [profile], [image_path]

    if ensure_default:
        await db.commit()

    return profiles, image_paths


def build_persona_reference_montage(user_id: int, image_paths: list[str], prefix: str = "persona_multi") -> str:
    valid_paths = []
    for path in image_paths:
        raw = str(path or "").strip()
        if raw and os.path.exists(raw) and raw not in valid_paths:
            valid_paths.append(raw)

    if not valid_paths:
        return ""
    if len(valid_paths) == 1:
        return valid_paths[0]

    output_dir = Path(settings.media_dir) / "temp_refs" / str(user_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{prefix}_{os.urandom(6).hex()}.png"

    images: list[Image.Image] = []
    try:
        for path in valid_paths[:6]:
            with Image.open(path) as src:
                img = ImageOps.exif_transpose(src).convert("RGB")
                images.append(img.copy())

        if len(images) == 1:
            return valid_paths[0]

        target_height = min(max(min(im.height for im in images), 512), 1024)
        resized: list[Image.Image] = []
        for image in images:
            ratio = target_height / max(1, image.height)
            target_width = max(256, int(image.width * ratio))
            resized.append(image.resize((target_width, target_height), Image.LANCZOS))

        gap = 8
        total_width = sum(im.width for im in resized) + gap * (len(resized) - 1)
        max_width = 2048
        if total_width > max_width:
            scale = max_width / float(total_width)
            scaled_images: list[Image.Image] = []
            for image in resized:
                sw = max(180, int(image.width * scale))
                sh = max(256, int(image.height * scale))
                scaled_images.append(image.resize((sw, sh), Image.LANCZOS))
            resized = scaled_images
            total_width = sum(im.width for im in resized) + gap * (len(resized) - 1)

        canvas_height = max(im.height for im in resized)
        canvas = Image.new("RGB", (total_width, canvas_height), (20, 20, 20))
        x = 0
        for image in resized:
            y = (canvas_height - image.height) // 2
            canvas.paste(image, (x, y))
            x += image.width + gap

        canvas.save(output_path, format="PNG", optimize=True)
        return str(output_path)
    except Exception as exc:
        logger.warning("Failed to build persona montage: %s", exc)
        return valid_paths[0]
    finally:
        for image in images:
            try:
                image.close()
            except Exception:
                pass


async def list_all_personas(db: AsyncSession, user_id: int) -> list[PersonaProfile]:
    result = await db.execute(
        select(PersonaProfile)
        .where(
            PersonaProfile.user_id == user_id,
            PersonaProfile.is_active == True,
        )
        .order_by(PersonaProfile.persona_type.asc(), PersonaProfile.is_default.desc(), PersonaProfile.created_at.desc())
    )
    return list(result.scalars().all())


__all__ = [
    "PERSONA_TYPES",
    "PERSONA_LABELS",
    "create_persona_profile",
    "delete_persona_profile",
    "build_persona_reference_montage",
    "list_all_personas",
    "list_persona_profiles",
    "normalize_persona_type",
    "resolve_persona_reference_image",
    "resolve_persona_reference_images",
    "serialize_persona_profile",
    "set_default_persona",
]
