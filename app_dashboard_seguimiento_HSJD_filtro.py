"""
Red TAMI — Panel de Seguimiento (local)
----------------------------------------
Dashboard local en Streamlit que se conecta a un Google Sheet privado
(vía cuenta de servicio) y visualiza el seguimiento de pacientes oncológicos:
diagnósticos, cirugías, complicaciones, recurrencias y terapias.

Ejecutar con:
    streamlit run app_dashboardredtami.py
"""

import re
import json
from datetime import datetime

import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import plotly.graph_objects as go
import plotly.express as px

# --------------------------------------------------------------------------
# Configuración de página y tema visual
# --------------------------------------------------------------------------
st.set_page_config(
    page_title="Cirugía de Tórax HSJD — Seguimiento Oncológico",
    page_icon="🎗",
    layout="wide",
)

ROSE = "#9E3A52"
ROSE_SOFT = "#F0D8DC"
TEAL = "#24504C"
TEAL_SOFT = "#DCE8E4"
GOLD = "#B9822F"
GOLD_SOFT = "#F1E1C6"
INK = "#202D2C"
INK_SOFT = "#5B6664"
PALETTE = [ROSE, TEAL, GOLD, "#6B7FA3", "#8C6E9C", "#4C8577"]

st.markdown(
    f"""
    <style>
    .stApp {{ background-color: #FBF6F4; }}
    h1, h2, h3 {{ color: {INK}; font-family: 'Georgia', serif; }}
    div[data-testid="stMetricValue"] {{ color: {ROSE}; font-weight: 600; }}
    div[data-testid="stMetricLabel"] {{ color: {INK_SOFT}; }}
    .journey-card {{
        background:#fff; border:1px solid #E6DAD5; border-radius:10px;
        padding:14px 10px; text-align:center; height:100%;
    }}
    .journey-num {{
        font-size:26px; font-weight:700; color:{TEAL}; margin:6px 0 2px;
    }}
    .journey-label {{ font-size:12.5px; font-weight:600; color:{INK}; }}
    .journey-sub {{ font-size:11px; color:{INK_SOFT}; }}
    .comment-box {{
        border-left:3px solid {TEAL}; background:{TEAL_SOFT}; border-radius:0 8px 8px 0;
        padding:10px 14px; margin-bottom:10px; font-size:13.5px; color:{INK};
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

CONFIG_PATH = "config.json"


# --------------------------------------------------------------------------
# Persistencia simple de la config (sheet id / worksheet) entre ejecuciones
# --------------------------------------------------------------------------
def load_local_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_local_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Conexión a Google Sheets
# --------------------------------------------------------------------------
@st.cache_resource
def get_gspread_client():
    """
    Creates the gspread client securely.
    Checks Streamlit Cloud secrets safely; falls back to local credentials.json.
    """
    import os
    
    # 1. Safely check for Streamlit Cloud production secrets without triggering a crash
    try:
        if hasattr(st, "secrets") and "gcp_service_account" in st.secrets:
            creds = Credentials.from_service_account_info(
                dict(st.secrets["gcp_service_account"]), scopes=SCOPES
            )
            return gspread.authorize(creds)
    except Exception:
        # If st.secrets throws a "No secrets found" error locally, catch it and bypass
        pass
        
    # 2. Local Fallback: Use the exact path configuration that worked for your original script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    full_cred_path = os.path.join(script_dir, "credentials.json")
    
    # Double check relative path if absolute fails
    if not os.path.exists(full_cred_path):
        full_cred_path = "credentials.json"
        
    if not os.path.exists(full_cred_path):
        raise FileNotFoundError(
            "No se encontraron credenciales de producción en Streamlit Cloud, "
            f"ni el archivo local en: {os.path.abspath(full_cred_path)}"
        )
        
    creds = Credentials.from_service_account_file(full_cred_path, scopes=SCOPES)
    return gspread.authorize(creds)


def extract_sheet_id(id_or_url: str) -> str:
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", id_or_url)
    return match.group(1) if match else id_or_url.strip()


@st.cache_data(ttl=300, show_spinner="Descargando datos desde Google Sheets…")
def fetch_sheet_df(sheet_id: str, worksheet_name: str, _cache_bust: int = 0) -> pd.DataFrame:
    gc = get_gspread_client()
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(worksheet_name) if worksheet_name else sh.sheet1
    records = ws.get_all_records()
    return pd.DataFrame(records)


# --------------------------------------------------------------------------
# Normalización de columnas y datos (Nueva Estructura Clínica)
# --------------------------------------------------------------------------
def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def find_col(columns, *keywords):
    for c in columns:
        low = norm(c).lower()
        if all(k.lower() in low for k in keywords):
            return c
    return None


def is_negative_or_empty(v) -> bool:
    val = norm(v).lower()
    return val in ("", "no", "ninguna", "sin complicaciones", "0", "negativo", "false")


def build_clean_df(raw: pd.DataFrame) -> pd.DataFrame:
    cols = list(raw.columns)
    
    # Nuevo mapa de columnas según tu especificación exacta
    mapping = {
        "patient_name": find_col(cols, "patient name") or find_col(cols, "nombre"),
        "diagnostics": find_col(cols, "diagnostics") or find_col(cols, "diagnóstico"),
        "surgery_date": find_col(cols, "surgery date") or find_col(cols, "fecha"),
        "surgery_type": find_col(cols, "type of surgery") or find_col(cols, "tipo"),
        "physician": find_col(cols, "physician name") or find_col(cols, "cirujano"),
        "tnm_stage": find_col(cols, "tnm cancer stage") or find_col(cols, "etapa", "tnm"),
        "complications": find_col(cols, "surgical complications") or find_col(cols, "complicaciones"),
        "induction": find_col(cols, "induction") or find_col(cols, "inducción"),
        "adjuvant": find_col(cols, "adjuvant therapy") or find_col(cols, "adyuvancia"),
        "tki": find_col(cols, "tki"),
        "immunology": find_col(cols, "immunology") or find_col(cols, "inmuno"),
        "rt": find_col(cols, "rt") or find_col(cols, "radioterapia"),
        "recurrence": find_col(cols, "recurrent cancer") or find_col(cols, "recurrencia"),
        "control_date": find_col(cols, "control date"),
        "control_indication": find_col(cols, "control indication"),
        "next_control": find_col(cols, "next control date"),
        "required_exams": find_col(cols, "required examinations") or find_col(cols, "exámenes")
    }

    out = pd.DataFrame()
    for key, col in mapping.items():
        out[key] = raw[col].map(norm) if col else ""

    # Descartar filas totalmente vacías
    out = out[
        out.apply(lambda r: any(v != "" for v in r), axis=1)
    ].reset_index(drop=True)

    return out, mapping


# --------------------------------------------------------------------------
# Sidebar — configuración de conexión
# --------------------------------------------------------------------------
st.sidebar.header("Conexión a Google Sheets")

saved_cfg = load_local_config()

# Coloca aquí la URL fija de tu Google Sheet por defecto
DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/TU_ID_DE_SHEET_AQUI/edit"
DEFAULT_WORKSHEET = "Respuestas de formulario 1" # O déjala vacía "" si quieres la primera hoja

# Si ya hay una config guardada localmente, la usa; si no, precarga la URL por defecto
initial_sheet = saved_cfg.get("sheet_id", DEFAULT_SHEET_URL)
initial_worksheet = saved_cfg.get("worksheet_name", DEFAULT_WORKSHEET)

sheet_input = st.sidebar.text_input(
    "URL o ID del Google Sheet",
    value=initial_sheet,
    placeholder="https://docs.google.com/spreadsheets/d/…",
)
worksheet_input = st.sidebar.text_input(
    "Nombre de la hoja (worksheet)",
    value=initial_worksheet,
    placeholder="Dejar en blanco para usar la primera hoja",
)

col_a, col_b = st.sidebar.columns(2)
connect_clicked = col_a.button("Conectar", type="primary", use_container_width=True)
refresh_clicked = col_b.button("Actualizar", use_container_width=True)

if "cache_bust" not in st.session_state:
    st.session_state["cache_bust"] = 0

if refresh_clicked:
    st.session_state["cache_bust"] += 1
    st.cache_data.clear()

if connect_clicked and sheet_input:
    save_local_config({"sheet_id": extract_sheet_id(sheet_input), "worksheet_name": worksheet_input})

st.sidebar.caption(
    "Los datos se cachean 5 minutos. Usa 'Actualizar' para forzar una nueva lectura."
)

# --------------------------------------------------------------------------
# Carga de datos base
# --------------------------------------------------------------------------
if not sheet_input:
    # Encabezado principal limpio antes de conectar
    st.markdown("##### CIRUGÍA DE TÓRAX HSJD · SEGUIMIENTO PACIENTE ONCOLÓGICO")
    st.title("Panel de Seguimiento Clínico")
    st.info(
        "👈 Ingresa la URL o el ID de tu Google Sheet en la barra lateral y presiona **Conectar** "
        "para cargar los datos clínicos."
    )
    st.stop()

sheet_id = extract_sheet_id(sheet_input)

try:
    raw_df = fetch_sheet_df(sheet_id, worksheet_input, st.session_state["cache_bust"])
except Exception as e:
    st.error(f"Error al conectar con Google Sheets: {e}")
    st.stop()

if raw_df.empty:
    st.warning("El Sheet se conectó correctamente pero no contiene datos.")
    st.stop()

# Procesamiento y limpieza global de datos
df_full, colmap = build_clean_df(raw_df)

# --------------------------------------------------------------------------
# Sidebar — Filtro de Médico Tratante
# --------------------------------------------------------------------------
st.sidebar.divider()
st.sidebar.header("Filtros de Cohorte")

# Extraer médicos únicos eliminando strings vacíos
unique_physicians = sorted([p for p in df_full["physician"].unique() if p.strip() != ""])
physician_options = ["Todos los médicos"] + unique_physicians

selected_physician = st.sidebar.selectbox(
    "Filtrar por Médico Tratante",
    options=physician_options,
    index=0
)

# Aplicar filtrado dinámico del DataFrame principal
if selected_physician != "Todos los médicos":
    df = df_full[df_full["physician"] == selected_physician].reset_index(drop=True)
else:
    df = df_full.copy()

# --------------------------------------------------------------------------
# Encabezado principal
# --------------------------------------------------------------------------
st.markdown("##### CIRUGÍA DE TÓRAX HSJD · SEGUIMIENTO PACIENTE ONCOLÓGICO")
st.title("Panel de Seguimiento Clínico")
st.caption(
    "Métricas clave de tratamientos oncológicos: diagnósticos, cirugías, terapias complementarias, "
    "complicaciones posoperatorias y recurrencias de la enfermedad."
)
st.caption(f"Última actualización: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')} · {len(df)} pacientes filtrados (de {len(df_full)} en total)")

if df.empty:
    st.warning("No hay registros clínicos que coincidan con el médico seleccionado.")
    st.stop()

# --------------------------------------------------------------------------
# KPIs Clínicos Principales
# --------------------------------------------------------------------------
total_patients = len(df)

# Cálculos basados en lógica de contenido clínico
has_recurrence = df[df["recurrence"].map(lambda x: not is_negative_or_empty(x))]
recurrence_rate = round(100 * len(has_recurrence) / total_patients) if total_patients else 0

has_surgery = df[df["surgery_date"] != ""]
surgery_rate = round(100 * len(has_surgery) / total_patients) if total_patients else 0

# Complicaciones sobre el universo de pacientes operados
has_complications = has_surgery[has_surgery["complications"].map(lambda x: not is_negative_or_empty(x))]
complication_rate = round(100 * len(has_complications) / len(has_surgery)) if len(has_surgery) else 0

active_therapies = df[(df["adjuvant"] != "") | (df["tki"] != "") | (df["immunology"] != "") | (df["rt"] != "")]

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Pacientes Totales", total_patients)
k2.metric("Tasa de Recurrencia", f"{recurrence_rate}%", delta=f"{len(has_recurrence)} casos", delta_color="inverse")
k3.metric("Pacientes Operados", f"{surgery_rate}%", delta=f"{len(has_surgery)} totales")
k4.metric("Complicación Quirúrgica", f"{complication_rate}%", delta=f"{len(has_complications)} casos", delta_color="inverse")
k5.metric("En Terapia Activa", f"{len(active_therapies)}")

st.divider()

# --------------------------------------------------------------------------
# 01 — El recorrido clínico del paciente
# --------------------------------------------------------------------------
st.subheader("01 · Flujo de tratamiento y progresión")
st.caption(
    "Distribución estructural de la cohorte según los hitos clínicos documentados desde el diagnóstico inicial "
    "hasta las etapas avanzadas de seguimiento."
)

has_induction = df[df["induction"] != ""]
has_adjuvant = df[df["adjuvant"] != ""]

journey_steps = [
    ("Diagnósticos", total_patients, "Total registrados"),
    ("Terapia Inducción", len(has_induction), "Previo a cirugía"),
    ("Llegaron a Cirugía", len(has_surgery), f"{len(has_surgery)} procedimientos"),
    ("Complicados vs Sanos", f"{len(has_complications)} C / {len(has_surgery) - len(has_complications)} S", "Eventos posquirúrgicos"),
    ("Terapia Adyuvante", len(has_adjuvant), "Post-procedimiento"),
    ("Recurrencia Controlada", total_patients - len(has_recurrence), f"De {total_patients} bajo observación"),
]

jcols = st.columns(len(journey_steps))
for jc, (label, value, sub) in zip(jcols, journey_steps):
    jc.markdown(
        f"""<div class="journey-card">
                <div class="journey-label">{label}</div>
                <div class="journey-num">{value}</div>
                <div class="journey-sub">{sub}</div>
            </div>""",
        unsafe_allow_html=True,
    )

st.divider()

# --------------------------------------------------------------------------
# 02 — Diagnósticos, Estadificación y Recurrencia
# --------------------------------------------------------------------------
st.subheader("02 · Análisis de diagnóstico y estatus oncológico")

c1, c2 = st.columns([1, 1.4])

with c1:
    st.markdown("**Estado de Recurrencia General**")
    fig_recurrence = go.Figure(
        data=[
            go.Pie(
                labels=["Sin Recurrencia / Remisión", "Cáncer Recurrente"],
                values=[total_patients - len(has_recurrence), len(has_recurrence)],
                hole=0.65,
                marker=dict(colors=[TEAL, ROSE]),
                textinfo="value+percent",
            )
        ]
    )
    fig_recurrence.update_layout(
        height=280, margin=dict(t=10, b=10, l=10, r=10),
        legend=dict(orientation="h", y=-0.15),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_recurrence, use_container_width=True)

with c2:
    st.markdown("**Distribución por Etapa TNM**")
    tnm_counts = df["tnm_stage"].replace("", "Sin clasificar").value_counts()
    
    if not tnm_counts.empty:
        fig_tnm = px.bar(
            x=tnm_counts.values, y=tnm_counts.index, orientation="h",
            labels={"x": "N° de Pacientes", "y": ""},
            color_discrete_sequence=[ROSE]
        )
        fig_tnm.update_layout(
            height=280, margin=dict(t=10, b=10, l=10, r=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_tnm, use_container_width=True)
    else:
        st.info("Sin clasificaciones TNM disponibles.")

st.divider()

# --------------------------------------------------------------------------
# 03 — Seguridad Quirúrgica y Complicaciones
# --------------------------------------------------------------------------
st.subheader("03 · Seguridad Quirúrgica y Complicaciones")
st.caption("Presencia de complicaciones de acuerdo con el tipo de procedimiento quirúrgico ejecutado.")

if not has_surgery.empty:
    comp_analysis = []
    for sx in has_surgery["surgery_type"].unique():
        if not sx: 
            continue
        sub_sx = has_surgery[has_surgery["surgery_type"] == sx]
        total_sx = len(sub_sx)
        comps_sx = len(sub_sx[sub_sx["complications"].map(lambda x: not is_negative_or_empty(x))])
        
        comp_analysis.append({
            "Tipo Quirúrgico": sx,
            "Con Complicaciones": comps_sx,
            "Sin Complicaciones": total_sx - comps_sx
        })
    
    if comp_analysis:
        comp_df = pd.DataFrame(comp_analysis)
        fig_comp = px.bar(
            comp_df, y="Tipo Quirúrgico", x=["Sin Complicaciones", "Con Complicaciones"],
            orientation="h", barmode="stack",
            color_discrete_map={"Sin Complicaciones": TEAL_SOFT, "Con Complicaciones": GOLD}
        )
        fig_comp.update_layout(
            height=300, margin=dict(t=10, b=10, l=10, r=10),
            legend=dict(title="", orientation="h", y=-0.2),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title="Volumen de Pacientes", yaxis_title=""
        )
        st.plotly_chart(fig_comp, use_container_width=True)
    else:
        st.info("Los pacientes quirúrgicos de esta selección no registran desglose de subtipos.")
else:
    st.info("No hay datos de cirugías registradas para este filtro para realizar el análisis cruzado.")

# --------------------------------------------------------------------------
# 04 — Perfil de Diagnósticos y Tratamientos Sistémicos
# --------------------------------------------------------------------------
st.subheader("04 · Perfil de patologías y esquemas sistémicos")

d1, d2 = st.columns(2)

with d1:
    st.markdown("**Principales Diagnósticos en Cohorte**")
    diag_counts = df["diagnostics"].replace("", "No especifica").value_counts().head(8)
    if not diag_counts.empty:
        fig_diag = px.bar(x=diag_counts.index, y=diag_counts.values, color_discrete_sequence=[TEAL])
        fig_diag.update_layout(
            height=260, margin=dict(t=10, b=10, l=10, r=10), xaxis_title="", yaxis_title="",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_diag, use_container_width=True)
    else:
        st.info("Sin registros de diagnósticos para esta selección.")

with d2:
    st.markdown("**Uso de Terapias Avanzadas e Inmunoterapia**")
    therapy_metrics = {
        "Inmunoterapia": len(df[df["immunology"] != ""]),
        "TKI (Inhibidores)": len(df[df["tki"] != ""]),
        "Radioterapia (RT)": len(df[df["rt"] != ""]),
        "Quimio Adyuvante": len(df[df["adjuvant"] != ""])
    }
    fig_tx = px.bar(
        x=list(therapy_metrics.values()), y=list(therapy_metrics.keys()),
        orientation="h", color_discrete_sequence=[ROSE]
    )
    fig_tx.update_layout(
        height=260, margin=dict(t=10, b=10, l=10, r=10), xaxis_title="Pacientes expuestos", yaxis_title="",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_tx, use_container_width=True)

st.divider()

# --------------------------------------------------------------------------
# 05 — Alertas de Control Médico y Exámenes Requeridos
# --------------------------------------------------------------------------
st.subheader("05 · Logística y Alertas de Controles Próximos")

e1, e2 = st.columns([1.2, 1])

with e1:
    st.markdown("**Bitácora de Próximos Exámenes Clínicos Solicitados**")
    exam_df = df[df["required_exams"] != ""][["patient_name", "next_control", "required_exams"]].head(10)
    if not exam_df.empty:
        exam_df.columns = ["Paciente", "Próximo Control", "Exámenes Requeridos"]
        st.dataframe(exam_df, use_container_width=True, hide_index=True)
    else:
        st.info("No hay exámenes de control específicos programados para la cohorte seleccionada.")

with e2:
    st.markdown("**Indicaciones Médicas Destacadas**")
    medical_notes = [n for n in df["control_indication"].tolist() if n and len(n) > 3][:4]
    if medical_notes:
        for i, note in enumerate(medical_notes, start=1):
            st.markdown(f'<div class="comment-box"><b>Nota de Control {i}</b><br>{note}</div>', unsafe_allow_html=True)
    else:
        st.info("No se registran observaciones médicas especiales en esta selección de registros.")

st.divider()

# Diagnóstico de coincidencia de columnas
with st.expander("Ver diagnóstico del mapeo automático de columnas clínicas"):
    st.json({k: (v if v else "❌ No encontrada (Revisar encabezado)") for k, v in colmap.items()})
    st.caption(
        "Si alguna columna clave aparece como No Encontrada, asegúrate de que el nombre coincida en tu Google Sheet "
        "o agrégala como término alternativo en la tupla de búsqueda dentro de `build_clean_df()`."
    )
