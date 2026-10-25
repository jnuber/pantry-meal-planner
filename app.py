import re
import streamlit as st
import sqlite3
import pandas as pd
import requests
import os
from google import genai

# --- DATABASE SETUP ---
DB_FILE = "pantry.db"

DEFAULT_APPLIANCES = [
    "Oven",
    "Stovetop",
    "Air Fryer",
    "Instant Pot / Pressure Cooker",
    "Microwave",
    "Slow Cooker",
    "Grill / BBQ",
    "Toaster Oven",
    "Rice Cooker",
    "Blender",
    "Food Processor",
    "Sous Vide",
    "Griddle / Cast Iron",
    "Bread Maker",
    "Waffle Iron",
    "Deep Fryer",
    "Smoker",
    "Induction Cooktop",
]

COUNT_UNITS = [
    "each",
    "pack",
    "can",
    "jar",
    "bag",
    "box",
    "bottle",
    "bunch",
    "clove",
    "slice",
    "loaf",
]

IMPERIAL_UNITS = COUNT_UNITS + [
    "tsp",
    "tbsp",
    "fl oz",
    "cup",
    "pint",
    "quart",
    "gallon",
    "oz",
    "lb",
]

METRIC_UNITS = COUNT_UNITS + [
    "ml",
    "L",
    "g",
    "kg",
]

UNIT_ALIASES = {
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "fluid ounce": "fl oz",
    "fluid ounces": "fl oz",
    "floz": "fl oz",
    "cups": "cup",
    "pints": "pint",
    "quarts": "quart",
    "gallons": "gallon",
    "ounce": "oz",
    "ounces": "oz",
    "lb": "lb",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "liter": "L",
    "liters": "L",
    "litre": "L",
    "litres": "L",
    "l": "L",
    "gram": "g",
    "grams": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "packs": "pack",
    "cans": "can",
    "jars": "jar",
    "bags": "bag",
    "boxes": "box",
    "bottles": "bottle",
    "bunches": "bunch",
    "cloves": "clove",
    "slices": "slice",
    "loaves": "loaf",
}


def units_for_system(system):
    return IMPERIAL_UNITS if system == "Imperial" else METRIC_UNITS


def normalize_unit(unit):
    cleaned = " ".join((unit or "").strip().split())
    return UNIT_ALIASES.get(cleaned.lower(), cleaned)


def detect_unit_system(unit):
    normalized = normalize_unit(unit)
    if normalized in METRIC_UNITS and normalized not in COUNT_UNITS:
        return "Metric"
    return "Imperial"


def format_quantity(amount, unit):
    amount_text = str(amount).strip() if amount is not None else ""
    unit_text = normalize_unit(unit)
    return " ".join(part for part in (amount_text, unit_text) if part).strip()


def parse_quantity(raw):
    text = " ".join(str(raw or "").strip().split())
    if not text:
        return "1", "pack", "Imperial"
    match = re.match(r"^([\d]+(?:[./]\d+)?)\s*(.*)$", text)
    if match:
        amount, unit = match.group(1), match.group(2)
    else:
        amount, unit = "1", text
    unit = normalize_unit(unit) or "each"
    return amount, unit, detect_unit_system(unit)


DEFAULT_DIETARY = [
    "None",
    "Vegetarian",
    "Vegan",
    "Pescatarian",
    "Gluten-Free",
    "Dairy-Free",
    "Keto",
    "Low-Carb",
    "Paleo",
    "Halal",
    "Kosher",
    "Nut-Free",
    "Peanut-Free",
    "Tree-Nut-Free",
    "Soy-Free",
    "Egg-Free",
    "Shellfish-Free",
    "Low-Sodium",
    "Low-FODMAP",
    "Diabetic-Friendly",
    "Whole30",
    "Mediterranean",
    "Sugar-Free",
    "High-Protein",
]


def get_conn():
    return sqlite3.connect(DB_FILE)


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item TEXT UNIQUE,
            category TEXT,
            quantity TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            ingredients TEXT,
            instructions TEXT,
            date_saved DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_tags (
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            UNIQUE(kind, name)
        )
        """
    )
    columns = {row[1] for row in c.execute("PRAGMA table_info(inventory)")}
    for column, definition in (
        ("amount", "TEXT"),
        ("unit", "TEXT"),
        ("unit_system", "TEXT"),
    ):
        if column not in columns:
            c.execute(f"ALTER TABLE inventory ADD COLUMN {column} {definition}")
    rows = c.execute(
        "SELECT id, quantity, amount, unit, unit_system FROM inventory"
    ).fetchall()
    for row_id, quantity, amount, unit, unit_system in rows:
        if amount and unit:
            continue
        parsed_amount, parsed_unit, parsed_system = parse_quantity(quantity)
        c.execute(
            """
            UPDATE inventory
            SET amount = ?, unit = ?, unit_system = ?, quantity = ?
            WHERE id = ?
            """,
            (
                parsed_amount,
                parsed_unit,
                parsed_system,
                format_quantity(parsed_amount, parsed_unit),
                row_id,
            ),
        )
    conn.commit()
    conn.close()


def load_tag_options(kind, defaults):
    conn = get_conn()
    rows = conn.execute(
        "SELECT name FROM custom_tags WHERE kind = ? ORDER BY name COLLATE NOCASE",
        (kind,),
    ).fetchall()
    conn.close()
    extras = [row[0] for row in rows if row[0] not in defaults]
    return defaults + extras


def add_custom_tag(kind, name, existing):
    cleaned = " ".join(name.strip().split())
    if not cleaned:
        return None
    if cleaned not in existing:
        conn = get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO custom_tags (kind, name) VALUES (?, ?)",
            (kind, cleaned),
        )
        conn.commit()
        conn.close()
    return cleaned


def remove_custom_tag(kind, name):
    conn = get_conn()
    conn.execute("DELETE FROM custom_tags WHERE kind = ? AND name = ?", (kind, name))
    conn.commit()
    conn.close()


def custom_tag_names(kind):
    conn = get_conn()
    rows = conn.execute(
        "SELECT name FROM custom_tags WHERE kind = ? ORDER BY name COLLATE NOCASE",
        (kind,),
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


init_db()

# --- STREAMLIT UI CONFIG ---
st.set_page_config(
    page_title="Pantry Chef & Meal Planner",
    page_icon="🍳",
    layout="wide",
)

st.title("🍳 Automated Household Meal Planner & Pantry Recipe Generator")

# --- SIDEBAR: SETTINGS & LLM CHOICE ---
st.sidebar.header("⚙️ AI & Configuration Settings")
ai_provider = st.sidebar.radio("Select AI Engine:", ["Google Gemini API", "Local Ollama (CPU)"])

gemini_key = ""
ollama_model = "llama3.2"

if ai_provider == "Google Gemini API":
    # Try to load from Streamlit secrets automatically first
    try:
        gemini_key = st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        gemini_key = ""
    
    # If no secret is found, show the text box so you can still type or override it
    if not gemini_key:
        gemini_key = st.sidebar.text_input("Gemini API Key:", type="password")
    else:
        st.sidebar.success("🔑 Gemini API Key loaded securely from secrets!")
else:
    ollama_model = st.sidebar.text_input("Ollama Model Name:", value="llama3.2")
    st.sidebar.caption("Ensure Ollama is running (`ollama run llama3.2`) on port 11434.")

st.sidebar.markdown("---")
st.sidebar.subheader("🍽️ Household Preferences")

appliance_options = load_tag_options("appliance", DEFAULT_APPLIANCES)
dietary_options = load_tag_options("dietary", DEFAULT_DIETARY)


def _drop_none_if_other_dietary():
    selected = st.session_state.get("dietary_select", [])
    if "None" in selected and len(selected) > 1:
        st.session_state.dietary_select = [item for item in selected if item != "None"]


appliances = st.sidebar.multiselect(
    "Available Appliances:",
    appliance_options,
    default=["Oven", "Stovetop"],
    key="appliance_select",
)
dietary_restrictions = st.sidebar.multiselect(
    "Dietary Restrictions:",
    dietary_options,
    default=["None"],
    key="dietary_select",
    on_change=_drop_none_if_other_dietary,
)

with st.sidebar.expander("Add / remove custom options"):
    st.caption("Type any extra appliance or nationally known diet, then add it to the lists above.")
    new_appliance = st.text_input("Custom appliance", placeholder="e.g., Pizza Oven, Ninja Creami")
    if st.button("Add appliance", use_container_width=True):
        added = add_custom_tag("appliance", new_appliance, appliance_options)
        if added:
            current = list(st.session_state.get("appliance_select", []))
            if added not in current:
                current.append(added)
            st.session_state.appliance_select = current
            st.rerun()
        else:
            st.warning("Enter an appliance name first.")

    new_diet = st.text_input("Custom dietary restriction", placeholder="e.g., Jain, Hindu Vegetarian")
    if st.button("Add dietary restriction", use_container_width=True):
        added = add_custom_tag("dietary", new_diet, dietary_options)
        if added:
            current = [item for item in st.session_state.get("dietary_select", []) if item != "None"]
            if added not in current:
                current.append(added)
            st.session_state.dietary_select = current
            st.rerun()
        else:
            st.warning("Enter a dietary restriction first.")

    removable = custom_tag_names("appliance") + [
        f"{name} (diet)" for name in custom_tag_names("dietary")
    ]
    if removable:
        to_remove = st.selectbox("Remove a custom option:", removable)
        if st.button("Remove custom option", use_container_width=True):
            if to_remove.endswith(" (diet)"):
                name = to_remove[: -len(" (diet)")]
                remove_custom_tag("dietary", name)
                st.session_state.dietary_select = [
                    item for item in st.session_state.get("dietary_select", []) if item != name
                ]
            else:
                remove_custom_tag("appliance", to_remove)
                st.session_state.appliance_select = [
                    item
                    for item in st.session_state.get("appliance_select", [])
                    if item != to_remove
                ]
            st.rerun()

preferred_system = st.sidebar.radio(
    "Measurement system:",
    ["Imperial", "Metric"],
    horizontal=True,
    key="pref_unit_system",
    help="Used as the default for new pantry items. You can still pick units per item.",
)
servings = st.sidebar.number_input("Number of Servings:", min_value=1, max_value=12, value=4)

# --- MAIN NAVIGATION TABS ---
tab1, tab2, tab3 = st.tabs(["🛒 Pantry Inventory", "🤖 Recipe Generator", "📖 Saved Recipes"])

# --- TAB 1: PANTRY INVENTORY ---
with tab1:
    st.header("Current Pantry Inventory")
    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("Add / Update Item")
        if "item_unit_system" not in st.session_state:
            st.session_state.item_unit_system = preferred_system
        unit_system = st.radio(
            "Unit system:",
            ["Imperial", "Metric"],
            key="item_unit_system",
            horizontal=True,
        )
        with st.form("add_item_form"):
            new_item = st.text_input("Ingredient Name (e.g., Chicken Breast, Rice, Eggs):")
            category = st.selectbox(
                "Category:",
                ["Produce", "Meat & Seafood", "Dairy & Refrigerated", "Pantry Staples", "Spices", "Frozen"],
            )
            amount_col, unit_col = st.columns(2)
            with amount_col:
                amount = st.text_input("Amount:", value="1")
            with unit_col:
                unit_choices = units_for_system(unit_system)
                default_unit = "oz" if unit_system == "Imperial" else "g"
                if default_unit not in unit_choices:
                    default_unit = unit_choices[0]
                unit = st.selectbox("Unit:", unit_choices, index=unit_choices.index(default_unit))
            submitted = st.form_submit_button("Add to Pantry")

            if submitted and new_item:
                amount_value = amount.strip() or "1"
                unit_value = normalize_unit(unit)
                quantity = format_quantity(amount_value, unit_value)
                conn = get_conn()
                c = conn.cursor()
                c.execute(
                    """
                    INSERT INTO inventory (item, category, quantity, amount, unit, unit_system)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(item) DO UPDATE SET
                        category = excluded.category,
                        quantity = excluded.quantity,
                        amount = excluded.amount,
                        unit = excluded.unit,
                        unit_system = excluded.unit_system
                    """,
                    (
                        new_item.strip().title(),
                        category,
                        quantity,
                        amount_value,
                        unit_value,
                        unit_system,
                    ),
                )
                conn.commit()
                conn.close()
                st.success(f"Added '{new_item}' ({quantity}) to pantry!")
                st.rerun()

    with col2:
        st.subheader("Items on Hand")
        conn = get_conn()
        df = pd.read_sql_query(
            """
            SELECT id, item, category, amount, unit, unit_system, quantity
            FROM inventory
            ORDER BY category, item
            """,
            conn,
        )
        conn.close()

        if not df.empty:
            display = df.copy()
            display["amount"] = display.apply(
                lambda row: row["amount"] if pd.notna(row["amount"]) and str(row["amount"]).strip() else parse_quantity(row["quantity"])[0],
                axis=1,
            )
            display["unit"] = display.apply(
                lambda row: row["unit"] if pd.notna(row["unit"]) and str(row["unit"]).strip() else parse_quantity(row["quantity"])[1],
                axis=1,
            )
            display["system"] = display["unit_system"].fillna("").replace("", "Imperial")
            st.dataframe(
                display[["item", "category", "amount", "unit", "system"]],
                width="stretch",
            )

            delete_id = st.selectbox("Select item to remove:", df["item"].tolist())
            if st.button("Delete Selected Item"):
                conn = get_conn()
                c = conn.cursor()
                c.execute("DELETE FROM inventory WHERE item = ?", (delete_id,))
                conn.commit()
                conn.close()
                st.warning(f"Removed '{delete_id}'.")
                st.rerun()
        else:
            st.info("Your pantry is currently empty. Add some items on the left!")

# --- TAB 2: RECIPE GENERATOR ---
with tab2:
    st.header("Generate Meal Plan & Recipes")

    conn = get_conn()
    df_pantry = pd.read_sql_query(
        "SELECT item, amount, unit, quantity FROM inventory ORDER BY item", conn
    )
    conn.close()

    pantry_list = df_pantry["item"].tolist() if not df_pantry.empty else []
    pantry_qty_by_item = {}
    if not df_pantry.empty:
        for _, row in df_pantry.iterrows():
            qty = format_quantity(row["amount"], row["unit"]) or row["quantity"] or ""
            pantry_qty_by_item[row["item"]] = f"{row['item']} ({qty})" if qty else row["item"]

    selected_pantry = st.multiselect(
        "Select ingredients to use for this meal:", pantry_list, default=pantry_list
    )
    additional_notes = st.text_input(
        "Extra craving or prompt (e.g., 'Quick 20-min dinner', 'Comfort food', 'Italian style'):"
    )

    if st.button("✨ Generate Recipe Ideas", type="primary"):
        if not selected_pantry:
            st.error("Please select at least a few pantry ingredients!")
        else:
            prompt = f"""
You are a expert chef and automated meal planner.
Given these ingredients: {', '.join(pantry_qty_by_item.get(item, item) for item in selected_pantry)}
Appliances available: {', '.join(appliances)}
Dietary restrictions: {', '.join(dietary_restrictions)}
Servings: {servings}
Additional Request: {additional_notes}

Please generate 2 distinct recipe suggestions. For each recipe provide:
1. Recipe Title
2. Prep Time & Cook Time
3. Ingredients List (highlighting which are from the list and standard pantry staples like oil/salt)
4. Step-by-Step Cooking Instructions
"""
            with st.spinner("Chef AI is creating recipes..."):
                recipe_result = ""
                try:
                    if ai_provider == "Google Gemini API":
                        if not gemini_key:
                            st.error("Please provide a Gemini API Key in the sidebar.")
                        else:
                            client = genai.Client(api_key=gemini_key)
                            response = client.models.generate_content(
                                model="gemini-3.8-flash",
                                contents=prompt,
                            )
                            recipe_result = response.text
                    else:
                        res = requests.post(
                            "http://localhost:11434/api/generate",
                            json={
                                "model": ollama_model,
                                "prompt": prompt,
                                "stream": False,
                            },
                            timeout=180,
                        )
                        if res.status_code == 200:
                            recipe_result = res.json().get("response", "")
                        else:
                            st.error(f"Ollama error: {res.text}")

                    if recipe_result:
                        st.session_state["last_recipe"] = recipe_result
                        title_line = next(
                            (
                                line.strip("# ").strip()
                                for line in recipe_result.splitlines()
                                if line.strip()
                            ),
                            "Generated Recipe",
                        )
                        st.session_state["last_recipe_title"] = title_line[:120]
                        st.session_state["last_recipe_ingredients"] = ", ".join(selected_pantry)

                except Exception as e:
                    st.error(f"Error generating recipe: {str(e)}")

    if st.session_state.get("last_recipe"):
        recipe_result = st.session_state["last_recipe"]
        st.markdown("### 🍽️ Generated Recipes")
        st.markdown(recipe_result)

        st.download_button(
            "📥 Save Recipe Output as Markdown",
            data=recipe_result,
            file_name="recipe.md",
            mime="text/markdown",
        )

        if st.button("⭐ Save to Household Favorites"):
            conn = get_conn()
            c = conn.cursor()
            c.execute(
                "INSERT INTO recipes (title, ingredients, instructions) VALUES (?, ?, ?)",
                (
                    st.session_state.get("last_recipe_title", "Generated Recipe"),
                    st.session_state.get("last_recipe_ingredients", ""),
                    recipe_result,
                ),
            )
            conn.commit()
            conn.close()
            st.success("Saved to Household Favorites.")
            st.rerun()

# --- TAB 3: SAVED RECIPES ---
with tab3:
    st.header("Saved Household Favorites")
    st.info("Archive recipes generated from your pantry so you can cook them again later.")

    conn = get_conn()
    df_recipes = pd.read_sql_query(
        "SELECT id, title, ingredients, instructions, date_saved FROM recipes ORDER BY date_saved DESC",
        conn,
    )
    conn.close()

    if df_recipes.empty:
        st.caption("No saved recipes yet. Generate a recipe, then click Save to Household Favorites.")
    else:
        for _, row in df_recipes.iterrows():
            with st.expander(f"{row['title']}  ·  {row['date_saved']}"):
                if row["ingredients"]:
                    st.caption(f"Pantry ingredients used: {row['ingredients']}")
                st.markdown(row["instructions"])
                if st.button("Delete recipe", key=f"delete_recipe_{row['id']}"):
                    conn = get_conn()
                    c = conn.cursor()
                    c.execute("DELETE FROM recipes WHERE id = ?", (int(row["id"]),))
                    conn.commit()
                    conn.close()
                    st.rerun()
