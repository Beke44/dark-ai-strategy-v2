import json
import os
from datetime import datetime
from jinja2 import Environment, FileSystemLoader

# ===== CONFIG =====
DATA_FILE = "data/analyses_by_date.json"
OUTPUT_DIR = "site"
TEMPLATE_DIR = "templates"

env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))


# ===== SAFE LOAD =====
def load_data():
    if not os.path.exists(DATA_FILE):
        print("⚠️ Data file not found, creating empty fallback...")
        ensure_dir(os.path.dirname(DATA_FILE))
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
        return {}

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except Exception as e:
        print(f"⚠️ JSON load error: {e}")
        return {}


# ===== SAFE TEMPLATE =====
def safe_get_template(name):
    try:
        return env.get_template(name)
    except Exception as e:
        print(f"❌ Template error ({name}): {e}")
        return None


# ===== UTILS =====
def ensure_dir(path):
    if path and not os.path.exists(path):
        os.makedirs(path)


def sanitize_date(date):
    return str(date).replace("/", "-").replace(" ", "_")


# ===== GENERATORS =====
def generate_day_page(date, matches):
    template = safe_get_template("day.html")
    if not template:
        return

    safe_date = sanitize_date(date)

    html = template.render(
        date=date,
        matches=matches
    )

    day_path = os.path.join(OUTPUT_DIR, safe_date)
    ensure_dir(day_path)

    with open(os.path.join(day_path, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


def generate_index(all_dates):
    template = safe_get_template("base.html")
    if not template:
        return

    html = template.render(
        dates=sorted(all_dates, reverse=True)
    )

    with open(os.path.join(OUTPUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


# ===== MAIN =====
def main():
    data = load_data()

    ensure_dir(OUTPUT_DIR)

    if not data:
        print("⚠️ No data available, generating empty site...")

    all_dates = []

    for date, matches in data.items():
        try:
            generate_day_page(date, matches)
            all_dates.append(date)
        except Exception as e:
            print(f"❌ Error generating page for {date}: {e}")

    generate_index(all_dates)

    print("✅ Site generated successfully")


if __name__ == "__main__":
    main()
