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


def export_pdf_bills(
    bills: list[MemberBill],
    collective_name: str,
    show_daily_detail: bool,
    language: str,
    output_dir: str | Path,
) -> list[Path]:
    """Write one PDF per bill. Returns paths to the generated files."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    t = get_translations(language)
    bill_title = t["bill_title"]

    paths: list[Path] = []
    for bill in bills:
        path = _generate_bill_pdf(bill, collective_name, bill_title, show_daily_detail, t, language, out)
        paths.append(path)

    logger.info("Generated {} PDF bill(s) in {}", len(paths), out)
    return paths


def _generate_bill_pdf(
    bill: MemberBill,
    collective_name: str,
    bill_title: str,
    show_daily_detail: bool,
    t: dict[str, str],
    language: str,
    out_dir: Path,
) -> Path:
    period_label = f"{get_month_name(language, bill.month)} {bill.year}"
    file_prefix = t["file_prefix"]
    filename = (
        f"{file_prefix}_{bill.year}-{bill.month:02d}"
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

    _table_row_4col(pdf, t["local_solar"], bill.local_consumption_kwh, local_rate, bill.local_cost, bill.currency)
    _table_row_4col(pdf, t["grid_bkw"], bill.bkw_consumption_kwh, bkw_rate, bill.bkw_cost, bill.currency)
    _table_total_4col(pdf, t["total"], bill.total_consumption_kwh, bill.total_cost, bill.currency)
    pdf.ln(2)

    # ---- Producer settlement (single table, only if produced) -------------
    if bill.total_production_kwh > 0:
        bkw_sell_rate = bill.bkw_sell_rate or 0.0

        _section_header(pdf, t["production_revenue"])
        _table_header_4col(pdf, t, revenue=True)

        local_sell_rate = bill.local_sell_rate or 0.0
        _table_row_4col(
            pdf, t["sold_locally"], bill.local_sell_kwh, local_sell_rate, bill.local_sell_revenue, bill.currency,
        )
        _table_row_4col(
            pdf, t["exported_to_grid"], bill.bkw_export_kwh, bkw_sell_rate, bill.bkw_export_revenue, bill.currency,
        )
        _table_total_4col(pdf, t["total"], bill.total_production_kwh, bill.total_revenue, bill.currency)
        pdf.ln(2)

    # ---- Summary box ------------------------------------------------------
    net = bill.total_revenue - bill.total_cost
    is_host = bill.member.is_host
    _summary_box(pdf, bill, net, is_host, t)

    # ---- Footer (page 1) --------------------------------------------------
    _draw_footer(pdf, t)

    # ---- Daily detail pages (optional) ------------------------------------
    if show_daily_detail and bill.daily_details:
        _draw_daily_detail_pages(pdf, bill, collective_name, bill_title, period_label, t)

    pdf.output(str(filepath))
    logger.debug("  PDF: {}", filepath.name)
    return filepath


# ===========================================================================
# Daily detail pages
# ===========================================================================


def _draw_daily_detail_pages(
    pdf: FPDF,
    bill: MemberBill,
    collective_name: str,
    bill_title: str,
    period_label: str,
    t: dict[str, str],
) -> None:
    """Add page(s) with daily consumption/cost and production/revenue tables."""
    is_host = bill.member.is_host
    details = bill.daily_details

    # --- Page: Daily consumption & cost ------------------------------------
    pdf.add_page()
    pdf.set_auto_page_break(auto=False)
    _draw_header_bar(pdf, bill_title, collective_name)
    pdf.set_y(28)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, _LH, f"{bill.member.full_name}  -  {period_label}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(1)

    _section_header(pdf, t["daily_consumption_cost"])
    _daily_consumption_table(pdf, details, bill.currency, t)
    _draw_footer(pdf, t)

    # --- Page: Daily production & revenue (host only) ----------------------
    if is_host and bill.total_production_kwh > 0:
        pdf.add_page()
        pdf.set_auto_page_break(auto=False)
        _draw_header_bar(pdf, bill_title, collective_name)
        pdf.set_y(28)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, _LH, f"{bill.member.full_name}  -  {period_label}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(1)

        _section_header(pdf, t["daily_production_revenue"])
        _daily_production_table(pdf, details, bill.currency, t)
        _draw_footer(pdf, t)


# Daily table column widths
_D_DAY = 18
_D_VAL = (190 - 18) // 6  # ~28 each for 6 value cols


def _daily_consumption_table(
    pdf: FPDF, details: list[DailyDetail], currency: str, t: dict[str, str],
) -> None:
    """Draw a table: Day | Local kWh | Grid kWh | Total kWh | Local CHF | Grid CHF | Total CHF."""
    col_w = _D_VAL
    h = 5.5

    # Header
    pdf.set_fill_color(240, 242, 246)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(70, 70, 70)
    pdf.cell(_D_DAY, h, f"  {t['day']}", fill=True)
    pdf.cell(col_w, h, t["local_kwh"], fill=True, align="R")
    pdf.cell(col_w, h, t["grid_kwh"], fill=True, align="R")
    pdf.cell(col_w, h, t["total_kwh"], fill=True, align="R")
    pdf.cell(col_w, h, f"Local {currency}", fill=True, align="R")
    pdf.cell(col_w, h, f"Grid {currency}", fill=True, align="R")
    pdf.cell(col_w, h, f"{t['total']} {currency}", fill=True, align="R")
    pdf.ln(h)
    pdf.set_text_color(0, 0, 0)

    # Rows
    pdf.set_font("Helvetica", "", 7)
    tot_local = tot_bkw = tot_cons = 0.0
    tot_local_c = tot_bkw_c = tot_total_c = 0.0
    stripe = False
    for d in details:
        if stripe:
            pdf.set_fill_color(250, 250, 252)
            fill = True
        else:
            fill = False
        stripe = not stripe

        pdf.cell(_D_DAY, h, f"  {d.day:>2}", fill=fill)
        pdf.cell(col_w, h, f"{d.local_consumption_kwh:,.0f}", align="R", fill=fill)
        pdf.cell(col_w, h, f"{d.bkw_consumption_kwh:,.0f}", align="R", fill=fill)
        pdf.cell(col_w, h, f"{d.total_consumption_kwh:,.0f}", align="R", fill=fill)
        pdf.cell(col_w, h, f"{d.local_cost:,.2f}", align="R", fill=fill)
        pdf.cell(col_w, h, f"{d.bkw_cost:,.2f}", align="R", fill=fill)
        pdf.cell(col_w, h, f"{d.total_cost:,.2f}", align="R", fill=fill)
        pdf.ln(h)

        tot_local += d.local_consumption_kwh
        tot_bkw += d.bkw_consumption_kwh
        tot_cons += d.total_consumption_kwh
        tot_local_c += d.local_cost
        tot_bkw_c += d.bkw_cost
        tot_total_c += d.total_cost

    # Total row
    pdf.set_draw_color(200, 200, 200)
    pdf.line(10, pdf.get_y(), 10 + _PAGE_W, pdf.get_y())
    pdf.set_draw_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 7)
    pdf.cell(_D_DAY, h + 1, f"  {t['total']}")
    pdf.cell(col_w, h + 1, f"{tot_local:,.0f}", align="R")
    pdf.cell(col_w, h + 1, f"{tot_bkw:,.0f}", align="R")
    pdf.cell(col_w, h + 1, f"{tot_cons:,.0f}", align="R")
    pdf.cell(col_w, h + 1, f"{tot_local_c:,.2f}", align="R")
    pdf.cell(col_w, h + 1, f"{tot_bkw_c:,.2f}", align="R")
    pdf.cell(col_w, h + 1, f"{tot_total_c:,.2f} {currency}", align="R")
    pdf.ln(h + 1)


def _daily_production_table(
    pdf: FPDF, details: list[DailyDetail], currency: str, t: dict[str, str],
) -> None:
    """Draw: Day | Produced kWh | Local kWh | Grid kWh | Local CHF | Grid CHF | Total CHF."""
    col_w = _D_VAL
    h = 5.5

    # Header
    pdf.set_fill_color(240, 242, 246)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(70, 70, 70)
    pdf.cell(_D_DAY, h, f"  {t['day']}", fill=True)
    pdf.cell(col_w, h, t["prod_kwh"], fill=True, align="R")
    pdf.cell(col_w, h, t["local_kwh"], fill=True, align="R")
    pdf.cell(col_w, h, t["grid_kwh"], fill=True, align="R")
    pdf.cell(col_w, h, f"Local {currency}", fill=True, align="R")
    pdf.cell(col_w, h, f"Grid {currency}", fill=True, align="R")
    pdf.cell(col_w, h, f"{t['total']} {currency}", fill=True, align="R")
    pdf.ln(h)
    pdf.set_text_color(0, 0, 0)

    # Rows
    pdf.set_font("Helvetica", "", 7)
    tot_prod = tot_local = tot_grid = 0.0
    tot_local_r = tot_grid_r = tot_total_r = 0.0
    stripe = False
    for d in details:
        if stripe:
            pdf.set_fill_color(250, 250, 252)
            fill = True
        else:
            fill = False
        stripe = not stripe

        pdf.cell(_D_DAY, h, f"  {d.day:>2}", fill=fill)
        pdf.cell(col_w, h, f"{d.total_production_kwh:,.0f}", align="R", fill=fill)
        pdf.cell(col_w, h, f"{d.local_sell_kwh:,.0f}", align="R", fill=fill)
        pdf.cell(col_w, h, f"{d.bkw_export_kwh:,.0f}", align="R", fill=fill)
        pdf.cell(col_w, h, f"{d.local_sell_revenue:,.2f}", align="R", fill=fill)
        pdf.cell(col_w, h, f"{d.bkw_export_revenue:,.2f}", align="R", fill=fill)
        pdf.cell(col_w, h, f"{d.total_revenue:,.2f}", align="R", fill=fill)
        pdf.ln(h)

        tot_prod += d.total_production_kwh
        tot_local += d.local_sell_kwh
        tot_grid += d.bkw_export_kwh
        tot_local_r += d.local_sell_revenue
        tot_grid_r += d.bkw_export_revenue
        tot_total_r += d.total_revenue

    # Total row
    pdf.set_draw_color(200, 200, 200)
    pdf.line(10, pdf.get_y(), 10 + _PAGE_W, pdf.get_y())
    pdf.set_draw_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 7)
    pdf.cell(_D_DAY, h + 1, f"  {t['total']}")
    pdf.cell(col_w, h + 1, f"{tot_prod:,.0f}", align="R")
    pdf.cell(col_w, h + 1, f"{tot_local:,.0f}", align="R")
    pdf.cell(col_w, h + 1, f"{tot_grid:,.0f}", align="R")
    pdf.cell(col_w, h + 1, f"{tot_local_r:,.2f}", align="R")
    pdf.cell(col_w, h + 1, f"{tot_grid_r:,.2f}", align="R")
    pdf.cell(col_w, h + 1, f"{tot_total_r:,.2f} {currency}", align="R")
    pdf.ln(h + 1)


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
) -> None:
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(_C_DESC, _TABLE_LH, f"  {desc}")
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


# ---- Summary box -----------------------------------------------------------


def _summary_box(
    pdf: FPDF, bill: MemberBill, net: float, is_host: bool, t: dict[str, str],
) -> None:
    box_h = 22
    y = pdf.get_y() + 1

    pdf.set_fill_color(245, 247, 250)
    pdf.rect(10, y, _PAGE_W, box_h, "F")
    pdf.set_draw_color(33, 60, 114)
    pdf.rect(10, y, _PAGE_W, box_h, "D")
    pdf.set_draw_color(0, 0, 0)

    row_y = y + 2

    if is_host:
        pdf.set_xy(14, row_y)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(50, _LH, t["total_cost"])
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(35, _LH, f"-{bill.total_cost:,.2f} {bill.currency}", align="R")

        pdf.set_xy(115, row_y)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(35, _LH, t["total_revenue"])
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(35, _LH, f"+{bill.total_revenue:,.2f} {bill.currency}", align="R")

        row_y += _LH + 3
        pdf.set_xy(14, row_y)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(33, 60, 114)
        sign = "+" if net >= 0 else ""
        pdf.cell(_PAGE_W - 10, 8, f"{t['net']}:  {sign}{net:,.2f} {bill.currency}")
        pdf.set_text_color(0, 0, 0)
    else:
        grid_rate = bill.bkw_rate or 0.0
        savings = bill.local_consumption_kwh * grid_rate - bill.local_cost

        pdf.set_xy(14, row_y)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(50, _LH, t["total_cost"])
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(35, _LH, f"{bill.total_cost:,.2f} {bill.currency}", align="R")

        pdf.set_xy(115, row_y)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(35, _LH, t["saved_with_solar"])
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(34, 139, 34)
        pdf.cell(35, _LH, f"{savings:,.2f} {bill.currency}", align="R")
        pdf.set_text_color(0, 0, 0)

        if bill.total_consumption_kwh > 0:
            solar_pct = bill.local_consumption_kwh / bill.total_consumption_kwh * 100
        else:
            solar_pct = 0.0

        row_y += _LH + 3
        pdf.set_xy(14, row_y)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(34, 139, 34)
        msg = t["you_saved"].format(
            amount=f"{savings:,.2f}",
            currency=bill.currency,
            pct=f"{solar_pct:.1f}",
        )
        pdf.cell(_PAGE_W - 10, 8, msg)
        pdf.set_text_color(0, 0, 0)

    pdf.set_y(y + box_h + 2)
