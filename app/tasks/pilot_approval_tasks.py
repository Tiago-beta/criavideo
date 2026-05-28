"""Tacit pre-approval worker for Piloto automatico Shorts.

Themes created by the pilot start with approval_status='pending_review' and an
approval_deadline_at timestamp. This worker flips them to 'approved' once the
window expires, so the existing auto_creation_tasks pipeline picks them up and
renders / publishes the Short automatically.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.database import async_session
from app.models import AutoScheduleTheme

logger = logging.getLogger(__name__)


async def process_tacit_pilot_approvals() -> int:
	"""Promote pilot Short themes from pending_review to approved when deadline passes.

	Returns count of themes promoted.
	"""
	now = datetime.utcnow()
	promoted = 0
	async with async_session() as db:
		result = await db.execute(
			select(AutoScheduleTheme)
			.where(AutoScheduleTheme.approval_status == "pending_review")
			.where(AutoScheduleTheme.approval_deadline_at.is_not(None))
			.where(AutoScheduleTheme.approval_deadline_at <= now)
			.limit(100)
		)
		themes = list(result.scalars())
		for theme in themes:
			theme.approval_status = "approved"
			theme.approved_at = now
			plan = dict(theme.preview_plan or {})
			plan["auto_approved"] = True
			plan["auto_approved_at"] = now.isoformat()
			theme.preview_plan = plan
			flag_modified(theme, "preview_plan")
			promoted += 1
		if promoted:
			await db.commit()
			logger.info("pilot tacit approval: promoted %s themes to approved", promoted)
	return promoted
