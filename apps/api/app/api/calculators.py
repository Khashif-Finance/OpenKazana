from __future__ import annotations

from fastapi import APIRouter

from app.schemas.calculator import (
    BrokerageCgtRequest,
    BrokerageCgtResponse,
    DailyDividendCalculatorRequest,
    DailyDividendCalculatorResponse,
    SipCalculatorRequest,
    SipCalculatorResponse,
    ZakatRequest,
    ZakatResponse,
)
from app.services.calculators import (
    run_brokerage_cgt_calculator,
    run_daily_dividend_calculator,
    run_sip_calculator,
    run_zakat_calculator,
)

router = APIRouter(prefix="/api/v1/calculators", tags=["calculators"])


@router.post("/sip", response_model=SipCalculatorResponse)
async def sip_calculator(payload: SipCalculatorRequest):
    return run_sip_calculator(payload)


@router.post("/brokerage-cgt", response_model=BrokerageCgtResponse)
async def brokerage_cgt_calculator(payload: BrokerageCgtRequest):
    return run_brokerage_cgt_calculator(payload)


@router.post("/zakat", response_model=ZakatResponse)
async def zakat_calculator(payload: ZakatRequest):
    return run_zakat_calculator(payload)


@router.post("/daily-dividend", response_model=DailyDividendCalculatorResponse)
async def daily_dividend_calculator(payload: DailyDividendCalculatorRequest):
    return run_daily_dividend_calculator(payload)
