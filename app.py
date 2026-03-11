import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(page_title="Calorie Tracker", page_icon="🍽️", layout="wide")

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
LOG_FILE = DATA_DIR / "food_log.csv"
PROFILE_FILE = DATA_DIR / "profile.json"

MEAL_TYPES = ["Desayuno", "Colación AM", "Almuerzo", "Once", "Cena", "Snack"]
ACTIVITY_LEVELS = {
    "Sedentario": 1.2,
    "Ligero (1-3 entrenamientos/semana)": 1.375,
    "Moderado (3-5 entrenamientos/semana)": 1.55,
    "Alto (6-7 entrenamientos/semana)": 1.725,
    "Muy alto / trabajo físico": 1.9,
}


def load_log() -> pd.DataFrame:
    if LOG_FILE.exists():
        df = pd.read_csv(LOG_FILE)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"]).dt.date
        return df
    return pd.DataFrame(
        columns=[
            "date",
            "meal_type",
            "food_name",
            "grams",
            "kcal_per_100g",
            "calories",
            "source",
        ]
    )



def save_log(df: pd.DataFrame) -> None:
    df_to_save = df.copy()
    if not df_to_save.empty:
        df_to_save["date"] = pd.to_datetime(df_to_save["date"]).dt.strftime("%Y-%m-%d")
    df_to_save.to_csv(LOG_FILE, index=False)



def load_profile() -> Dict[str, Any]:
    if PROFILE_FILE.exists():
        return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    return {
        "weight": 80.0,
        "height": 175.0,
        "age": 30,
        "sex": "Hombre",
        "activity_level": "Moderado (3-5 entrenamientos/semana)",
    }



def save_profile(profile: Dict[str, Any]) -> None:
    PROFILE_FILE.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")



def calculate_bmr(weight: float, height: float, age: int, sex: str) -> float:
    base = 10 * weight + 6.25 * height - 5 * age
    return base + 5 if sex == "Hombre" else base - 161



def calculate_tdee(weight: float, height: float, age: int, sex: str, activity_level: str) -> float:
    bmr = calculate_bmr(weight, height, age, sex)
    return bmr * ACTIVITY_LEVELS[activity_level]



def get_openfoodfacts_results(query: str) -> List[Dict[str, Any]]:
    url = "https://world.openfoodfacts.org/cgi/search.pl"
    params = {
        "search_terms": query,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": 10,
        "fields": "product_name,brands,nutriments,code",
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        products = response.json().get("products", [])
    except Exception:
        return []

    items = []
    for product in products:
        nutriments = product.get("nutriments", {}) or {}
        kcal = nutriments.get("energy-kcal_100g")
        if kcal is None:
            kj = nutriments.get("energy_100g")
            if kj is not None:
                kcal = round(float(kj) / 4.184, 1)
        if kcal is None:
            continue
        name = product.get("product_name") or "Producto sin nombre"
        brand = product.get("brands") or ""
        label = f"{name} - {brand}" if brand else name
        items.append(
            {
                "label": label,
                "kcal_per_100g": float(kcal),
                "source": "Open Food Facts",
            }
        )
    return items



def get_usda_results(query: str, api_key: Optional[str]) -> List[Dict[str, Any]]:
    if not api_key:
        return []
    url = "https://api.nal.usda.gov/fdc/v1/foods/search"
    params = {"api_key": api_key}
    payload = {"query": query, "pageSize": 10}
    try:
        response = requests.post(url, params=params, json=payload, timeout=20)
        response.raise_for_status()
        foods = response.json().get("foods", [])
    except Exception:
        return []

    items = []
    for food in foods:
        kcal = None
        for nutrient in food.get("foodNutrients", []):
            nutrient_name = str(nutrient.get("nutrientName", "")).lower()
            unit_name = str(nutrient.get("unitName", "")).upper()
            if "energy" in nutrient_name and unit_name == "KCAL":
                kcal = nutrient.get("value")
                break
        if kcal is None:
            continue
        desc = food.get("description") or "Alimento USDA"
        brand = food.get("brandOwner") or ""
        label = f"{desc} - {brand}" if brand else desc
        items.append(
            {
                "label": label.title(),
                "kcal_per_100g": float(kcal),
                "source": "USDA FoodData Central",
            }
        )
    return items



def search_food(query: str) -> List[Dict[str, Any]]:
    api_key = st.secrets.get("USDA_API_KEY", None) if hasattr(st, "secrets") else None
    results = []
    results.extend(get_openfoodfacts_results(query))
    results.extend(get_usda_results(query, api_key))

    unique = []
    seen = set()
    for item in results:
        key = (item["label"], round(item["kcal_per_100g"], 1), item["source"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:15]



def ensure_session_state() -> None:
    if "search_results" not in st.session_state:
        st.session_state.search_results = []



def add_food_entry(df: pd.DataFrame, entry: Dict[str, Any]) -> pd.DataFrame:
    new_row = pd.DataFrame([entry])
    updated = pd.concat([df, new_row], ignore_index=True)
    save_log(updated)
    return updated



def daily_summary(df: pd.DataFrame, selected_date: date) -> pd.DataFrame:
    if df.empty:
        return df
    day_df = df[df["date"] == selected_date].copy()
    return day_df.sort_values(by=["meal_type", "food_name"])



def build_charts(df: pd.DataFrame, tdee: float) -> None:
    if df.empty:
        st.info("Aún no hay registros para graficar.")
        return

    summary = df.groupby("date", as_index=False)["calories"].sum()
    summary["tdee"] = tdee
    summary["balance"] = summary["calories"] - summary["tdee"]

    fig1 = px.line(
        summary,
        x="date",
        y=["calories", "tdee"],
        markers=True,
        title="Consumo diario vs gasto estimado",
        labels={"value": "kcal", "date": "Fecha", "variable": "Serie"},
    )
    st.plotly_chart(fig1, use_container_width=True)

    fig2 = px.bar(
        summary,
        x="date",
        y="balance",
        title="Balance calórico diario (positivo o negativo)",
        labels={"balance": "kcal", "date": "Fecha"},
    )
    st.plotly_chart(fig2, use_container_width=True)


ensure_session_state()
profile = load_profile()
log_df = load_log()

st.title("🍽️ Registro diario de calorías")
st.caption("Busca alimentos desde la web, registra gramos consumidos y compáralos con tu gasto diario estimado.")

with st.sidebar:
    st.header("Tu perfil")
    weight = st.number_input("Peso (kg)", min_value=30.0, max_value=250.0, value=float(profile["weight"]), step=0.1)
    height = st.number_input("Altura (cm)", min_value=120.0, max_value=230.0, value=float(profile["height"]), step=0.1)
    age = st.number_input("Edad", min_value=10, max_value=100, value=int(profile["age"]), step=1)
    sex = st.selectbox("Sexo", ["Hombre", "Mujer"], index=0 if profile["sex"] == "Hombre" else 1)
    activity_level = st.selectbox(
        "Nivel de actividad",
        list(ACTIVITY_LEVELS.keys()),
        index=list(ACTIVITY_LEVELS.keys()).index(profile["activity_level"]),
    )

    profile = {
        "weight": weight,
        "height": height,
        "age": age,
        "sex": sex,
        "activity_level": activity_level,
    }
    save_profile(profile)

    bmr = calculate_bmr(weight, height, age, sex)
    tdee = calculate_tdee(weight, height, age, sex, activity_level)

    st.metric("BMR estimado", f"{bmr:,.0f} kcal/día")
    st.metric("Gasto diario estimado (TDEE)", f"{tdee:,.0f} kcal/día")

left, right = st.columns([1.2, 1])

with left:
    st.subheader("Agregar alimento")
    selected_date = st.date_input("Fecha", value=date.today())
    meal_type = st.selectbox("Tipo de comida", MEAL_TYPES)
    search_query = st.text_input("Buscar alimento", placeholder="Ej: yogur proteína, arroz, pechuga de pollo")

    col_a, col_b = st.columns([1, 1])
    with col_a:
        if st.button("Buscar calorías", use_container_width=True):
            if search_query.strip():
                st.session_state.search_results = search_food(search_query.strip())
            else:
                st.warning("Escribe un alimento para buscar.")

    results = st.session_state.search_results

    source = "Manual"
    kcal_per_100g = 0.0
    food_name = search_query.strip() if search_query.strip() else ""

    if results:
        options = [f"{item['label']} | {item['kcal_per_100g']:.1f} kcal/100 g | {item['source']}" for item in results]
        selected_option = st.selectbox("Resultados encontrados", options)
        selected_index = options.index(selected_option)
        selected_item = results[selected_index]
        food_name = selected_item["label"]
        kcal_per_100g = selected_item["kcal_per_100g"]
        source = selected_item["source"]
    else:
        st.info("Puedes buscar un alimento o ingresar manualmente sus calorías por 100 g.")
        food_name = st.text_input("Nombre del alimento", value=food_name)
        kcal_per_100g = st.number_input("Calorías por 100 g", min_value=0.0, value=0.0, step=1.0)

    grams = st.number_input("Gramos consumidos", min_value=0.0, value=100.0, step=1.0)
    calories = (grams * kcal_per_100g) / 100 if kcal_per_100g else 0
    st.metric("Calorías estimadas de esta porción", f"{calories:,.0f} kcal")

    if st.button("Guardar registro", type="primary", use_container_width=True):
        if not food_name:
            st.error("Debes indicar el nombre del alimento.")
        elif grams <= 0:
            st.error("Los gramos deben ser mayores que 0.")
        else:
            entry = {
                "date": selected_date,
                "meal_type": meal_type,
                "food_name": food_name,
                "grams": grams,
                "kcal_per_100g": kcal_per_100g,
                "calories": round(calories, 2),
                "source": source,
            }
            log_df = add_food_entry(log_df, entry)
            st.success("Registro guardado correctamente.")

with right:
    st.subheader("Resumen del día")
    today_df = daily_summary(log_df, selected_date)
    total_day = float(today_df["calories"].sum()) if not today_df.empty else 0.0
    balance_day = total_day - tdee

    c1, c2 = st.columns(2)
    c1.metric("Consumido hoy", f"{total_day:,.0f} kcal")
    c2.metric("Balance vs TDEE", f"{balance_day:,.0f} kcal")

    if not today_df.empty:
        st.dataframe(today_df, use_container_width=True, hide_index=True)
    else:
        st.write("Sin registros para esta fecha.")

st.divider()
st.subheader("Gráficos")
build_charts(log_df, tdee)

st.divider()
st.subheader("Historial completo")
if not log_df.empty:
    st.dataframe(log_df.sort_values(by="date", ascending=False), use_container_width=True, hide_index=True)

    csv = log_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Descargar historial CSV",
        data=csv,
        file_name=f"food_log_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )

    if st.button("Eliminar todos los registros"):
        empty_df = pd.DataFrame(columns=log_df.columns)
        save_log(empty_df)
        st.session_state.search_results = []
        st.rerun()
else:
    st.write("Todavía no hay datos registrados.")

st.info(
    "Tip: si agregas tu clave USDA en secrets.toml con el nombre USDA_API_KEY, la app buscará también en FoodData Central."
)
