from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])

ADMIN_EMAIL = "tgsantos66@hotmail.com"
APP_SOURCE = "criavideo"


class AdminCreditChangeRequest(BaseModel):
    amount: int
    reason: str = Field(default="Credito admin", max_length=200)


class AdminPlanChangeRequest(BaseModel):
    plan: str = Field(min_length=4, max_length=10)
    durationDays: int | None = Field(default=None, ge=1, le=365 * 5)


class TrackViewRequest(BaseModel):
    page: str = Field(min_length=1, max_length=120)


def _normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _as_json_number(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value


def _rows_to_dicts(rows) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for key, value in dict(row._mapping).items():
            item[str(key)] = _as_json_number(value)
        payload.append(item)
    return payload


def _is_admin_user(user: dict) -> bool:
    role = str(user.get("role") or "").strip().lower()
    email = _normalize_email(user.get("email"))
    return role == "admin" or email == ADMIN_EMAIL


def _require_admin(user: dict) -> None:
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")


async def _table_exists(db: AsyncSession, table_name: str) -> bool:
    result = await db.execute(text("SELECT to_regclass(:table_name)"), {"table_name": table_name})
    return bool(result.scalar())


async def _get_columns(db: AsyncSession, table_name: str) -> set[str]:
    result = await db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = :table_name
            """
        ),
        {"table_name": table_name},
    )
    return {str(row[0]).strip().lower() for row in result.fetchall()}


async def _fetch_scalar(db: AsyncSession, sql: str, params: dict[str, Any] | None = None, default: Any = 0) -> Any:
    result = await db.execute(text(sql), params or {})
    value = result.scalar()
    if value is None:
        return default
    return _as_json_number(value)


async def _fetch_rows(db: AsyncSession, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    result = await db.execute(text(sql), params or {})
    return _rows_to_dicts(result.fetchall())


def _join_where(parts: list[str]) -> str:
    filtered = [p for p in parts if p]
    if not filtered:
        return ""
    return " WHERE " + " AND ".join(filtered)


@router.get("/stats")
async def admin_stats(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)

    if not await _table_exists(db, "auth_users"):
        return {
            "appSource": APP_SOURCE,
            "totalUsers": 0,
            "proUsers": 0,
            "totalProjects": 0,
            "totalRenders": 0,
            "totalCreditsInCirculation": 0,
            "totalRevenue": 0,
            "newUsersLast30d": 0,
            "activeUsersLast7d": 0,
            "monthlySignups": [],
            "monthlyProjects": [],
            "monthlyRenders": [],
            "monthlyRevenue": [],
            "planDistribution": [],
            "pageViews": {
                "totals": [],
                "today": [],
                "last7d": [],
                "last30d": [],
                "monthly": [],
            },
        }

    auth_cols = await _get_columns(db, "auth_users")
    has_plan = "plan" in auth_cols
    has_plan_expires = "plan_expires_at" in auth_cols
    has_credits = "credits" in auth_cols
    has_last_login = "last_login_at" in auth_cols
    has_created_at = "created_at" in auth_cols
    has_is_active = "is_active" in auth_cols

    user_filter = _join_where(["COALESCE(is_active, TRUE)" if has_is_active else ""])

    total_users = _as_int(
        await _fetch_scalar(
            db,
            f"SELECT COUNT(*) AS total FROM auth_users{user_filter}",
            default=0,
        )
    )

    pro_users = 0
    if has_plan:
        pro_filters = [
            "COALESCE(plan, 'free') = 'pro'",
            "(plan_expires_at IS NULL OR plan_expires_at > NOW())" if has_plan_expires else "",
            "COALESCE(is_active, TRUE)" if has_is_active else "",
        ]
        pro_users = _as_int(
            await _fetch_scalar(
                db,
                f"SELECT COUNT(*) AS total FROM auth_users{_join_where(pro_filters)}",
                default=0,
            )
        )

    new_users_30d = 0
    if has_created_at:
        new_user_filters = ["created_at >= NOW() - INTERVAL '30 days'"]
        if has_is_active:
            new_user_filters.append("COALESCE(is_active, TRUE)")
        new_user_where = _join_where(new_user_filters)
        new_users_30d = _as_int(
            await _fetch_scalar(
                db,
                f"SELECT COUNT(*) AS total FROM auth_users{new_user_where}",
                default=0,
            )
        )

    active_users_7d = 0
    if has_last_login:
        active_user_filters = ["last_login_at >= NOW() - INTERVAL '7 days'"]
        if has_is_active:
            active_user_filters.append("COALESCE(is_active, TRUE)")
        active_user_where = _join_where(active_user_filters)
        active_users_7d = _as_int(
            await _fetch_scalar(
                db,
                f"SELECT COUNT(*) AS total FROM auth_users{active_user_where}",
                default=0,
            )
        )

    total_credits = 0
    if has_credits:
        total_credits = _as_int(
            await _fetch_scalar(
                db,
                f"SELECT COALESCE(SUM(credits), 0) AS total FROM auth_users{user_filter}",
                default=0,
            )
        )

    total_projects = 0
    monthly_projects: list[dict[str, Any]] = []
    if await _table_exists(db, "video_projects"):
        total_projects = _as_int(
            await _fetch_scalar(db, "SELECT COUNT(*) AS total FROM video_projects", default=0)
        )
        monthly_projects = await _fetch_rows(
            db,
            """
            SELECT date_trunc('month', created_at) AS month, COUNT(*) AS count
            FROM video_projects
            WHERE created_at >= NOW() - INTERVAL '6 months'
            GROUP BY month
            ORDER BY month
            """,
        )

    total_renders = 0
    monthly_renders: list[dict[str, Any]] = []
    if await _table_exists(db, "video_renders"):
        total_renders = _as_int(
            await _fetch_scalar(db, "SELECT COUNT(*) AS total FROM video_renders", default=0)
        )
        monthly_renders = await _fetch_rows(
            db,
            """
            SELECT date_trunc('month', created_at) AS month, COUNT(*) AS count
            FROM video_renders
            WHERE created_at >= NOW() - INTERVAL '6 months'
            GROUP BY month
            ORDER BY month
            """,
        )

    total_revenue = 0
    monthly_revenue: list[dict[str, Any]] = []
    if await _table_exists(db, "credit_purchases"):
        cp_cols = await _get_columns(db, "credit_purchases")
        cp_filters = ["status = 'confirmed'" if "status" in cp_cols else ""]
        cp_params: dict[str, Any] = {}
        if "source_app" in cp_cols:
            cp_filters.append("source_app = :source_app")
            cp_params["source_app"] = APP_SOURCE
        cp_where = _join_where(cp_filters)
        total_revenue = _as_json_number(
            await _fetch_scalar(
                db,
                f"SELECT COALESCE(SUM(amount), 0) AS total FROM credit_purchases{cp_where}",
                cp_params,
                default=0,
            )
        )
        cp_window_where = _join_where(cp_filters + ["created_at >= NOW() - INTERVAL '6 months'"])
        monthly_revenue = await _fetch_rows(
            db,
            f"""
            SELECT date_trunc('month', created_at) AS month, COALESCE(SUM(amount), 0) AS total
            FROM credit_purchases
            {cp_window_where}
            GROUP BY month
            ORDER BY month
            """,
            cp_params,
        )

    monthly_signups: list[dict[str, Any]] = []
    if has_created_at:
        signup_filters = ["created_at >= NOW() - INTERVAL '6 months'"]
        if has_is_active:
            signup_filters.append("COALESCE(is_active, TRUE)")
        signup_where = _join_where(signup_filters)
        monthly_signups = await _fetch_rows(
            db,
            f"""
            SELECT date_trunc('month', created_at) AS month, COUNT(*) AS count
            FROM auth_users
            {signup_where}
            GROUP BY month
            ORDER BY month
            """,
        )

    plan_distribution: list[dict[str, Any]] = []
    if has_plan:
        plan_distribution = await _fetch_rows(
            db,
            f"""
            SELECT COALESCE(plan, 'free') AS plan, COUNT(*) AS count
            FROM auth_users
            {user_filter}
            GROUP BY COALESCE(plan, 'free')
            ORDER BY COALESCE(plan, 'free')
            """,
        )
    else:
        plan_distribution = [{"plan": "free", "count": total_users}]

    page_views = {
        "totals": [],
        "today": [],
        "last7d": [],
        "last30d": [],
        "monthly": [],
    }
    if await _table_exists(db, "page_views"):
        pv_cols = await _get_columns(db, "page_views")
        if "page" in pv_cols:
            base_filters = []
            pv_params: dict[str, Any] = {}
            if "source_app" in pv_cols:
                base_filters.append("source_app = :source_app")
                pv_params["source_app"] = APP_SOURCE

            base_where = _join_where(base_filters)
            today_where = _join_where(base_filters + ["created_at >= CURRENT_DATE"])
            last7_where = _join_where(base_filters + ["created_at >= NOW() - INTERVAL '7 days'"])
            last30_where = _join_where(base_filters + ["created_at >= NOW() - INTERVAL '30 days'"])
            monthly_pv_where = _join_where(base_filters + ["created_at >= NOW() - INTERVAL '6 months'"])
            page_views["totals"] = await _fetch_rows(
                db,
                f"SELECT page, COUNT(*) AS total FROM page_views{base_where} GROUP BY page ORDER BY total DESC",
                pv_params,
            )
            page_views["today"] = await _fetch_rows(
                db,
                f"SELECT page, COUNT(*) AS total FROM page_views{today_where} GROUP BY page ORDER BY total DESC",
                pv_params,
            )
            page_views["last7d"] = await _fetch_rows(
                db,
                f"SELECT page, COUNT(*) AS total FROM page_views{last7_where} GROUP BY page ORDER BY total DESC",
                pv_params,
            )
            page_views["last30d"] = await _fetch_rows(
                db,
                f"SELECT page, COUNT(*) AS total FROM page_views{last30_where} GROUP BY page ORDER BY total DESC",
                pv_params,
            )
            page_views["monthly"] = await _fetch_rows(
                db,
                f"""
                SELECT page, date_trunc('month', created_at) AS month, COUNT(*) AS count
                FROM page_views
                {monthly_pv_where}
                GROUP BY page, month
                ORDER BY month
                """,
                pv_params,
            )

    return {
        "appSource": APP_SOURCE,
        "totalUsers": total_users,
        "proUsers": pro_users,
        "totalProjects": total_projects,
        "totalRenders": total_renders,
        "totalCreditsInCirculation": total_credits,
        "totalRevenue": _as_json_number(total_revenue),
        "newUsersLast30d": new_users_30d,
        "activeUsersLast7d": active_users_7d,
        "monthlySignups": monthly_signups,
        "monthlyProjects": monthly_projects,
        "monthlyRenders": monthly_renders,
        "monthlyRevenue": monthly_revenue,
        "planDistribution": plan_distribution,
        "pageViews": page_views,
    }


@router.get("/users")
async def admin_list_users(
    search: str = "",
    page: int = 1,
    limit: int = 50,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)

    if not await _table_exists(db, "auth_users"):
        return {"users": [], "total": 0, "page": 1, "pages": 1}

    auth_cols = await _get_columns(db, "auth_users")
    has_plan = "plan" in auth_cols
    has_plan_expires = "plan_expires_at" in auth_cols
    has_credits = "credits" in auth_cols
    has_last_login = "last_login_at" in auth_cols
    has_is_active = "is_active" in auth_cols
    has_updated_at = "updated_at" in auth_cols
    has_created_at = "created_at" in auth_cols
    name_col = "display_name" if "display_name" in auth_cols else ("name" if "name" in auth_cols else "email")

    page = max(1, int(page or 1))
    limit = min(100, max(10, int(limit or 50)))
    offset = (page - 1) * limit
    q = str(search or "").strip().lower()

    filters = ["COALESCE(is_active, TRUE)" if has_is_active else ""]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if q:
        filters.append(f"(LOWER(COALESCE({name_col}, '')) LIKE :search OR LOWER(email) LIKE :search)")
        params["search"] = f"%{q}%"
    where_clause = _join_where(filters)

    count_sql = f"SELECT COUNT(*) AS total FROM auth_users{where_clause}"
    total = _as_int(await _fetch_scalar(db, count_sql, params, default=0))

    plan_expr = "COALESCE(plan, 'free')" if has_plan else "'free'"
    plan_expires_expr = "plan_expires_at" if has_plan_expires else "NULL::timestamp"
    credits_expr = "COALESCE(credits, 0)" if has_credits else "0"
    updated_expr = "updated_at" if has_updated_at else "NULL::timestamp"
    last_login_expr = "last_login_at" if has_last_login else "NULL::timestamp"
    created_expr = "created_at" if has_created_at else "NOW()"
    order_clause = "ORDER BY last_login_at DESC NULLS LAST, created_at DESC" if has_last_login and has_created_at else (
        "ORDER BY created_at DESC" if has_created_at else "ORDER BY id DESC"
    )

    users_sql = f"""
        SELECT
            id,
            email,
            COALESCE({name_col}, email) AS name,
            COALESCE(role, 'user') AS role,
            {plan_expr} AS plan,
            {plan_expires_expr} AS plan_expires_at,
            {credits_expr} AS ai_credits,
            {created_expr} AS created_at,
            {updated_expr} AS updated_at,
            {last_login_expr} AS last_seen_at,
            {last_login_expr} AS first_seen_at
        FROM auth_users
        {where_clause}
        {order_clause}
        LIMIT :limit OFFSET :offset
    """

    users = await _fetch_rows(db, users_sql, params)
    pages = max(1, (total + limit - 1) // limit)
    return {
        "users": users,
        "total": total,
        "page": page,
        "pages": pages,
    }


@router.get("/users/{user_id}")
async def admin_user_details(
    user_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)

    auth_cols = await _get_columns(db, "auth_users") if await _table_exists(db, "auth_users") else set()
    if not auth_cols:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")

    name_col = "display_name" if "display_name" in auth_cols else ("name" if "name" in auth_cols else "email")
    plan_expr = "COALESCE(plan, 'free')" if "plan" in auth_cols else "'free'"
    plan_expires_expr = "plan_expires_at" if "plan_expires_at" in auth_cols else "NULL::timestamp"
    credits_expr = "COALESCE(credits, 0)" if "credits" in auth_cols else "0"
    last_login_expr = "last_login_at" if "last_login_at" in auth_cols else "NULL::timestamp"
    created_expr = "created_at" if "created_at" in auth_cols else "NOW()"
    updated_expr = "updated_at" if "updated_at" in auth_cols else "NULL::timestamp"

    user_row = await _fetch_rows(
        db,
        f"""
        SELECT
            id,
            email,
            COALESCE({name_col}, email) AS name,
            COALESCE(role, 'user') AS role,
            {plan_expr} AS plan,
            {plan_expires_expr} AS plan_expires_at,
            {credits_expr} AS ai_credits,
            {created_expr} AS created_at,
            {updated_expr} AS updated_at,
            {last_login_expr} AS first_seen_at,
            {last_login_expr} AS last_seen_at
        FROM auth_users
        WHERE id = :uid
        """,
        {"uid": user_id},
    )
    if not user_row:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")

    project_count = 0
    if await _table_exists(db, "video_projects"):
        project_count = _as_int(
            await _fetch_scalar(
                db,
                "SELECT COUNT(*) AS total FROM video_projects WHERE user_id = :uid",
                {"uid": user_id},
                default=0,
            )
        )

    render_count = 0
    if await _table_exists(db, "video_projects") and await _table_exists(db, "video_renders"):
        render_count = _as_int(
            await _fetch_scalar(
                db,
                """
                SELECT COUNT(*) AS total
                FROM video_renders vr
                JOIN video_projects vp ON vp.id = vr.project_id
                WHERE vp.user_id = :uid
                """,
                {"uid": user_id},
                default=0,
            )
        )

    social_count = 0
    if await _table_exists(db, "social_accounts"):
        social_count = _as_int(
            await _fetch_scalar(
                db,
                "SELECT COUNT(*) AS total FROM social_accounts WHERE user_id = :uid",
                {"uid": user_id},
                default=0,
            )
        )

    publish_count = 0
    if await _table_exists(db, "publish_jobs"):
        publish_count = _as_int(
            await _fetch_scalar(
                db,
                "SELECT COUNT(*) AS total FROM publish_jobs WHERE user_id = :uid",
                {"uid": user_id},
                default=0,
            )
        )

    credit_history: list[dict[str, Any]] = []
    if await _table_exists(db, "credit_usage"):
        credit_history = await _fetch_rows(
            db,
            """
            SELECT credits, action, created_at
            FROM credit_usage
            WHERE user_id = :uid
            ORDER BY created_at DESC
            LIMIT 20
            """,
            {"uid": user_id},
        )

    payment_history: list[dict[str, Any]] = []
    if await _table_exists(db, "credit_purchases"):
        cp_cols = await _get_columns(db, "credit_purchases")
        cp_filters = ["user_id = :uid"]
        cp_params: dict[str, Any] = {"uid": user_id}
        if "source_app" in cp_cols:
            cp_filters.append("source_app = :source_app")
            cp_params["source_app"] = APP_SOURCE
        payment_history = await _fetch_rows(
            db,
            f"""
            SELECT
                'creditos' AS kind,
                COALESCE(type, '') AS method,
                COALESCE(status, '') AS status,
                COALESCE(amount, 0) AS amount,
                created_at
            FROM credit_purchases
            {_join_where(cp_filters)}
            ORDER BY created_at DESC
            LIMIT 10
            """,
            cp_params,
        )

    return {
        "appSource": APP_SOURCE,
        "user": user_row[0],
        "projectCount": project_count,
        "renderCount": render_count,
        "socialAccountCount": social_count,
        "publishJobCount": publish_count,
        "creditHistory": credit_history,
        "paymentHistory": payment_history,
    }


@router.post("/users/{user_id}/credits")
async def admin_change_credits(
    user_id: int,
    req: AdminCreditChangeRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    amount = _as_int(req.amount)
    if amount == 0:
        raise HTTPException(status_code=400, detail="Quantidade invalida.")

    auth_cols = await _get_columns(db, "auth_users") if await _table_exists(db, "auth_users") else set()
    if "credits" not in auth_cols:
        raise HTTPException(status_code=400, detail="Coluna de creditos nao encontrada em auth_users.")

    set_parts = ["credits = GREATEST(0, COALESCE(credits, 0) + :amount)"]
    if "updated_at" in auth_cols:
        set_parts.append("updated_at = NOW()")

    result = await db.execute(
        text(
            f"""
            UPDATE auth_users
            SET {', '.join(set_parts)}
            WHERE id = :uid
            RETURNING credits
            """
        ),
        {"amount": amount, "uid": user_id},
    )
    new_balance = result.scalar()
    if new_balance is None:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")

    # Commit credit update first so legacy credit_usage schema issues do not block balance changes.
    await db.commit()

    if await _table_exists(db, "credit_usage"):
        reason = str(req.reason or "Credito admin").strip()[:200] or "Credito admin"
        action = f"admin_add: {reason}" if amount > 0 else f"admin_remove: {reason}"
        usage_cols = await _get_columns(db, "credit_usage")
        if {"user_id", "credits", "action"}.issubset(usage_cols):
            usage_columns = ["user_id", "credits", "action"]
            usage_values = [":uid", ":credits", ":action"]
            usage_params: dict[str, Any] = {
                "uid": user_id,
                "credits": abs(amount),
                "action": action,
            }
            if "job_id" in usage_cols:
                usage_columns.append("job_id")
                usage_values.append("NULL")
            if "created_at" in usage_cols:
                usage_columns.append("created_at")
                usage_values.append("NOW()")

            try:
                await db.execute(
                    text(
                        f"INSERT INTO credit_usage ({', '.join(usage_columns)}) "
                        f"VALUES ({', '.join(usage_values)})"
                    ),
                    usage_params,
                )
                await db.commit()
            except Exception:
                # Keep admin credit operation successful even when legacy credit_usage FK/schema is incompatible.
                await db.rollback()

    return {"ai_credits": _as_int(new_balance)}


@router.post("/users/{user_id}/plan")
async def admin_change_plan(
    user_id: int,
    req: AdminPlanChangeRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)

    plan = str(req.plan or "").strip().lower()
    if plan not in {"free", "pro"}:
        raise HTTPException(status_code=400, detail='Plano invalido. Use "free" ou "pro".')

    auth_cols = await _get_columns(db, "auth_users") if await _table_exists(db, "auth_users") else set()
    if "plan" not in auth_cols:
        raise HTTPException(status_code=400, detail="Coluna de plano nao encontrada em auth_users.")

    expires_at = None
    if plan == "pro" and "plan_expires_at" in auth_cols:
        duration_days = int(req.durationDays or 30)
        duration_days = max(1, min(365 * 5, duration_days))
        expires_at = datetime.utcnow() + timedelta(days=duration_days)

    set_parts = ["plan = :plan"]
    params: dict[str, Any] = {"plan": plan, "uid": user_id}
    if "plan_expires_at" in auth_cols:
        set_parts.append("plan_expires_at = :expires_at")
        params["expires_at"] = expires_at
    if "updated_at" in auth_cols:
        set_parts.append("updated_at = NOW()")

    returning_clause = "plan, plan_expires_at" if "plan_expires_at" in auth_cols else "plan"
    result = await db.execute(
        text(
            f"""
            UPDATE auth_users
            SET {', '.join(set_parts)}
            WHERE id = :uid
            RETURNING {returning_clause}
            """
        ),
        params,
    )
    row = result.mappings().first()
    if not row:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")

    await db.commit()
    return {
        "plan": str(row.get("plan") or plan),
        "plan_expires_at": row.get("plan_expires_at"),
    }


@router.post("/track-view")
async def track_view(
    req: TrackViewRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    page = str(req.page or "").strip().lower()[:120]
    if not page:
        raise HTTPException(status_code=400, detail="Pagina invalida.")

    if not await _table_exists(db, "page_views"):
        return {"ok": True, "stored": False}

    pv_cols = await _get_columns(db, "page_views")
    if "page" not in pv_cols:
        return {"ok": True, "stored": False}

    columns = []
    values = []
    params: dict[str, Any] = {}

    if "user_id" in pv_cols:
        columns.append("user_id")
        values.append(":uid")
        params["uid"] = int(user.get("id") or 0)

    columns.append("page")
    values.append(":page")
    params["page"] = page

    if "source_app" in pv_cols:
        columns.append("source_app")
        values.append(":source_app")
        params["source_app"] = APP_SOURCE

    if "created_at" in pv_cols:
        columns.append("created_at")
        values.append("NOW()")

    try:
        await db.execute(
            text(
                f"INSERT INTO page_views ({', '.join(columns)}) VALUES ({', '.join(values)})"
            ),
            params,
        )
        await db.commit()
        return {"ok": True, "stored": True}
    except Exception:
        await db.rollback()
        return {"ok": True, "stored": False}
