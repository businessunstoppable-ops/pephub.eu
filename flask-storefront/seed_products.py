# seed_products.py
from app import app
from models import db, Product

products_to_add = [
    Product(name="Vitality Complex", description="Blend of adaptogens and mitochondrial support for daily energy.", price=49.99),
    Product(name="NeuroBoost", description="Science-backed nootropics to enhance focus and cognitive clarity.", price=69.99),
    Product(name="Circadian Reset", description="Regulates sleep-wake cycles using natural light-mimicking compounds.", price=59.99),
    Product(name="Cellular Renew", description="Activates autophagy and supports cellular repair mechanisms.", price=79.99),
    Product(name="ImmunoShield", description="Plant-based immunomodulators for balanced immune response.", price=54.99),
    Product(name="Mitochondrial Prime", description="Enhances ATP production and reduces oxidative stress.", price=89.99),
    Product(name="Calm & Clarity", description="Reduces cortisol while sharpening mental focus.", price=64.99),
    Product(name="Joint Flex", description="Science-backed peptides for joint lubrication and comfort.", price=74.99),
    Product(name="Detox Core", description="Supports liver phase I and II detox pathways.", price=44.99),
    Product(name="Longevity Blend", description="Combines resveratrol, NMN, and pterostilbene.", price=99.99),
    Product(name="Sleep Renew", description="Melatonin‑free sleep regulation via GABA and magnesium.", price=39.99),
    Product(name="Metabolic Boost", description="Activates brown adipose tissue and thermogenesis.", price=69.99),
]

with app.app_context():
    db.session.add_all(products_to_add)
    db.session.commit()
    print(f"Added {len(products_to_add)} products.")