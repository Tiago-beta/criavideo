"""
Auto pilot tasks - autonomous channel growth loop based on analysis.
"""

import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models import AutoChannelPilot, AutoPilotCycleRun, AutoSchedule, AutoScheduleTheme, Platform
from app.routers.analyze import build_channel_analysis_payload

logger = logging.getLogger(__name__)

_PILOT_SCHEDULE_NAME_PREFIX = "Piloto automatico"
_SHORT_MIX_ALLOWED = {"realistic_all", "image_all", "mixed_realistic2_image1"}


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _guess_channel_mode(analysis_payload: dict) -> str:
    recommendations = analysis_payload.get("recommendations", {}) if isinstance(analysis_payload, dict) else {}
    keyword_focus = recommendations.get("keyword_focus") or []
    top_videos = analysis_payload.get("top_videos") or []
    channel = analysis_payload.get("channel") or {}

    corpus_parts = [str(channel.get("title") or "")]
    corpus_parts.extend(str(word or "") for word in keyword_focus)
    corpus_parts.extend(str((video or {}).get("title") or "") for video in top_videos[:12])
    corpus = " ".join(corpus_parts).lower()

    music_tokens = {
        "musica", "música", "song", "lyric", "lyrics", "beat", "remix", "cover",
        "instrumental", "gospel", "worship", "lofi", "dj", "sertanejo", "funk",
        "pagode", "forro", "forró", "rap", "trap", "hip hop", "mpb", "rock",
    }
    if any(token in corpus for token in music_tokens):
        return "music"
    return "general"


def _resolve_short_render_modes(shorts_per_cycle: int, short_mix_mode: str) -> list[str]:
    count = max(1, shorts_per_cycle)
    mix_mode = str(short_mix_mode or "realistic_all").strip().lower()

    if mix_mode == "image_all":
        return ["image"] * count

    if mix_mode == "mixed_realistic2_image1":
        if count == 1:
            return ["realistic"]
        if count == 2:
            return ["realistic", "image"]
        modes = ["realistic", "realistic"]
        while len(modes) < count:
            modes.append("image")
        return modes

    return ["realistic"] * count


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


async def _ensure_pilot_schedules(db, pilot: AutoChannelPilot, account, analysis_payload: dict) -> tuple[AutoSchedule, AutoSchedule]:
    long_schedule = None
    shorts_schedule = None

    if pilot.long_schedule_id:
        long_schedule = await db.get(AutoSchedule, pilot.long_schedule_id)
        if long_schedule and (long_schedule.user_id != pilot.user_id or long_schedule.social_account_id != account.id):
            long_schedule = None

    if not long_schedule and pilot.auto_schedule_id:
        long_schedule = await db.get(AutoSchedule, pilot.auto_schedule_id)
        if long_schedule and (long_schedule.user_id != pilot.user_id or long_schedule.social_account_id != account.id):
            long_schedule = None

    if pilot.shorts_schedule_id:
        shorts_schedule = await db.get(AutoSchedule, pilot.shorts_schedule_id)
        if shorts_schedule and (shorts_schedule.user_id != pilot.user_id or shorts_schedule.social_account_id != account.id):
            shorts_schedule = None

    schedule_name = account.account_label or account.platform_username or f"Canal {account.id}"

    if not long_schedule or not shorts_schedule:
        result = await db.execute(
            select(AutoSchedule)
            .where(AutoSchedule.user_id == pilot.user_id)
            .where(AutoSchedule.social_account_id == account.id)
            .where(AutoSchedule.name.like(f"{_PILOT_SCHEDULE_NAME_PREFIX}%"))
            .order_by(AutoSchedule.created_at.desc())
        )
        existing = result.scalars().all()
        if not long_schedule:
            for item in existing:
                item_name = str(item.name or "").lower()
                if "long" in item_name or "principal" in item_name:
                    long_schedule = item
                    break
        if not shorts_schedule:
            for item in existing:
                item_name = str(item.name or "").lower()
                if "short" in item_name:
                    shorts_schedule = item
                    break

    channel_mode = str(pilot.channel_mode or "auto").strip().lower()
    if channel_mode not in {"auto", "music", "general"}:
        channel_mode = "auto"

    guessed_mode = _guess_channel_mode(analysis_payload)
    effective_channel_mode = guessed_mode if channel_mode == "auto" else channel_mode

    short_mix_mode = str(pilot.short_mix_mode or "realistic_all").strip().lower()
    if short_mix_mode not in _SHORT_MIX_ALLOWED:
        short_mix_mode = "realistic_all"

    shorts_per_cycle = max(1, min(_safe_int(pilot.shorts_per_cycle, 3), 6))

    base_settings_common = {
        "pilot_mode": True,
        "pilot_account_id": account.id,
        "pilot_channel_mode": effective_channel_mode,
        "pilot_tool_study": analysis_payload.get("tool_study") or [],
        "pilot_keyword_focus": (analysis_payload.get("recommendations") or {}).get("keyword_focus") or [],
        "pilot_last_generated_at": (analysis_payload.get("source") or {}).get("generated_at") or "",
    }

    long_settings = {
        **(dict(long_schedule.default_settings or {}) if long_schedule else {}),
        **base_settings_common,
        "pilot_stream": "long",
        "pilot_short_mix_mode": short_mix_mode,
        "pilot_shorts_per_cycle": shorts_per_cycle,
    }

    short_render_modes = _resolve_short_render_modes(shorts_per_cycle, short_mix_mode)
    default_short_render_mode = short_render_modes[0] if short_render_modes else "realistic"

    short_settings = {
        **(dict(shorts_schedule.default_settings or {}) if shorts_schedule else {}),
        **base_settings_common,
        "pilot_stream": "short",
        "auto_items_per_run": shorts_per_cycle,
        "short_render_mode": default_short_render_mode,
    }

    if not long_schedule:
        long_schedule = AutoSchedule(
            user_id=pilot.user_id,
            name=f"{_PILOT_SCHEDULE_NAME_PREFIX} - {schedule_name} - Long",
            video_type="music",
            creation_mode="auto",
            platform="youtube",
            social_account_id=account.id,
            frequency="daily",
            time_utc="14:00",
            day_of_week=0,
            timezone="UTC",
            default_settings=long_settings,
            is_active=bool(pilot.is_enabled),
        )
        db.add(long_schedule)
        await db.flush()
    else:
        long_schedule.is_active = bool(pilot.is_enabled)
        long_schedule.platform = "youtube"
        long_schedule.social_account_id = account.id
        long_schedule.video_type = "music"
        long_schedule.creation_mode = "auto"
        long_schedule.default_settings = long_settings

    short_settings["pilot_long_schedule_id"] = long_schedule.id

    if not shorts_schedule:
        shorts_schedule = AutoSchedule(
            user_id=pilot.user_id,
            name=f"{_PILOT_SCHEDULE_NAME_PREFIX} - {schedule_name} - Shorts",
            video_type="musical_shorts",
            creation_mode="manual",
            platform="youtube",
            social_account_id=account.id,
            frequency="daily",
            time_utc="15:00",
            day_of_week=0,
            timezone="UTC",
            default_settings=short_settings,
            is_active=bool(pilot.is_enabled),
        )
        db.add(shorts_schedule)
        await db.flush()
    else:
        shorts_schedule.is_active = bool(pilot.is_enabled)
        shorts_schedule.platform = "youtube"
        shorts_schedule.social_account_id = account.id
        shorts_schedule.video_type = "musical_shorts"
        shorts_schedule.creation_mode = "manual"
        shorts_schedule.default_settings = short_settings

    long_settings["pilot_short_schedule_id"] = shorts_schedule.id
    long_schedule.default_settings = long_settings

    pilot.auto_schedule_id = long_schedule.id
    pilot.long_schedule_id = long_schedule.id
    pilot.shorts_schedule_id = shorts_schedule.id
    pilot.channel_mode = channel_mode
    pilot.short_mix_mode = short_mix_mode
    pilot.shorts_per_cycle = shorts_per_cycle

    return long_schedule, shorts_schedule


async def _enqueue_pilot_themes(db, pilot: AutoChannelPilot, long_schedule: AutoSchedule, analysis_payload: dict) -> int:
    result = await db.execute(
        select(AutoScheduleTheme)
        .where(AutoScheduleTheme.auto_schedule_id == long_schedule.id)
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
    short_mix_mode = str(pilot.short_mix_mode or "realistic_all").strip().lower()
    shorts_per_cycle = max(1, min(_safe_int(pilot.shorts_per_cycle, 3), 6))
    short_modes = _resolve_short_render_modes(shorts_per_cycle, short_mix_mode)

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
        cycle_key = f"pilot-{pilot.id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{max_pos}"

        theme_entry = AutoScheduleTheme(
            auto_schedule_id=long_schedule.id,
            theme=normalized,
            status="pending",
            position=max_pos,
            custom_settings={
                "pilot_cycle_key": cycle_key,
                "pilot_shorts_per_cycle": shorts_per_cycle,
                "pilot_short_mix_mode": short_mix_mode,
                "pilot_short_modes": short_modes,
            },
        )
        db.add(theme_entry)
        await db.flush()

        db.add(
            AutoPilotCycleRun(
                pilot_id=pilot.id,
                cycle_key=cycle_key,
                base_theme=normalized,
                long_theme_id=theme_entry.id,
                status="planned",
                planned_shorts=shorts_per_cycle,
                completed_shorts=0,
                short_mix_mode=short_mix_mode,
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

            long_schedule, shorts_schedule = await _ensure_pilot_schedules(db, pilot, account, analysis_payload)
            added = await _enqueue_pilot_themes(db, pilot, long_schedule, analysis_payload)

            pending_long_q = await db.execute(
                select(AutoScheduleTheme.id)
                .where(AutoScheduleTheme.auto_schedule_id == long_schedule.id)
                .where(AutoScheduleTheme.status == "pending")
            )
            pending_shorts_q = await db.execute(
                select(AutoScheduleTheme.id)
                .where(AutoScheduleTheme.auto_schedule_id == shorts_schedule.id)
                .where(AutoScheduleTheme.status == "pending")
            )
            pending_long = len(pending_long_q.scalars().all())
            pending_shorts = len(pending_shorts_q.scalars().all())

            now = datetime.utcnow()
            pilot.last_analysis_at = now
            pilot.last_run_at = now
            pilot.last_error = None
            pilot.last_summary = {
                "channel_title": (analysis_payload.get("channel") or {}).get("title") or "",
                "analysis_model": (analysis_payload.get("source") or {}).get("analysis_model") or "",
                "best_publish_window": (analysis_payload.get("history") or {}).get("best_publish_window") or "",
                "themes_added_last_cycle": added,
                "channel_mode": pilot.channel_mode,
                "short_mix_mode": pilot.short_mix_mode,
                "shorts_per_cycle": pilot.shorts_per_cycle,
                "long_schedule_id": long_schedule.id,
                "shorts_schedule_id": shorts_schedule.id,
                "pending_long_themes": pending_long,
                "pending_short_themes": pending_shorts,
                "tool_study": analysis_payload.get("tool_study") or [],
                "automation_blueprint": analysis_payload.get("automation_blueprint") or {},
                "generated_at": (analysis_payload.get("source") or {}).get("generated_at") or "",
            }
            long_schedule.is_active = True
            shorts_schedule.is_active = True

            await db.commit()
            return {
                "ok": True,
                "pilot_id": pilot.id,
                "schedule_id": long_schedule.id,
                "long_schedule_id": long_schedule.id,
                "shorts_schedule_id": shorts_schedule.id,
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
