"""
Auto pilot tasks - autonomous channel growth loop based on analysis.
"""

import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models import AutoChannelPilot, AutoSchedule, AutoScheduleTheme, Platform
from app.routers.analyze import build_channel_analysis_payload

logger = logging.getLogger(__name__)

_PILOT_SCHEDULE_NAME_PREFIX = "Piloto automatico"


def _normalize_theme_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    text = re.sub(r"^\d+[\.)\-:\s]+", "", text)
    text = re.sub(r"\s+", " ", text)

    if "|" in text:
        left, right = [part.strip() for part in text.split("|", 1)]
        # Use the topic identity when available, otherwise keep the hook.
        text = right or left

    text = re.sub(r"#[\w\-]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -|,.;:")

    if len(text) > 120:
        text = text[:120].rsplit(" ", 1)[0].strip()

    return text


def _extract_theme_candidates(payload: dict) -> list[str]:
    recommendations = payload.get("recommendations", {}) if isinstance(payload, dict) else {}
    blueprint = payload.get("automation_blueprint", {}) if isinstance(payload, dict) else {}

    raw_candidates = []
    raw_candidates.extend(blueprint.get("priority_themes") or [])
    raw_candidates.extend(recommendations.get("title_ideas") or [])
    raw_candidates.extend(recommendations.get("content_gaps") or [])

    for action in recommendations.get("growth_actions") or []:
        action_text = str(action or "").strip()
        if not action_text:
            continue
        raw_candidates.append(action_text)

    normalized = []
    seen = set()
    for item in raw_candidates:
        theme = _normalize_theme_text(str(item or ""))
        if not theme:
            continue
        key = theme.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(theme)

    if normalized:
        return normalized[:20]

    keywords = recommendations.get("keyword_focus") or []
    fallback = []
    for word in keywords[:6]:
        token = _normalize_theme_text(str(word or ""))
        if token:
            fallback.append(f"Como evoluir em {token}")

    return fallback[:10]


async def _ensure_pilot_schedule(db, pilot: AutoChannelPilot, account, analysis_payload: dict) -> AutoSchedule:
    schedule = None

    if pilot.auto_schedule_id:
        schedule = await db.get(AutoSchedule, pilot.auto_schedule_id)
        if schedule and (schedule.user_id != pilot.user_id or schedule.social_account_id != account.id):
            schedule = None

    if not schedule:
        result = await db.execute(
            select(AutoSchedule)
            .where(AutoSchedule.user_id == pilot.user_id)
            .where(AutoSchedule.social_account_id == account.id)
            .where(AutoSchedule.name.like(f"{_PILOT_SCHEDULE_NAME_PREFIX}%"))
            .order_by(AutoSchedule.created_at.desc())
            .limit(1)
        )
        schedule = result.scalar_one_or_none()

    base_settings = dict(schedule.default_settings or {}) if schedule else {}
    base_settings.update(
        {
            "pilot_mode": True,
            "pilot_account_id": account.id,
            "pilot_tool_study": analysis_payload.get("tool_study") or [],
            "pilot_keyword_focus": (analysis_payload.get("recommendations") or {}).get("keyword_focus") or [],
            "pilot_last_generated_at": (analysis_payload.get("source") or {}).get("generated_at") or "",
        }
    )

    if not schedule:
        schedule_name = account.account_label or account.platform_username or f"Canal {account.id}"
        schedule = AutoSchedule(
            user_id=pilot.user_id,
            name=f"{_PILOT_SCHEDULE_NAME_PREFIX} - {schedule_name}",
            video_type="music",
            creation_mode="auto",
            platform="youtube",
            social_account_id=account.id,
            frequency="daily",
            time_utc="14:00",
            day_of_week=0,
            timezone="UTC",
            default_settings=base_settings,
            is_active=True,
        )
        db.add(schedule)
        await db.flush()
    else:
        schedule.is_active = pilot.is_enabled
        schedule.platform = "youtube"
        schedule.social_account_id = account.id
        schedule.default_settings = base_settings

    pilot.auto_schedule_id = schedule.id
    return schedule


async def _enqueue_pilot_themes(db, pilot: AutoChannelPilot, schedule: AutoSchedule, analysis_payload: dict) -> int:
    result = await db.execute(
        select(AutoScheduleTheme)
        .where(AutoScheduleTheme.auto_schedule_id == schedule.id)
        .order_by(AutoScheduleTheme.position.asc())
    )
    existing = result.scalars().all()

    pending_count = sum(1 for theme in existing if theme.status == "pending")
    needed = max(0, int(pilot.min_pending_themes or 0) - pending_count)
    if needed <= 0:
        return 0

    add_cap = max(1, min(int(pilot.themes_per_cycle or 4), 12))
    to_add = min(needed, add_cap)

    candidates = _extract_theme_candidates(analysis_payload)

    existing_keys = {(_normalize_theme_text(theme.theme)).lower() for theme in existing if theme.theme}
    max_pos = max([theme.position for theme in existing], default=-1)

    added = 0
    for candidate in candidates:
        if added >= to_add:
            break
        normalized = _normalize_theme_text(candidate)
        if not normalized:
            continue
        key = normalized.lower()
        if key in existing_keys:
            continue

        max_pos += 1
        db.add(
            AutoScheduleTheme(
                auto_schedule_id=schedule.id,
                theme=normalized,
                status="pending",
                position=max_pos,
            )
        )
        existing_keys.add(key)
        added += 1

    return added


async def run_channel_pilot_cycle(pilot_id: int) -> dict:
    """Run one analysis + theme replenishment cycle for a pilot channel."""
    async with async_session() as db:
        result = await db.execute(
            select(AutoChannelPilot)
            .options(selectinload(AutoChannelPilot.social_account))
            .where(AutoChannelPilot.id == pilot_id)
        )
        pilot = result.scalar_one_or_none()
        if not pilot:
            return {"ok": False, "error": "pilot_not_found"}

        if not pilot.is_enabled:
            return {"ok": False, "error": "pilot_disabled"}

        account = pilot.social_account
        if not account or account.user_id != pilot.user_id:
            pilot.last_error = "Conta social do piloto nao encontrada."
            await db.commit()
            return {"ok": False, "error": "account_not_found"}

        if account.platform != Platform.YOUTUBE:
            pilot.last_error = "Piloto automatico suporta apenas YouTube."
            pilot.is_enabled = False
            await db.commit()
            return {"ok": False, "error": "platform_not_supported"}

        try:
            analysis_payload = await build_channel_analysis_payload(
                user_id=pilot.user_id,
                account=account,
                db=db,
            )

            schedule = await _ensure_pilot_schedule(db, pilot, account, analysis_payload)
            added = await _enqueue_pilot_themes(db, pilot, schedule, analysis_payload)

            now = datetime.utcnow()
            pilot.last_analysis_at = now
            pilot.last_run_at = now
            pilot.last_error = None
            pilot.last_summary = {
                "channel_title": (analysis_payload.get("channel") or {}).get("title") or "",
                "analysis_model": (analysis_payload.get("source") or {}).get("analysis_model") or "",
                "best_publish_window": (analysis_payload.get("history") or {}).get("best_publish_window") or "",
                "themes_added_last_cycle": added,
                "tool_study": analysis_payload.get("tool_study") or [],
                "automation_blueprint": analysis_payload.get("automation_blueprint") or {},
                "generated_at": (analysis_payload.get("source") or {}).get("generated_at") or "",
            }
            schedule.is_active = True

            await db.commit()
            return {
                "ok": True,
                "pilot_id": pilot.id,
                "schedule_id": schedule.id,
                "themes_added": added,
            }
        except Exception as err:
            logger.exception("Auto pilot cycle failed: pilot=%s error=%s", pilot.id, err)
            pilot.last_error = str(err)[:1000]
            pilot.last_run_at = datetime.utcnow()
            await db.commit()
            return {"ok": False, "error": str(err)}


async def run_due_channel_pilots() -> None:
    """Run pilots whose analysis interval has elapsed."""
    now = datetime.utcnow()

    async with async_session() as db:
        result = await db.execute(
            select(AutoChannelPilot)
            .where(AutoChannelPilot.is_enabled == True)
        )
        pilots = result.scalars().all()

    due_ids: list[int] = []
    for pilot in pilots:
        interval_hours = max(1, int(pilot.analysis_interval_hours or 24))
        if not pilot.last_analysis_at:
            due_ids.append(pilot.id)
            continue
        if (pilot.last_analysis_at + timedelta(hours=interval_hours)) <= now:
            due_ids.append(pilot.id)

    for pilot_id in due_ids:
        result = await run_channel_pilot_cycle(pilot_id)
        if result.get("ok"):
            logger.info(
                "Auto pilot cycle complete: pilot=%s schedule=%s themes_added=%s",
                pilot_id,
                result.get("schedule_id"),
                result.get("themes_added", 0),
            )
