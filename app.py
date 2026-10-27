import json
import re
import streamlit as st
import pandas as pd
import requests
from google import genai
from supabase import create_client, Client

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

PANTRY_CATEGORIES = [
    "Produce",
    "Meat & Seafood",
    "Dairy & Refrigerated",
    "Pantry Staples",
    "Spices",
    "Frozen",
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


def recipe_to_markdown(recipe):
    if recipe.get("markdown"):
        return recipe["markdown"]
    lines = [f"## {recipe.get('title') or 'Recipe'}"]
    prep = recipe.get("prep_time")
    cook = recipe.get("cook_time")
    if prep or cook:
        lines.append(f"**Prep:** {prep or '—'}  ·  **Cook:** {cook or '—'}")
    ingredients = recipe.get("ingredients") or []
    if ingredients:
        lines.append("### Ingredients")
        lines.extend(f"- {item}" for item in ingredients)
    instructions = recipe.get("instructions") or []
    if instructions:
        lines.append("### Instructions")
        if isinstance(instructions, str):
            lines.append(instructions)
        else:
            lines.extend(f"{index}. {step}" for index, step in enumerate(instructions, 1))
    return "\n\n".join(lines)


def _title_from_markdown(text, fallback="Generated Recipe"):
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return re.sub(r"^#{1,6}\s*", "", stripped).strip(" *-")[:120] or fallback
    return fallback


def split_markdown_recipes(text):
    chunks = [part.strip() for part in re.split(r"(?=^#{1,3}\s+)", text.strip(), flags=re.M) if part.strip()]
    if len(chunks) < 2:
        chunks = [
            part.strip()
            for part in re.split(r"(?=^\s*(?:\*\*)?Recipe\s+\d+)", text.strip(), flags=re.I | re.M)
            if part.strip()
        ]
    if not chunks:
        chunks = [text.strip()]
    recipes = []
    for index, chunk in enumerate(chunks, 1):
        recipes.append(
            {
                "title": _title_from_markdown(chunk, f"Generated Recipe {index}"),
                "markdown": chunk,
            }
        )
    return recipes


def parse_generated_recipes(raw_text):
    text = (raw_text or "").strip()
    if not text:
        return []
    payload = text
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        payload = fenced.group(1).strip()
    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            data = data.get("recipes") or data.get("suggestions") or [data]
        if isinstance(data, list) and data:
            recipes = []
            for index, item in enumerate(data, 1):
                if not isinstance(item, dict):
                    continue
                recipe = {
                    "title": (item.get("title") or f"Generated Recipe {index}").strip()[:120],
                    "prep_time": item.get("prep_time") or item.get("prepTime"),
                    "cook_time": item.get("cook_time") or item.get("cookTime"),
                    "ingredients": item.get("ingredients") or [],
                    "instructions": item.get("instructions") or item.get("steps") or [],
                }
                recipe["markdown"] = recipe_to_markdown(recipe)
                recipes.append(recipe)
            if recipes:
                return recipes
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return split_markdown_recipes(text)


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


def quantity_value(amount_text):
    text = str(amount_text).strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            return float(numerator) / float(denominator)
        value = float(text)
        return int(value) if value.is_integer() else value
    except (TypeError, ValueError, ZeroDivisionError):
        return text


def inventory_records(rows):
    records = []
    for row in rows or []:
        name = row.get("ingredient_name") or row.get("item") or ""
        unit = row.get("unit") or ""
        amount = row.get("quantity")
        if amount is None:
            amount = row.get("amount")
        unit_system = row.get("unit_system") or detect_unit_system(unit)
        records.append(
            {
                "id": row.get("id"),
                "item": name,
                "category": row.get("category") or "",
                "amount": amount,
                "unit": unit,
                "unit_system": unit_system,
                "quantity": format_quantity(amount, unit),
            }
        )
    return records


# --- STREAMLIT UI CONFIG ---
st.set_page_config(
    page_title="Pantry Chef & Meal Planner",
    page_icon="🍳",
    layout="wide",
)


@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


try:
    supabase: Client = init_supabase()
except Exception:
    st.error("Add SUPABASE_URL and SUPABASE_KEY to `.streamlit/secrets.toml`, then rerun.")
    st.stop()


def load_tag_options(kind, defaults):
    response = supabase.table("custom_tags").select("name").eq("kind", kind).order("name").execute()
    extras = [row["name"] for row in (response.data or []) if row.get("name") and row["name"] not in defaults]
    return defaults + extras


def add_custom_tag(kind, name, existing):
    cleaned = " ".join(name.strip().split())
    if not cleaned:
        return None
    if cleaned not in existing:
        supabase.table("custom_tags").upsert(
            {"kind": kind, "name": cleaned},
            on_conflict="kind,name",
        ).execute()
    return cleaned


def remove_custom_tag(kind, name):
    supabase.table("custom_tags").delete().eq("kind", kind).eq("name", name).execute()


def custom_tag_names(kind):
    response = supabase.table("custom_tags").select("name").eq("kind", kind).order("name").execute()
    return [row["name"] for row in (response.data or []) if row.get("name")]


def fetch_inventory_df():
    response = (
        supabase.table("pantry_inventory")
        .select("*")
        .order("category")
        .order("ingredient_name")
        .execute()
    )
    records = inventory_records(response.data)
    return pd.DataFrame(records)


def upsert_inventory_item(name, category, amount_value, unit_value, unit_system):
    payload = {
        "ingredient_name": name,
        "category": category,
        "quantity": quantity_value(amount_value),
        "unit": unit_value,
        "unit_system": unit_system,
    }
    try:
        supabase.table("pantry_inventory").upsert(
            payload,
            on_conflict="ingredient_name",
        ).execute()
    except Exception:
        payload.pop("unit_system", None)
        supabase.table("pantry_inventory").upsert(
            payload,
            on_conflict="ingredient_name",
        ).execute()


def delete_inventory_item(name):
    supabase.table("pantry_inventory").delete().eq("ingredient_name", name).execute()


def _has_item_id(item_id):
    if item_id is None:
        return False
    try:
        if pd.isna(item_id):
            return False
    except (TypeError, ValueError):
        pass
    return str(item_id).strip() not in ("", "nan", "None")


def update_inventory_item(item_id, original_name, name, category, amount_value, unit_value, unit_system):
    payload = {
        "ingredient_name": name,
        "category": category,
        "quantity": quantity_value(amount_value),
        "unit": unit_value,
        "unit_system": unit_system,
    }

    def _run(data):
        query = supabase.table("pantry_inventory").update(data)
        if _has_item_id(item_id):
            query = query.eq("id", item_id)
        else:
            query = query.eq("ingredient_name", original_name)
        return query.execute()

    try:
        _run(payload)
    except Exception:
        payload.pop("unit_system", None)
        _run(payload)


def save_favorite_recipe(title, ingredients, instructions):
    supabase.table("saved_recipes").insert(
        {
            "title": title,
            "ingredients": ingredients,
            "instructions": instructions,
        }
    ).execute()


def _parse_rating(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        rating = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= rating <= 5:
        return rating
    return None


def format_star_rating(rating):
    parsed = _parse_rating(rating) or 0
    return ("★" * parsed) + ("☆" * (5 - parsed))


def render_star_rating(rating, size="1.7rem"):
    parsed = _parse_rating(rating) or 0
    stars = []
    for index in range(1, 6):
        filled = index <= parsed
        glyph = "★" if filled else "☆"
        color = "#e0b000" if filled else "#c5b8a8"
        stars.append(
            f"<span style='color:{color};font-size:{size};line-height:1;'>{glyph}</span>"
        )
    return (
        "<div style='text-align:center;letter-spacing:0.12rem;white-space:nowrap;'>"
        + "".join(stars)
        + "</div>"
    )


def fetch_saved_recipes_df():
    response = (
        supabase.table("saved_recipes")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    rows = []
    for row in response.data or []:
        rows.append(
            {
                "id": row.get("id"),
                "title": row.get("title") or "Generated Recipe",
                "ingredients": row.get("ingredients") or "",
                "instructions": row.get("instructions") or "",
                "date_saved": row.get("date_saved") or row.get("created_at") or "",
                "rating": _parse_rating(row.get("rating")),
            }
        )
    return pd.DataFrame(rows)


def sort_saved_recipes(df, sort_by, sort_order="Descending"):
    if df is None or df.empty:
        return df
    ascending = sort_order == "Ascending"
    ordered = df.copy()
    ordered["_title_sort"] = ordered["title"].astype(str).str.casefold()
    if sort_by == "Name":
        ordered = ordered.sort_values("_title_sort", ascending=ascending, kind="mergesort")
    else:
        ordered["_rating_sort"] = pd.to_numeric(ordered["rating"], errors="coerce").fillna(0)
        ordered = ordered.sort_values(
            by=["_rating_sort", "_title_sort"],
            ascending=[ascending, True],
            kind="mergesort",
        )
        ordered = ordered.drop(columns=["_rating_sort"])
    return ordered.drop(columns=["_title_sort"])


def delete_saved_recipe(recipe_id):
    supabase.table("saved_recipes").delete().eq("id", recipe_id).execute()


def update_recipe_rating(recipe_id, rating):
    parsed = _parse_rating(rating)
    if parsed is None:
        raise ValueError("Rating must be between 1 and 5 stars.")
    supabase.table("saved_recipes").update({"rating": parsed}).eq("id", recipe_id).execute()


HELP_GUIDE = """
### Welcome
Pantry Chef turns what you already have on hand into meal ideas. Work left to right: set household preferences, stock the pantry, generate recipes, then save the ones you want to cook.

### Sidebar: AI and household setup
- **AI engine:** Google Gemini uses the key in Streamlit secrets. Local Ollama talks to a model on port 11434.
- **Appliances and diets:** Select what matches your kitchen. Open **Add / remove custom options** to type extras (for example a pizza oven or a Jain diet).
- **Measurement system:** Imperial or Metric is the default for new pantry items. You can still change units on each item.

### Pantry Inventory
Add an ingredient name, category, amount, and unit. Choosing **Imperial** or **Metric** on the item switches the unit list (`oz` / `lb` / `cup` versus `g` / `kg` / `ml`). Click a row in **Items on Hand** to edit any field except the row number, then save. Adding an existing name also updates that item. Use **Delete Selected Item** to remove something you used up.

### Recipe Generator
Pick which pantry items to cook with, add an optional craving (such as “quick 20-minute dinner”), then generate ideas. The chef usually returns **two** recipes.

Each recipe has its own card. Check the ones you want, then **Save selected**. **Save all separately** stores every suggestion as its own Household Favorite. Uncheck a card if you only want the other one.

### Saved Recipes
Favorites are stored in Supabase, one entry per recipe. Sort the list by **name** or **rating**, then choose **ascending** or **descending**. Open a card to reread it. **Rate favorite** opens a 5-star dialog. **Delete favorite** removes it after you confirm.

### Tips
- Be specific in the extra prompt (“air fryer, high protein”) so the chef stays inside your appliances and diet.
- Keep amounts up to date so generated recipes know how much you actually have.
- If generation fails, confirm the Gemini key or that Ollama is running.
"""

CHEF_HELP_ICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
    "%3Cellipse cx='32' cy='23' rx='21' ry='15' fill='%23fffaf3' stroke='%233d2b1f' stroke-width='2.2'/%3E"
    "%3Cpath d='M14 26c1-11 8-18 18-18s17 7 18 18' fill='%23fffaf3' stroke='%233d2b1f' stroke-width='2.2' "
    "stroke-linejoin='round'/%3E"
    "%3Crect x='17' y='33' width='30' height='12' rx='4' fill='%23fffaf3' stroke='%233d2b1f' stroke-width='2.2'/%3E"
    "%3Ccircle cx='32' cy='22' r='9' fill='%23c45c26'/%3E"
    "%3Ctext x='32' y='27.5' text-anchor='middle' font-family='Georgia,Times,serif' font-size='16' "
    "font-weight='700' fill='%23fffaf3'%3E?%3C/text%3E"
    "%3C/svg%3E"
)

st.markdown(
    f"""
    <style>
      .st-key-chef_help_corner {{
        display: flex;
        justify-content: flex-end;
      }}
      .st-key-chef_help_corner button {{
        width: 56px !important;
        height: 56px !important;
        min-height: 56px !important;
        border-radius: 50% !important;
        padding: 0 !important;
        border: 1px solid rgba(61, 43, 31, 0.18) !important;
        background: #fffaf3 url("{CHEF_HELP_ICON}") center / 42px 42px no-repeat !important;
        box-shadow: 0 4px 14px rgba(61, 43, 31, 0.12);
      }}
      .st-key-chef_help_corner button:hover {{
        transform: scale(1.05);
        box-shadow: 0 6px 16px rgba(61, 43, 31, 0.18);
      }}
      .st-key-chef_help_corner button p,
      .st-key-chef_help_corner button span {{
        font-size: 0 !important;
        color: transparent !important;
      }}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.dialog("Kitchen Help", width="large")
def show_help_dialog():
    st.markdown(HELP_GUIDE)


def _close_edit_dialog():
    st.session_state.show_edit_dialog = False


def _open_edit_dialog(item):
    st.session_state.edit_item = dict(item)
    st.session_state.edit_pantry_name = str(item.get("item") or "")
    st.session_state.edit_pantry_category = item.get("category") if item.get("category") in PANTRY_CATEGORIES else PANTRY_CATEGORIES[0]
    system = item.get("system") or item.get("unit_system") or detect_unit_system(item.get("unit"))
    if system not in ("Imperial", "Metric"):
        system = "Imperial"
    st.session_state.edit_pantry_system = system
    amount = item.get("amount")
    if amount is None or (isinstance(amount, float) and pd.isna(amount)):
        amount, _, _ = parse_quantity(item.get("quantity"))
    st.session_state.edit_pantry_amount = str(amount).strip()
    unit = normalize_unit(item.get("unit") or "")
    choices = units_for_system(system)
    st.session_state.edit_pantry_unit = unit if unit in choices else choices[0]
    st.session_state.show_edit_dialog = True


@st.dialog("Edit pantry item", width="small", on_dismiss=_close_edit_dialog)
def edit_inventory_dialog():
    item = st.session_state.get("edit_item") or {}
    original_name = str(item.get("item") or "")
    st.caption("Update any field except the table index, then save.")
    name = st.text_input("Ingredient Name:", key="edit_pantry_name")
    category = st.selectbox("Category:", PANTRY_CATEGORIES, key="edit_pantry_category")
    unit_system = st.radio("Unit system:", ["Imperial", "Metric"], horizontal=True, key="edit_pantry_system")
    amount_col, unit_col = st.columns(2)
    with amount_col:
        amount = st.text_input("Amount:", key="edit_pantry_amount")
    with unit_col:
        unit_choices = units_for_system(unit_system)
        current_unit = st.session_state.get("edit_pantry_unit")
        if current_unit not in unit_choices:
            st.session_state.edit_pantry_unit = unit_choices[0]
        unit = st.selectbox("Unit:", unit_choices, key="edit_pantry_unit")

    save_col, cancel_col = st.columns(2)
    with save_col:
        if st.button("Save changes", type="primary"):
            cleaned_name = (name or "").strip().title()
            if not cleaned_name:
                st.warning("Ingredient name is required.")
            else:
                try:
                    update_inventory_item(
                        item.get("id"),
                        original_name,
                        cleaned_name,
                        category,
                        (amount or "").strip() or "1",
                        normalize_unit(unit),
                        unit_system,
                    )
                    st.session_state.show_edit_dialog = False
                    st.success(f"Updated '{cleaned_name}'.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not update item: {e}")
    with cancel_col:
        if st.button("Cancel"):
            st.session_state.show_edit_dialog = False
            st.rerun()


def _close_rate_dialog():
    st.session_state.show_rate_dialog = False


def _close_delete_dialog():
    st.session_state.show_delete_dialog = False


def _open_rate_dialog(recipe):
    st.session_state.rating_recipe = dict(recipe)
    st.session_state.pop("favorite_star_feedback", None)
    st.session_state.show_rate_dialog = True


def _open_delete_dialog(recipe):
    st.session_state.delete_recipe = dict(recipe)
    st.session_state.show_delete_dialog = True


@st.dialog("Rate this favorite", width="small", on_dismiss=_close_rate_dialog)
def rate_favorite_dialog():
    recipe = st.session_state.get("rating_recipe") or {}
    title = recipe.get("title") or "this recipe"
    current = _parse_rating(recipe.get("rating"))
    st.write(f"How did **{title}** turn out?")
    if current:
        st.caption(f"Current rating: {format_star_rating(current)}")
    else:
        st.caption("Not rated yet. Choose 1 to 5 stars.")
    stars = st.feedback(
        "stars",
        key="favorite_star_feedback",
        default=(current - 1) if current else None,
    )
    if st.button("Save rating", type="primary"):
        if stars is None:
            st.warning("Choose a star rating first.")
        else:
            try:
                update_recipe_rating(recipe.get("id"), int(stars) + 1)
                st.session_state.show_rate_dialog = False
                st.rerun()
            except Exception as e:
                st.error(f"Could not save rating: {e}")
    if st.button("Cancel"):
        st.session_state.show_rate_dialog = False
        st.rerun()


@st.dialog("Delete favorite?", width="small", on_dismiss=_close_delete_dialog)
def confirm_delete_favorite_dialog():
    recipe = st.session_state.get("delete_recipe") or {}
    title = recipe.get("title") or "this recipe"
    st.write(f"Remove **{title}** from household favorites? This cannot be undone.")
    delete_col, keep_col = st.columns(2)
    with delete_col:
        if st.button("Delete favorite", type="primary"):
            try:
                delete_saved_recipe(recipe.get("id"))
                st.session_state.show_delete_dialog = False
                st.rerun()
            except Exception as e:
                st.error(f"Could not delete favorite: {e}")
    with keep_col:
        if st.button("Keep it"):
            st.session_state.show_delete_dialog = False
            st.rerun()


title_col, help_col = st.columns([0.9, 0.1], vertical_alignment="center")
with title_col:
    st.title("🍳 Automated Household Meal Planner & Pantry Recipe Generator")
with help_col:
    with st.container(key="chef_help_corner", horizontal_alignment="right"):
        if st.button("Help", key="chef_help_btn", help="Open the kitchen help guide"):
            show_help_dialog()

# --- SIDEBAR: SETTINGS & LLM CHOICE ---
st.sidebar.caption("☁️ Connected to Supabase")
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

try:
    appliance_options = load_tag_options("appliance", DEFAULT_APPLIANCES)
    dietary_options = load_tag_options("dietary", DEFAULT_DIETARY)
except Exception as e:
    appliance_options = list(DEFAULT_APPLIANCES)
    dietary_options = list(DEFAULT_DIETARY)
    st.sidebar.warning(f"Custom preference lists could not be loaded: {e}")


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
                PANTRY_CATEGORIES,
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
                try:
                    upsert_inventory_item(
                        new_item.strip().title(),
                        category,
                        amount_value,
                        unit_value,
                        unit_system,
                    )
                    st.success(f"Added '{new_item}' ({quantity}) to pantry!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not save pantry item: {e}")

    with col2:
        st.subheader("Items on Hand")
        try:
            df = fetch_inventory_df()
        except Exception as e:
            df = pd.DataFrame()
            st.error(f"Could not load pantry inventory: {e}")

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
            table = st.dataframe(
                display[["item", "category", "amount", "unit", "system"]],
                width="stretch",
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="pantry_table",
            )
            st.caption("Click a row to edit that item. The row number is not editable.")

            selected_rows = []
            selection = getattr(table, "selection", None)
            if isinstance(selection, dict):
                selected_rows = list(selection.get("rows") or [])
            elif selection is not None:
                selected_rows = list(getattr(selection, "rows", []) or [])

            if selected_rows:
                selected_item = display.iloc[int(selected_rows[0])].to_dict()
                selection_token = f"{selected_rows[0]}:{selected_item.get('id')}:{selected_item.get('item')}"
                if st.session_state.get("pantry_selection_token") != selection_token:
                    st.session_state.pantry_selection_token = selection_token
                    _open_edit_dialog(selected_item)

            selected_name = st.selectbox(
                "Selected item:",
                df["item"].tolist(),
                key="pantry_selected_name",
            )
            edit_col, delete_col = st.columns(2)
            with edit_col:
                if st.button("Edit selected item"):
                    match = display[display["item"] == selected_name]
                    if not match.empty:
                        _open_edit_dialog(match.iloc[0].to_dict())
            with delete_col:
                if st.button("Delete selected item"):
                    try:
                        delete_inventory_item(selected_name)
                        st.warning(f"Removed '{selected_name}'.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not remove item: {e}")

            if st.session_state.get("show_edit_dialog"):
                edit_inventory_dialog()
        else:
            st.info("Your pantry is currently empty. Add some items on the left!")

# --- TAB 2: RECIPE GENERATOR ---
with tab2:
    st.header("Generate Meal Plan & Recipes")

    try:
        df_pantry = fetch_inventory_df()
        if not df_pantry.empty:
            df_pantry = df_pantry.sort_values("item")
    except Exception as e:
        df_pantry = pd.DataFrame()
        st.error(f"Could not load pantry ingredients: {e}")

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

Generate 2 distinct recipe suggestions.
Return ONLY valid JSON (no markdown fences) as an array of objects with keys:
title, prep_time, cook_time, ingredients (array of strings), instructions (array of step strings).
In ingredients, note which items came from the pantry list versus staples like oil/salt.
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
                        recipes = parse_generated_recipes(recipe_result)
                        st.session_state["last_recipe"] = recipe_result
                        st.session_state["generated_recipes"] = recipes
                        st.session_state["last_recipe_ingredients"] = ", ".join(selected_pantry)
                        for key in list(st.session_state.keys()):
                            if str(key).startswith("save_pick_"):
                                del st.session_state[key]

                except Exception as e:
                    st.error(f"Error generating recipe: {str(e)}")

    generated_recipes = st.session_state.get("generated_recipes") or []
    if generated_recipes:
        st.markdown("### 🍽️ Generated Recipes")
        if len(generated_recipes) > 1:
            st.caption("Select the recipes you want to keep. Saving all stores each one as its own favorite.")

        selected_to_save = []
        for index, recipe in enumerate(generated_recipes):
            title = recipe.get("title") or f"Generated Recipe {index + 1}"
            markdown = recipe_to_markdown(recipe)
            with st.expander(title, expanded=True):
                st.markdown(markdown)
                chosen = st.checkbox(
                    f"Select “{title}” to save",
                    value=True,
                    key=f"save_pick_{index}",
                )
            if chosen:
                selected_to_save.append(recipe)

        combined_markdown = "\n\n---\n\n".join(recipe_to_markdown(recipe) for recipe in generated_recipes)
        st.download_button(
            "📥 Download all as Markdown",
            data=combined_markdown,
            file_name="recipes.md",
            mime="text/markdown",
        )

        save_col, save_all_col = st.columns(2)
        pantry_used = st.session_state.get("last_recipe_ingredients", "")

        def _save_recipe_list(recipes_to_store):
            if not recipes_to_store:
                st.warning("Select at least one recipe to save.")
                return
            saved_titles = []
            for recipe in recipes_to_store:
                title = recipe.get("title") or "Generated Recipe"
                save_favorite_recipe(title, pantry_used, recipe_to_markdown(recipe))
                saved_titles.append(title)
            st.success(
                "Saved "
                + (", ".join(f"“{name}”" for name in saved_titles))
                + " as separate favorites."
            )
            st.rerun()

        with save_col:
            if st.button("⭐ Save selected", type="primary"):
                try:
                    _save_recipe_list(selected_to_save)
                except Exception as e:
                    st.error(f"Could not save recipe: {e}")
        with save_all_col:
            if len(generated_recipes) > 1:
                if st.button("⭐ Save all separately"):
                    try:
                        _save_recipe_list(generated_recipes)
                    except Exception as e:
                        st.error(f"Could not save recipes: {e}")
            else:
                st.caption("Only one recipe was generated.")
    elif st.session_state.get("last_recipe"):
        st.markdown("### 🍽️ Generated Recipes")
        st.markdown(st.session_state["last_recipe"])

# --- TAB 3: SAVED RECIPES ---
with tab3:
    st.header("Saved Household Favorites")
    st.info("Archive recipes generated from your pantry so you can cook them again later.")

    try:
        df_recipes = fetch_saved_recipes_df()
    except Exception as e:
        df_recipes = pd.DataFrame()
        st.error(f"Could not load saved recipes: {e}")

    if df_recipes.empty:
        st.caption("No saved recipes yet. Generate a recipe, then click Save to Household Favorites.")
    else:
        sort_col, order_col = st.columns(2)
        with sort_col:
            sort_by = st.radio(
                "Sort favorites by:",
                ["Rating", "Name"],
                horizontal=True,
                key="favorite_sort",
            )
        with order_col:
            sort_order = st.radio(
                "Order:",
                ["Descending", "Ascending"],
                horizontal=True,
                key="favorite_sort_order",
            )
        df_recipes = sort_saved_recipes(df_recipes, sort_by, sort_order)
        for _, row in df_recipes.iterrows():
            recipe = row.to_dict()
            heading = f"{recipe['title']}  ·  {format_star_rating(recipe.get('rating'))}"
            with st.expander(heading):
                recipe_col, rating_col = st.columns([0.82, 0.18], vertical_alignment="top")
                with recipe_col:
                    if recipe.get("ingredients"):
                        st.caption(f"Pantry ingredients used: {recipe['ingredients']}")
                    st.markdown(recipe["instructions"])
                    rate_col, delete_col = st.columns(2)
                    with rate_col:
                        if st.button("Rate favorite", key=f"rate_recipe_{recipe['id']}"):
                            _open_rate_dialog(recipe)
                    with delete_col:
                        if st.button("Delete favorite", key=f"delete_recipe_{recipe['id']}"):
                            _open_delete_dialog(recipe)
                with rating_col:
                    st.markdown(render_star_rating(recipe.get("rating")), unsafe_allow_html=True)
                    st.caption("Rating")

        if st.session_state.get("show_rate_dialog"):
            rate_favorite_dialog()
        if st.session_state.get("show_delete_dialog"):
            confirm_delete_favorite_dialog()
