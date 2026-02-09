"""Pydantic data models for the vZEV calculator."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Configuration models (mirror the TOML structure)
# ---------------------------------------------------------------------------


class SettingsConfig(BaseModel):
    """Top-level [settings] section."""

    csv_directory: str = "./data"
    output_directory: str = "./output"
    database_path: str = "./vzev.db"


class CollectiveConfig(BaseModel):
    """Top-level [collective] section."""

    name: str
    language: str = "en"
    show_daily_detail: bool = False
    bill_months: list[str] = Field(default_factory=list)
    period_start: date
    period_end: date
    local_rate: float = 0.0
    bkw_buy_rate: float = 0.0
    bkw_sell_rate: float = 0.0


class MeterConfig(BaseModel):
    """A single meter entry nested under a member."""

    external_id: str
    name: str
    is_production: bool = False
    is_virtual: bool = False


class MemberConfig(BaseModel):
    """A single [[members]] entry."""

    first_name: str
    last_name: str
    street: str = ""
    zip: str = ""
    city: str = ""
    canton: str = ""
    is_host: bool = False
    meters: list[MeterConfig] = Field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class AppConfig(BaseModel):
    """Root configuration object parsed from config.toml."""

    settings: SettingsConfig = Field(default_factory=SettingsConfig)
    collective: CollectiveConfig
    members: list[MemberConfig] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Database / runtime models
# ---------------------------------------------------------------------------


class Member(BaseModel):
    """A member row from the database."""

    id: int
    first_name: str
    last_name: str
    street: str
    zip: str
    city: str
    canton: str
    is_host: bool

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Meter(BaseModel):
    """A meter row from the database."""

    id: int
    member_id: int
    external_id: str
    name: str
    is_production: bool
    is_virtual: bool


class Agreement(BaseModel):
    """An agreement row from the database."""

    id: int
    type: str
    meter_id: int | None
    period_start: str
    period_end: str
    rate: float | None
    payment_multiplier: float | None
    bkw_rate: float | None
    bkw_sell_rate: float | None


class AgreementProducerRate(BaseModel):
    """A producer rate row from the database."""

    id: int
    agreement_id: int
    producer_meter_id: int
    rate: float
    ratio: int


class MeterEnergy(BaseModel):
    """A single 15-minute energy reading."""

    id: int
    meter_id: int
    timestamp: datetime
    kwh_consumption: float
    kwh_production: float


class InvoiceDaily(BaseModel):
    """A single 15-minute allocation record per member."""

    id: int | None = None
    member_id: int
    timestamp: datetime
    year: int
    month: int
    day: int
    virtual_consumption: float
    virtual_production: float
    local_consumption: float
    bkw_consumption: float
    physical_consumption: float
    physical_production: float


class DailyDetail(BaseModel):
    """Aggregated daily data for a single member."""

    day: int
    local_consumption_kwh: float = 0.0
    bkw_consumption_kwh: float = 0.0
    total_consumption_kwh: float = 0.0
    local_cost: float = 0.0
    bkw_cost: float = 0.0
    total_cost: float = 0.0
    # Production (host only)
    total_production_kwh: float = 0.0
    local_sell_kwh: float = 0.0
    bkw_export_kwh: float = 0.0
    local_sell_revenue: float = 0.0
    bkw_export_revenue: float = 0.0
    total_revenue: float = 0.0


class MemberBill(BaseModel):
    """Calculated bill for a single member for a billing period."""

    member: Member
    year: int
    month: int
    # Consumption totals (kWh)
    total_consumption_kwh: float = 0.0
    local_consumption_kwh: float = 0.0
    bkw_consumption_kwh: float = 0.0
    # Production totals (kWh) — only for producers
    total_production_kwh: float = 0.0
    local_sell_kwh: float = 0.0
    bkw_export_kwh: float = 0.0
    # Costs (CHF)
    local_cost: float = 0.0
    bkw_cost: float = 0.0
    total_cost: float = 0.0
    # Revenue for producers (CHF)
    local_sell_revenue: float = 0.0
    bkw_export_revenue: float = 0.0
    total_revenue: float = 0.0
    # Rates used
    local_rate: float | None = None
    local_sell_rate: float | None = None
    bkw_rate: float | None = None
    bkw_sell_rate: float | None = None
    currency: str = "CHF"
    # Optional daily detail
    daily_details: list[DailyDetail] = Field(default_factory=list)
