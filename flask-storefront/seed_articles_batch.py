"""Bulk-seed the Science Hub library: 5 articles per category (30 total).

Reads the per-category JSON files in science_seed/ (each a list of 5 article
objects) and publishes them. Per category, the first 2 are back-dated into
2025 and the remaining 3 are dated across 2026 — so each category has a mix of
older and recent pieces. Idempotent: existing slugs are skipped.

Run (local):  DATABASE_URL="sqlite:///$(pwd)/pephub.db" ./venv/bin/python seed_articles_batch.py
Run (Render Shell):  python seed_articles_batch.py
"""
import json
import os
from datetime import datetime

import app
from app import db, Article

HERE = os.path.dirname(os.path.abspath(__file__))
SEED_DIR = os.path.join(HERE, "science_seed")

# filename (without .json)  ->  Science Hub topic label
FILE_TOPIC = {
    "peptide-science":    "Peptide Science",
    "nutrition":          "Nutrition",
    "bio-hacking":        "Bio-hacking",
    "vitality-longevity": "Vitality & Longevity",
    "training-recovery":  "Training & Recovery",
    "health-wellbeing":   "Health & Wellbeing",
}

# published date per article index within a category:
#   index 0,1 -> 2025 (back-dated),  index 2,3,4 -> 2026 (recent)
DATE_BY_INDEX = [
    (2025, 3), (2025, 9), (2026, 2), (2026, 4), (2026, 6),
]


def _date_for(cat_i, art_i):
    year, month = DATE_BY_INDEX[art_i % len(DATE_BY_INDEX)]
    day = 6 + (cat_i * 3)          # 6..21 — unique-ish per category, always valid
    minute = (cat_i * 7) % 60      # keep timestamps distinct for ordering
    return datetime(year, month, day, 9, minute, 0)


def main():
    with app.app.app_context():
        created = skipped = 0
        for cat_i, (fname, topic) in enumerate(FILE_TOPIC.items()):
            path = os.path.join(SEED_DIR, fname + ".json")
            if not os.path.exists(path):
                print(f"WARN: missing {path} — skipping {topic}")
                continue
            with open(path, encoding="utf-8") as fh:
                articles = json.load(fh)
            for art_i, a in enumerate(articles):
                slug = a["slug"].strip().lower()
                if Article.query.filter_by(slug=slug).first():
                    print("skip (exists):", slug)
                    skipped += 1
                    continue
                when = _date_for(cat_i, art_i)
                db.session.add(Article(
                    slug=slug,
                    title=a["title"][:240],
                    topic=topic,
                    excerpt=a.get("excerpt", ""),
                    body_html=a["body_html"].strip(),
                    takeaways_json=json.dumps(a.get("takeaways", [])),
                    sources_json=json.dumps(a.get("sources", [])),
                    status="PUBLISHED",
                    created_at=when,
                    published_at=when,
                ))
                created += 1
                print(f"seeded [{topic} · {when:%Y-%m-%d}]: {a['title'][:52]}")
        db.session.commit()
        print(f"\nDone. {created} published, {skipped} skipped.")


if __name__ == "__main__":
    main()
