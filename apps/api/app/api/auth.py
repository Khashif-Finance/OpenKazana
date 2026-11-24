from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, resolve_asset_by_symbol, set_auth_cookie_settings
from app.core.config import get_settings
from app.core.database import apply_rls_context, get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models.portfolio import (
    AlertRule,
    Portfolio,
    Transaction,
    Watchlist,
    WatchlistItem,
)
from app.models.user import TaxProfile, User, UserPreference
from app.schemas.auth import (
    AuthConfigOut,
    AuthResponse,
    DemoUpgradeRequest,
    GoogleOAuthRequest,
    LoginRequest,
    ProfileUpdateRequest,
    RegisterRequest,
    TaxProfileOut,
    TaxProfileUpdate,
    UserOut,
    UserPreferenceOut,
    UserPreferenceUpdate,
    WorkspaceImportRequest,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _serialize_user(user: User) -> UserOut:
    tp = user.tax_profile
    pref = user.preferences
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_demo=user.is_demo,
        is_admin=user.is_admin,
        demo_expires_at=user.demo_expires_at,
        preferred_currency=user.preferred_currency,
        telegram_chat_id=user.telegram_chat_id,
        created_at=user.created_at,
        tax_profile=TaxProfileOut(
            filer_status=tp.filer_status,
            fiscal_year=tp.fiscal_year,
            default_broker_rate_bps=tp.default_broker_rate_bps,
            zakat_exempt=tp.zakat_exempt,
            us_w8ben_active=tp.us_w8ben_active,
            foreign_cgt_rate_pct=tp.foreign_cgt_rate_pct,
        )
        if tp
        else None,
        preferences=UserPreferenceOut(
            shariah_only_default=pref.shariah_only_default,
            default_market_view=pref.default_market_view,
            compact_tables=pref.compact_tables,
            show_news_ticker=pref.show_news_ticker,
            preferred_news_sources=pref.preferred_news_sources,
        )
        if pref
        else UserPreferenceOut(),
    )


def _issue_session(response: Response, user: User) -> AuthResponse:
    token = create_access_token(user.id, user.email, is_demo=user.is_demo)
    cookie_cfg = set_auth_cookie_settings()
    response.set_cookie(value=token, **cookie_cfg)
    return AuthResponse(user=_serialize_user(user), access_token=token)


@router.get("/config", response_model=AuthConfigOut)
async def auth_config():
    """Return public authentication mode & capabilities (local self-hosted vs multiuser cloud)."""
    settings = get_settings()
    local_mode = settings.is_local_auth_mode
    return AuthConfigOut(
        auth_mode=settings.effective_auth_mode,
        local_mode=local_mode,
        allow_registration=not local_mode,
        google_client_id=settings.google_client_id or None,
        demo_ttl_hours=settings.demo_ttl_hours,
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)):
    await apply_rls_context(db, bypass=True)
    existing = (await db.execute(select(User).where(User.email == payload.email.lower()))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered.")

    admin_count = (
        await db.execute(select(func.count(User.id)).where(User.is_admin.is_(True), User.is_demo.is_(False)))
    ).scalar_one()

    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        full_name=payload.full_name.strip(),
        is_demo=False,
        is_admin=(admin_count == 0),
    )
    db.add(user)
    await db.flush()
    await apply_rls_context(db, user_id=user.id, bypass=False)

    db.add(TaxProfile(user_id=user.id, filer_status=payload.filer_status))
    db.add(UserPreference(user_id=user.id))
    db.add(
        Portfolio(
            user_id=user.id,
            name="Main Portfolio",
            base_currency="PKR",
            auto_manage_cash=True,
            is_default=True,
        )
    )
    db.add(Watchlist(user_id=user.id, name="Core Market Watch"))
    await db.commit()

    stmt = select(User).options(selectinload(User.tax_profile), selectinload(User.preferences)).where(User.id == user.id)
    user = (await db.execute(stmt)).scalar_one()
    return _issue_session(response, user)


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(User)
        .options(selectinload(User.tax_profile), selectinload(User.preferences))
        .where(User.email == payload.email.lower())
    )
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None or not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")
    await apply_rls_context(db, user_id=user.id, bypass=False)
    return _issue_session(response, user)


@router.post("/demo", response_model=AuthResponse)
async def demo_login():
    """Demo Sandbox has been disabled; all accounts use real user portfolios and live market feeds."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Demo Sandbox has been removed. Please create a real account or sign in.",
    )


@router.post("/upgrade-demo", response_model=AuthResponse)
async def upgrade_demo_account(
    payload: DemoUpgradeRequest,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Convert any legacy demo session into a permanent email/password account."""
    if not user.is_demo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current session is already a permanent account.",
        )

    target_email = payload.email.lower()
    existing = (
        await db.execute(select(User).where(User.email == target_email, User.id != user.id))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered.")

    user.email = target_email
    user.password_hash = hash_password(payload.password)
    user.full_name = payload.full_name.strip()
    user.is_demo = False
    user.demo_expires_at = None
    await db.commit()

    stmt = select(User).options(selectinload(User.tax_profile)).where(User.id == user.id)
    updated = (await db.execute(stmt)).scalar_one()
    return _issue_session(response, updated)


@router.post("/google", response_model=AuthResponse)
async def google_oauth_login(payload: GoogleOAuthRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """Verify Google ID token and sign in or create user account."""
    settings = get_settings()
    if not settings.google_client_id or not payload.id_token:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth is not configured on this instance. Set GOOGLE_CLIENT_ID.",
        )

    async with httpx.AsyncClient(timeout=10.0) as client:
        verify_resp = await client.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": payload.id_token},
        )
        if verify_resp.status_code != 200:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google ID token.")
        info = verify_resp.json()

    if info.get("aud") != settings.google_client_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token audience mismatch.")

    email = (info.get("email") or "").lower()
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google token missing email.")

    stmt = select(User).options(selectinload(User.tax_profile)).where(User.email == email)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        await apply_rls_context(db, bypass=True)
        admin_count = (
            await db.execute(select(func.count(User.id)).where(User.is_admin.is_(True), User.is_demo.is_(False)))
        ).scalar_one()
        user = User(
            email=email,
            full_name=info.get("name") or email.split("@")[0],
            oauth_provider="google",
            oauth_subject=info.get("sub"),
            is_admin=(admin_count == 0),
        )
        db.add(user)
        await db.flush()
        await apply_rls_context(db, user_id=user.id, bypass=False)
        db.add(TaxProfile(user_id=user.id))
        db.add(Portfolio(user_id=user.id, name="Main Portfolio", base_currency="PKR", auto_manage_cash=True, is_default=True))
        db.add(Watchlist(user_id=user.id, name="Core Market Watch"))
        await db.commit()
        user = (await db.execute(stmt)).scalar_one()
    else:
        await apply_rls_context(db, user_id=user.id, bypass=False)

    return _issue_session(response, user)


@router.post("/logout")
async def logout(response: Response):
    settings = get_settings()
    response.delete_cookie(settings.cookie_name, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(get_current_user)):
    return _serialize_user(user)


@router.patch("/profile", response_model=UserOut)
async def update_profile(
    payload: ProfileUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user display name, preferred display currency, Telegram chat ID, or password."""
    if payload.full_name is not None:
        user.full_name = payload.full_name.strip()
    if payload.preferred_currency is not None:
        user.preferred_currency = payload.preferred_currency
    if payload.telegram_chat_id is not None:
        cleaned_chat = payload.telegram_chat_id.strip()
        user.telegram_chat_id = cleaned_chat or None
    if payload.new_password is not None:
        if user.password_hash and not user.is_demo and not user.oauth_provider:
            if not payload.current_password or not verify_password(payload.current_password, user.password_hash):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Current password is incorrect.",
                )
        user.password_hash = hash_password(payload.new_password)

    await db.commit()
    stmt = select(User).options(selectinload(User.tax_profile), selectinload(User.preferences)).where(User.id == user.id)
    refreshed = (await db.execute(stmt)).scalar_one()
    return _serialize_user(refreshed)


@router.patch("/preferences", response_model=UserPreferenceOut)
async def update_preferences(
    payload: UserPreferenceUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update workspace preferences (Shariah-only default, default market, compact tables, news sources)."""
    pref = user.preferences
    if pref is None:
        pref = UserPreference(user_id=user.id)
        db.add(pref)
    if payload.shariah_only_default is not None:
        pref.shariah_only_default = payload.shariah_only_default
    if payload.default_market_view is not None:
        pref.default_market_view = payload.default_market_view
    if payload.compact_tables is not None:
        pref.compact_tables = payload.compact_tables
    if payload.show_news_ticker is not None:
        pref.show_news_ticker = payload.show_news_ticker
    if payload.preferred_news_sources is not None:
        pref.preferred_news_sources = payload.preferred_news_sources.strip() or "ALL"
    await db.commit()
    await db.refresh(pref)
    return UserPreferenceOut(
        shariah_only_default=pref.shariah_only_default,
        default_market_view=pref.default_market_view,
        compact_tables=pref.compact_tables,
        show_news_ticker=pref.show_news_ticker,
        preferred_news_sources=pref.preferred_news_sources,
    )


@router.get("/workspace/export")
async def export_workspace(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export the full self-hosted user workspace (profile, tax, preferences, portfolios, trades, watchlists, alerts) as JSON."""
    portfolios = (
        await db.execute(
            select(Portfolio)
            .options(selectinload(Portfolio.transactions).selectinload(Transaction.asset))
            .where(Portfolio.user_id == user.id)
            .order_by(Portfolio.created_at.asc())
        )
    ).scalars().all()
    watchlists = (
        await db.execute(
            select(Watchlist)
            .options(selectinload(Watchlist.items).selectinload(WatchlistItem.asset))
            .where(Watchlist.user_id == user.id)
            .order_by(Watchlist.created_at.asc())
        )
    ).scalars().all()
    alerts = (
        await db.execute(
            select(AlertRule)
            .options(selectinload(AlertRule.asset))
            .where(AlertRule.user_id == user.id)
            .order_by(AlertRule.created_at.asc())
        )
    ).scalars().all()

    tp = user.tax_profile
    pref = user.preferences
    return {
        "schema_version": "openkhazana-workspace-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "profile": {
            "full_name": user.full_name,
            "preferred_currency": user.preferred_currency,
            "telegram_chat_id": user.telegram_chat_id,
        },
        "tax_profile": {
            "filer_status": tp.filer_status,
            "fiscal_year": tp.fiscal_year,
            "default_broker_rate_bps": tp.default_broker_rate_bps,
            "zakat_exempt": tp.zakat_exempt,
            "us_w8ben_active": tp.us_w8ben_active,
            "foreign_cgt_rate_pct": str(tp.foreign_cgt_rate_pct),
        }
        if tp
        else None,
        "preferences": {
            "shariah_only_default": pref.shariah_only_default,
            "default_market_view": pref.default_market_view,
            "compact_tables": pref.compact_tables,
            "show_news_ticker": pref.show_news_ticker,
            "preferred_news_sources": pref.preferred_news_sources,
        }
        if pref
        else None,
        "portfolios": [
            {
                "name": p.name,
                "base_currency": p.base_currency,
                "description": p.description,
                "auto_manage_cash": p.auto_manage_cash,
                "is_default": p.is_default,
                "transactions": [
                    {
                        "symbol": t.asset.symbol if t.asset else "",
                        "tx_type": t.tx_type,
                        "tx_date": t.tx_date.isoformat(),
                        "quantity": str(t.quantity),
                        "unit_price": str(t.unit_price),
                        "gross_amount": str(t.gross_amount),
                        "fee_amount": str(t.fee_amount),
                        "tax_withheld": str(t.tax_withheld),
                        "deemed_ex_price": str(t.deemed_ex_price),
                        "shariah_purification_pkr": str(t.shariah_purification_pkr),
                        "fx_rate_to_pkr": str(t.fx_rate_to_pkr),
                        "notes": t.notes,
                    }
                    for t in p.transactions
                    if t.asset is not None
                ],
            }
            for p in portfolios
        ],
        "watchlists": [
            {
                "name": w.name,
                "symbols": [item.asset.symbol for item in w.items if item.asset is not None],
            }
            for w in watchlists
        ],
        "alert_rules": [
            {
                "symbol": a.asset.symbol,
                "rule_type": a.rule_type,
                "threshold_value": str(a.threshold_value) if a.threshold_value is not None else None,
                "channels": a.channels,
                "is_active": a.is_active,
            }
            for a in alerts
            if a.asset is not None
        ],
    }


@router.post("/workspace/import")
async def import_workspace(
    payload: WorkspaceImportRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Idempotently restore profile, tax, preferences, portfolios, transactions, and watchlists from a workspace JSON backup."""
    restored = {"portfolios": 0, "transactions": 0, "watchlists": 0, "watchlist_items": 0, "alerts": 0}

    if payload.profile:
        if payload.profile.get("full_name"):
            user.full_name = str(payload.profile["full_name"])[:120]
        if payload.profile.get("preferred_currency") in {"PKR", "USD"}:
            user.preferred_currency = payload.profile["preferred_currency"]
        if "telegram_chat_id" in payload.profile:
            user.telegram_chat_id = payload.profile.get("telegram_chat_id") or None

    if payload.tax_profile:
        tp = user.tax_profile or TaxProfile(user_id=user.id)
        if user.tax_profile is None:
            db.add(tp)
        if payload.tax_profile.get("filer_status") in {"ACTIVE_FILER", "LATE_FILER", "NON_FILER"}:
            tp.filer_status = payload.tax_profile["filer_status"]
        if payload.tax_profile.get("fiscal_year"):
            tp.fiscal_year = str(payload.tax_profile["fiscal_year"])[:16]
        if payload.tax_profile.get("default_broker_rate_bps") is not None:
            tp.default_broker_rate_bps = max(0, min(500, int(payload.tax_profile["default_broker_rate_bps"])))
        if payload.tax_profile.get("zakat_exempt") is not None:
            tp.zakat_exempt = bool(payload.tax_profile["zakat_exempt"])
        if payload.tax_profile.get("us_w8ben_active") is not None:
            tp.us_w8ben_active = bool(payload.tax_profile["us_w8ben_active"])
        if payload.tax_profile.get("foreign_cgt_rate_pct") is not None:
            tp.foreign_cgt_rate_pct = Decimal(str(payload.tax_profile["foreign_cgt_rate_pct"]))

    if payload.preferences:
        pref = user.preferences or UserPreference(user_id=user.id)
        if user.preferences is None:
            db.add(pref)
        if payload.preferences.get("shariah_only_default") is not None:
            pref.shariah_only_default = bool(payload.preferences["shariah_only_default"])
        if payload.preferences.get("default_market_view") in {"PSX", "ALL", "INTL"}:
            pref.default_market_view = payload.preferences["default_market_view"]
        if payload.preferences.get("compact_tables") is not None:
            pref.compact_tables = bool(payload.preferences["compact_tables"])
        if payload.preferences.get("show_news_ticker") is not None:
            pref.show_news_ticker = bool(payload.preferences["show_news_ticker"])
        if payload.preferences.get("preferred_news_sources"):
            pref.preferred_news_sources = str(payload.preferences["preferred_news_sources"])[:400]

    existing_portfolios = {
        p.name.lower(): p
        for p in (await db.execute(select(Portfolio).where(Portfolio.user_id == user.id))).scalars().all()
    }
    for p_in in payload.portfolios:
        p_name = str(p_in.get("name") or "").strip()[:120]
        if not p_name:
            continue
        port = existing_portfolios.get(p_name.lower())
        if port is None:
            port = Portfolio(
                user_id=user.id,
                name=p_name,
                base_currency=str(p_in.get("base_currency") or "PKR")[:8],
                description=str(p_in.get("description") or "")[:255],
                auto_manage_cash=bool(p_in.get("auto_manage_cash", True)),
                is_default=bool(p_in.get("is_default", False)),
            )
            db.add(port)
            await db.flush()
            existing_portfolios[p_name.lower()] = port
            restored["portfolios"] += 1

        existing_tx_sigs = {
            (t.asset_id, t.tx_type, t.tx_date.isoformat(), str(t.quantity), str(t.unit_price))
            for t in (await db.execute(select(Transaction).where(Transaction.portfolio_id == port.id))).scalars().all()
        }
        for tx_in in p_in.get("transactions") or []:
            sym = str(tx_in.get("symbol") or "").strip().upper()
            if not sym:
                continue
            asset = await resolve_asset_by_symbol(sym, db, owner_user_id=user.id)
            if asset is None:
                continue
            try:
                tx_date_val = date.fromisoformat(str(tx_in.get("tx_date")))
                qty = Decimal(str(tx_in.get("quantity", "0")))
                px = Decimal(str(tx_in.get("unit_price", "0")))
                sig = (asset.id, str(tx_in.get("tx_type", "BUY")), tx_date_val.isoformat(), str(qty), str(px))
                if sig in existing_tx_sigs:
                    continue
                existing_tx_sigs.add(sig)
                db.add(
                    Transaction(
                        portfolio_id=port.id,
                        asset_id=asset.id,
                        tx_type=str(tx_in.get("tx_type", "BUY"))[:24],
                        tx_date=tx_date_val,
                        quantity=qty,
                        unit_price=px,
                        gross_amount=Decimal(str(tx_in.get("gross_amount", "0"))),
                        fee_amount=Decimal(str(tx_in.get("fee_amount", "0"))),
                        tax_withheld=Decimal(str(tx_in.get("tax_withheld", "0"))),
                        deemed_ex_price=Decimal(str(tx_in.get("deemed_ex_price", "0"))),
                        shariah_purification_pkr=Decimal(str(tx_in.get("shariah_purification_pkr", "0"))),
                        fx_rate_to_pkr=Decimal(str(tx_in.get("fx_rate_to_pkr", "1"))),
                        notes=str(tx_in.get("notes") or ""),
                    )
                )
                restored["transactions"] += 1
            except Exception:
                continue

    existing_wls = {
        w.name.lower(): w
        for w in (
            await db.execute(
                select(Watchlist).options(selectinload(Watchlist.items)).where(Watchlist.user_id == user.id)
            )
        ).scalars().all()
    }
    for wl_in in payload.watchlists:
        w_name = str(wl_in.get("name") or "").strip()[:100]
        if not w_name:
            continue
        wl = existing_wls.get(w_name.lower())
        if wl is None:
            wl = Watchlist(user_id=user.id, name=w_name)
            db.add(wl)
            await db.flush()
            existing_wls[w_name.lower()] = wl
            restored["watchlists"] += 1
            existing_asset_ids: set[str] = set()
        else:
            existing_asset_ids = {item.asset_id for item in wl.items}
        for sym in wl_in.get("symbols") or []:
            asset = await resolve_asset_by_symbol(str(sym), db, owner_user_id=user.id)
            if asset is None or asset.id in existing_asset_ids:
                continue
            existing_asset_ids.add(asset.id)
            db.add(WatchlistItem(watchlist_id=wl.id, asset_id=asset.id))
            restored["watchlist_items"] += 1

    await db.commit()
    return {"ok": True, "restored": restored}


@router.patch("/tax-profile", response_model=TaxProfileOut)
async def update_tax_profile(
    payload: TaxProfileUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tp = user.tax_profile
    if tp is None:
        tp = TaxProfile(user_id=user.id)
        db.add(tp)
    if payload.filer_status is not None:
        tp.filer_status = payload.filer_status
    if payload.fiscal_year is not None:
        tp.fiscal_year = payload.fiscal_year
    if payload.default_broker_rate_bps is not None:
        tp.default_broker_rate_bps = payload.default_broker_rate_bps
    if payload.zakat_exempt is not None:
        tp.zakat_exempt = payload.zakat_exempt
    if payload.us_w8ben_active is not None:
        tp.us_w8ben_active = payload.us_w8ben_active
    if payload.foreign_cgt_rate_pct is not None:
        tp.foreign_cgt_rate_pct = payload.foreign_cgt_rate_pct
    await db.commit()
    await db.refresh(tp)
    return TaxProfileOut(
        filer_status=tp.filer_status,
        fiscal_year=tp.fiscal_year,
        default_broker_rate_bps=tp.default_broker_rate_bps,
        zakat_exempt=tp.zakat_exempt,
        us_w8ben_active=tp.us_w8ben_active,
        foreign_cgt_rate_pct=tp.foreign_cgt_rate_pct,
    )
