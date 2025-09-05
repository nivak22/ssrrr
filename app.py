import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import json
from firebase_admin import credentials, firestore, initialize_app, _apps
import io

# --- Configuración de la página ---
st.set_page_config(layout="wide")

# --- Utilidades sin depender de locale ---
SPANISH_DAYS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
SPANISH_MONTHS = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
}

def format_date_es(d: datetime.date) -> str:
    day = SPANISH_DAYS[d.weekday()]
    month = SPANISH_MONTHS[d.month]
    return f"{day}, {d.day:02d} de {month}"

def day_from_formatted(col_label: str) -> str:
    return col_label.split(",", 1)[0].strip().lower()

def today_spanish_day() -> str:
    return SPANISH_DAYS[datetime.now().weekday()]

# --- Inicialización de Firebase ---
@st.cache_resource
def setup_firebase():
    if 'db' not in st.session_state:
        try:
            firebase_key_str = st.secrets["firebase_key"]
            firebase_dict = json.loads(firebase_key_str)
            app_id = st.secrets["app_id"]

            if not _apps:
                cred = credentials.Certificate(firebase_dict)
                initialize_app(cred)

            db = firestore.client()
            st.session_state.db = db
            st.session_state.app_id = app_id
            return db, app_id
        except Exception as e:
            st.error(f"Error al inicializar Firebase: {e}")
            st.info("Asegúrate de que tus credenciales de cuenta de servicio son correctas en st.secrets.")
            return None, None
    return st.session_state.db, st.session_state.app_id

db, app_id = setup_firebase()

# --- Funciones de la aplicación ---
def load_and_process_data(uploaded_file):
    try:
        if uploaded_file.name.endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        elif uploaded_file.name.endswith('.xlsx'):
            df = pd.read_excel(uploaded_file)
        else:
            st.error("Formato de archivo no soportado. Por favor, sube un archivo .csv o .xlsx.")
            return None

        df.columns = df.columns.str.strip()
        establishment_col = 'establishment_name'
        branch_col = 'establishment_branch_address'

        if establishment_col in df.columns and branch_col in df.columns:
            df['establecimiento_sede'] = df[establishment_col] + ' - ' + df[branch_col]
            new_estabs = sorted(df['establecimiento_sede'].unique())

            # --- Cambio: unir con los existentes en lugar de sobrescribir ---
            if "establecimientos_list" in st.session_state:
                all_estabs = set(st.session_state.establecimientos_list) | set(new_estabs)
                st.session_state.establecimientos_list = sorted(all_estabs)
            else:
                st.session_state.establecimientos_list = new_estabs
        else:
            st.warning("Las columnas 'establishment_name' o 'establishment_branch_address' no se encontraron. La gestión de metas podría no funcionar correctamente.")

        if "establecimientos_list" not in st.session_state:
            st.session_state.establecimientos_list = []

        st.success("Archivo cargado exitosamente.")
        st.write("Vista previa de los datos cargados:")
        st.dataframe(df.head(), use_container_width=True)
        return df
    except Exception as e:
        st.error(f"Ocurrió un error al procesar el archivo. Verifica el formato. Error: {e}")
        return None

def fetch_goals(app_id):
    if db is None or app_id is None:
        return {}
    current_day_name = today_spanish_day()
    goals_ref = db.collection('artifacts').document(app_id).collection('metas').document(current_day_name)
    doc = goals_ref.get()
    return doc.to_dict() if doc.exists else {}

def apply_style_pax(df_to_style, daily_goals_matrix):
    styled = pd.DataFrame('', index=df_to_style.index, columns=df_to_style.columns)
    for (col_date, col_type) in df_to_style.columns:
        if col_type == 'PAX':
            day_key = day_from_formatted(col_date)
            pax_series = df_to_style[(col_date, 'PAX')]
            for idx, pax_val in pax_series.items():
                goal_val = daily_goals_matrix.get(idx, {}).get(day_key, 0)
                if isinstance(pax_val, (int, float)) and isinstance(goal_val, (int, float)) and goal_val > 0:
                    if pax_val >= goal_val * 1.05:
                        styled.loc[idx, (col_date, 'PAX')] = 'background-color: #d4edda; color: black'
                    elif pax_val >= goal_val * 0.95:
                        styled.loc[idx, (col_date, 'PAX')] = 'background-color: #fff3cd; color: black'
                    else:
                        styled.loc[idx, (col_date, 'PAX')] = 'background-color: #f8d7da; color: black'
    return styled

def create_dashboard(df, all_goals):
    required_columns = ['status', 'establishment_name', 'establishment_branch_address', 'meta_reservation_date', 'meta_reservation_persons']
    if not all(col in df.columns for col in required_columns):
        st.error(f"El archivo debe contener las siguientes columnas: {', '.join(required_columns)}")
        return

    df['meta_reservation_date'] = pd.to_datetime(df['meta_reservation_date'], errors='coerce')
    today = datetime.now().date()
    date_range = [today + timedelta(days=i) for i in range(7)]
    formatted_dates = [format_date_es(d) for d in date_range]

    df_filtrado = df[
        (df['status'] == 'Asignado') & (df['meta_reservation_date'].dt.date.isin(date_range))
    ].copy()

    if not df_filtrado.empty:
        df_filtrado['establecimiento_sede'] = df_filtrado['establishment_name'] + ' - ' + df_filtrado['establishment_branch_address']
        df_filtrado['fecha_formato'] = df_filtrado['meta_reservation_date'].dt.date.apply(format_date_es)

        conteo_pax = df_filtrado.groupby(['establecimiento_sede', 'fecha_formato']).agg(
            rsv=('status', 'size'),
            pax=('meta_reservation_persons', 'sum')
        ).reset_index()

        pivot_rsv = conteo_pax.pivot_table(
            index='establecimiento_sede',
            columns='fecha_formato',
            values='rsv',
            fill_value=0
        ).reindex(columns=formatted_dates).fillna(0).astype(int)

        pivot_pax = conteo_pax.pivot_table(
            index='establecimiento_sede',
            columns='fecha_formato',
            values='pax',
            fill_value=0
        ).reindex(columns=formatted_dates).fillna(0).astype(int)

        daily_goals_matrix = all_goals if isinstance(all_goals, dict) else {}

        combined_df = pd.concat({'RSV': pivot_rsv, 'PAX': pivot_pax}, axis=1)
        combined_df = combined_df.swaplevel(axis=1).sort_index(axis=1)

        final_df_display = combined_df.reindex(
            columns=pd.MultiIndex.from_product([formatted_dates, ['RSV', 'PAX']])
        )

        st.markdown("---")
        st.header("Tablero de Reservas Asignadas (Próximos 7 días)")
        st.write("RSV: número de reservas | PAX: total de personas.")

        styled_df = final_df_display.style.apply(apply_style_pax, axis=None, daily_goals_matrix=daily_goals_matrix)
        st.dataframe(styled_df, use_container_width=True)
    else:
        st.warning("No se encontraron reservas con estado 'Asignado' en los próximos 7 días.")

def metas_page(db, app_id):
    st.header("Gestión de Metas Diarias")
    dias_semana_full = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
    selected_day_to_edit = st.selectbox(
        "Selecciona el día para el cual quieres establecer las metas de la semana:",
        dias_semana_full
    )

    goals_ref = db.collection('artifacts').document(app_id).collection('metas').document(selected_day_to_edit.lower())
    doc = goals_ref.get()
    current_goals = doc.to_dict() if doc.exists else {}

    # --- Cambio: unir establecimientos de Firebase con los de sesión ---
    firebase_estabs = list(current_goals.keys())
    session_estabs = st.session_state.get("establecimientos_list", [])
    establecimientos = sorted(set(firebase_estabs) | set(session_estabs))

    metas_df = pd.DataFrame(index=establecimientos)
    for day in dias_semana_full:
        metas_df[day] = metas_df.index.to_series().apply(lambda x: current_goals.get(x, {}).get(day.lower(), 0))

    st.write(f"### Metas de la semana para el día: {selected_day_to_edit}")
    st.write("Edita las metas de PAX (personas) para cada establecimiento y día.")

    edited_df = st.data_editor(metas_df, use_container_width=True, num_rows="dynamic")

    if st.button("Guardar Metas"):
        try:
            goals_to_save = {}
            for establecimiento, row in edited_df.iterrows():
                goals_to_save[establecimiento] = {day.lower(): int(row[day]) for day in dias_semana_full}
            goals_ref.set(goals_to_save)
            st.success("Metas guardadas con éxito.")
        except Exception as e:
            st.error(f"Error al guardar las metas: {e}")

# --- Lógica principal ---
st.title("Sistema de Gestión y Análisis de Reservas")

page_selection = st.sidebar.radio("Navegación", ["Análisis de Reservas", "Gestión de Metas"])

if page_selection == "Análisis de Reservas":
    st.header("1. Carga tu archivo de reservas")
    st.write("Carga el archivo de reservas para generar el tablero. Se admiten formatos CSV y XLSX.")
    uploaded_file = st.file_uploader("Elige un archivo", type=["csv", "xlsx"])
    if uploaded_file is not None:
        st.session_state.uploaded_file_name = uploaded_file.name
        st.session_state.df = load_and_process_data(uploaded_file)
        if 'df' in st.session_state and st.session_state.df is not None:
            create_dashboard(st.session_state.df, fetch_goals(app_id))
    else:
        st.info("Sube un archivo para ver el tablero.")
elif page_selection == "Gestión de Metas":
    if db and app_id:
        metas_page(db, app_id)
    else:
        st.error("No se pudo conectar a la base de datos de Firebase.")
