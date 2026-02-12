"""BKW vZEV Calculator — Streamlit GUI.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import copy
import io
import tomllib
from datetime import date, datetime
from pathlib import Path

import streamlit as st
from loguru import logger
from streamlit_sortables import sort_items

from src.database import get_connection, get_month_availability
from src.translations import get_gui_translations, get_month_name

_CONFIG_PATH = Path("config.toml")
_LANGUAGES = ["en", "de", "fr", "it"]
_CANTONS = [
    "AG",
    "AI",
    "AR",
    "BE",
    "BL",
    "BS",
    "FR",
    "GE",
    "GL",
    "GR",
    "JU",
    "LU",
    "NE",
    "NW",
    "OW",
    "SG",
    "SH",
    "SO",
    "SZ",
    "TG",
    "TI",
    "UR",
    "VD",
    "VS",
    "ZG",
    "ZH",
]


# ===========================================================================
# TOML read / write helpers
# ===========================================================================


def _load_config_dict() -> dict:
    """Load config.toml into a plain dict, or return defaults."""
    if _CONFIG_PATH.exists():
        with _CONFIG_PATH.open("rb") as fh:
            return tomllib.load(fh)
    return {
        "settings": {
            "csv_directory": "./data",
            "output_directory": "./output",
            "database_path": "./vzev.db",
        },
        "collective": {
            "name": "My vZEV",
            "language": "en",
            "show_daily_detail": False,
            "show_icons": False,
            "billing_start": "2025-01",
            "billing_end": "2025-12",
            "billing_interval": "monthly",
            "local_rate": 0.16,
            "bkw_buy_rate": 0.2816,
            "bkw_sell_rate": 0.1311,
            "vat_rate": 0.0,
        },
        "members": [],
    }


_BILLING_INTERVAL_KEYS = ["monthly", "quarterly", "semi_annual", "annual"]
_YEARS = [str(y) for y in range(2020, 2036)]
_MONTHS = [f"{m:02d}" for m in range(1, 13)]


def _parse_billing_month(value: str) -> tuple[str, str]:
    """Parse 'YYYY-MM' into (year, month) strings."""
    try:
        parts = value.split("-")
        if len(parts) == 2:
            return parts[0], parts[1]
    except (ValueError, AttributeError):
        pass
    return "2025", "01"


def _serialize_toml(settings: dict, collective: dict, members: list[dict]) -> str:
    """Produce a clean TOML string from the config dicts."""
    lines: list[str] = []

    def _q(v: str) -> str:
        return f'"{v}"'

    # [settings]
    lines.append("[settings]")
    lines.append(f"csv_directory = {_q(settings['csv_directory'])}")
    lines.append(f"output_directory = {_q(settings['output_directory'])}")
    lines.append(f"database_path = {_q(settings['database_path'])}")
    lines.append("")

    # [collective]
    lines.append("[collective]")
    lines.append(f"name = {_q(collective['name'])}")
    lines.append(f"language = {_q(collective['language'])}")
    lines.append(
        f"show_daily_detail = {'true' if collective['show_daily_detail'] else 'false'}"
    )
    lines.append(
        f"show_icons = {'true' if collective.get('show_icons') else 'false'}"
    )
    lines.append(f"billing_start = {_q(collective['billing_start'])}")
    lines.append(f"billing_end = {_q(collective['billing_end'])}")
    lines.append(f"billing_interval = {_q(collective['billing_interval'])}")
    lines.append(f"local_rate = {collective['local_rate']}")
    lines.append(f"bkw_buy_rate = {collective['bkw_buy_rate']}")
    lines.append(f"bkw_sell_rate = {collective['bkw_sell_rate']}")
    lines.append(f"vat_rate = {collective['vat_rate']}")
    lines.append("")

    # [[members]]
    for member in members:
        lines.append("# " + "-" * 77)
        label = "Host" if member.get("is_host") else "Member"
        lines.append(
            f"# {label} - {member.get('first_name', '')} {member.get('last_name', '')}"
        )
        lines.append("# " + "-" * 77)
        lines.append("")
        lines.append("[[members]]")
        lines.append(f"first_name = {_q(member['first_name'])}")
        lines.append(f"last_name = {_q(member['last_name'])}")
        lines.append(f"street = {_q(member.get('street', ''))}")
        lines.append(f"zip = {_q(member.get('zip', ''))}")
        lines.append(f"city = {_q(member.get('city', ''))}")
        lines.append(f"canton = {_q(member.get('canton', ''))}")
        lines.append(f"is_host = {'true' if member.get('is_host') else 'false'}")
        lines.append("")

        for meter in member.get("meters", []):
            lines.append("[[members.meters]]")
            lines.append(f"external_id = {_q(meter['external_id'])}")
            lines.append(f"name = {_q(meter['name'])}")
            lines.append(
                f"is_production = {'true' if meter.get('is_production') else 'false'}"
            )
            lines.append(
                f"is_virtual = {'true' if meter.get('is_virtual') else 'false'}"
            )
            lines.append("")

        # Custom fees (order matters!)
        for fee in member.get("custom_fees", []):
            if fee.get("name"):  # Only serialize fees with a name
                lines.append("[[members.custom_fees]]")
                lines.append(f"name = {_q(fee['name'])}")
                lines.append(f"value = {fee.get('value', 0.0)}")
                lines.append(f"fee_type = {_q(fee.get('fee_type', 'percent'))}")
                lines.append("")

    return "\n".join(lines)


# ===========================================================================
# Session state initialization
# ===========================================================================


def _init_state() -> None:
    """Load config.toml into session state on first run."""
    if st.session_state.get("_initialized"):
        return

    cfg = _load_config_dict()

    st.session_state["settings"] = cfg.get("settings", {})

    coll = cfg.get("collective", {})
    # Ensure new fields have defaults
    coll.setdefault("billing_start", "2025-01")
    coll.setdefault("billing_end", "2025-12")
    coll.setdefault("billing_interval", "monthly")
    st.session_state["collective"] = coll
    st.session_state["app_language"] = coll.get("language", "en")

    members = []
    for m in cfg.get("members", []):
        member = dict(m)
        member.setdefault("meters", [])
        member["meters"] = [dict(mt) for mt in member["meters"]]
        member.setdefault("custom_fees", [])
        member["custom_fees"] = [dict(f) for f in member["custom_fees"]]
        members.append(member)
    st.session_state["members"] = members

    st.session_state["_initialized"] = True


# ===========================================================================
# UI helpers
# ===========================================================================


def _gui_lang() -> dict[str, str]:
    """Return the GUI translation dict based on the current app language."""
    lang = st.session_state.get("app_language", "en")
    return get_gui_translations(lang)


# ===========================================================================
# UI sections
# ===========================================================================


def _sidebar() -> None:
    """Render the sidebar with settings and collective config."""
    t = _gui_lang()
    c = st.session_state["collective"]

    # Settings section (collapsible)
    with st.sidebar.expander(t["settings"], expanded=False):
        s = st.session_state["settings"]
        s["csv_directory"] = st.text_input(
            t["csv_directory"], value=s.get("csv_directory", "./data")
        )
        s["output_directory"] = st.text_input(
            t["output_directory"], value=s.get("output_directory", "./output")
        )
        s["database_path"] = st.text_input(
            t["database_path"], value=s.get("database_path", "./vzev.db")
        )

    # Collective section (collapsible) - just the name
    with st.sidebar.expander(t["collective"], expanded=False):
        c["name"] = st.text_input(t["name"], value=c.get("name", ""))

    # Rates section (collapsible)
    with st.sidebar.expander(t["rates"], expanded=False):
        c["local_rate"] = st.number_input(
            t["local_rate"], value=float(c.get("local_rate", 0.0)), format="%.6f", step=0.01, min_value=0.0
        )
        c["bkw_buy_rate"] = st.number_input(
            t["bkw_buy_rate"],
            value=float(c.get("bkw_buy_rate", 0.0)),
            format="%.6f",
            step=0.01,
            min_value=0.0,
        )
        c["bkw_sell_rate"] = st.number_input(
            t["bkw_sell_rate"],
            value=float(c.get("bkw_sell_rate", 0.0)),
            format="%.6f",
            step=0.01,
            min_value=0.0,
        )
        c["vat_rate"] = st.number_input(
            t["vat_rate"],
            value=float(c.get("vat_rate", 0.0)),
            format="%.2f",
            step=0.1,
            min_value=0.0,
            help=t.get("vat_rate_help", ""),
        )

        # Custom fees button
        st.divider()
        if st.button(t["custom_fees"], use_container_width=True):
            st.session_state["_show_custom_fees_dialog"] = True
            st.rerun()

    # Custom fees dialog (outside expander to render properly)
    if st.session_state.get("_show_custom_fees_dialog"):
        _render_custom_fees_dialog()
        # Reset the flag so dialog doesn't reopen on next rerun (e.g., when clicking Create Bill)
        st.session_state["_show_custom_fees_dialog"] = False

    # Bill section (collapsible) - billing period, interval, daily detail
    with st.sidebar.expander(t["bill"], expanded=False):
        # Billing start: year and month dropdowns
        st.markdown(f"**{t['billing_start']}**")
        start_year, start_month = _parse_billing_month(c.get("billing_start", "2025-01"))
        start_year_idx = _YEARS.index(start_year) if start_year in _YEARS else 5
        start_month_idx = _MONTHS.index(start_month) if start_month in _MONTHS else 0
        col_sy, col_sm = st.columns(2)
        start_year_sel = col_sy.selectbox(
            t["year"], _YEARS, index=start_year_idx, key="billing_start_year", label_visibility="collapsed"
        )
        start_month_sel = col_sm.selectbox(
            t["month"], _MONTHS, index=start_month_idx, key="billing_start_month", label_visibility="collapsed"
        )
        c["billing_start"] = f"{start_year_sel}-{start_month_sel}"

        # Billing end: year and month dropdowns
        st.markdown(f"**{t['billing_end']}**")
        end_year, end_month = _parse_billing_month(c.get("billing_end", "2025-12"))
        end_year_idx = _YEARS.index(end_year) if end_year in _YEARS else 5
        end_month_idx = _MONTHS.index(end_month) if end_month in _MONTHS else 11
        col_ey, col_em = st.columns(2)
        end_year_sel = col_ey.selectbox(
            t["year"], _YEARS, index=end_year_idx, key="billing_end_year", label_visibility="collapsed"
        )
        end_month_sel = col_em.selectbox(
            t["month"], _MONTHS, index=end_month_idx, key="billing_end_month", label_visibility="collapsed"
        )
        c["billing_end"] = f"{end_year_sel}-{end_month_sel}"

        interval_val = c.get("billing_interval", "monthly")
        interval_idx = _BILLING_INTERVAL_KEYS.index(interval_val) if interval_val in _BILLING_INTERVAL_KEYS else 0
        # Create translated labels for billing intervals
        interval_labels = [t.get(f"interval_{key}", key) for key in _BILLING_INTERVAL_KEYS]
        selected_label = st.selectbox(
            t["billing_interval"], interval_labels, index=interval_idx
        )
        # Map back to the internal key
        c["billing_interval"] = _BILLING_INTERVAL_KEYS[interval_labels.index(selected_label)]

        c["show_daily_detail"] = st.checkbox(
            t["show_daily_detail"], value=c.get("show_daily_detail", False)
        )
        c["show_icons"] = st.checkbox(
            t["show_icons"], value=c.get("show_icons", False)
        )


def _render_custom_fees_dialog() -> None:
    """Render the custom fees dialog popup with drag-and-drop reordering."""
    t = _gui_lang()
    members = st.session_state["members"]

    @st.dialog(t["custom_fees"], width="large")
    def _custom_fees_dialog():
        if not members:
            st.info(t.get("no_fees_yet", "No members defined yet"))
            return

        # Member selector (outside fragment)
        member_names = [f"{m.get('first_name', '')} {m.get('last_name', '')}".strip() or f"Member {i+1}"
                        for i, m in enumerate(members)]
        selected_idx = st.selectbox(
            t["select_member"],
            range(len(members)),
            format_func=lambda i: member_names[i],
            key="custom_fees_member_select"
        )

        st.divider()

        # Fragment for fee editing - allows rerun without closing dialog
        @st.fragment
        def _fees_editor():
            member = members[selected_idx]
            if "custom_fees" not in member:
                member["custom_fees"] = []

            fees = member["custom_fees"]

            # Process pending actions from previous fragment rerun
            pending = st.session_state.pop("_fee_pending_action", None)
            if pending:
                action_type, idx = pending
                if action_type == "add":
                    fees.append({"name": "", "value": 0.0, "fee_type": "yearly"})
                elif action_type == "remove" and idx < len(fees):
                    fees.pop(idx)
                elif action_type == "copy_to_all":
                    # Copy current member's fees to all other members
                    for i, other_member in enumerate(members):
                        if i != selected_idx:
                            other_member["custom_fees"] = copy.deepcopy(fees)

            if fees:
                # Build sortable items
                def _fee_label(fee: dict, idx: int) -> str:
                    name = fee.get("name") or f"Fee {idx + 1}"
                    val = fee.get("value", 0.0)
                    return f"{name} ({val:.2f} CHF/year)"

                fee_labels = [_fee_label(f, i) for i, f in enumerate(fees)]

                # Drag-and-drop sortable list
                st.caption(t.get("drag_to_reorder", "Drag to reorder"))
                sorted_labels = sort_items(fee_labels, key=f"fee_sort_{selected_idx}")

                # If order changed, reorder the fees list
                if sorted_labels != fee_labels:
                    label_to_idx = {_fee_label(f, i): i for i, f in enumerate(fees)}
                    new_order = [label_to_idx[label] for label in sorted_labels]
                    member["custom_fees"] = [fees[i] for i in new_order]
                    fees = member["custom_fees"]

                st.divider()

                # Editable fee details
                for i, fee in enumerate(fees):
                    col1, col2, col3, col4 = st.columns([4, 2, 2, 1])

                    # Fee name
                    new_name = col1.text_input(
                        t["fee_name"],
                        value=fee.get("name", ""),
                        key=f"fee_name_{selected_idx}_{i}",
                        label_visibility="collapsed",
                        placeholder=t["fee_name"]
                    )
                    fee["name"] = new_name

                    # Fee value (yearly CHF)
                    new_value = col2.number_input(
                        t["fee_value"],
                        value=float(fee.get("value", 0.0)),
                        format="%.2f",
                        step=0.1,
                        key=f"fee_value_{selected_idx}_{i}",
                        label_visibility="collapsed"
                    )
                    fee["value"] = new_value

                    # Label showing CHF/year
                    col3.markdown(f"<div style='margin-top: -8px;'>{t.get('fee_unit_yearly', 'CHF / year')}</div>", unsafe_allow_html=True)

                    # Always set fee type to yearly
                    fee["fee_type"] = "yearly"

                    # Remove button
                    if col4.button("✕", key=f"fee_remove_{selected_idx}_{i}", help=t["remove_fee"], use_container_width=True):
                        st.session_state["_fee_pending_action"] = ("remove", i)
                        st.rerun(scope="fragment")
            else:
                st.info(t["no_fees_yet"])

            st.divider()

            # Add new fee / Copy to all buttons
            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button(t["add_fee"], use_container_width=True):
                    st.session_state["_fee_pending_action"] = ("add", 0)
                    st.rerun(scope="fragment")
            with btn_col2:
                # Only show copy button if there are fees and more than one member
                if fees and len(members) > 1:
                    if st.button(t["copy_to_all"], use_container_width=True):
                        st.session_state["_fee_pending_action"] = ("copy_to_all", selected_idx)
                        st.rerun(scope="fragment")

        _fees_editor()

    _custom_fees_dialog()


def _sidebar_bottom() -> None:
    """Render the bottom section of sidebar with Data Availability and sticky action button."""
    t = _gui_lang()

    st.sidebar.divider()

    # Data Availability button
    _render_data_availability_button()

    # Add spacer to push action button to bottom
    st.sidebar.markdown(
        """
        <style>
        /* Make sidebar scrollable with fixed footer */
        [data-testid="stSidebar"] > div:first-child {
            display: flex;
            flex-direction: column;
            height: 100vh;
        }
        [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
            flex: 1;
            overflow-y: auto;
        }
        /* Sticky action button container */
        .sidebar-footer {
            position: sticky;
            bottom: 0;
            background: var(--background-color);
            padding: 1rem 0;
            border-top: 1px solid rgba(128, 128, 128, 0.2);
            margin-top: auto;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Combined Save & Create Bills button in sticky footer
    st.sidebar.markdown('<div class="sidebar-footer">', unsafe_allow_html=True)
    if st.sidebar.button(t["run_pipeline"], type="primary", use_container_width=True):
        _run_full_pipeline()
    st.sidebar.markdown('</div>', unsafe_allow_html=True)


def _run_full_pipeline() -> None:
    """Save config, load CSV, and create bills - all in one action."""
    t = _gui_lang()

    s = st.session_state["settings"]
    for key in ("csv_directory", "output_directory", "database_path"):
        if not s.get(key, "").strip():
            st.sidebar.warning(f"{key} is empty")
            return

    # Save config
    coll = dict(st.session_state["collective"])
    toml_str = _serialize_toml(s, coll, st.session_state["members"])
    _CONFIG_PATH.write_text(toml_str, encoding="utf-8")

    # Run full pipeline
    log_buffer = io.StringIO()
    sink_id = logger.add(
        log_buffer, format="{time:HH:mm:ss} | {level: <8} | {message}", level="INFO"
    )

    try:
        from main import main as run_main

        run_main(str(_CONFIG_PATH))
        st.session_state["_pipeline_success"] = True
        st.session_state["_pipeline_error"] = None
    except SystemExit:
        st.session_state["_pipeline_success"] = False
        st.session_state["_pipeline_error"] = "exit"
    except Exception as exc:
        st.session_state["_pipeline_success"] = False
        st.session_state["_pipeline_error"] = str(exc)
    finally:
        try:
            logger.remove(sink_id)
        except ValueError:
            pass

    st.session_state["_pipeline_log"] = log_buffer.getvalue()
    st.rerun()


def _render_data_availability_button() -> None:
    """Render a button that opens a popup with month availability overview."""
    t = _gui_lang()
    db_path = st.session_state["settings"].get("database_path", "./vzev.db")

    @st.dialog(t.get("data_availability", "Data Availability"), width="large")
    def _show_availability_dialog():
        lang = st.session_state.get("app_language", "en")

        if not Path(db_path).exists():
            st.info(t.get("no_data_yet", "No data yet"))
            return

        try:
            conn = get_connection(db_path)
            availability = get_month_availability(conn)
            conn.close()
        except Exception:
            st.info(t.get("no_data_yet", "No data yet"))
            return

        if not availability:
            st.info(t.get("no_data_yet", "No data yet"))
            return

        # Month headers
        month_names = [get_month_name(lang, m) for m in range(1, 13)]

        # Build the grid using Streamlit columns
        # Header row
        cols = st.columns([1] + [1] * 12)
        cols[0].markdown(f"**{t['year']}**")
        for i, name in enumerate(month_names):
            cols[i + 1].markdown(f"**{name[:3]}**")

        # Data rows
        for year, months in sorted(availability.items()):
            cols = st.columns([1] + [1] * 12)
            cols[0].markdown(f"**{year}**")
            for m in range(1, 13):
                info = months.get(m, {})
                if info.get("complete"):
                    cols[m].markdown("🟢")  # Green = complete
                elif info.get("has_data"):
                    cols[m].markdown("🟠")  # Orange = not complete (partial data)
                else:
                    cols[m].markdown("⚪")  # White = missing

        st.divider()

        # Legend
        leg_cols = st.columns(3)
        leg_cols[0].markdown("🟢 " + t.get("legend_complete", "■ Complete").replace("■ ", ""))
        leg_cols[1].markdown("🟠 " + t.get("legend_partial", "◫ Not complete").replace("◫ ", ""))
        leg_cols[2].markdown("⚪ " + t.get("legend_none", "· Missing").replace("· ", ""))

        # Help text about CSV location
        csv_dir = st.session_state["settings"].get("csv_directory", "./data")
        st.info(t.get("csv_location_hint", "Place energy CSV files in: {path}").format(path=csv_dir))

    if st.sidebar.button(t.get("data_availability", "Data Availability"), use_container_width=True):
        _show_availability_dialog()


def _members_section() -> None:
    """Render the members and meters editor."""
    t = _gui_lang()
    st.header(t["members"])

    # Render member editing as a fragment to preserve expander state on widget changes
    @st.fragment
    def _render_members():
        members = st.session_state["members"]

        # Track which member should be expanded (new member or meter action)
        expand_member_idx = st.session_state.pop("_new_member_idx", None)
        if expand_member_idx is None:
            expand_member_idx = st.session_state.pop("_keep_member_expanded", None)

        for i, member in enumerate(members):
            role = t["host"] if member.get("is_host") else t["member"]
            label = f"[{role}] {member.get('first_name', '')} {member.get('last_name', '')}"

            # Expand if this member was just added or had meter changes
            force_expand = (i == expand_member_idx)

            with st.expander(label, expanded=force_expand):
                col1, col2 = st.columns(2)
                member["first_name"] = col1.text_input(
                    t["first_name"],
                    value=member.get("first_name", ""),
                    key=f"m{i}_fn",
                    placeholder=t["placeholder_first_name"],
                )
                member["last_name"] = col2.text_input(
                    t["last_name"],
                    value=member.get("last_name", ""),
                    key=f"m{i}_ln",
                    placeholder=t["placeholder_last_name"],
                )

                col3, col4, col5 = st.columns(3)
                member["street"] = col3.text_input(
                    t["street"],
                    value=member.get("street", ""),
                    key=f"m{i}_st",
                    placeholder=t["placeholder_street"],
                )
                member["zip"] = col4.text_input(
                    t["zip"],
                    value=member.get("zip", ""),
                    key=f"m{i}_zip",
                    placeholder=t["placeholder_zip"],
                )
                member["city"] = col5.text_input(
                    t["city"],
                    value=member.get("city", ""),
                    key=f"m{i}_city",
                    placeholder=t["placeholder_city"],
                )

                col6, col7 = st.columns(2)
                canton_val = member.get("canton", "BE")
                canton_idx = _CANTONS.index(canton_val) if canton_val in _CANTONS else 0
                member["canton"] = col6.selectbox(
                    t["canton"], _CANTONS, index=canton_idx, key=f"m{i}_canton"
                )

                # Check if another member is already host
                other_host_exists = any(
                    m.get("is_host", False) for idx, m in enumerate(members) if idx != i
                )
                is_host_disabled = other_host_exists and not member.get("is_host", False)

                if is_host_disabled:
                    col7.checkbox(
                        t["is_host"], value=False, key=f"m{i}_host", disabled=True,
                        help=t["only_one_host"]
                    )
                else:
                    member["is_host"] = col7.checkbox(
                        t["is_host"], value=member.get("is_host", False), key=f"m{i}_host"
                    )

                # Meters
                st.markdown(f"**{t['meters']}**")
                meters = member.get("meters", [])
                is_host = member.get("is_host", False)

                # Determine meter name based on type
                def _get_meter_name(is_prod: bool, is_virt: bool) -> str:
                    if is_virt:
                        return t["meter_production_virtual"] if is_prod else t["meter_consumption_virtual"]
                    else:
                        return t["meter_production_physical"] if is_prod else t["meter_consumption_physical"]

                for j, meter in enumerate(meters):
                    is_prod = meter.get("is_production", False)
                    is_virt = meter.get("is_virtual", False)

                    # Set the name based on actual flags
                    meter["name"] = _get_meter_name(is_prod, is_virt)

                    # Display meter with name label and external ID input
                    meter_label = _get_meter_name(is_prod, is_virt)
                    st.markdown(f"**{meter_label}**")

                    mc1, mc2 = st.columns([4, 1])
                    meter["external_id"] = mc1.text_input(
                        t["external_id"],
                        value=meter.get("external_id", ""),
                        key=f"m{i}_mt{j}_eid",
                        placeholder=t["placeholder_external_id"],
                        label_visibility="collapsed",
                    )
                    if mc2.button(t["remove_meter"], key=f"m{i}_mt{j}_rm", use_container_width=True):
                        # Set pending meter deletion for confirmation
                        st.session_state["_pending_delete_meter"] = (i, j)
                        st.rerun()  # Full rerun to show dialog

                    if j < len(meters) - 1:
                        st.divider()

                # Determine which meter types already exist
                current_meters = meters  # bind for closure
                def _has_meter(prod: bool, virt: bool) -> bool:
                    return any(
                        m.get("is_production", False) == prod and m.get("is_virtual", False) == virt
                        for m in current_meters
                    )

                if is_host:
                    # Host needs 4 meters: physical + virtual, consumption + production
                    # Max 4 meters for host
                    missing = []
                    if not _has_meter(False, False):
                        missing.append((False, False))  # Consumption Physical
                    if not _has_meter(True, False):
                        missing.append((True, False))  # Production Physical
                    if not _has_meter(False, True):
                        missing.append((False, True))  # Consumption Virtual
                    if not _has_meter(True, True):
                        missing.append((True, True))  # Production Virtual

                    if missing:
                        cols = st.columns(len(missing))
                        for col_idx, (is_prod, is_virt) in enumerate(missing):
                            name = _get_meter_name(is_prod, is_virt)
                            if cols[col_idx].button(f"+ {name}", key=f"m{i}_add_{is_prod}_{is_virt}", use_container_width=True):
                                meters.append(
                                    {
                                        "external_id": "",
                                        "name": name,
                                        "is_production": is_prod,
                                        "is_virtual": is_virt,
                                    }
                                )
                                st.session_state["_keep_member_expanded"] = i
                                st.rerun(scope="fragment")
                else:
                    # Member: physical consumption (required) + optional physical production
                    # Max 2 meters for member
                    btn_col1, btn_col2 = st.columns(2)

                    if not _has_meter(False, False):
                        if btn_col1.button(t["add_consumption_meter"], key=f"m{i}_add_cons", use_container_width=True):
                            meters.append(
                                {
                                    "external_id": "",
                                    "name": _get_meter_name(False, False),
                                    "is_production": False,
                                    "is_virtual": False,
                                }
                            )
                            st.session_state["_keep_member_expanded"] = i
                            st.rerun(scope="fragment")

                    if not _has_meter(True, False):
                        if btn_col2.button(t["add_production_meter"], key=f"m{i}_add_prod", use_container_width=True):
                            meters.append(
                                {
                                    "external_id": "",
                                    "name": _get_meter_name(True, False),
                                    "is_production": True,
                                    "is_virtual": False,
                                }
                            )
                            st.session_state["_keep_member_expanded"] = i
                            st.rerun(scope="fragment")

                # Remove member button - separate section with visual warning
                st.markdown("---")
                _, rm_col, _ = st.columns([2, 1, 2])
                if rm_col.button(
                    t["remove_member"],
                    key=f"m{i}_rm",
                    type="secondary",
                    use_container_width=True,
                ):
                    # Set pending deletion for confirmation
                    st.session_state["_pending_delete_member"] = i
                    st.rerun()  # Full rerun to show dialog

        # Confirmation dialog for member deletion
        pending_delete = st.session_state.get("_pending_delete_member")
        if pending_delete is not None and pending_delete < len(members):
            member_to_delete = members[pending_delete]
            member_name = f"{member_to_delete.get('first_name', '')} {member_to_delete.get('last_name', '')}".strip() or t["member"]

            @st.dialog(t["confirm_delete_member"])
            def _confirm_delete_member_dialog():
                st.warning(f"{t['confirm_delete_member_msg']}\n\n**{member_name}**")
                col1, col2 = st.columns(2)
                if col1.button(t["cancel"], use_container_width=True, key="cancel_member_delete"):
                    st.session_state.pop("_pending_delete_member", None)
                    st.rerun()
                if col2.button(t["yes_delete"], type="primary", use_container_width=True, key="confirm_member_delete"):
                    idx = st.session_state.pop("_pending_delete_member", None)
                    if idx is not None and idx < len(members):
                        members.pop(idx)
                    st.rerun()

            _confirm_delete_member_dialog()

        # Confirmation dialog for meter deletion
        pending_meter_delete = st.session_state.get("_pending_delete_meter")
        if pending_meter_delete is not None:
            member_idx, meter_idx = pending_meter_delete
            if member_idx < len(members):
                member_meters = members[member_idx].get("meters", [])
                if meter_idx < len(member_meters):
                    meter_to_delete = member_meters[meter_idx]
                    meter_name = meter_to_delete.get("name", "") or meter_to_delete.get("external_id", "") or t["meters"]

                    @st.dialog(t["confirm_delete_meter"])
                    def _confirm_delete_meter_dialog():
                        st.warning(f"{t['confirm_delete_meter_msg']}\n\n**{meter_name}**")
                        col1, col2 = st.columns(2)
                        if col1.button(t["cancel"], use_container_width=True, key="cancel_meter_delete"):
                            st.session_state.pop("_pending_delete_meter", None)
                            st.rerun()
                        if col2.button(t["yes_delete"], type="primary", use_container_width=True, key="confirm_meter_delete"):
                            m_idx, mt_idx = st.session_state.pop("_pending_delete_meter", (None, None))
                            if m_idx is not None and mt_idx is not None:
                                if m_idx < len(members):
                                    m_meters = members[m_idx].get("meters", [])
                                    if mt_idx < len(m_meters):
                                        m_meters.pop(mt_idx)
                                # Keep this member expanded after deletion
                                st.session_state["_keep_member_expanded"] = m_idx
                            st.rerun()

                    _confirm_delete_meter_dialog()

    _render_members()

    # Add member button (outside the fragment)
    members = st.session_state["members"]
    if st.button(t["add_member"]):
        members.append(
            {
                "first_name": "",
                "last_name": "",
                "street": "",
                "zip": "",
                "city": "",
                "canton": "BE",
                "is_host": False,
                "meters": [
                    {
                        "external_id": "",
                        "name": t["meter_consumption_physical"],
                        "is_production": False,
                        "is_virtual": False,
                    }
                ],
            }
        )
        # Track the new member index so the expander opens automatically
        st.session_state["_new_member_idx"] = len(members) - 1
        st.rerun()


def _actions_section() -> None:
    """Render pipeline results and output display."""
    t = _gui_lang()
    st.divider()

    # Show pipeline results (persisted across reruns)
    if st.session_state.get("_pipeline_success") is True:
        st.success(t["pipeline_success"])
    elif st.session_state.get("_pipeline_error") == "exit":
        st.error(t["pipeline_exit_error"])
    elif st.session_state.get("_pipeline_error"):
        st.error(t["pipeline_failed"].format(error=st.session_state["_pipeline_error"]))

    log_output = st.session_state.get("_pipeline_log", "")
    if log_output:
        st.subheader(t["log_output"])
        st.code(log_output, language="text")

    # Always show generated files if they exist
    out_dir = Path(st.session_state["settings"].get("output_directory", "./output"))
    _show_output_files(out_dir)


def _show_output_files(out_dir: Path) -> None:
    """Display download buttons for generated PDFs and CSVs."""
    t = _gui_lang()

    if not out_dir.exists():
        return

    pdfs = sorted(out_dir.glob("*.pdf"))
    csvs = sorted(out_dir.glob("*.csv"))

    if not pdfs and not csvs:
        return

    st.subheader(t["generated_files"])

    if pdfs:
        st.markdown(f"**{t['pdf_bills']}**")
        for pdf_path in pdfs:
            data = pdf_path.read_bytes()
            st.download_button(
                label=pdf_path.name,
                data=data,
                file_name=pdf_path.name,
                mime="application/pdf",
                key=f"dl_{pdf_path.name}",
            )

    if csvs:
        st.markdown(f"**{t['csv_summaries']}**")
        for csv_path in csvs:
            data = csv_path.read_bytes()
            st.download_button(
                label=csv_path.name,
                data=data,
                file_name=csv_path.name,
                mime="text/csv",
                key=f"dl_{csv_path.name}",
            )


# ===========================================================================
# Main
# ===========================================================================


def _language_selector() -> None:
    """Render a compact language dropdown in the sidebar."""
    t = _gui_lang()
    current = st.session_state.get("app_language", "en")
    idx = _LANGUAGES.index(current) if current in _LANGUAGES else 0

    # Language selector in sidebar
    chosen_code = st.sidebar.selectbox(
        t["language"],
        _LANGUAGES,
        index=idx,
        key="lang_selector",
    )

    # Sync into session state and collective config
    st.session_state["app_language"] = chosen_code
    st.session_state["collective"]["language"] = chosen_code

    # Title in main area
    st.title(t["page_title"])


def main() -> None:
    st.set_page_config(
        page_title="BKW vZEV Calculator",
        page_icon="",
        layout="wide",
        initial_sidebar_state="expanded",  # Force sidebar to be open by default
    )

    # Hide deploy button and add custom styling for vertical alignment
    st.markdown(
        """
        <style>
        /* Hide deploy button only */
        .stDeployButton {display: none;}
        
        /* Vertically center buttons in columns */
        [data-testid="stHorizontalBlock"] {
            align-items: center;
        }
        
        /* Add some vertical spacing for checkboxes to align with inputs */
        [data-testid="stCheckbox"] {
            padding-top: 0.5rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    _init_state()
    _language_selector()
    _sidebar()
    _sidebar_bottom()
    _members_section()
    _actions_section()


if __name__ == "__main__":
    main()
