"""BKW vZEV Calculator — Streamlit GUI.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import io
import tomllib
from datetime import date, datetime
from pathlib import Path

import streamlit as st
from loguru import logger

from src.translations import get_gui_translations

_CONFIG_PATH = Path("config.toml")
_LANGUAGES = ["en", "de", "fr", "it"]
_CANTONS = [
    "AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR",
    "JU", "LU", "NE", "NW", "OW", "SG", "SH", "SO", "SZ", "TG",
    "TI", "UR", "VD", "VS", "ZG", "ZH",
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
            "bill_months": [],
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
            "local_rate": 0.16,
            "bkw_buy_rate": 0.2816,
            "bkw_sell_rate": 0.1311,
        },
        "members": [],
    }


def _to_date(val) -> date:
    """Convert various date representations to a date object."""
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    return date.fromisoformat(str(val))


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
    lines.append(f"show_daily_detail = {'true' if collective['show_daily_detail'] else 'false'}")
    bm = collective.get("bill_months", [])
    if bm:
        items = ", ".join(_q(m) for m in bm)
        lines.append(f"bill_months = [{items}]")
    else:
        lines.append("bill_months = []")
    lines.append(f"period_start = {collective['period_start']}")
    lines.append(f"period_end = {collective['period_end']}")
    lines.append(f"local_rate = {collective['local_rate']}")
    lines.append(f"bkw_buy_rate = {collective['bkw_buy_rate']}")
    lines.append(f"bkw_sell_rate = {collective['bkw_sell_rate']}")
    lines.append("")

    # [[members]]
    for member in members:
        lines.append("# " + "-" * 77)
        label = "Host" if member.get("is_host") else "Member"
        lines.append(f"# {label} - {member.get('first_name', '')} {member.get('last_name', '')}")
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
            lines.append(f"is_production = {'true' if meter.get('is_production') else 'false'}")
            lines.append(f"is_virtual = {'true' if meter.get('is_virtual') else 'false'}")
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
    coll["period_start"] = _to_date(coll.get("period_start", "2025-01-01"))
    coll["period_end"] = _to_date(coll.get("period_end", "2025-12-31"))
    st.session_state["collective"] = coll
    st.session_state["app_language"] = coll.get("language", "en")

    members = []
    for m in cfg.get("members", []):
        member = dict(m)
        member.setdefault("meters", [])
        member["meters"] = [dict(mt) for mt in member["meters"]]
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

    st.sidebar.header(t["settings"])
    s = st.session_state["settings"]
    s["csv_directory"] = st.sidebar.text_input(t["csv_directory"], value=s.get("csv_directory", "./data"))
    s["output_directory"] = st.sidebar.text_input(t["output_directory"], value=s.get("output_directory", "./output"))
    s["database_path"] = st.sidebar.text_input(t["database_path"], value=s.get("database_path", "./vzev.db"))

    st.sidebar.divider()
    st.sidebar.header(t["collective"])
    c = st.session_state["collective"]
    c["name"] = st.sidebar.text_input(t["name"], value=c.get("name", ""))
    c["show_daily_detail"] = st.sidebar.checkbox(t["show_daily_detail"], value=c.get("show_daily_detail", False))

    bm_str = ", ".join(c.get("bill_months", []))
    bm_input = st.sidebar.text_input(t["bill_months"], value=bm_str)
    c["bill_months"] = [x.strip() for x in bm_input.split(",") if x.strip()] if bm_input.strip() else []

    c["period_start"] = st.sidebar.date_input(t["period_start"], value=c.get("period_start", date(2025, 1, 1)))
    c["period_end"] = st.sidebar.date_input(t["period_end"], value=c.get("period_end", date(2025, 12, 31)))

    st.sidebar.divider()
    st.sidebar.header(t["rates"])
    c["local_rate"] = st.sidebar.number_input(t["local_rate"], value=float(c.get("local_rate", 0.0)), format="%.4f", step=0.01)
    c["bkw_buy_rate"] = st.sidebar.number_input(t["bkw_buy_rate"], value=float(c.get("bkw_buy_rate", 0.0)), format="%.4f", step=0.01)
    c["bkw_sell_rate"] = st.sidebar.number_input(t["bkw_sell_rate"], value=float(c.get("bkw_sell_rate", 0.0)), format="%.4f", step=0.01)


def _members_section() -> None:
    """Render the members and meters editor."""
    t = _gui_lang()
    st.header(t["members"])
    members = st.session_state["members"]

    to_remove_member = None

    for i, member in enumerate(members):
        role = t["host"] if member.get("is_host") else t["member"]
        label = f"[{role}] {member.get('first_name', '')} {member.get('last_name', '')}"
        with st.expander(label, expanded=False):
            col1, col2 = st.columns(2)
            member["first_name"] = col1.text_input(t["first_name"], value=member.get("first_name", ""), key=f"m{i}_fn")
            member["last_name"] = col2.text_input(t["last_name"], value=member.get("last_name", ""), key=f"m{i}_ln")

            col3, col4, col5 = st.columns(3)
            member["street"] = col3.text_input(t["street"], value=member.get("street", ""), key=f"m{i}_st")
            member["zip"] = col4.text_input(t["zip"], value=member.get("zip", ""), key=f"m{i}_zip")
            member["city"] = col5.text_input(t["city"], value=member.get("city", ""), key=f"m{i}_city")

            col6, col7 = st.columns(2)
            canton_val = member.get("canton", "BE")
            canton_idx = _CANTONS.index(canton_val) if canton_val in _CANTONS else 0
            member["canton"] = col6.selectbox(t["canton"], _CANTONS, index=canton_idx, key=f"m{i}_canton")
            member["is_host"] = col7.checkbox(t["is_host"], value=member.get("is_host", False), key=f"m{i}_host")

            # Meters
            st.markdown(f"**{t['meters']}**")
            meters = member.get("meters", [])
            to_remove_meter = None

            for j, meter in enumerate(meters):
                mc1, mc2 = st.columns([3, 2])
                meter["external_id"] = mc1.text_input(t["external_id"], value=meter.get("external_id", ""), key=f"m{i}_mt{j}_eid")
                meter["name"] = mc2.text_input(t["name"], value=meter.get("name", ""), key=f"m{i}_mt{j}_name")

                mc3, mc4, mc5 = st.columns(3)
                meter["is_production"] = mc3.checkbox(t["production"], value=meter.get("is_production", False), key=f"m{i}_mt{j}_prod")
                meter["is_virtual"] = mc4.checkbox(t["virtual"], value=meter.get("is_virtual", False), key=f"m{i}_mt{j}_virt")
                if mc5.button(t["remove_meter"], key=f"m{i}_mt{j}_rm"):
                    to_remove_meter = j

                if j < len(meters) - 1:
                    st.divider()

            if to_remove_meter is not None:
                meters.pop(to_remove_meter)
                st.rerun()

            bc1, bc2 = st.columns(2)
            if bc1.button(t["add_meter"], key=f"m{i}_add_mt"):
                meters.append({"external_id": "", "name": "", "is_production": False, "is_virtual": False})
                st.rerun()
            if bc2.button(t["remove_member"], key=f"m{i}_rm", type="secondary"):
                to_remove_member = i

    if to_remove_member is not None:
        members.pop(to_remove_member)
        st.rerun()

    if st.button(t["add_member"]):
        members.append({
            "first_name": "",
            "last_name": "",
            "street": "",
            "zip": "",
            "city": "",
            "canton": "BE",
            "is_host": False,
            "meters": [],
        })
        st.rerun()


def _actions_section() -> None:
    """Render save and run buttons, output display."""
    t = _gui_lang()
    st.divider()

    col1, col2 = st.columns(2)

    # ---- Save config ----
    if col1.button(t["save_config"], type="primary"):
        coll = dict(st.session_state["collective"])
        coll["period_start"] = str(coll["period_start"])
        coll["period_end"] = str(coll["period_end"])

        toml_str = _serialize_toml(
            st.session_state["settings"],
            coll,
            st.session_state["members"],
        )
        _CONFIG_PATH.write_text(toml_str, encoding="utf-8")
        st.success(t["saved_to"].format(path=_CONFIG_PATH))

    # ---- Run pipeline ----
    if col2.button(t["run_pipeline"]):
        if not _CONFIG_PATH.exists():
            st.error(t["config_not_found"])
            return

        log_buffer = io.StringIO()
        sink_id = logger.add(log_buffer, format="{time:HH:mm:ss} | {level: <8} | {message}", level="INFO")

        with st.spinner(t["running_pipeline"]):
            try:
                from main import main as run_main
                run_main(str(_CONFIG_PATH))
                st.success(t["pipeline_success"])
            except SystemExit:
                st.error(t["pipeline_exit_error"])
            except Exception as exc:
                st.error(t["pipeline_failed"].format(error=exc))
            finally:
                logger.remove(sink_id)

        log_output = log_buffer.getvalue()
        if log_output:
            st.subheader(t["log_output"])
            st.code(log_output, language="text")

        # Show generated files
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
    """Render a compact language dropdown at the top-right."""
    current = st.session_state.get("app_language", "en")
    idx = _LANGUAGES.index(current) if current in _LANGUAGES else 0

    # Right-align the dropdown using columns
    _, lang_col = st.columns([6, 1])
    chosen_code = lang_col.selectbox(
        " ",
        _LANGUAGES,
        index=idx,
        key="lang_selector",
        label_visibility="collapsed",
    )

    # Sync into session state and collective config
    st.session_state["app_language"] = chosen_code
    st.session_state["collective"]["language"] = chosen_code

    t = _gui_lang()
    st.title(t["page_title"])


def main() -> None:
    st.set_page_config(page_title="BKW vZEV Calculator", page_icon="", layout="wide")

    _init_state()
    _language_selector()
    _sidebar()
    _members_section()
    _actions_section()


if __name__ == "__main__":
    main()
