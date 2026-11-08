import html
import io
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import streamlit as st
import pandas as pd
import requests
from fpdf import FPDF
from google import genai
from google.genai import types
from supabase import create_client, Client

APP_VERSION = "1.7.2"

DEFAULT_APPLIANCES = [
    "Air Fryer",
    "Blender",
    "Bread Maker",
    "Deep Fryer",
    "Food Processor",
    "Griddle / Cast Iron",
    "Grill / BBQ",
    "Induction Cooktop",
    "Instant Pot / Pressure Cooker",
    "Iron Skillet",
    "Microwave",
    "Oven",
    "Rice Cooker",
    "Slow Cooker",
    "Smoker - Pellet",
    "Smoker - Wood",
    "Sous Vide",
    "Stovetop",
    "Toaster Oven",
    "Waffle Iron",
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
    "Baking & Cooking Staples",
    "Breakfast & Cereals",
    "Beverages & Coffee",
    "Dairy & Refrigerated",
    "Frozen",
    "Grains & Pastas",
    "Meat & Seafood",
    "Oils, Vinegars & Condiments",
    "Pantry Staples",
    "Produce",
    "Spices",
   ]

MEAL_TYPES = [
    "Breakfast",
    "Brunch",
    "Lunch",
    "Dinner",
    "Snack",
    "Appetizer",
    "Side dish",
    "Soup / stew",
    "Salad",
    "Dessert",
    "Meal prep",
]

# Keep generated JSON short so Gemini / Ollama finish faster.
RECIPE_MAX_OUTPUT_TOKENS = 2560
RECIPE_SUGGESTION_COUNT = 2
RECIPE_MAX_STEPS = 8

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
    units = IMPERIAL_UNITS if system == "Imperial" else METRIC_UNITS
    return sorted(units, key=str.casefold)


def default_unit_for_system(system):
    return "oz" if system == "Imperial" else "g"


def add_item_unit_key(system):
    return f"add_item_unit_{system or 'Imperial'}"


def sync_unit_widget(key, system):
    choices = units_for_system(system)
    default_unit = default_unit_for_system(system)
    if st.session_state.get(key) not in choices:
        st.session_state[key] = default_unit if default_unit in choices else choices[0]


def _on_pref_unit_system_change():
    system = st.session_state.get("pref_unit_system") or "Imperial"
    st.session_state.item_unit_system = system
    sync_unit_widget(add_item_unit_key(system), system)


def _on_item_unit_system_change():
    system = st.session_state.get("item_unit_system") or "Imperial"
    sync_unit_widget(add_item_unit_key(system), system)


def _on_edit_unit_system_change():
    sync_unit_widget("edit_pantry_unit", st.session_state.get("edit_pantry_system") or "Imperial")


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


BARCODE_CATEGORY_HINTS = (
    ("meat", "Meat & Seafood"),
    ("seafood", "Meat & Seafood"),
    ("fish", "Meat & Seafood"),
    ("poultry", "Meat & Seafood"),
    ("dairy", "Dairy & Refrigerated"),
    ("cheese", "Dairy & Refrigerated"),
    ("yogurt", "Dairy & Refrigerated"),
    ("milk", "Dairy & Refrigerated"),
    ("egg", "Dairy & Refrigerated"),
    ("frozen", "Frozen"),
    ("spice", "Spices"),
    ("seasoning", "Spices"),
    ("oil", "Oils, Vinegars & Condiments"),
    ("vinegar", "Oils, Vinegars & Condiments"),
    ("sauce", "Oils, Vinegars & Condiments"),
    ("condiment", "Oils, Vinegars & Condiments"),
    ("cereal", "Breakfast & Cereals"),
    ("breakfast", "Breakfast & Cereals"),
    ("coffee", "Beverages & Coffee"),
    ("tea", "Beverages & Coffee"),
    ("beverage", "Beverages & Coffee"),
    ("drink", "Beverages & Coffee"),
    ("pasta", "Grains & Pastas"),
    ("rice", "Grains & Pastas"),
    ("grain", "Grains & Pastas"),
    ("flour", "Baking & Cooking Staples"),
    ("sugar", "Baking & Cooking Staples"),
    ("baking", "Baking & Cooking Staples"),
    ("fruit", "Produce"),
    ("vegetable", "Produce"),
    ("produce", "Produce"),
)


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


def _normalize_barcode_digits(text):
    digits = re.sub(r"\D", "", str(text or ""))
    if len(digits) in (8, 12, 13, 14):
        return digits
    return ""


def pick_product_barcode(texts):
    found = []
    for text in texts or []:
        digits = _normalize_barcode_digits(text)
        if digits:
            found.append(digits)
        else:
            match = re.search(r"\b(\d{8}|\d{12}|\d{13}|\d{14})\b", str(text or ""))
            if match:
                found.append(match.group(1))
    if not found:
        return ""
    return max(found, key=len)


def guess_pantry_category(text):
    haystack = (text or "").casefold()
    for needle, category in BARCODE_CATEGORY_HINTS:
        if needle in haystack and category in PANTRY_CATEGORIES:
            return category
    return PANTRY_CATEGORIES[0]


def parse_off_quantity(text, preferred_system):
    cleaned = re.sub(r"\be\b", " ", str(text or ""), flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        default_unit = default_unit_for_system(preferred_system)
        return "1", default_unit, preferred_system
    amount, unit, system = parse_quantity(cleaned)
    unit = normalize_unit(unit.split()[0] if unit else "")
    choices = units_for_system(system)
    if unit not in choices:
        unit = default_unit_for_system(system)
        if unit not in choices:
            unit = choices[0]
    return amount, unit, system


def _register_heif_opener():
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except Exception:
        pass


def _barcode_image_from_bytes(image_bytes):
    from PIL import Image, ImageFile, ImageOps

    ImageFile.LOAD_TRUNCATED_IMAGES = True
    _register_heif_opener()
    image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    width, height = image.size
    long_edge = max(width, height)
    if long_edge < 1200:
        scale = 1600 / long_edge
        image = image.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
    elif long_edge > 2200:
        image.thumbnail((2200, 2200))
    return image


def _jpeg_bytes_for_lookup(image_bytes):
    try:
        from PIL import Image

        image = _barcode_image_from_bytes(image_bytes)
        if image.mode != "RGB":
            image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()
    except Exception:
        return image_bytes


def decode_barcodes_from_image_bytes(image_bytes):
    try:
        from PIL import ImageEnhance, ImageFilter, ImageOps
        import zxingcpp
    except ImportError:
        return []
    try:
        image = _barcode_image_from_bytes(image_bytes)
    except Exception:
        return []
    gray = ImageOps.autocontrast(image.convert("L"))
    sharp = gray.filter(ImageFilter.UnsharpMask(radius=1.5, percent=160, threshold=2))
    contrast = ImageEnhance.Contrast(gray).enhance(2.2)
    frames = [gray, sharp, contrast]
    formats = getattr(zxingcpp, "AllRetail", None) or getattr(zxingcpp, "EANUPC", None)
    binarizers = [
        getattr(zxingcpp, "LocalAverage", None),
        getattr(zxingcpp, "GlobalHistogram", None),
        getattr(zxingcpp, "FixedThreshold", None),
    ]
    binarizers = [item for item in binarizers if item is not None] or [None]
    seen = []
    for frame in frames:
        for binarizer in binarizers:
            options = {
                "try_rotate": True,
                "try_downscale": True,
                "try_invert": True,
            }
            if formats is not None:
                options["formats"] = formats
            if binarizer is not None:
                options["binarizer"] = binarizer
            try:
                results = zxingcpp.read_barcodes(frame, **options) or []
            except Exception:
                continue
            for result in results:
                text = str(getattr(result, "text", "") or "").strip()
                if text and text not in seen:
                    seen.append(text)
            if seen:
                return seen
    return seen


def extract_barcode_with_gemini(image_bytes, mime_type, gemini_key):
    if not gemini_key:
        return ""
    payload_bytes = _jpeg_bytes_for_lookup(image_bytes)
    client = genai.Client(api_key=gemini_key)
    try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=[
                types.Part.from_bytes(data=payload_bytes, mime_type="image/jpeg"),
                'Read the UPC or EAN barcode number on this package. JSON only: {"barcode":"digits"}. If none, {"barcode":""}.',
            ],
            config=types.GenerateContentConfig(
                max_output_tokens=128,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
            ),
        )
    except Exception:
        return ""
    payload = (response.text or "").strip()
    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            return pick_product_barcode([data.get("barcode") or data.get("upc") or ""])
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return pick_product_barcode([payload])


def _barcode_request_headers():
    return {"User-Agent": f"PantryChef/{APP_VERSION} (household meal planner)", "Accept": "application/json"}


def _barcode_lookup_codes(barcode):
    code = pick_product_barcode([barcode])
    if not code:
        return []
    codes = [code]
    if len(code) == 12:
        codes.append("0" + code)
    elif len(code) == 13 and code.startswith("0") and code[1:] not in codes:
        codes.append(code[1:])
    return codes


def _http_get_json(url, headers, not_found_ok=False):
    response = None
    last_error = None
    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, timeout=12)
            if not_found_ok and response.status_code == 404:
                return None
            if response.status_code in (429, 502, 503, 504):
                last_error = requests.HTTPError(
                    f"{response.status_code} Server Error: {response.reason} for url: {url}",
                    response=response,
                )
                time.sleep(0.7 * (attempt + 1))
                continue
            response.raise_for_status()
            break
        except (requests.Timeout, requests.ConnectionError) as error:
            last_error = error
            time.sleep(0.7 * (attempt + 1))
    else:
        if last_error:
            raise last_error
    if response is None:
        raise last_error or RuntimeError("Barcode lookup did not return a response.")
    return response.json() or {}


def lookup_open_food_facts(barcode):
    codes = _barcode_lookup_codes(barcode)
    if not codes:
        return None
    headers = _barcode_request_headers()
    empty = {
        "barcode": codes[0],
        "name": "",
        "category": PANTRY_CATEGORIES[0],
        "quantity": "",
        "source": "Open Food Facts",
    }
    for code in codes:
        data = _http_get_json(
            f"https://world.openfoodfacts.org/api/v2/product/{code}.json",
            headers,
            not_found_ok=True,
        )
        if not data:
            continue
        product = data.get("product") if data.get("status") == 1 else None
        if not product:
            continue
        name = (
            product.get("product_name")
            or product.get("product_name_en")
            or product.get("generic_name")
            or product.get("brands")
            or ""
        )
        name = " ".join(str(name).split()).strip()
        if not name:
            continue
        hint = " ".join(
            part
            for part in (
                name,
                product.get("generic_name") or "",
                product.get("categories") or "",
                " ".join(product.get("categories_tags") or []),
            )
            if part
        )
        return {
            "barcode": codes[0],
            "name": name,
            "category": guess_pantry_category(hint),
            "quantity": product.get("quantity") or product.get("product_quantity") or "",
            "source": "Open Food Facts",
        }
    return empty


def lookup_upcitemdb(barcode):
    codes = _barcode_lookup_codes(barcode)
    if not codes:
        return None
    headers = _barcode_request_headers()
    data = _http_get_json(
        f"https://api.upcitemdb.com/prod/trial/lookup?upc={codes[0]}",
        headers,
        not_found_ok=True,
    )
    items = (data or {}).get("items") or []
    if not items:
        return None
    item = items[0] or {}
    name = " ".join(str(item.get("title") or item.get("brand") or "").split()).strip()
    if not name:
        return None
    hint = " ".join(
        part
        for part in (name, item.get("brand") or "", item.get("category") or "", item.get("description") or "")
        if part
    )
    quantity = item.get("size") or item.get("weight") or ""
    return {
        "barcode": codes[0],
        "name": name,
        "category": guess_pantry_category(hint),
        "quantity": quantity,
        "source": "UPCitemdb",
    }


def lookup_barcode_product(barcode):
    off_error = None
    try:
        product = lookup_open_food_facts(barcode)
        if product and product.get("name"):
            return product
    except Exception as error:
        off_error = error
        product = None
    try:
        upcitem = lookup_upcitemdb(barcode)
        if upcitem and upcitem.get("name"):
            return upcitem
    except Exception:
        if off_error:
            raise off_error
        raise
    if product is not None:
        return product
    if off_error:
        raise off_error
    return None


def apply_barcode_product_to_form(product, preferred_system):
    product = product or {}
    name = " ".join(str(product.get("name") or "").split()).strip()
    if name:
        st.session_state.add_item_name = name.title()
    category = product.get("category")
    if category in PANTRY_CATEGORIES:
        st.session_state.add_item_category = category
    amount, unit, system = parse_off_quantity(product.get("quantity"), preferred_system)
    if system in ("Imperial", "Metric"):
        st.session_state.item_unit_system = system
    st.session_state.add_item_amount = str(amount)
    choices = units_for_system(system)
    fallback = default_unit_for_system(system)
    chosen = unit if unit in choices else (fallback if fallback in choices else choices[0])
    st.session_state.add_item_unit = chosen
    st.session_state[add_item_unit_key(system)] = chosen


NUTRITION_DISCLAIMER = (
    "Nutrition values and diet labels are estimates only, not lab analysis or medical advice."
)

NUTRITION_FIELDS = [
    ("calories", "Calories", ("calories", "kcal", "calorie")),
    ("protein", "Protein", ("protein",)),
    ("fat", "Fat", ("fat", "total_fat", "totalFat")),
    ("saturated_fat", "Sat. fat", ("saturated_fat", "sat_fat", "saturatedFat")),
    ("carbs", "Carbs", ("carbs", "carbohydrates", "carbohydrate")),
    ("fiber", "Fiber", ("fiber", "fibre")),
    ("sugar", "Sugar", ("sugar", "sugars")),
    ("sodium", "Sodium", ("sodium", "na")),
]


def _nutrition_from_mapping(raw):
    if not isinstance(raw, dict):
        return {}
    nutrition = {}
    for key, _label, aliases in NUTRITION_FIELDS:
        for alias in aliases:
            value = raw.get(alias)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                nutrition[key] = text
                break
    return nutrition


def extract_diet_tags(recipe):
    recipe = recipe or {}
    tags = (
        recipe.get("diet_tags")
        or recipe.get("dietary_tags")
        or recipe.get("diet")
        or recipe.get("dietary_category")
        or []
    )
    if isinstance(tags, str):
        tags = [part.strip() for part in re.split(r"[,;/|]", tags) if part.strip()]
    cleaned = []
    seen = set()
    for tag in _as_text_list(tags):
        key = tag.casefold()
        if key in seen or key in {"none", "n/a"}:
            continue
        seen.add(key)
        cleaned.append(tag)
        if len(cleaned) == 4:
            break
    return cleaned


def extract_nutrition(recipe):
    recipe = recipe or {}
    nutrition = _nutrition_from_mapping(recipe.get("nutrition") or recipe.get("nutrition_per_serving"))
    if not nutrition:
        leftover = {}
        for key, _label, aliases in NUTRITION_FIELDS:
            for alias in aliases:
                if recipe.get(alias) not in (None, ""):
                    leftover[alias] = recipe.get(alias)
        nutrition = _nutrition_from_mapping(leftover)
    return nutrition


def nutrition_summary(nutrition):
    parts = []
    for key, label, _aliases in NUTRITION_FIELDS:
        value = (nutrition or {}).get(key)
        if value:
            parts.append(f"{label} {value}")
    return " · ".join(parts)


def _merge_nutrition_line(line, target):
    clean = (line or "").replace("**", "")
    for key, label, aliases in NUTRITION_FIELDS:
        names = (label, key) + aliases
        for name in names:
            match = re.search(re.escape(name) + r"\s*:?\s*([^·|*]+)", clean, re.I)
            if match:
                value = match.group(1).strip(" ·|-")
                if value:
                    target[key] = value
                break


def recipe_to_markdown(recipe):
    if recipe.get("markdown"):
        return recipe["markdown"]
    lines = [f"## {recipe.get('title') or 'Recipe'}"]
    prep = recipe.get("prep_time")
    cook = recipe.get("cook_time")
    if prep or cook:
        lines.append(f"**Prep:** {prep or '—'}  ·  **Cook:** {cook or '—'}")
    diet_tags = extract_diet_tags(recipe)
    if diet_tags:
        lines.append("### Diet")
        lines.append(", ".join(diet_tags))
    nutrition = extract_nutrition(recipe)
    if nutrition:
        lines.append("### Nutrition (per serving, estimated)")
        lines.append("**" + nutrition_summary(nutrition).replace(" · ", "**  ·  **") + "**")
    lines.append(f"*{NUTRITION_DISCLAIMER}*")
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


def _as_text_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        items = []
        for item in value:
            text = " ".join(str(item or "").split())
            if text:
                items.append(text)
        return items
    text = str(value).strip()
    if not text:
        return []
    return [" ".join(line.strip(" -•*\t").split()) for line in text.splitlines() if line.strip()]


def _optional_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def recipe_parts(recipe):
    recipe = recipe or {}
    prep = str(recipe.get("prep_time") or recipe.get("prepTime") or "").strip()
    cook = str(recipe.get("cook_time") or recipe.get("cookTime") or "").strip()
    ingredients = _as_text_list(recipe.get("ingredients"))
    instructions = _as_text_list(recipe.get("instructions") or recipe.get("steps"))
    nutrition = extract_nutrition(recipe)
    diet_tags = extract_diet_tags(recipe)
    markdown = (recipe.get("markdown") or "").strip()
    if markdown and (not ingredients or not instructions or not nutrition or not diet_tags):
        parsed = parse_recipe_markdown(markdown)
        ingredients = ingredients or parsed["ingredients"]
        instructions = instructions or parsed["instructions"]
        nutrition = nutrition or parsed.get("nutrition") or {}
        diet_tags = diet_tags or parsed.get("diet_tags") or []
        prep = prep or parsed.get("prep") or ""
        cook = cook or parsed.get("cook") or ""
    return {
        "title": (recipe.get("title") or "Recipe").strip() or "Recipe",
        "prep": prep,
        "cook": cook,
        "ingredients": ingredients,
        "instructions": instructions,
        "markdown": markdown,
        "pantry": _optional_text(recipe.get("pantry")),
        "note": _optional_text(recipe.get("note") or recipe.get("comment")),
        "nutrition": nutrition,
        "diet_tags": diet_tags,
    }


def recipe_to_html(recipe, show_title=True):
    parts = recipe_parts(recipe)
    meta = []
    if parts["prep"]:
        meta.append(f"Prep {html.escape(parts['prep'])}")
    if parts["cook"]:
        meta.append(f"Cook {html.escape(parts['cook'])}")
    meta_html = f'<p class="recipe-meta">{" · ".join(meta)}</p>' if meta else ""
    diet_html = ""
    diet_tags = parts.get("diet_tags") or []
    if diet_tags:
        pills = "".join(f"<span>{html.escape(tag)}</span>" for tag in diet_tags)
        diet_html = f'<div class="recipe-diet"><strong>Leans toward</strong> {pills}</div>'
    nutrition_html = ""
    nutrition = parts.get("nutrition") or {}
    chips = "".join(
        f"<span><strong>{html.escape(label)}</strong> {html.escape(str(nutrition[key]))}</span>"
        for key, label, _aliases in NUTRITION_FIELDS
        if nutrition.get(key)
    )
    if chips:
        nutrition_html = f'<div class="recipe-nutrition">{chips}</div>'
    disclaimer_html = f'<p class="recipe-nutrition-note">{html.escape(NUTRITION_DISCLAIMER)}</p>'
    columns = []
    if parts["ingredients"]:
        items = "".join(f"<li>{html.escape(item)}</li>" for item in parts["ingredients"])
        columns.append(f'<div class="recipe-col"><h3>Ingredients</h3><ul>{items}</ul></div>')
    if parts["instructions"]:
        steps = "".join(f"<li>{html.escape(step)}</li>" for step in parts["instructions"])
        columns.append(f'<div class="recipe-col"><h3>Method</h3><ol>{steps}</ol></div>')
    grid = f'<div class="recipe-grid">{"".join(columns)}</div>' if columns else ""
    title_html = f'<h2>{html.escape(parts["title"])}</h2>' if show_title else ""
    return (
        f'<article class="recipe-sheet">'
        f"{title_html}{meta_html}{diet_html}{nutrition_html}{disclaimer_html}{grid}"
        f"</article>"
    )


_APP_DIR = Path(__file__).resolve().parent
_PDF_ASCII = str.maketrans(
    {
        "•": "-",
        "·": "-",
        "—": "-",
        "–": "-",
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "…": "...",
        "½": "1/2",
        "¼": "1/4",
        "¾": "3/4",
        "°": " deg",
    }
)


def _kitchen_fonts():
    bundled = _APP_DIR / "fonts"
    candidates = [
        (bundled / "DejaVuSans.ttf", bundled / "DejaVuSans-Bold.ttf"),
        (
            Path("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf"),
            Path("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
    ]
    for regular, bold in candidates:
        if regular.is_file() and bold.is_file():
            return str(regular), str(bold)
    return None, None


def _pdf_text(text, unicode_ok=True):
    value = str(text or "")
    if unicode_ok:
        return value
    return value.translate(_PDF_ASCII).encode("latin-1", "replace").decode("latin-1")


def _plain_from_markdown(text):
    lines = []
    for line in (text or "").splitlines():
        stripped = re.sub(r"^#{1,6}\s*", "", line.strip())
        stripped = stripped.replace("**", "")
        lines.append(stripped)
    return "\n".join(lines).strip()


class KitchenRecipePDF(FPDF):
    def __init__(self, font_family, unicode_ok=True):
        super().__init__(format="Letter")
        self.font_family_name = font_family
        self.unicode_ok = unicode_ok
        self.set_auto_page_break(auto=True, margin=22)
        self.set_margins(18, 26, 18)

    def header(self):
        self.set_fill_color(255, 250, 243)
        self.rect(0, 0, self.w, self.h, "F")
        self.set_fill_color(196, 92, 38)
        self.rect(0, 0, self.w, 16, "F")
        self.set_text_color(255, 255, 255)
        self.set_font(self.font_family_name, "B", 11)
        self.set_xy(18, 4.5)
        self.cell(0, 7, _pdf_text("Pantry Chef  ·  Household kitchen copy", self.unicode_ok))
        self.set_fill_color(232, 214, 196)
        self.rect(0, 16, self.w, 1.2, "F")
        self.set_xy(self.l_margin, 24)
        self.set_text_color(61, 43, 31)

    def footer(self):
        self.set_y(-16)
        self.set_draw_color(232, 214, 196)
        self.set_line_width(0.4)
        self.line(18, self.get_y(), self.w - 18, self.get_y())
        self.set_font(self.font_family_name, "", 8)
        self.set_text_color(138, 112, 96)
        self.cell(
            0,
            10,
            _pdf_text(f"Pretty-print recipe card   ·   Page {self.page_no()}", self.unicode_ok),
            align="C",
        )


def _write_pdf_section_label(pdf, label):
    pdf.ln(4)
    pdf.set_x(pdf.l_margin)
    pdf.set_font(pdf.font_family_name, "B", 10)
    pdf.set_text_color(196, 92, 38)
    pdf.cell(0, 6, label.upper())
    pdf.ln(2)
    y = pdf.get_y()
    pdf.set_draw_color(232, 214, 196)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(4)
    pdf.set_xy(pdf.l_margin, pdf.get_y())
    pdf.set_text_color(61, 43, 31)
    pdf.set_font(pdf.font_family_name, "", 11)


def _write_pdf_hanging_line(pdf, marker, text, marker_w, line_h, unicode_ok):
    pdf.set_x(pdf.l_margin)
    family = pdf.font_family_name
    pdf.set_font(family, "B", 11)
    pdf.set_text_color(196, 92, 38)
    pdf.cell(marker_w, line_h, _pdf_text(marker, unicode_ok))
    pdf.set_font(family, "", 11)
    pdf.set_text_color(61, 43, 31)
    remain = max(pdf.w - pdf.r_margin - pdf.get_x(), 20)
    pdf.multi_cell(remain, line_h, _pdf_text(text, unicode_ok))
    pdf.set_x(pdf.l_margin)


def build_recipes_pdf(recipes):
    regular, bold = _kitchen_fonts()
    unicode_ok = bool(regular)
    family = "Kitchen" if unicode_ok else "Helvetica"
    pdf = KitchenRecipePDF(family, unicode_ok=unicode_ok)
    if unicode_ok:
        pdf.add_font(family, "", regular)
        pdf.add_font(family, "B", bold)
    bullet = "•" if unicode_ok else "-"

    for recipe in recipes or []:
        pdf.add_page()
        parts = recipe_parts(recipe)
        pdf.set_xy(pdf.l_margin, pdf.get_y())
        pdf.set_text_color(61, 43, 31)
        pdf.set_font(family, "B", 20)
        pdf.multi_cell(0, 9, _pdf_text(parts["title"], unicode_ok))
        pdf.ln(1)
        meta = []
        if parts["prep"]:
            meta.append(f"Prep {parts['prep']}")
        if parts["cook"]:
            meta.append(f"Cook {parts['cook']}")
        if meta:
            pdf.set_font(family, "B", 11)
            pdf.set_text_color(196, 92, 38)
            pdf.multi_cell(0, 7, _pdf_text("  ·  ".join(meta), unicode_ok))
            pdf.set_text_color(61, 43, 31)
        nutrition_line = nutrition_summary(parts.get("nutrition"))
        if nutrition_line:
            pdf.set_x(pdf.l_margin)
            pdf.set_font(family, "", 9)
            pdf.set_text_color(61, 43, 31)
            pdf.multi_cell(0, 5, _pdf_text(nutrition_line, unicode_ok))
            pdf.set_text_color(61, 43, 31)
        diet_tags = parts.get("diet_tags") or []
        if diet_tags:
            pdf.set_x(pdf.l_margin)
            pdf.set_font(family, "B", 9)
            pdf.set_text_color(196, 92, 38)
            pdf.multi_cell(0, 5, _pdf_text("Leans toward: " + " · ".join(diet_tags), unicode_ok))
            pdf.set_text_color(61, 43, 31)
        pdf.set_x(pdf.l_margin)
        pdf.set_font(family, "", 8)
        pdf.set_text_color(138, 112, 96)
        pdf.multi_cell(0, 4, _pdf_text(NUTRITION_DISCLAIMER, unicode_ok))
        pdf.set_text_color(61, 43, 31)
        if parts.get("pantry"):
            pdf.set_x(pdf.l_margin)
            pdf.set_font(family, "", 9)
            pdf.set_text_color(138, 112, 96)
            pdf.multi_cell(0, 5, _pdf_text(f"Pantry used: {parts['pantry']}", unicode_ok))
            pdf.set_text_color(61, 43, 31)

        if parts["ingredients"]:
            _write_pdf_section_label(pdf, "Ingredients")
            for item in parts["ingredients"]:
                _write_pdf_hanging_line(pdf, bullet, item, 6, 6, unicode_ok)
                pdf.ln(1)

        if parts["instructions"]:
            _write_pdf_section_label(pdf, "Method")
            for index, step in enumerate(parts["instructions"], 1):
                _write_pdf_hanging_line(pdf, f"{index}.", step, 10, 6, unicode_ok)
                pdf.ln(2)

        if not parts["ingredients"] and not parts["instructions"] and parts["markdown"]:
            _write_pdf_section_label(pdf, "Recipe")
            pdf.multi_cell(0, 6, _pdf_text(_plain_from_markdown(parts["markdown"]), unicode_ok))

        if parts.get("note"):
            _write_pdf_section_label(pdf, "Kitchen note")
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 6, _pdf_text(parts["note"], unicode_ok))

    return bytes(pdf.output())


def recipes_pdf_filename(recipes):
    if len(recipes) == 1:
        slug = re.sub(r"[^a-z0-9]+", "-", (recipes[0].get("title") or "recipe").lower()).strip("-")
        return f"{(slug or 'recipe')[:48]}.pdf"
    return "pantry-recipes.pdf"


def parse_recipe_markdown(text):
    raw = (text or "").strip()
    parsed = {
        "title": "",
        "prep": "",
        "cook": "",
        "ingredients": [],
        "instructions": [],
        "nutrition": {},
        "diet_tags": [],
        "markdown": raw,
    }
    if not raw:
        return parsed
    section = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.match(r"^#{1,6}\s+(.*)$", stripped)
        if heading:
            label = heading.group(1).strip()
            key = re.sub(r"[^a-z]+", "", label.lower())
            if key.startswith("ingredient"):
                section = "ingredients"
            elif key in ("instructions", "method", "steps", "directions"):
                section = "instructions"
            elif key.startswith("nutrition"):
                section = "nutrition"
            elif key in ("diet", "diets", "dietary", "diettags") or key.startswith("diet"):
                section = "diet"
            elif not parsed["title"]:
                parsed["title"] = label
                section = None
            continue
        meta_line = stripped.replace("**", "")
        if "estimates only" in meta_line.casefold() or "not lab" in meta_line.casefold():
            continue
        if section == "diet":
            parsed["diet_tags"] = extract_diet_tags({"diet_tags": meta_line})
            section = None
            continue
        if re.search(r"calories\s*:?", meta_line, re.I) or section == "nutrition":
            _merge_nutrition_line(meta_line, parsed["nutrition"])
            if section == "nutrition" or re.search(r"calories\s*:?", meta_line, re.I):
                continue
        if re.search(r"prep\s*:", meta_line, re.I) or re.search(r"cook\s*:", meta_line, re.I):
            prep = re.search(r"prep\s*:\s*([^·|\n]+)", meta_line, re.I)
            cook = re.search(r"cook\s*:\s*([^·|\n]+)", meta_line, re.I)
            if prep:
                parsed["prep"] = prep.group(1).strip(" ·|-")
            if cook:
                parsed["cook"] = cook.group(1).strip(" ·|-")
            continue
        item = re.sub(r"^[-*•]\s+", "", stripped)
        item = re.sub(r"^\d+[.)]\s+", "", item)
        if section == "ingredients":
            parsed["ingredients"].append(item)
        elif section == "instructions":
            parsed["instructions"].append(item)
    return parsed


def favorite_for_pdf(recipe):
    recipe = recipe or {}
    parsed = parse_recipe_markdown(recipe.get("instructions") or recipe.get("markdown") or "")
    structured = bool(parsed["ingredients"] or parsed["instructions"])
    return {
        "title": _optional_text(recipe.get("title")) or parsed["title"] or "Recipe",
        "prep_time": parsed["prep"],
        "cook_time": parsed["cook"],
        "ingredients": parsed["ingredients"],
        "instructions": parsed["instructions"],
        "markdown": "" if structured else parsed["markdown"],
        "pantry": _optional_text(recipe.get("ingredients")),
        "note": _optional_text(recipe.get("comment") or recipe.get("note")),
        "nutrition": parsed.get("nutrition") or {},
        "diet_tags": parsed.get("diet_tags") or [],
    }


_GROCERY_STOP_TOKENS = {
    "and",
    "the",
    "for",
    "with",
    "from",
    "oil",
    "salt",
    "pepper",
    "water",
    "fresh",
    "dried",
    "ground",
    "sauce",
    "optional",
}


def recipe_for_grocery(recipe):
    ingredients = _as_text_list((recipe or {}).get("ingredients"))
    if ingredients and isinstance((recipe or {}).get("ingredients"), list):
        return recipe
    converted = favorite_for_pdf(recipe)
    if converted.get("ingredients"):
        return converted
    return recipe or {}


def _ingredient_core(line):
    text = _optional_text(line)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(
        r"^[\d¼½¾./\s-]+(?:fl\s*oz|tsp|tbsp|tbsp|oz|lb|lbs|g|kg|ml|l|cup|cups|can|cans|"
        r"pack|each|clove|slice|pinch|dash|bunch|jar|bag)?s?\b\s*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\b(diced|sliced|chopped|grated|minced|fresh|dried|optional|divided|to taste|"
        r"for serving|plus more|staple|pantry)\b",
        " ",
        text,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", text).strip(" ,.-")


def _name_tokens(value):
    return [
        token
        for token in re.split(r"[^a-z0-9]+", (value or "").casefold())
        if len(token) > 2 and token not in _GROCERY_STOP_TOKENS
    ]


def match_pantry_item(line, pantry_names):
    core = _ingredient_core(line)
    if not core:
        return None
    core_l = core.casefold()
    ranked = sorted((str(name) for name in pantry_names or [] if name), key=len, reverse=True)

    def _same_words(left, right):
        if left == right:
            return True
        pattern = r"(^|[^a-z0-9])" + re.escape(left) + r"([^a-z0-9]|$)"
        return bool(re.search(pattern, right))

    for name in ranked:
        pantry_l = name.casefold()
        if _same_words(core_l, pantry_l) or _same_words(pantry_l, core_l):
            return name
    core_tokens = _name_tokens(core)
    if not core_tokens:
        return None
    best_name = None
    best_score = 0
    for name in ranked:
        pantry_tokens = _name_tokens(name)
        if not pantry_tokens:
            continue
        hits = sum(
            1
            for token in core_tokens
            if any(
                token == pt
                or (
                    len(token) >= 6
                    and len(pt) >= 6
                    and (token.startswith(pt) or pt.startswith(token))
                )
                for pt in pantry_tokens
            )
        )
        score = hits / len(core_tokens)
        if score > best_score:
            best_score = score
            best_name = name
    if best_score >= 0.6:
        return best_name
    return None


def build_grocery_items(recipe, pantry_names, pantry_qty_by_item=None):
    pantry_qty_by_item = pantry_qty_by_item or {}
    prepared = recipe_for_grocery(recipe)
    items = []
    seen = set()
    for line in _as_text_list(prepared.get("ingredients")):
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        matched = match_pantry_item(line, pantry_names)
        items.append(
            {
                "text": line,
                "in_pantry": bool(matched),
                "pantry_match": matched or "",
                "on_hand": pantry_qty_by_item.get(matched, matched or ""),
            }
        )
    return items


def grocery_plain_text(title, items):
    lines = [f"Grocery list: {title}", ""]
    need = [item for item in items if not item.get("in_pantry")]
    have = [item for item in items if item.get("in_pantry")]
    if need:
        lines.append("Need to buy:")
        lines.extend(f"- {item['text']}" for item in need)
        lines.append("")
    if have:
        lines.append("Already in pantry:")
        for item in have:
            extra = f" ({item['on_hand']})" if item.get("on_hand") else ""
            lines.append(f"✓ {item['text']}{extra}")
    return "\n".join(lines).strip()


def grocery_mailto(title, items):
    subject = f"Grocery list: {title}"
    body = grocery_plain_text(title, items)
    return f"mailto:?subject={quote(subject)}&body={quote(body)}"


def build_grocery_pdf(title, items):
    regular, bold = _kitchen_fonts()
    unicode_ok = bool(regular)
    family = "Kitchen" if unicode_ok else "Helvetica"
    pdf = KitchenRecipePDF(family, unicode_ok=unicode_ok)
    if unicode_ok:
        pdf.add_font(family, "", regular)
        pdf.add_font(family, "B", bold)
    empty = "☐" if unicode_ok else "[ ]"
    checked = "✓" if unicode_ok else "[x]"
    pdf.add_page()
    pdf.set_xy(pdf.l_margin, pdf.get_y())
    pdf.set_font(family, "B", 18)
    pdf.multi_cell(0, 8, _pdf_text(f"Grocery list: {title}", unicode_ok))
    pdf.ln(2)
    need = [item for item in items if not item.get("in_pantry")]
    have = [item for item in items if item.get("in_pantry")]
    if need:
        _write_pdf_section_label(pdf, "Need to buy")
        for item in need:
            _write_pdf_hanging_line(pdf, empty, item["text"], 10, 7, unicode_ok)
            pdf.ln(1)
    if have:
        _write_pdf_section_label(pdf, "Already in pantry")
        for item in have:
            extra = f"  ({item['on_hand']})" if item.get("on_hand") else ""
            _write_pdf_hanging_line(pdf, checked, item["text"] + extra, 10, 7, unicode_ok)
            pdf.ln(1)
    if not need and not have:
        pdf.set_font(family, "", 11)
        pdf.multi_cell(0, 6, _pdf_text("No ingredients were found on this recipe.", unicode_ok))
    return bytes(pdf.output())


def grocery_pdf_filename(title):
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "grocery-list").lower()).strip("-")
    return f"{(slug or 'grocery-list')[:48]}-grocery.pdf"


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
                    "nutrition": extract_nutrition(item),
                    "diet_tags": extract_diet_tags(item),
                }
                recipe["markdown"] = recipe_to_markdown(recipe)
                recipes.append(recipe)
            if recipes:
                return recipes
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return split_markdown_recipes(text)


def build_recipe_prompt(meal_type, pantry_items, appliances, dietary_restrictions, servings, additional_notes):
    notes = (additional_notes or "").strip() or "None"
    appliance_text = ", ".join(appliances) or "Stovetop"
    diet_text = ", ".join(dietary_restrictions) or "None"
    pantry_text = ", ".join(pantry_items)
    return f"""Write {RECIPE_SUGGESTION_COUNT} short {meal_type} recipes from this pantry.

Meal type: {meal_type}
Pantry (prefer these; amounts shown): {pantry_text}
Appliances: {appliance_text}
Diet: {diet_text}
Servings: {servings}
Extra: {notes}

Rules:
- JSON array only. No markdown, no intro, no trailing notes.
- Each object: title, prep_time, cook_time, ingredients (string[]), instructions (string[]), nutrition (object), diet_tags (string[]).
- nutrition is per serving for {servings} servings, estimated: calories (kcal), protein, fat, saturated_fat, carbs, fiber, sugar, sodium. Short values like "28 g" or "420 mg".
- diet_tags: 1 to 3 labels this recipe fits or leans toward (e.g. High-Protein, Low-Sodium, Mediterranean). Prefer household Diet when it matches. Do not claim a certified medical diet.
- Use pantry items plus oil, salt, pepper, water if needed. Mark pantry vs staple.
- At most {RECIPE_MAX_STEPS} instruction steps. Each step is one short sentence.
- Keep titles under 8 words.
"""


def generate_recipe_text(ai_provider, prompt, gemini_key, ollama_model):
    if ai_provider == "Google Gemini API":
        if not gemini_key:
            raise ValueError("Please provide a Gemini API Key in the sidebar.")
        client = genai.Client(api_key=gemini_key)
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=RECIPE_MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW,
                ),
            ),
        )
        return (response.text or "").strip()

    res = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": ollama_model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "num_predict": RECIPE_MAX_OUTPUT_TOKENS,
            },
        },
        timeout=180,
    )
    if res.status_code != 200:
        raise RuntimeError(f"Ollama error: {res.text}")
    return (res.json().get("response") or "").strip()


DEFAULT_DIETARY = [
    "Dairy-Free",
    "Diabetic-Friendly",
    "Egg-Free",
    "Gluten-Free",
    "Halal",
    "High-Protein",
    "Keto",
    "Kosher",
    "Low-Carb",
    "Low-Cholesterol",
    "Low-FODMAP",
    "Low-Sodium",
    "Mediterranean",
    "None",
    "Nut-Free",
    "Paleo",
    "Peanut-Free",
    "Pescatarian",
    "Shellfish-Free",
    "Soy-Free",
    "Sugar-Free",
    "Tree-Nut-Free",
    "Vegan",
    "Vegetarian",
    "Whole30",
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
    combined = list(defaults) + extras
    return sorted(combined, key=str.casefold)


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
        .order("ingredient_name")
        .execute()
    )
    records = inventory_records(response.data)
    df = pd.DataFrame(records)
    if df.empty or "item" not in df.columns:
        return df
    ordered = df.copy()
    ordered["_item_sort"] = ordered["item"].astype(str).str.casefold()
    if "unit" in ordered.columns:
        ordered["_unit_sort"] = ordered["unit"].astype(str).str.casefold()
        ordered = ordered.sort_values(["_item_sort", "_unit_sort"], kind="mergesort")
        return ordered.drop(columns=["_item_sort", "_unit_sort"])
    return ordered.sort_values("_item_sort", kind="mergesort").drop(columns=["_item_sort"])


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


def save_grocery_list(title, items):
    supabase.table("grocery_lists").insert(
        {
            "title": title,
            "recipe_title": title,
            "items": items,
        }
    ).execute()


def fetch_grocery_lists():
    response = (
        supabase.table("grocery_lists")
        .select("*")
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )
    rows = []
    for row in response.data or []:
        rows.append(
            {
                "id": row.get("id"),
                "title": row.get("title") or row.get("recipe_title") or "Grocery list",
                "items": row.get("items") or [],
                "created_at": row.get("created_at") or "",
            }
        )
    return rows


def delete_grocery_list(list_id):
    supabase.table("grocery_lists").delete().eq("id", list_id).execute()


def current_pantry_lookup():
    names = []
    qty_by_item = {}
    try:
        df_pantry = fetch_inventory_df()
    except Exception:
        return names, qty_by_item
    if df_pantry.empty:
        return names, qty_by_item
    names = df_pantry["item"].tolist()
    for _, row in df_pantry.iterrows():
        qty = format_quantity(row.get("amount"), row.get("unit")) or row.get("quantity") or ""
        qty_by_item[row["item"]] = f"{row['item']} ({qty})" if qty else row["item"]
    return names, qty_by_item


def _text_or_empty(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def has_recipe_comment(recipe):
    return bool(_text_or_empty((recipe or {}).get("comment")))


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
                "comment": _text_or_empty(row.get("comment") or row.get("notes")),
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


def update_saved_recipe(recipe_id, title, ingredients, instructions):
    cleaned_title = (title or "").strip() or "Generated Recipe"
    supabase.table("saved_recipes").update(
        {
            "title": cleaned_title,
            "ingredients": (ingredients or "").strip(),
            "instructions": (instructions or "").strip(),
        }
    ).eq("id", recipe_id).execute()


def update_recipe_comment(recipe_id, comment):
    text = _text_or_empty(comment)
    supabase.table("saved_recipes").update({"comment": text or None}).eq("id", recipe_id).execute()


HELP_GUIDE = """
### Welcome
Pantry Chef turns what you already have on hand into meal ideas. Work left to right: set household preferences, stock the pantry, generate recipes, then save the ones you want to cook.

### Sidebar: AI and household setup
- **AI engine:** Google Gemini uses the key in Streamlit secrets. Local Ollama talks to a model on port 11434.
- **Appliances and diets:** Select what matches your kitchen. Open **Add / remove custom options** to type extras (for example a pizza oven or a Jain diet).
- **Measurement system:** Imperial or Metric is the default for new pantry items. You can still change units on each item.

### Pantry Inventory
Add an ingredient name, category, amount, and unit. Choosing **Imperial** or **Metric** on the item switches the unit list (`oz` / `lb` / `cup` versus `g` / `kg` / `ml`). Click a row in **Items on Hand** to edit any field except the row number, then save. Filter that table by item, category, amount, or unit. Adding an existing name also updates that item. Use **Delete selected item** to remove something you used up.

### Scan a barcode
The burnt-orange **Scan a barcode** card looks up a UPC/EAN in Open Food Facts first, then UPCitemdb if that catalog has no name, and fills the add form. Check the name, category, and amount, then **Add to Pantry**. Names come from public catalogs — sometimes you get the brand instead of the product (for example “Hidden Valley” instead of “Ranch Mix”). Edit the name before you save. Official GS1 US lookup is a paid business API, so the app does not call gs1us.org.

**On iPhone (best picture):** tap **Take photo or upload**, then **Take Photo** or pick from **Photo Library**. **Take Photo** opens the real iPhone Camera app, so you can zoom, use flash, and tap to focus on the barcode. After the photo is attached, tap **Look up barcode**. The status spinner sits to the right of that button. The scan card stays open after you upload.

**In-page camera:** the checkbox **In-page camera (desktop / quick snap, no zoom)** is a small browser preview. It does **not** use iPhone camera modes (no zoom, flash, or tap-to-focus). Use it on a computer webcam, or skip it on the phone.

**Type the digits:** if the photo will not read, enter the numbers under the barcode and Look up. Hold the package so the bars are sharp, well lit, and fill most of the frame.

### Recipe Generator
Choose a **meal type** (Dinner, Breakfast, Soup, or type your own). Then pick pantry items to cook with — nothing is pre-selected. Add an optional craving, then generate ideas. The chef looks for that meal type using what you chose and usually returns **two short** recipes (capped length so it stays quick).

Each recipe has its own card, including estimated per-serving calories, protein, fat, carbs, and sodium, plus diet labels it leans toward. Those numbers and labels are AI estimates, not lab values or medical advice. Check the ones you want, then **Save selected**. **Save all separately** stores every suggestion as its own Household Favorite. Uncheck a card if you only want the other one. **Download pretty PDF** saves a print-ready kitchen copy of every generated recipe. **Grocery list** compares the recipe to your pantry: items you already have are checked off, and the rest get a shopping checkbox. You can save, download/print, or email that list.

### Saved Recipes
Favorites are stored in Supabase, one entry per recipe. Sort the list by **name** or **rating**, then choose **ascending** or **descending**. Open a card to reread it — nutrition, diet lean, and the estimate disclaimer show when they were saved with the recipe. **Edit recipe** changes the title, pantry line, or instructions. **Add comment** / the 💬 note badge opens a kitchen note. **Rate favorite** opens a 5-star dialog. **Delete favorite** removes it after you confirm. **PDF** on a card saves the same print-ready kitchen copy as the Recipe Generator. **Grocery list** builds a shopping list from that favorite. Saved lists appear at the bottom of this tab.

### Tips
- Be specific in the extra prompt (“air fryer, high protein”) so the chef stays inside your appliances and diet.
- Keep amounts up to date so generated recipes know how much you actually have.
- If generation fails, confirm the Gemini key or that Ollama is running.
- If a barcode lookup is slow or fails, try Look up again, or type the numbers. Open Food Facts or UPCitemdb may be busy.
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
      .st-key-barcode_scan_card [data-testid="stExpander"] {{
        background: linear-gradient(180deg, #d36a2d 0%, #c45c26 42%, #9a4018 100%);
        border: 1px solid #7a3212 !important;
        border-radius: 16px !important;
        box-shadow: 0 8px 22px rgba(61, 43, 31, 0.2);
        overflow: hidden;
      }}
      .st-key-barcode_scan_card [data-testid="stExpander"] details {{
        background: transparent !important;
      }}
      .st-key-barcode_scan_card [data-testid="stExpander"] summary {{
        background: rgba(61, 43, 31, 0.28) !important;
      }}
      .st-key-barcode_scan_card [data-testid="stExpander"] summary,
      .st-key-barcode_scan_card [data-testid="stExpander"] summary p,
      .st-key-barcode_scan_card [data-testid="stExpander"] summary span {{
        color: #fffaf3 !important;
        font-weight: 700 !important;
      }}
      .st-key-barcode_scan_card [data-testid="stCaptionContainer"] p,
      .st-key-barcode_scan_card [data-testid="stMarkdown"] p,
      .st-key-barcode_scan_card label,
      .st-key-barcode_scan_card [data-testid="stWidgetLabel"] p {{
        color: #fff6ea !important;
      }}
      .st-key-barcode_scan_card [data-testid="stTextInput"] input {{
        background: #fffaf3 !important;
        color: #3d2b1f !important;
      }}
      .st-key-barcode_scan_card button {{
        background: #fffaf3 !important;
        color: #c45c26 !important;
        border: 1px solid #f0dcc8 !important;
        font-weight: 700 !important;
      }}
      .st-key-barcode_lookup_status [data-testid="stSpinner"] {{
        justify-content: flex-start !important;
        align-items: center !important;
      }}
      .st-key-barcode_lookup_status [data-testid="stSpinner"] span,
      .st-key-barcode_lookup_status p {{
        color: #fffaf3 !important;
      }}
      h1 {{
        font-size: 1.45rem !important;
        line-height: 1.25 !important;
        margin-bottom: 0.35rem !important;
      }}
      @media (max-width: 720px) {{
        h1 {{
          font-size: 1.2rem !important;
        }}
      }}
      .st-key-saved_favorites_list [data-testid="stExpander"] summary,
      .st-key-saved_favorites_list [data-testid="stExpander"] summary p {{
        font-size: 0.92rem !important;
        line-height: 1.3 !important;
        font-weight: 600 !important;
      }}
      .st-key-saved_favorites_list h1,
      .st-key-saved_favorites_list h2 {{
        font-size: 1.08rem !important;
        line-height: 1.3 !important;
        margin: 0.15rem 0 0.4rem !important;
      }}
      .recipe-sheet {{
        background: #fffaf3;
        border: 1px solid rgba(61, 43, 31, 0.12);
        border-radius: 16px;
        padding: 1.15rem 1.3rem 1.2rem;
        margin: 0.15rem 0 0.55rem;
        box-shadow: 0 8px 24px rgba(61, 43, 31, 0.08);
      }}
      .recipe-sheet h2 {{
        font-family: Georgia, "Times New Roman", serif;
        color: #3d2b1f;
        margin: 0 0 0.3rem;
        font-size: 1.42rem;
        line-height: 1.25;
      }}
      .recipe-meta {{
        color: #c45c26;
        font-weight: 600;
        letter-spacing: 0.02em;
        margin: 0 0 0.85rem;
      }}
      .recipe-nutrition {{
        display: flex;
        flex-wrap: wrap;
        gap: 0.35rem 0.75rem;
        margin: 0 0 0.35rem;
        padding: 0.45rem 0.65rem;
        background: rgba(196, 92, 38, 0.08);
        border-radius: 10px;
        color: #3d2b1f;
        font-size: 0.86rem;
      }}
      .recipe-nutrition strong {{
        color: #c45c26;
        font-weight: 650;
        margin-right: 0.15rem;
      }}
      .recipe-diet {{
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 0.35rem;
        margin: 0 0 0.45rem;
        font-size: 0.82rem;
        color: #3d2b1f;
      }}
      .recipe-diet strong {{
        color: #c45c26;
        margin-right: 0.2rem;
      }}
      .recipe-diet span {{
        background: #fff;
        border: 1px solid #e8d6c4;
        border-radius: 999px;
        padding: 0.12rem 0.55rem;
      }}
      .recipe-nutrition-note {{
        margin: 0 0 0.85rem;
        font-size: 0.75rem;
        color: #8a7060;
      }}
      [data-testid="stCameraInput"] video,
      [data-testid="stCameraInput"] img {{
        width: 100% !important;
        max-height: min(70vh, 520px) !important;
        object-fit: cover !important;
      }}
      .recipe-grid {{
        display: grid;
        grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
        gap: 1.05rem 1.4rem;
      }}
      .recipe-sheet h3 {{
        font-size: 0.76rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: #c45c26;
        margin: 0 0 0.4rem;
        border-bottom: 1px solid #e8d6c4;
        padding-bottom: 0.22rem;
      }}
      .recipe-sheet ul,
      .recipe-sheet ol {{
        margin: 0;
        padding-left: 1.15rem;
        color: #3d2b1f;
      }}
      .recipe-sheet li {{
        margin: 0.28rem 0;
        line-height: 1.45;
      }}
      @media (max-width: 720px) {{
        .recipe-grid {{
          grid-template-columns: 1fr;
        }}
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
    unit_system = st.radio(
        "Unit system:",
        ["Imperial", "Metric"],
        horizontal=True,
        key="edit_pantry_system",
        on_change=_on_edit_unit_system_change,
    )
    amount_col, unit_col = st.columns(2)
    with amount_col:
        amount = st.text_input("Amount:", key="edit_pantry_amount")
    with unit_col:
        unit_choices = units_for_system(unit_system)
        sync_unit_widget("edit_pantry_unit", unit_system)
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


def _close_edit_favorite_dialog():
    st.session_state.show_edit_favorite_dialog = False


def _close_comment_dialog():
    st.session_state.show_comment_dialog = False


def _close_grocery_dialog():
    st.session_state.show_grocery_dialog = False


def _open_grocery_dialog(recipe, source_key):
    pantry_names, pantry_qty = current_pantry_lookup()
    items = build_grocery_items(recipe, pantry_names, pantry_qty)
    prepared = recipe_for_grocery(recipe)
    st.session_state.grocery_title = prepared.get("title") or recipe.get("title") or "Recipe"
    st.session_state.grocery_items = items
    st.session_state.grocery_source_key = source_key
    prefix = f"gshop_{source_key}_"
    for key in list(st.session_state.keys()):
        if str(key).startswith(prefix):
            del st.session_state[key]
    st.session_state.show_grocery_dialog = True


def _open_edit_favorite_dialog(recipe):
    item = dict(recipe)
    st.session_state.edit_favorite = item
    st.session_state.edit_fav_title = item.get("title") or ""
    st.session_state.edit_fav_ingredients = item.get("ingredients") or ""
    st.session_state.edit_fav_instructions = item.get("instructions") or ""
    st.session_state.show_edit_favorite_dialog = True


def _open_comment_dialog(recipe):
    item = dict(recipe)
    st.session_state.comment_recipe = item
    st.session_state.fav_comment_text = item.get("comment") or ""
    st.session_state.show_comment_dialog = True


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


@st.dialog("Edit favorite recipe", width="large", on_dismiss=_close_edit_favorite_dialog)
def edit_favorite_dialog():
    recipe = st.session_state.get("edit_favorite") or {}
    st.caption("Change the title, pantry ingredients, or cooking instructions, then save.")
    title = st.text_input("Recipe title:", key="edit_fav_title")
    ingredients = st.text_area("Pantry ingredients used:", key="edit_fav_ingredients", height=80)
    instructions = st.text_area("Recipe:", key="edit_fav_instructions", height=280)
    save_col, cancel_col = st.columns(2)
    with save_col:
        if st.button("Save recipe", type="primary"):
            if not (title or "").strip():
                st.warning("A recipe title is required.")
            else:
                try:
                    update_saved_recipe(
                        recipe.get("id"),
                        title,
                        ingredients,
                        instructions,
                    )
                    st.session_state.show_edit_favorite_dialog = False
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not update recipe: {e}")
    with cancel_col:
        if st.button("Cancel", key="cancel_edit_favorite"):
            st.session_state.show_edit_favorite_dialog = False
            st.rerun()


@st.dialog("Kitchen note", width="small", on_dismiss=_close_comment_dialog)
def recipe_comment_dialog():
    recipe = st.session_state.get("comment_recipe") or {}
    title = recipe.get("title") or "this recipe"
    existing = _text_or_empty(recipe.get("comment"))
    if existing:
        st.write(f"Note for **{title}**")
    else:
        st.write(f"Add a note for **{title}**")
        st.caption("Use this for tweaks, leftovers, or how the family liked it.")
    comment = st.text_area("Comment:", key="fav_comment_text", height=160)
    save_col, clear_col = st.columns(2)
    with save_col:
        if st.button("Save comment", type="primary"):
            try:
                update_recipe_comment(recipe.get("id"), comment)
                st.session_state.show_comment_dialog = False
                st.rerun()
            except Exception as e:
                st.error(f"Could not save comment: {e}")
    with clear_col:
        if existing and st.button("Remove comment"):
            try:
                update_recipe_comment(recipe.get("id"), "")
                st.session_state.show_comment_dialog = False
                st.rerun()
            except Exception as e:
                st.error(f"Could not remove comment: {e}")
    if st.button("Close", key="close_comment_dialog"):
        st.session_state.show_comment_dialog = False
        st.rerun()


@st.dialog("Grocery list", width="large", on_dismiss=_close_grocery_dialog)
def grocery_list_dialog():
    title = st.session_state.get("grocery_title") or "Recipe"
    items = list(st.session_state.get("grocery_items") or [])
    source_key = st.session_state.get("grocery_source_key") or "list"
    st.write(f"Shopping list for **{title}**")
    st.caption("Items already in pantry_inventory are marked. Check off what you still need as you shop.")
    need = [item for item in items if not item.get("in_pantry")]
    have = [item for item in items if item.get("in_pantry")]
    if not items:
        st.info("This recipe has no ingredient list to shop from.")
    if need:
        st.markdown("**Need to buy**")
        for index, item in enumerate(need):
            st.checkbox(item["text"], key=f"gshop_{source_key}_{index}")
    else:
        st.success("Everything on this recipe is already in the pantry.")
    if have:
        st.markdown("**Already in pantry**")
        for item in have:
            extra = f" — on hand: {item['on_hand']}" if item.get("on_hand") else ""
            st.markdown(f"✓ {item['text']}{extra}")

    def _items_with_checks():
        updated = []
        need_index = 0
        for item in items:
            row = dict(item)
            if row.get("in_pantry"):
                row["checked"] = True
            else:
                row["checked"] = bool(st.session_state.get(f"gshop_{source_key}_{need_index}"))
                need_index += 1
            updated.append(row)
        return updated

    save_col, print_col, email_col = st.columns(3)
    with save_col:
        if st.button("Save list", type="primary"):
            try:
                save_grocery_list(title, _items_with_checks())
                st.success("Grocery list saved for later.")
            except Exception as e:
                st.error(
                    "Could not save the grocery list. Add the grocery_lists table in Supabase, then try again. "
                    f"({e})"
                )
    with print_col:
        try:
            st.download_button(
                "Download / print PDF",
                data=build_grocery_pdf(title, items),
                file_name=grocery_pdf_filename(title),
                mime="application/pdf",
                help="Open the PDF and use your browser or phone print dialog.",
                key=f"grocery_pdf_{source_key}",
            )
        except Exception as e:
            st.caption(f"PDF unavailable: {e}")
    with email_col:
        st.link_button("Email list", grocery_mailto(title, items))
    if st.button("Close", key="close_grocery_dialog"):
        st.session_state.show_grocery_dialog = False
        st.rerun()


title_col, help_col = st.columns([0.9, 0.1], vertical_alignment="center")
with title_col:
    st.title("🍳 Meal Planner & Pantry Recipe Generator")
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
    appliance_options = sorted(DEFAULT_APPLIANCES, key=str.casefold)
    dietary_options = sorted(DEFAULT_DIETARY, key=str.casefold)
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
    help="Sets the unit list for new pantry items. Metric shows ml, L, g, kg.",
    on_change=_on_pref_unit_system_change,
)
servings = st.sidebar.number_input("Number of Servings:", min_value=1, max_value=12, value=4)
st.sidebar.markdown("---")
st.sidebar.caption(f"Pantry Chef  ·  v{APP_VERSION}")

# --- MAIN NAVIGATION TABS ---
tab1, tab2, tab3 = st.tabs(["🛒 Pantry Inventory", "🤖 Recipe Generator", "📖 Saved Recipes"])

# --- TAB 1: PANTRY INVENTORY ---
with tab1:
    st.header("Current Pantry Inventory")
    if st.session_state.pop("clear_add_item_form", False):
        st.session_state.add_item_name = ""
        st.session_state.add_item_amount = "1"
        st.session_state.pop("barcode_last_code", None)
    pending_product = st.session_state.pop("barcode_form_pending", None)
    if pending_product:
        apply_barcode_product_to_form(
            pending_product,
            pending_product.get("preferred_system") or preferred_system,
        )
    if "item_unit_system" not in st.session_state:
        st.session_state.item_unit_system = preferred_system
    if "add_item_name" not in st.session_state:
        st.session_state.add_item_name = ""
    if "add_item_category" not in st.session_state:
        st.session_state.add_item_category = PANTRY_CATEGORIES[0]
    if "add_item_amount" not in st.session_state:
        st.session_state.add_item_amount = "1"

    lookup_note = st.session_state.pop("barcode_lookup_message", None)
    if lookup_note:
        kind, text = lookup_note
        if kind == "warning":
            st.warning(text)
        elif kind == "error":
            st.error(text)
        else:
            st.success(text)
    save_note = st.session_state.pop("pantry_save_message", None)
    if save_note:
        st.success(save_note)

    if (
        st.session_state.get("barcode_upload")
        or st.session_state.get("barcode_camera")
        or st.session_state.get("barcode_last_code")
        or st.session_state.get("barcode_lookup_busy")
    ):
        st.session_state.barcode_scan_open = True

    with st.container(key="barcode_scan_card"):
        with st.expander(
            "Scan a barcode",
            expanded=bool(st.session_state.get("barcode_scan_open")),
            key="barcode_scan_expander",
        ):
            st.caption(
                "On iPhone, tap **Take photo or upload**, then **Take Photo**. That opens the real Camera app so you can zoom, use flash, and tap to focus. Fill the barcode, then Look up. Check the add form before saving."
            )
            upload = st.file_uploader(
                "Take photo or upload",
                type=["jpg", "jpeg", "png", "webp", "heic", "heif"],
                key="barcode_upload",
                help="On iPhone this offers Take Photo (full Camera app with zoom) or Photo Library. iPhone photos in HEIC are converted automatically.",
            )
            photo = None
            if st.checkbox("In-page camera (desktop / quick snap, no zoom)", key="barcode_use_camera"):
                photo = st.camera_input("In-page camera", key="barcode_camera")
            with st.form("barcode_lookup_form", border=False):
                typed_code = st.text_input("Barcode numbers", key="barcode_typed")
                button_col, status_col = st.columns([0.42, 0.58], vertical_alignment="center")
                with button_col:
                    lookup_clicked = st.form_submit_button("Look up barcode")
                with status_col:
                    with st.container(key="barcode_lookup_status"):
                        lookup_status = st.empty()
            image_file = photo or upload or st.session_state.get("barcode_camera") or st.session_state.get("barcode_upload")
            if lookup_clicked:
                st.session_state.barcode_scan_open = True
                barcode = pick_product_barcode([typed_code])
                image_bytes = image_file.getvalue() if image_file else b""
                mime_type = getattr(image_file, "type", None) if image_file else ""
                lookup_system = st.session_state.get("item_unit_system") or preferred_system
                if not barcode and image_bytes:
                    with lookup_status:
                        with st.spinner("Reading barcode..."):
                            try:
                                barcode = pick_product_barcode(decode_barcodes_from_image_bytes(image_bytes))
                                if not barcode and gemini_key:
                                    barcode = extract_barcode_with_gemini(image_bytes, mime_type, gemini_key)
                            except Exception:
                                barcode = ""
                if not barcode:
                    st.session_state.barcode_lookup_message = (
                        "warning",
                        "Could not read a barcode. Try a closer photo, better light, Look up again, or type the digits.",
                    )
                    st.rerun()
                else:
                    try:
                        with lookup_status:
                            with st.spinner(f"Looking up {barcode}..."):
                                product = lookup_barcode_product(barcode)
                    except Exception as e:
                        product = None
                        detail = str(e)
                        if any(code in detail for code in ("502", "503", "504", "429")) or "Gateway" in detail:
                            detail = "The barcode catalog was busy. Try Look up again."
                        st.session_state.barcode_lookup_message = (
                            "error",
                            f"Could not look up that barcode: {detail}",
                        )
                    if product is not None:
                        product["preferred_system"] = lookup_system
                        st.session_state.barcode_form_pending = product
                        st.session_state.barcode_last_code = product.get("barcode") or barcode
                        source = product.get("source") or "the catalog"
                        if product.get("name"):
                            st.session_state.barcode_lookup_message = (
                                "success",
                                f"Found {product['name']} ({product.get('barcode')}) via {source}. Check the form, then Add to Pantry.",
                            )
                        else:
                            st.session_state.barcode_lookup_message = (
                                "warning",
                                f"Read barcode {barcode}, but no catalog had a product name. Enter a name and add it.",
                            )
                    st.rerun()

    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("Add / Update Item")
        unit_system = st.radio(
            "Unit system:",
            ["Imperial", "Metric"],
            key="item_unit_system",
            horizontal=True,
            on_change=_on_item_unit_system_change,
        )
        unit_choices = units_for_system(unit_system)
        unit_key = add_item_unit_key(unit_system)
        sync_unit_widget(unit_key, unit_system)
        if st.session_state.get("add_item_category") not in PANTRY_CATEGORIES:
            st.session_state.add_item_category = PANTRY_CATEGORIES[0]

        with st.form("add_item_form"):
            new_item = st.text_input(
                "Ingredient Name (e.g., Chicken Breast, Rice, Eggs):",
                key="add_item_name",
            )
            category = st.selectbox(
                "Category:",
                PANTRY_CATEGORIES,
                key="add_item_category",
            )
            amount_col, unit_col = st.columns(2)
            with amount_col:
                amount = st.text_input("Amount:", key="add_item_amount")
            with unit_col:
                unit = st.selectbox("Unit:", unit_choices, key=unit_key)
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
                    st.session_state.pantry_save_message = f"Added '{new_item}' ({quantity}) to pantry!"
                    st.session_state.clear_add_item_form = True
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
            visible = display[["item", "category", "amount", "unit"]].copy()
            filter_cols = st.columns(4)
            with filter_cols[0]:
                item_filter = st.text_input(
                    "Filter item",
                    key="pantry_filter_item",
                    placeholder="Name",
                )
            with filter_cols[1]:
                categories = sorted(
                    {str(value) for value in visible["category"].dropna() if str(value).strip()},
                    key=str.casefold,
                )
                category_filter = st.multiselect(
                    "Filter category",
                    categories,
                    key="pantry_filter_category",
                )
            with filter_cols[2]:
                amount_filter = st.text_input(
                    "Filter amount",
                    key="pantry_filter_amount",
                    placeholder="Amount",
                )
            with filter_cols[3]:
                units = sorted(
                    {str(value) for value in visible["unit"].dropna() if str(value).strip()},
                    key=str.casefold,
                )
                unit_filter = st.multiselect(
                    "Filter unit",
                    units,
                    key="pantry_filter_unit",
                )
            mask = pd.Series(True, index=display.index)
            if (item_filter or "").strip():
                mask &= display["item"].astype(str).str.contains(
                    item_filter.strip(), case=False, na=False, regex=False
                )
            if category_filter:
                mask &= display["category"].isin(category_filter)
            if (amount_filter or "").strip():
                mask &= display["amount"].astype(str).str.contains(
                    amount_filter.strip(), case=False, na=False, regex=False
                )
            if unit_filter:
                mask &= display["unit"].astype(str).isin(unit_filter)
            shown = display.loc[mask].reset_index(drop=True)
            if shown.empty:
                st.info("No items match those filters.")
                table = None
            else:
                table = st.dataframe(
                    shown[["item", "category", "amount", "unit"]],
                    width="stretch",
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="pantry_table",
                    column_config={
                        "item": st.column_config.TextColumn("Item", width="medium"),
                        "category": st.column_config.TextColumn("Category", width="medium"),
                        "amount": st.column_config.TextColumn("Amount", width=72),
                        "unit": st.column_config.TextColumn("Unit", width=80),
                    },
                )
            st.caption("Click a row to edit that item. Filters apply to item, category, amount, and unit.")

            selected_rows = []
            selection = getattr(table, "selection", None) if table is not None else None
            if isinstance(selection, dict):
                selected_rows = list(selection.get("rows") or [])
            elif selection is not None:
                selected_rows = list(getattr(selection, "rows", []) or [])

            if selected_rows:
                selected_item = shown.iloc[int(selected_rows[0])].to_dict()
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
    except Exception as e:
        df_pantry = pd.DataFrame()
        st.error(f"Could not load pantry ingredients: {e}")

    pantry_list = df_pantry["item"].tolist() if not df_pantry.empty else []
    pantry_qty_by_item = {}
    if not df_pantry.empty:
        for _, row in df_pantry.iterrows():
            qty = format_quantity(row["amount"], row["unit"]) or row["quantity"] or ""
            pantry_qty_by_item[row["item"]] = f"{row['item']} ({qty})" if qty else row["item"]

    meal_type = st.selectbox(
        "Meal type:",
        MEAL_TYPES,
        index=None,
        placeholder="Choose or type a meal type",
        accept_new_options=True,
        help="The chef will look for this kind of meal. Type if you need something not on the list.",
        key="recipe_meal_type",
    )
    selected_pantry = st.multiselect(
        "Select ingredients to use for this meal:",
        pantry_list,
        default=[],
        placeholder="Choose pantry items (none selected)",
        help="Starts empty so you pick only what you want in this meal.",
        key="recipe_selected_pantry",
    )
    additional_notes = st.text_input(
        "Extra craving or prompt (e.g., 'Quick 20-min', 'Comfort food', 'Italian style'):"
    )

    if st.button("✨ Generate Recipe Ideas", type="primary"):
        if not meal_type:
            st.error("Please choose or type a meal type.")
        elif not selected_pantry:
            st.error("Please select at least a few pantry ingredients!")
        else:
            pantry_for_prompt = [
                pantry_qty_by_item.get(item, item) for item in selected_pantry
            ]
            prompt = build_recipe_prompt(
                meal_type,
                pantry_for_prompt,
                appliances,
                dietary_restrictions,
                servings,
                additional_notes,
            )
            with st.spinner("Chef AI is creating recipes..."):
                try:
                    recipe_result = generate_recipe_text(
                        ai_provider, prompt, gemini_key, ollama_model
                    )
                    if recipe_result:
                        recipes = parse_generated_recipes(recipe_result)
                        st.session_state["last_recipe"] = recipe_result
                        st.session_state["generated_recipes"] = recipes
                        st.session_state["last_recipe_ingredients"] = ", ".join(selected_pantry)
                        for key in list(st.session_state.keys()):
                            if str(key).startswith("save_pick_"):
                                del st.session_state[key]
                    else:
                        st.error("The chef returned an empty recipe. Try again.")
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
                parts = recipe_parts(recipe)
                st.markdown(recipe_to_html(recipe, show_title=False), unsafe_allow_html=True)
                if not parts["ingredients"] and not parts["instructions"]:
                    st.markdown(markdown)
                chosen = st.checkbox(
                    f"Select “{title}” to save",
                    value=True,
                    key=f"save_pick_{index}",
                )
                if st.button("Grocery list", key=f"grocery_gen_{index}"):
                    _open_grocery_dialog(recipe, f"gen-{index}")
            if chosen:
                selected_to_save.append(recipe)

        try:
            pdf_bytes = build_recipes_pdf(generated_recipes)
            st.download_button(
                "📥 Download pretty PDF",
                data=pdf_bytes,
                file_name=recipes_pdf_filename(generated_recipes),
                mime="application/pdf",
                help="Print-ready kitchen cards, one recipe per page.",
            )
        except Exception as e:
            st.error(f"Could not build the PDF: {e}")

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
        with st.container(key="saved_favorites_list"):
            for _, row in df_recipes.iterrows():
                recipe = row.to_dict()
                note_mark = "  ·  💬" if has_recipe_comment(recipe) else ""
                heading = f"{recipe['title']}  ·  {format_star_rating(recipe.get('rating'))}{note_mark}"
                with st.expander(heading):
                    recipe_col, rating_col = st.columns([0.82, 0.18], vertical_alignment="top")
                    with recipe_col:
                        if recipe.get("ingredients"):
                            st.caption(f"Pantry ingredients used: {recipe['ingredients']}")
                        display_recipe = favorite_for_pdf(recipe)
                        if display_recipe.get("ingredients") or display_recipe.get("instructions"):
                            st.markdown(recipe_to_html(display_recipe, show_title=False), unsafe_allow_html=True)
                        else:
                            st.markdown(recipe["instructions"])
                        action_row = st.columns(5)
                        with action_row[0]:
                            if st.button("Edit recipe", key=f"edit_recipe_{recipe['id']}"):
                                _open_edit_favorite_dialog(recipe)
                        with action_row[1]:
                            comment_label = "Add comment" if not has_recipe_comment(recipe) else "Edit comment"
                            if st.button(comment_label, key=f"comment_recipe_{recipe['id']}"):
                                _open_comment_dialog(recipe)
                        with action_row[2]:
                            if st.button("Rate favorite", key=f"rate_recipe_{recipe['id']}"):
                                _open_rate_dialog(recipe)
                        with action_row[3]:
                            if st.button("Delete favorite", key=f"delete_recipe_{recipe['id']}"):
                                _open_delete_dialog(recipe)
                        with action_row[4]:
                            if st.button("Grocery list", key=f"grocery_fav_{recipe['id']}"):
                                _open_grocery_dialog(recipe, f"fav-{recipe['id']}")
                    with rating_col:
                        st.markdown(render_star_rating(recipe.get("rating")), unsafe_allow_html=True)
                        st.caption("Rating")
                        pdf_recipe = favorite_for_pdf(recipe)
                        try:
                            st.download_button(
                                "📥 PDF",
                                data=build_recipes_pdf([pdf_recipe]),
                                file_name=recipes_pdf_filename([pdf_recipe]),
                                mime="application/pdf",
                                help="Download this favorite as a pretty kitchen PDF",
                                key=f"pdf_fav_{recipe['id']}",
                            )
                        except Exception as e:
                            st.caption(f"PDF unavailable: {e}")
                        if has_recipe_comment(recipe):
                            if st.button("💬 View note", key=f"view_comment_{recipe['id']}", help="Open the kitchen note for this recipe"):
                                _open_comment_dialog(recipe)
                        else:
                            st.caption("No note yet")

        if st.session_state.get("show_edit_favorite_dialog"):
            edit_favorite_dialog()
        if st.session_state.get("show_comment_dialog"):
            recipe_comment_dialog()
        if st.session_state.get("show_rate_dialog"):
            rate_favorite_dialog()
        if st.session_state.get("show_delete_dialog"):
            confirm_delete_favorite_dialog()

    st.markdown("### Saved grocery lists")
    try:
        grocery_lists = fetch_grocery_lists()
    except Exception:
        grocery_lists = None
        st.caption(
            "To save grocery lists, run the grocery_lists SQL in the Supabase SQL editor, then rerun."
        )
    if grocery_lists:
        for glist in grocery_lists:
            heading = glist["title"]
            with st.expander(heading):
                need = [item for item in glist["items"] if not item.get("in_pantry")]
                have = [item for item in glist["items"] if item.get("in_pantry")]
                if need:
                    st.markdown("**Need to buy**")
                    for item in need:
                        mark = "☑" if item.get("checked") else "☐"
                        st.markdown(f"{mark} {item.get('text') or ''}")
                if have:
                    st.markdown("**Already in pantry**")
                    for item in have:
                        extra = f" — {item.get('on_hand')}" if item.get("on_hand") else ""
                        st.markdown(f"✓ {item.get('text') or ''}{extra}")
                pdf_col, del_col = st.columns(2)
                with pdf_col:
                    try:
                        st.download_button(
                            "Download / print PDF",
                            data=build_grocery_pdf(glist["title"], glist["items"]),
                            file_name=grocery_pdf_filename(glist["title"]),
                            mime="application/pdf",
                            key=f"saved_grocery_pdf_{glist['id']}",
                        )
                    except Exception as e:
                        st.caption(f"PDF unavailable: {e}")
                with del_col:
                    if st.button("Delete list", key=f"delete_grocery_{glist['id']}"):
                        try:
                            delete_grocery_list(glist["id"])
                            st.rerun()
                        except Exception as e:
                            st.error(f"Could not delete list: {e}")
    elif grocery_lists is not None:
        st.caption("No saved grocery lists yet. Open a recipe and click Grocery list.")

if st.session_state.get("show_grocery_dialog"):
    grocery_list_dialog()
