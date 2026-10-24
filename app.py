import streamlit as st
import sqlite3
import pandas as pd
import requests
import os
from google import genai

# --- DATABASE SETUP ---
DB_FILE = "pantry.db"


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
    conn.commit()
    conn.close()


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
appliances = st.sidebar.multiselect(
    "Available Appliances:",
    ["Oven", "Stovetop", "Air Fryer", "Instant Pot / Pressure Cooker", "Microwave", "Slow Cooker"],
    default=["Oven", "Stovetop"],
)
dietary_restrictions = st.sidebar.multiselect(
    "Dietary Restrictions:",
    ["None", "Vegetarian", "Vegan", "Gluten-Free", "Keto", "Dairy-Free", "Low-Carb"],
    default=["None"],
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
        with st.form("add_item_form"):
            new_item = st.text_input("Ingredient Name (e.g., Chicken Breast, Rice, Eggs):")
            category = st.selectbox(
                "Category:",
                ["Produce", "Meat & Seafood", "Dairy & Refrigerated", "Pantry Staples", "Spices", "Frozen"],
            )
            quantity = st.text_input("Quantity/Amount:", value="1 pack")
            submitted = st.form_submit_button("Add to Pantry")

            if submitted and new_item:
                conn = get_conn()
                c = conn.cursor()
                c.execute(
                    """
                    INSERT INTO inventory (item, category, quantity)
                    VALUES (?, ?, ?)
                    ON CONFLICT(item) DO UPDATE SET
                        category = excluded.category,
                        quantity = excluded.quantity
                    """,
                    (new_item.strip().title(), category, quantity),
                )
                conn.commit()
                conn.close()
                st.success(f"Added '{new_item}' to pantry!")
                st.rerun()

    with col2:
        st.subheader("Items on Hand")
        conn = get_conn()
        df = pd.read_sql_query(
            "SELECT id, item, category, quantity FROM inventory ORDER BY category, item", conn
        )
        conn.close()

        if not df.empty:
            st.dataframe(df[["item", "category", "quantity"]], width="stretch")

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
    df_pantry = pd.read_sql_query("SELECT item FROM inventory", conn)
    conn.close()

    pantry_list = df_pantry["item"].tolist() if not df_pantry.empty else []

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
Given these ingredients: {', '.join(selected_pantry)}
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
