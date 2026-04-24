import json
import os
from datetime import datetime
from jinja2 import Environment, FileSystemLoader

# ===== CONFIG =====
DATA_FILE = "data/analyses_by_date.json"
OUTPUT_DIR = "site"
TEMPLATE_DIR = "templates"

env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))


def load_data():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)


def generate_day_page(date, matches):
    template = env.get_template("day.html")

    html = template.render(
        date=date,
        matches=matches
    )

    day_path = os.path.join(OUTPUT_DIR, date)
    ensure_dir(day_path)

    with open(os.path.join(day_path, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


def generate_index(all_dates):
    template = env.get_template("base.html")

    html = template.render(
        dates=sorted(all_dates, reverse=True)
    )

    with open(os.path.join(OUTPUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


def main():
    data = load_data()

    ensure_dir(OUTPUT_DIR)

    all_dates = []

    for date, matches in data.items():
        generate_day_page(date, matches)
        all_dates.append(date)

    generate_index(all_dates)

    print("✅ Site generated successfully")


if __name__ == "__main__":
    main()