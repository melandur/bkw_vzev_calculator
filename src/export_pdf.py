"""Generate professional PDF bills for each member using fpdf2."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fpdf import FPDF
from loguru import logger

from src.models import DailyDetail, MemberBill
from src.translations import get_month_name, get_translations

# Layout constants
_PAGE_W = 190  # usable width (A4 - margins)
_LH = 5.5  # line height (compact)
_TABLE_LH = 6  # table row height
_FOOTER_Y = 282  # absolute Y for footer on A4

# 4-column table widths: Description | kWh | Rate | Total
_C_DESC = 80
_C_KWH = 40
_C_RATE = 35
_C_TOTAL = 35


def _draw_sun_icon(pdf: FPDF, cx: float, cy: float) -> None:
    """Draw a small sun icon (star shape) at (cx, cy)."""
    pdf.set_fill_color(245, 180, 0)
    pdf.set_draw_color(200, 150, 0)
    pdf.set_line_width(0.15)
    # 8-pointed star resembling a sun
    pdf.star(x=cx, y=cy, r_in=0.8, r_out=1.8, corners=8, rotate_degrees=22, style="DF")
    # Reset
    pdf.set_line_width(0.2)
    pdf.set_draw_color(0, 0, 0)
    pdf.set_fill_color(255, 255, 255)


def _draw_lightning_icon(pdf: FPDF, cx: float, cy: float, h: float = 3.5) -> None:
    """Draw a small lightning bolt icon at (cx, cy)."""
    top = cy - h / 2
    pdf.set_fill_color(50, 120, 200)
    pdf.set_draw_color(30, 80, 160)
    pdf.set_line_width(0.15)
    points = (
        (cx - 0.3, top),
        (cx + 1.0, top),
        (cx + 0.2, top + h * 0.42),
        (cx + 1.0, top + h * 0.42),
        (cx - 0.6, top + h),
        (cx + 0.1, top + h * 0.55),
        (cx - 0.7, top + h * 0.55),
    )
    pdf.polygon(points, style="DF")
    # Reset
    pdf.set_line_width(0.2)
    pdf.set_draw_color(0, 0, 0)
    pdf.set_fill_color(255, 255, 255)


def export_pdf_bills(
    bills: list[MemberBill],
    collective_name: str,
    show_daily_detail: bool,
    show_icons: bool,
    language: str,
    output_dir: str | Path,
    label_overrides: dict[str, str] | None = None,
) -> list[Path]:
    """Write one PDF per bill. Returns paths to the generated files."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    t = get_translations(language)
    if label_overrides:
        t = {**t, **label_overrides}
    bill_title = t["bill_title"]

    paths: list[Path] = []
    for bill in bills:
        path = _generate_bill_pdf(bill, collective_name, bill_title, show_daily_detail, show_icons, t, language, out)
        paths.append(path)

    logger.info("Generated {} PDF bill(s) in {}", len(paths), out)
    return paths


def _generate_bill_pdf(
    bill: MemberBill,
    collective_name: str,
    bill_title: str,
    show_daily_detail: bool,
    show_icons: bool,
    t: dict[str, str],
    language: str,
    out_dir: Path,
) -> Path:
    # Generate period label based on number of months
    period_months = bill.period_months if bill.period_months else [(bill.year, bill.month)]

    if len(period_months) == 1:
        # Single month
        period_label = f"{get_month_name(language, bill.month)} {bill.year}"
        period_suffix = f"{bill.year}-{bill.month:02d}"
    else:
        # Multi-month period (e.g. quarterly)
        first_year, first_month = period_months[0]
        last_year, last_month = period_months[-1]
        if first_year == last_year:
            period_label = f"{get_month_name(language, first_month)} - {get_month_name(language, last_month)} {first_year}"
            period_suffix = f"{first_year}-{first_month:02d}_to_{last_month:02d}"
        else:
            period_label = f"{get_month_name(language, first_month)} {first_year} - {get_month_name(language, last_month)} {last_year}"
            period_suffix = f"{first_year}-{first_month:02d}_to_{last_year}-{last_month:02d}"

    file_prefix = t["file_prefix"]
    filename = (
        f"{file_prefix}_{period_suffix}"
        f"_{bill.member.last_name}_{bill.member.first_name}.pdf"
    )
    filepath = out_dir / filename

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=False)

    # ---- Header bar -------------------------------------------------------
    _draw_header_bar(pdf, bill_title, collective_name)

    # ---- Addresses --------------------------------------------------------
    pdf.set_y(28)
    y_addr = pdf.get_y()

    # From (left)
    pdf.set_xy(10, y_addr)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(130, 130, 130)
    pdf.cell(90, 4, t["from"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_x(10)
    pdf.cell(90, _LH, collective_name, new_x="LMARGIN", new_y="NEXT")

    # To (right)
    pdf.set_xy(110, y_addr)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(130, 130, 130)
    pdf.cell(90, 4, t["to"])
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(110, y_addr + 4)
    pdf.cell(90, _LH, bill.member.full_name)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(110, y_addr + 4 + _LH)
    pdf.cell(90, _LH, bill.member.street)
    city_line = f"{bill.member.zip} {bill.member.city}"
    if bill.member.canton:
        city_line += f", {bill.member.canton}"
    pdf.set_xy(110, y_addr + 4 + _LH * 2)
    pdf.cell(90, _LH, city_line)

    pdf.set_y(y_addr + 4 + _LH * 3 + 2)

    # ---- Billing period ---------------------------------------------------
    _thin_line(pdf)
    pdf.ln(1.5)
    _info_row(pdf, t["billing_period"], period_label)
    pdf.ln(2)

    # ---- Consumption & Cost (single table) --------------------------------
    _section_header(pdf, t["consumption_cost"])
    _table_header_4col(pdf, t, revenue=False)

    local_rate = bill.local_rate or 0.0
    bkw_rate = bill.bkw_rate or 0.0

    # Icons: drawn as vector shapes when enabled
    icon_solar = "sun" if show_icons else ""
    icon_grid = "lightning" if show_icons else ""

    _table_row_4col(pdf, t["local_solar"], bill.local_consumption_kwh, local_rate, bill.local_cost, bill.currency, icon=icon_solar)
    _table_row_4col(pdf, t["grid_bkw"], bill.bkw_consumption_kwh, bkw_rate, bill.bkw_cost, bill.currency, icon=icon_grid)
    _table_total_4col(pdf, t["total"], bill.total_consumption_kwh, bill.total_cost, bill.currency)
    pdf.ln(2)

    # ---- Producer settlement (single table, only if produced) -------------
    if bill.total_production_kwh > 0:
        bkw_sell_rate = bill.bkw_sell_rate or 0.0

        _section_header(pdf, t["production_revenue"])
        _table_header_4col(pdf, t, revenue=True)

        local_sell_rate = bill.local_sell_rate or 0.0
        _table_row_4col(
            pdf, t["sold_locally"], bill.local_sell_kwh, local_sell_rate, bill.local_sell_revenue, bill.currency, icon=icon_solar,
        )
        _table_row_4col(
            pdf, t["exported_to_grid"], bill.bkw_export_kwh, bkw_sell_rate, bill.bkw_export_revenue, bill.currency, icon=icon_grid,
        )
        _table_total_4col(pdf, t["total"], bill.total_production_kwh, bill.total_revenue, bill.currency)
        pdf.ln(2)

    # ---- Additional Fees (if any) -----------------------------------------
    if bill.calculated_fees:
        _draw_additional_fees(pdf, bill, t)
        pdf.ln(2)

    # ---- Summary box ------------------------------------------------------
    net = bill.total_revenue - bill.total_cost - bill.total_fees
    is_host = bill.member.is_host
    _summary_box(pdf, bill, net, is_host, t)

    # ---- Footer (page 1) --------------------------------------------------
    _draw_footer(pdf, t)

    # ---- Daily detail pages (optional) ------------------------------------
    if show_daily_detail and bill.daily_details:
        _draw_daily_detail_pages(pdf, bill, collective_name, bill_title, period_label, t, language)

    pdf.output(str(filepath))
    logger.debug("  PDF: {}", filepath.name)
    return filepath


# ===========================================================================
# Daily detail pages – grouped by month for multi-month billing periods
# ===========================================================================

# Daily table column widths
_D_DAY = 18
_D_VAL = (190 - 18) // 6  # ~28 each for 6 value cols
_D_ROW_H = 5.5  # row height
_D_MAX_Y = 275  # must stay above this to leave room for footer


def _draw_daily_detail_pages(
    pdf: FPDF,
    bill: MemberBill,
    collective_name: str,
    bill_title: str,
    period_label: str,
    t: dict[str, str],
    language: str,
) -> None:
    """Add page(s) with daily consumption/cost and production/revenue tables.

    For multi-month periods (quarterly / semi-annual / annual) the days are
    grouped by month with a month sub-header and per-month subtotals so
    that the listing fits neatly on paper.
    """
    from itertools import groupby

    details = bill.daily_details
    period_months = bill.period_months if bill.period_months else [(bill.year, bill.month)]
    multi_month = len(period_months) > 1

    # Group details by (year, month) – details are already in chronological order
    month_groups: list[tuple[tuple[int, int], list[DailyDetail]]] = []
    for key, group in groupby(details, key=lambda d: (d.year, d.month)):
        month_groups.append((key, list(group)))

    # --- Consumption pages -------------------------------------------------
    _draw_daily_consumption_pages(
        pdf, bill, collective_name, bill_title, period_label,
        t, language, month_groups, multi_month,
    )

    # --- Production pages (if applicable) ----------------------------------
    if bill.total_production_kwh > 0:
        _draw_daily_production_pages(
            pdf, bill, collective_name, bill_title, period_label,
            t, language, month_groups, multi_month,
        )


# ---------------------------------------------------------------------------
# Page / section helpers for daily tables
# ---------------------------------------------------------------------------


def _start_daily_page(
    pdf: FPDF,
    bill_title: str,
    collective_name: str,
    member_name: str,
    period_label: str,
    section_title: str,
    t: dict[str, str],
) -> None:
    """Add a new page and draw page header, member name, and section title."""
    pdf.add_page()
    pdf.set_auto_page_break(auto=False)
    _draw_header_bar(pdf, bill_title, collective_name)
    pdf.set_y(28)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, _LH, f"{member_name}  -  {period_label}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(1)
    _section_header(pdf, section_title)


def _draw_month_sub_header(pdf: FPDF, label: str) -> None:
    """Draw a coloured month sub-header row spanning the full table width."""
    pdf.set_fill_color(33, 60, 114)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(_PAGE_W, 6, f"  {label}", fill=True)
    pdf.ln(6)
    pdf.set_text_color(0, 0, 0)


def _draw_consumption_col_headers(pdf: FPDF, currency: str, t: dict[str, str]) -> None:
    """Draw the column header row for daily consumption tables."""
    col_w = _D_VAL
    h = _D_ROW_H
    local_lbl = t.get("local_currency", "Local")
    grid_lbl = t.get("grid_currency", "Grid")
    pdf.set_fill_color(240, 242, 246)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(70, 70, 70)
    pdf.cell(_D_DAY, h, f"  {t['day']}", fill=True)
    pdf.cell(col_w, h, t["local_kwh"], fill=True, align="R")
    pdf.cell(col_w, h, t["grid_kwh"], fill=True, align="R")
    pdf.cell(col_w, h, t["total_kwh"], fill=True, align="R")
    pdf.cell(col_w, h, f"{local_lbl} {currency}", fill=True, align="R")
    pdf.cell(col_w, h, f"{grid_lbl} {currency}", fill=True, align="R")
    pdf.cell(col_w, h, f"{t['total']} {currency}", fill=True, align="R")
    pdf.ln(h)
    pdf.set_text_color(0, 0, 0)


def _draw_production_col_headers(pdf: FPDF, currency: str, t: dict[str, str]) -> None:
    """Draw the column header row for daily production tables."""
    col_w = _D_VAL
    h = _D_ROW_H
    local_lbl = t.get("local_currency", "Local")
    grid_lbl = t.get("grid_currency", "Grid")
    pdf.set_fill_color(240, 242, 246)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(70, 70, 70)
    pdf.cell(_D_DAY, h, f"  {t['day']}", fill=True)
    pdf.cell(col_w, h, t["prod_kwh"], fill=True, align="R")
    pdf.cell(col_w, h, t["local_kwh"], fill=True, align="R")
    pdf.cell(col_w, h, t["grid_kwh"], fill=True, align="R")
    pdf.cell(col_w, h, f"{local_lbl} {currency}", fill=True, align="R")
    pdf.cell(col_w, h, f"{grid_lbl} {currency}", fill=True, align="R")
    pdf.cell(col_w, h, f"{t['total']} {currency}", fill=True, align="R")
    pdf.ln(h)
    pdf.set_text_color(0, 0, 0)


def _draw_daily_total_row(
    pdf: FPDF, label: str, vals: tuple[float, ...], currency: str, bold: bool = True,
) -> None:
    """Draw a totals/subtotals row with 6 numeric values.

    *vals* order: v1_kwh, v2_kwh, v3_kwh, v4_chf, v5_chf, v6_chf.
    """
    col_w = _D_VAL
    h = _D_ROW_H + 1
    pdf.set_draw_color(200, 200, 200)
    pdf.line(10, pdf.get_y(), 10 + _PAGE_W, pdf.get_y())
    pdf.set_draw_color(0, 0, 0)
    weight = "B" if bold else ""
    pdf.set_font("Helvetica", weight, 7)
    pdf.cell(_D_DAY, h, f"  {label}")
    pdf.cell(col_w, h, f"{vals[0]:,.0f}", align="R")
    pdf.cell(col_w, h, f"{vals[1]:,.0f}", align="R")
    pdf.cell(col_w, h, f"{vals[2]:,.0f}", align="R")
    pdf.cell(col_w, h, f"{vals[3]:,.2f}", align="R")
    pdf.cell(col_w, h, f"{vals[4]:,.2f}", align="R")
    pdf.cell(col_w, h, f"{vals[5]:,.2f} {currency}", align="R")
    pdf.ln(h)


# ---------------------------------------------------------------------------
# Daily consumption pages (grouped by month)
# ---------------------------------------------------------------------------


def _draw_daily_consumption_pages(
    pdf: FPDF,
    bill: MemberBill,
    collective_name: str,
    bill_title: str,
    period_label: str,
    t: dict[str, str],
    language: str,
    month_groups: list[tuple[tuple[int, int], list[DailyDetail]]],
    multi_month: bool,
) -> None:
    member_name = bill.member.full_name
    section_title = t["daily_consumption_cost"]
    currency = bill.currency
    col_w = _D_VAL
    h = _D_ROW_H

    # Grand totals
    g_local = g_bkw = g_cons = 0.0
    g_local_c = g_bkw_c = g_total_c = 0.0

    for mi, ((year, month), days) in enumerate(month_groups):
        # Calculate space needed for this month block
        needed = len(days) * h
        if multi_month:
            needed += 6  # month sub-header
            needed += h + 1  # subtotal row
        # Column headers if this is the first block or a new page
        col_hdr_h = h  # column header height

        if mi == 0:
            # First month – start fresh page
            _start_daily_page(pdf, bill_title, collective_name, member_name, period_label, section_title, t)
            _draw_consumption_col_headers(pdf, currency, t)
        else:
            # Subsequent months – check if block fits on current page
            space_left = _D_MAX_Y - pdf.get_y()
            if needed + 4 > space_left:
                # Need a new page
                _draw_footer(pdf, t)
                _start_daily_page(pdf, bill_title, collective_name, member_name, period_label, section_title, t)
                _draw_consumption_col_headers(pdf, currency, t)
            else:
                pdf.ln(2)  # small gap between months on same page

        # Month sub-header (only for multi-month periods)
        if multi_month:
            month_label = f"{get_month_name(language, month)} {year}"
            _draw_month_sub_header(pdf, month_label)

        # Data rows
        s_local = s_bkw = s_cons = 0.0
        s_local_c = s_bkw_c = s_total_c = 0.0
        stripe = False

        for d in days:
            # Safety: per-row page overflow check
            if pdf.get_y() + h > _D_MAX_Y:
                _draw_footer(pdf, t)
                _start_daily_page(pdf, bill_title, collective_name, member_name, period_label, section_title, t)
                _draw_consumption_col_headers(pdf, currency, t)
                if multi_month:
                    month_label = f"{get_month_name(language, month)} {year}"
                    _draw_month_sub_header(pdf, f"{month_label} …")

            if stripe:
                pdf.set_fill_color(250, 250, 252)
                fill = True
            else:
                fill = False
            stripe = not stripe

            pdf.set_font("Helvetica", "", 7)
            pdf.cell(_D_DAY, h, f"  {d.day:>2}", fill=fill)
            pdf.cell(col_w, h, f"{d.local_consumption_kwh:,.0f}", align="R", fill=fill)
            pdf.cell(col_w, h, f"{d.bkw_consumption_kwh:,.0f}", align="R", fill=fill)
            pdf.cell(col_w, h, f"{d.total_consumption_kwh:,.0f}", align="R", fill=fill)
            pdf.cell(col_w, h, f"{d.local_cost:,.2f}", align="R", fill=fill)
            pdf.cell(col_w, h, f"{d.bkw_cost:,.2f}", align="R", fill=fill)
            pdf.cell(col_w, h, f"{d.total_cost:,.2f}", align="R", fill=fill)
            pdf.ln(h)

            s_local += d.local_consumption_kwh
            s_bkw += d.bkw_consumption_kwh
            s_cons += d.total_consumption_kwh
            s_local_c += d.local_cost
            s_bkw_c += d.bkw_cost
            s_total_c += d.total_cost

        g_local += s_local
        g_bkw += s_bkw
        g_cons += s_cons
        g_local_c += s_local_c
        g_bkw_c += s_bkw_c
        g_total_c += s_total_c

        # Month subtotal (only for multi-month)
        if multi_month:
            _draw_daily_total_row(
                pdf, t["subtotal"],
                (s_local, s_bkw, s_cons, s_local_c, s_bkw_c, s_total_c),
                currency, bold=False,
            )

    # Grand total
    _draw_daily_total_row(
        pdf, t["total"],
        (g_local, g_bkw, g_cons, g_local_c, g_bkw_c, g_total_c),
        currency, bold=True,
    )
    _draw_footer(pdf, t)


# ---------------------------------------------------------------------------
# Daily production pages (grouped by month)
# ---------------------------------------------------------------------------


def _draw_daily_production_pages(
    pdf: FPDF,
    bill: MemberBill,
    collective_name: str,
    bill_title: str,
    period_label: str,
    t: dict[str, str],
    language: str,
    month_groups: list[tuple[tuple[int, int], list[DailyDetail]]],
    multi_month: bool,
) -> None:
    member_name = bill.member.full_name
    section_title = t["daily_production_revenue"]
    currency = bill.currency
    col_w = _D_VAL
    h = _D_ROW_H

    # Grand totals
    g_prod = g_local = g_grid = 0.0
    g_local_r = g_grid_r = g_total_r = 0.0

    for mi, ((year, month), days) in enumerate(month_groups):
        # Calculate space needed for this month block
        needed = len(days) * h
        if multi_month:
            needed += 6  # month sub-header
            needed += h + 1  # subtotal row

        if mi == 0:
            _start_daily_page(pdf, bill_title, collective_name, member_name, period_label, section_title, t)
            _draw_production_col_headers(pdf, currency, t)
        else:
            space_left = _D_MAX_Y - pdf.get_y()
            if needed + 4 > space_left:
                _draw_footer(pdf, t)
                _start_daily_page(pdf, bill_title, collective_name, member_name, period_label, section_title, t)
                _draw_production_col_headers(pdf, currency, t)
            else:
                pdf.ln(2)

        if multi_month:
            month_label = f"{get_month_name(language, month)} {year}"
            _draw_month_sub_header(pdf, month_label)

        # Data rows
        s_prod = s_local = s_grid = 0.0
        s_local_r = s_grid_r = s_total_r = 0.0
        stripe = False

        for d in days:
            if pdf.get_y() + h > _D_MAX_Y:
                _draw_footer(pdf, t)
                _start_daily_page(pdf, bill_title, collective_name, member_name, period_label, section_title, t)
                _draw_production_col_headers(pdf, currency, t)
                if multi_month:
                    month_label = f"{get_month_name(language, month)} {year}"
                    _draw_month_sub_header(pdf, f"{month_label} …")

            if stripe:
                pdf.set_fill_color(250, 250, 252)
                fill = True
            else:
                fill = False
            stripe = not stripe

            pdf.set_font("Helvetica", "", 7)
            pdf.cell(_D_DAY, h, f"  {d.day:>2}", fill=fill)
            pdf.cell(col_w, h, f"{d.total_production_kwh:,.0f}", align="R", fill=fill)
            pdf.cell(col_w, h, f"{d.local_sell_kwh:,.0f}", align="R", fill=fill)
            pdf.cell(col_w, h, f"{d.bkw_export_kwh:,.0f}", align="R", fill=fill)
            pdf.cell(col_w, h, f"{d.local_sell_revenue:,.2f}", align="R", fill=fill)
            pdf.cell(col_w, h, f"{d.bkw_export_revenue:,.2f}", align="R", fill=fill)
            pdf.cell(col_w, h, f"{d.total_revenue:,.2f}", align="R", fill=fill)
            pdf.ln(h)

            s_prod += d.total_production_kwh
            s_local += d.local_sell_kwh
            s_grid += d.bkw_export_kwh
            s_local_r += d.local_sell_revenue
            s_grid_r += d.bkw_export_revenue
            s_total_r += d.total_revenue

        g_prod += s_prod
        g_local += s_local
        g_grid += s_grid
        g_local_r += s_local_r
        g_grid_r += s_grid_r
        g_total_r += s_total_r

        if multi_month:
            _draw_daily_total_row(
                pdf, t["subtotal"],
                (s_prod, s_local, s_grid, s_local_r, s_grid_r, s_total_r),
                currency, bold=False,
            )

    # Grand total
    _draw_daily_total_row(
        pdf, t["total"],
        (g_prod, g_local, g_grid, g_local_r, g_grid_r, g_total_r),
        currency, bold=True,
    )
    _draw_footer(pdf, t)


# ===========================================================================
# Shared drawing helpers
# ===========================================================================


def _draw_header_bar(pdf: FPDF, bill_title: str, collective_name: str) -> None:
    """Draw the blue header bar with title and collective name."""
    pdf.set_fill_color(33, 60, 114)
    pdf.rect(10, 10, _PAGE_W, 15, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_xy(14, 11)
    pdf.cell(100, 13, bill_title)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(114, 11)
    pdf.cell(_PAGE_W - 108, 13, collective_name, align="R")
    pdf.set_text_color(0, 0, 0)


def _draw_footer(pdf: FPDF, t: dict[str, str]) -> None:
    """Draw footer at the bottom of the current page."""
    pdf.set_y(_FOOTER_Y)
    _thin_line(pdf)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(130, 130, 130)
    pdf.cell(0, 8, f"{t['footer']}  |  {date.today().strftime('%d.%m.%Y')}", align="C")
    pdf.set_text_color(0, 0, 0)


def _thin_line(pdf: FPDF) -> None:
    pdf.set_draw_color(200, 200, 200)
    pdf.line(10, pdf.get_y(), 10 + _PAGE_W, pdf.get_y())
    pdf.set_draw_color(0, 0, 0)


def _info_row(pdf: FPDF, label: str, value: str) -> None:
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(40, _LH, label)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, _LH, value, new_x="LMARGIN", new_y="NEXT")


def _section_header(pdf: FPDF, title: str) -> None:
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(33, 60, 114)
    pdf.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)


# ---- 4-column table helpers -----------------------------------------------


def _table_header_4col(pdf: FPDF, t: dict[str, str], revenue: bool = False) -> None:
    pdf.set_fill_color(240, 242, 246)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(70, 70, 70)
    total_label = t["revenue"] if revenue else t["cost"]
    pdf.cell(_C_DESC, _TABLE_LH, f"  {t['description']}", fill=True)
    pdf.cell(_C_KWH, _TABLE_LH, "kWh", fill=True, align="R")
    pdf.cell(_C_RATE, _TABLE_LH, "CHF/kWh", fill=True, align="R")
    pdf.cell(_C_TOTAL, _TABLE_LH, total_label, fill=True, align="R")
    pdf.ln(_TABLE_LH)
    pdf.set_text_color(0, 0, 0)


def _table_row_4col(
    pdf: FPDF,
    desc: str,
    kwh: float,
    rate: float,
    total: float,
    currency: str,
    hide_rate: bool = False,
    icon: str = "",
) -> None:
    x_start = pdf.get_x()
    y_row = pdf.get_y()
    icon_w = 0

    # Draw vector icon if requested
    if icon:
        icon_cx = x_start + 4.5
        icon_cy = y_row + _TABLE_LH / 2
        if icon == "sun":
            _draw_sun_icon(pdf, icon_cx, icon_cy)
        elif icon == "lightning":
            _draw_lightning_icon(pdf, icon_cx, icon_cy, h=3.2)
        icon_w = 8

    pdf.set_font("Helvetica", "", 8)
    pdf.set_xy(x_start + icon_w, y_row)
    pdf.cell(_C_DESC - icon_w, _TABLE_LH, f"  {desc}" if not icon else desc)
    pdf.cell(_C_KWH, _TABLE_LH, f"{kwh:,.0f}", align="R")
    if hide_rate:
        pdf.cell(_C_RATE, _TABLE_LH, "", align="R")
    else:
        pdf.cell(_C_RATE, _TABLE_LH, f"{rate:.4f}" if rate else "-", align="R")
    pdf.cell(_C_TOTAL, _TABLE_LH, f"{total:,.2f}", align="R")
    pdf.ln(_TABLE_LH)


def _table_total_4col(
    pdf: FPDF,
    label: str,
    kwh: float,
    total: float,
    currency: str,
) -> None:
    pdf.set_draw_color(200, 200, 200)
    pdf.line(10, pdf.get_y(), 10 + _PAGE_W, pdf.get_y())
    pdf.set_draw_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(_C_DESC, _TABLE_LH + 1, f"  {label}")
    pdf.cell(_C_KWH, _TABLE_LH + 1, f"{kwh:,.0f}", align="R")
    pdf.cell(_C_RATE, _TABLE_LH + 1, "", align="R")
    pdf.cell(_C_TOTAL, _TABLE_LH + 1, f"{total:,.2f} {currency}", align="R")
    pdf.ln(_TABLE_LH + 1)


# ---- Additional fees section -----------------------------------------------


def _draw_additional_fees(pdf: FPDF, bill: MemberBill, t: dict[str, str]) -> None:
    """Draw the additional fees section with each fee as a row."""
    _section_header(pdf, t.get("additional_fees", "Additional Fees"))

    # Header row
    pdf.set_fill_color(240, 242, 246)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(70, 70, 70)
    pdf.cell(_C_DESC, _TABLE_LH, f"  {t['description']}", fill=True)
    pdf.cell(_C_KWH + _C_RATE, _TABLE_LH, "", fill=True)  # Empty columns
    pdf.cell(_C_TOTAL, _TABLE_LH, t["cost"], fill=True, align="R")
    pdf.ln(_TABLE_LH)
    pdf.set_text_color(0, 0, 0)

    per_year = t.get("per_year", "year")

    # Fee rows
    for fee in bill.calculated_fees:
        pdf.set_font("Helvetica", "", 8)
        # Build description with fee type indicator
        if fee.fee_type == "per_kwh":
            basis_lbl = t.get("local_solar", "Local") if fee.basis == "local" else t.get("grid_bkw", "Grid")
            desc = f"  {fee.name} ({fee.value:.4f} {bill.currency}/kWh - {basis_lbl})"
        elif fee.fee_type == "percent":
            desc = f"  {fee.name} ({fee.value:.1f}%)"
        else:  # yearly
            desc = f"  {fee.name} ({fee.value:.2f} {bill.currency}/{per_year})"
        pdf.cell(_C_DESC, _TABLE_LH, desc)
        pdf.cell(_C_KWH + _C_RATE, _TABLE_LH, "")  # Empty columns
        pdf.cell(_C_TOTAL, _TABLE_LH, f"{fee.amount:,.2f}", align="R")
        pdf.ln(_TABLE_LH)

    # Total fees row
    pdf.set_draw_color(200, 200, 200)
    pdf.line(10, pdf.get_y(), 10 + _PAGE_W, pdf.get_y())
    pdf.set_draw_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(_C_DESC, _TABLE_LH + 1, f"  {t['total']}")
    pdf.cell(_C_KWH + _C_RATE, _TABLE_LH + 1, "")
    pdf.cell(_C_TOTAL, _TABLE_LH + 1, f"{bill.total_fees:,.2f} {bill.currency}", align="R")
    pdf.ln(_TABLE_LH + 1)


# ---- Summary box -----------------------------------------------------------


def _summary_box(
    pdf: FPDF, bill: MemberBill, net: float, is_host: bool, t: dict[str, str],
) -> None:
    # Calculate box height based on whether fees/VAT exist
    has_fees = bill.total_fees > 0
    has_vat = bill.vat_amount > 0
    extra_rows = (1 if has_fees else 0) + (1 if has_vat else 0)
    box_h = 26 + extra_rows * 6  # Base height + extra rows
    y = pdf.get_y() + 1

    pdf.set_fill_color(245, 247, 250)
    pdf.rect(10, y, _PAGE_W, box_h, "F")
    pdf.set_draw_color(33, 60, 114)
    pdf.rect(10, y, _PAGE_W, box_h, "D")
    pdf.set_draw_color(0, 0, 0)

    row_y = y + 3
    label_w = 100
    value_w = 80

    if is_host:
        # Total cost row
        pdf.set_xy(14, row_y)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(label_w, _LH, t["total_cost"])
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(value_w, _LH, f"-{bill.total_cost:,.2f} {bill.currency}", align="R")

        # Total revenue row
        row_y += _LH
        pdf.set_xy(14, row_y)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(label_w, _LH, t["total_revenue"])
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(value_w, _LH, f"+{bill.total_revenue:,.2f} {bill.currency}", align="R")

        # Fees row (if present)
        if has_fees:
            row_y += _LH
            pdf.set_xy(14, row_y)
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(label_w, _LH, t.get("additional_fees", "Fees"))
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(value_w, _LH, f"-{bill.total_fees:,.2f} {bill.currency}", align="R")

        # VAT row (if present)
        if has_vat:
            row_y += _LH
            pdf.set_xy(14, row_y)
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(label_w, _LH, f"{t.get('vat', 'VAT')} ({bill.vat_rate:.2f}%)")
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(value_w, _LH, f"-{bill.vat_amount:,.2f} {bill.currency}", align="R")

        # Separator line
        row_y += _LH + 1
        pdf.set_draw_color(33, 60, 114)
        pdf.line(14, row_y, 14 + label_w + value_w, row_y)
        pdf.set_draw_color(0, 0, 0)

        # Net total row (use grand_total which includes fees + VAT)
        row_y += 2
        pdf.set_xy(14, row_y)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(33, 60, 114)
        pdf.cell(label_w, _LH + 2, t["net"])
        # For host: grand_total is negative if revenue > costs
        grand = bill.grand_total
        sign = "+" if grand <= 0 else ""
        # Flip sign for display: negative grand_total means profit
        display_val = -grand if grand != 0 else 0
        pdf.cell(value_w, _LH + 2, f"{sign}{display_val:,.2f} {bill.currency}", align="R")
        pdf.set_text_color(0, 0, 0)
    else:
        grid_rate = bill.bkw_rate or 0.0
        savings = bill.local_consumption_kwh * grid_rate - bill.local_cost

        # Total cost row
        pdf.set_xy(14, row_y)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(label_w, _LH, t["total_cost"])
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(value_w, _LH, f"{bill.total_cost:,.2f} {bill.currency}", align="R")

        # Fees row (if present)
        if has_fees:
            row_y += _LH
            pdf.set_xy(14, row_y)
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(label_w, _LH, t.get("additional_fees", "Fees"))
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(value_w, _LH, f"+{bill.total_fees:,.2f} {bill.currency}", align="R")

        # VAT row (if present)
        if has_vat:
            row_y += _LH
            pdf.set_xy(14, row_y)
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(label_w, _LH, f"{t.get('vat', 'VAT')} ({bill.vat_rate:.2f}%)")
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(value_w, _LH, f"+{bill.vat_amount:,.2f} {bill.currency}", align="R")

        # Separator line
        row_y += _LH + 1
        pdf.set_draw_color(33, 60, 114)
        pdf.line(14, row_y, 14 + label_w + value_w, row_y)
        pdf.set_draw_color(0, 0, 0)

        # Grand total or savings row
        row_y += 2
        pdf.set_xy(14, row_y)

        if has_fees or has_vat:
            # Show grand total when fees or VAT exist
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(33, 60, 114)
            pdf.cell(label_w, _LH + 2, t.get("grand_total", "Grand Total"))
            pdf.cell(value_w, _LH + 2, f"{bill.grand_total:,.2f} {bill.currency}", align="R")
        else:
            # Show savings message when no fees/VAT
            if bill.total_consumption_kwh > 0:
                solar_pct = bill.local_consumption_kwh / bill.total_consumption_kwh * 100
            else:
                solar_pct = 0.0
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(34, 139, 34)
            msg = t["you_saved"].format(
                amount=f"{savings:,.2f}",
                currency=bill.currency,
                pct=f"{solar_pct:.1f}",
            )
            pdf.cell(label_w + value_w, _LH + 2, msg)
        pdf.set_text_color(0, 0, 0)

    pdf.set_y(y + box_h + 2)
