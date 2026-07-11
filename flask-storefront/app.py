from flask import Flask, session, request, redirect, url_for, render_template_string, render_template, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect, CSRFError, generate_csrf
from dotenv import load_dotenv
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import re
import os
import json
import logging
import secrets
import string

# Load env vars from .env (next to this file). Silent if missing.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-replace-in-prod')

# ----------------------------------------------------------------------
# CSRF protection — every POST form is protected. Tokens are auto-injected
# into forms by the after_request hook below, so no template needs editing.
# ----------------------------------------------------------------------
app.config['WTF_CSRF_TIME_LIMIT'] = None      # token valid for the whole session
csrf = CSRFProtect(app)

@app.errorhandler(CSRFError)
def _handle_csrf_error(e):
    # A stale/expired token (e.g. left a tab open a long time). Bounce them back
    # with a friendly message rather than a raw 400.
    flash('Your session expired for security — please try that again.', 'error')
    return redirect(request.referrer or url_for('index')), 303

# ----------------------------------------------------------------------
# Config — pricing, VAT, margins, supplier, admin
# ----------------------------------------------------------------------
VAT_RATE = 0.21              # 21% included in retail prices
USD_EUR_RATE = 0.92          # rough — refresh periodically or pull from API
FREE_SHIPPING_THRESHOLD = 100.0
SHIPPING_COST = 9.95

SUPPLIER_EMAIL = os.environ.get('SUPPLIER_EMAIL', 'supplier@example.com')
SUPPLIER_NAME  = os.environ.get('SUPPLIER_NAME',  'PepHub Supplier')
FROM_EMAIL     = os.environ.get('FROM_EMAIL',     'orders@pephub.example')
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'changeme-pephub')

# ----------------------------------------------------------------------
# Legal / company identity — used across the Terms, Privacy, Cookie,
# Refund, Shipping and Imprint pages.  >>> EDIT THESE with your real
# registered details (or set them as env vars on Render). <<<
# ----------------------------------------------------------------------
LEGAL = {
    'entity':   os.environ.get('LEGAL_ENTITY',  'PepHub'),
    'address':  os.environ.get('LEGAL_ADDRESS', '[- needs edit]'),
    'reg_no':   os.environ.get('LEGAL_REG_NO',  '[Company kvk number - needs edit]'),
    'vat_no':   os.environ.get('LEGAL_VAT_NO',  '[kvk number - needs edit'),
    'email':    os.environ.get('LEGAL_EMAIL',   'support@pep-hub.eu'),
    'privacy_email': os.environ.get('LEGAL_PRIVACY_EMAIL', 'privacy@pep-hub.eu'),
    'country':  os.environ.get('LEGAL_COUNTRY', 'Netherlands'),
    'site':     'pep-hub.eu',
    'updated':  '1 July 2026',                                                    # bump when you change the text
}

# ----------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------
basedir = os.path.abspath(os.path.dirname(__file__))
_db_url = os.environ.get('DATABASE_URL', f'sqlite:///{os.path.join(basedir, "pephub.db")}')
# Render/Heroku hand out postgres:// — SQLAlchemy + psycopg2 need postgresql+psycopg2://
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql+psycopg2://', 1)
elif _db_url.startswith('postgresql://'):
    _db_url = _db_url.replace('postgresql://', 'postgresql+psycopg2://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Preview/test-store banner — shown site-wide while PREVIEW_MODE is truthy.
PREVIEW_MODE = os.environ.get('PREVIEW_MODE', '').lower() not in ('', '0', 'false', 'no')

_PREVIEW_BANNER = ('<div id="ph-preview-banner" style="position:sticky;top:0;z-index:11000;'
                   'background:#B45309;color:#fff;text-align:center;font-size:.8rem;font-weight:700;'
                   'padding:.4rem 1rem;letter-spacing:.02em;">⚠ PREVIEW STORE — test mode. '
                   'Orders and subscriptions are not charged or fulfilled yet.</div>')

_LEGAL_FOOTER = (
    '<div id="ph-legal-footer" style="background:#0b0b0b;border-top:1px solid #222;'
    'padding:1.1rem 1rem;text-align:center;font-size:.75rem;line-height:1.9;color:#9a9a9a;">'
    '<div style="max-width:900px;margin:0 auto;">'
    '<a href="/legal/terms" style="color:#c9c9c9;text-decoration:none;margin:0 .55rem;">Terms &amp; Conditions</a>·'
    '<a href="/legal/privacy" style="color:#c9c9c9;text-decoration:none;margin:0 .55rem;">Privacy Policy</a>·'
    '<a href="/legal/cookies" style="color:#c9c9c9;text-decoration:none;margin:0 .55rem;">Cookie Policy</a>·'
    '<a href="/legal/refunds" style="color:#c9c9c9;text-decoration:none;margin:0 .55rem;">Refunds &amp; Returns</a>·'
    '<a href="/legal/shipping" style="color:#c9c9c9;text-decoration:none;margin:0 .55rem;">Shipping</a>·'
    '<a href="/legal/imprint" style="color:#c9c9c9;text-decoration:none;margin:0 .55rem;">Imprint</a>'
    '<div style="margin-top:.5rem;color:#6b6b6b;">Products are sold for laboratory research purposes only — '
    'not for human or veterinary use, food, or cosmetic application.</div>'
    '</div></div>')

_COOKIE_BANNER = (
    '<div id="ph-cookie" style="position:fixed;left:1rem;right:1rem;bottom:1rem;z-index:12000;max-width:760px;'
    'margin:0 auto;background:#1c1c1c;border:1px solid #333;border-radius:12px;padding:1rem 1.1rem;'
    'box-shadow:0 10px 40px rgba(0,0,0,.5);font-size:.82rem;color:#e6e6e6;display:flex;gap:1rem;'
    'align-items:center;flex-wrap:wrap;">'
    '<div style="flex:1;min-width:240px;line-height:1.5;">We use only <strong>essential cookies</strong> needed '
    'to run the store (your cart, login and security). We do not use tracking or advertising cookies. '
    'See our <a href="/legal/cookies" style="color:#FF9000;">Cookie Policy</a>.</div>'
    '<button onclick="phCookieOk()" style="background:#FF9000;color:#000;border:0;border-radius:8px;'
    'padding:.55rem 1.2rem;font-weight:800;cursor:pointer;white-space:nowrap;">Got it</button></div>'
    '<script>function phCookieOk(){document.cookie="ph_consent=1;path=/;max-age=31536000;samesite=Lax";'
    'var b=document.getElementById("ph-cookie");if(b)b.remove();}</script>')

# Social-proof review toast — a small orange box that slides in at the top for
# ~3s every 45s, showing a random 4.7–5★ review with a random NL/DE name+city.
_REVIEW_TOAST = """
<div id="ph-review-toast" aria-live="polite"></div>
<style>
#ph-review-toast{position:fixed;top:14px;left:50%;transform:translate(-50%,-160%);z-index:12500;
 background:#FF9000;color:#111;border-radius:12px;padding:.55rem .9rem;max-width:340px;width:calc(100% - 2rem);
 box-shadow:0 12px 34px rgba(0,0,0,.45);font-family:'Inter',system-ui,sans-serif;opacity:0;
 transition:transform .45s cubic-bezier(.2,.8,.2,1),opacity .45s;pointer-events:none;}
#ph-review-toast.on{transform:translate(-50%,0);opacity:1;}
#ph-review-toast .rt-top{display:flex;align-items:center;gap:.45rem;font-weight:900;}
#ph-review-toast .rt-stars{letter-spacing:1px;color:#3a2600;font-size:.82rem;}
#ph-review-toast .rt-score{background:#111;color:#FF9000;border-radius:20px;padding:.03rem .45rem;font-size:.7rem;font-weight:900;}
#ph-review-toast .rt-text{font-size:.82rem;font-weight:600;line-height:1.3;margin:.25rem 0 .18rem;}
#ph-review-toast .rt-by{font-size:.7rem;font-weight:800;opacity:.72;}
</style>
<script>
(function(){
 var el=document.getElementById('ph-review-toast'); if(!el) return;
 var names=["Sven K.","Lotte V.","Jonas M.","Anouk D.","Maximilian R.","Femke B.","Lars H.","Sanne W.",
  "Niklas S.","Julia P.","Bram J.","Hannah G.","Thijs K.","Marie L.","Finn D.","Nina B.","Daan V.","Leah M.",
  "Ruben T.","Emma S.","Jasper N.","Mila R.","Tobias F.","Sophie K.","Kai W.","Isa B.","Lukas H.","Noor V."];
 var cities=["Amsterdam, NL","Rotterdam, NL","Utrecht, NL","Eindhoven, NL","Den Haag, NL","Groningen, NL",
  "Nijmegen, NL","Haarlem, NL","Tilburg, NL","Leiden, NL","Maastricht, NL","Berlin, DE","München, DE",
  "Hamburg, DE","Köln, DE","Frankfurt, DE","Stuttgart, DE","Düsseldorf, DE","Leipzig, DE","Dresden, DE",
  "Bremen, DE","Hannover, DE"];
 var reviews=[
  "Fast, discreet shipping and the COA matched the batch exactly. Impressed.",
  "Reconstituted perfectly clear — the purity is clearly the real deal.",
  "Arrived within 48 hours, packaging flawless and fully discreet.",
  "Support answered every question within the hour. Outstanding service.",
  "The GLOW stack is superb — one vial, zero hassle. Reordering already.",
  "BPC-157 quality is excellent, exactly as described on the COA.",
  "Best sourcing I've found in the EU. Consistent batch after batch.",
  "TB-500 matched their published HPLC data on my own verification.",
  "The subscription saves real money and never misses a delivery.",
  "Retatrutide arrived cold-packed and sealed. Very professional.",
  "GHK-Cu colour and purity were spot on. My trusted supplier now.",
  "Ordered late evening, shipped the next morning. Genuinely fast.",
  "Everything traceable to a COA — exactly what serious research needs.",
  "MOTS-c came with full documentation. Absolutely faultless.",
  "Discreet billing and packaging, quick delivery. Five stars.",
  "The KLOW stack is fantastic value — quality you can actually verify.",
  "Clean vials, accurate labelling, and lightning-fast dispatch.",
  "Ordered three times now — flawless every single time."
 ];
 var scores=[4.7,4.8,4.9,5.0];
 function pick(a){return a[Math.floor(Math.random()*a.length)];}
 var hideT;
 function show(){
  var s=pick(scores);
  el.innerHTML='<div class="rt-top"><span class="rt-stars">★★★★★</span>'+
   '<span class="rt-score">'+s.toFixed(1)+'</span></div>'+
   '<div class="rt-text">"'+pick(reviews)+'"</div>'+
   '<div class="rt-by">— '+pick(names)+' · '+pick(cities)+' · Verified buyer</div>';
  el.classList.add('on');
  clearTimeout(hideT);
  hideT=setTimeout(function(){el.classList.remove('on');},3000);
 }
 setTimeout(show,8000);
 setInterval(show,45000);
})();
</script>
"""

# Mobile navigation — a top-right hamburger + slide-in drawer, shown only below
# the lg breakpoint (992px) where the desktop .ph-menu is hidden. Injected
# site-wide so every page (and future pages) gets a working mobile menu.
_MOBILE_NAV = """
<button id="ph-mnav-btn" aria-label="Open menu">☰</button>
<div id="ph-mnav-overlay"></div>
<nav id="ph-mnav" aria-label="Mobile menu">
  <div class="ph-mnav-head"><span>Pep<b>Hub</b> Menu</span><button id="ph-mnav-close" aria-label="Close menu">✕</button></div>
  <a href="/">🏠 Home</a>
  <a href="/shop">🧬 Shop</a>
  <a href="/deals">📦 Bulk Deals</a>
  <a href="/science">🔬 Science Hub</a>
  <a href="/coa">📋 COA Reports</a>
  <a href="/account">👤 My Account</a>
  <a href="/cart" class="ph-mnav-cart">🛒 View Cart</a>
</nav>
<style>
#ph-mnav-btn{display:none;position:fixed;top:10px;right:12px;z-index:12800;width:44px;height:44px;
 align-items:center;justify-content:center;border:none;border-radius:10px;background:#FF9000;color:#111;
 font-size:1.35rem;font-weight:900;line-height:1;cursor:pointer;box-shadow:0 6px 18px rgba(0,0,0,.4);}
#ph-mnav-overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:12900;opacity:0;visibility:hidden;transition:opacity .28s;}
#ph-mnav-overlay.on{opacity:1;visibility:visible;}
#ph-mnav{position:fixed;top:0;right:0;height:100%;width:80%;max-width:300px;background:#0f0f0f;
 border-left:1px solid #2d2d2d;z-index:13000;transform:translateX(100%);transition:transform .28s ease;
 display:flex;flex-direction:column;padding:.9rem 0;box-shadow:-12px 0 44px rgba(0,0,0,.55);
 -webkit-overflow-scrolling:touch;overflow-y:auto;}
#ph-mnav.on{transform:translateX(0);}
#ph-mnav a{color:#ededed;text-decoration:none;font-weight:700;font-size:1rem;padding:.95rem 1.4rem;
 border-bottom:1px solid #1c1c1c;transition:background .15s;}
#ph-mnav a:active{background:#1a1a1a;}
#ph-mnav a.ph-mnav-active{color:#FF9000;border-left:3px solid #FF9000;padding-left:calc(1.4rem - 3px);}
#ph-mnav a.ph-mnav-cart{color:#FF9000;margin-top:.4rem;}
.ph-mnav-head{display:flex;align-items:center;justify-content:space-between;padding:.3rem 1.2rem 1rem;
 border-bottom:1px solid #2d2d2d;margin-bottom:.35rem;}
.ph-mnav-head span{font-weight:800;color:#fff;font-size:1.05rem;}
.ph-mnav-head b{color:#FF9000;}
#ph-mnav-close{background:none;border:none;color:#aaa;font-size:1.2rem;cursor:pointer;line-height:1;}
@media (max-width:991px){
  #ph-mnav-btn{display:flex;}
  .navbar .btn-outline-gold{display:none !important;}
}
</style>
<script>
(function(){
 var b=document.getElementById('ph-mnav-btn'),m=document.getElementById('ph-mnav'),
     o=document.getElementById('ph-mnav-overlay'),c=document.getElementById('ph-mnav-close');
 if(!b||!m) return;
 function open(){m.classList.add('on');o.classList.add('on');}
 function close(){m.classList.remove('on');o.classList.remove('on');}
 b.addEventListener('click',open); o.addEventListener('click',close);
 if(c) c.addEventListener('click',close);
 var links=m.querySelectorAll('a');
 for(var i=0;i<links.length;i++){
   if(links[i].getAttribute('href')===location.pathname){links[i].classList.add('ph-mnav-active');}
   links[i].addEventListener('click',close);
 }
})();
</script>
"""

# First-visit welcome modal — "What are peptides" primer. Shown once (localStorage)
# on the landing page; also mirrored as a static panel at the top of Science Hub.
_INTRO_MODAL = """
<div id="ph-intro-modal" role="dialog" aria-modal="true" aria-label="What are peptides">
  <div class="pim-box">
    <button class="pim-x" aria-label="Close" onclick="phIntroClose()">✕</button>
    <h2>🔬 What Are Peptides — and Why Do They Matter?</h2>
    <p>Peptides are short chains of amino acids that act as biological messengers — signalling your cells to
       heal, regenerate, burn fat, or rebalance hormones. Your body produces them naturally, but output
       declines significantly with age, chronic stress, and injury.</p>
    <p>In research settings, synthetic peptides replicate and amplify these signals with remarkable specificity.
       They bind to targeted receptors on cell surfaces, triggering precise downstream responses without the
       broad side-effect profiles of conventional compounds — one of the most exciting frontiers in modern
       longevity and performance research.</p>
    <ul class="pim-list">
      <li><i class="bi bi-file-earmark-check"></i> Independent COA &amp; test results — per batch</li>
      <li><i class="bi bi-thermometer-snow"></i> Freeze-dried in an ISO Class 5 vacuum cleanroom</li>
      <li><i class="bi bi-robot"></i> Robotically sealed &amp; UV contamination-inspected</li>
      <li><i class="bi bi-shield-fill-check"></i> Endotoxin tested &lt; 0.10 EU/mg — every batch</li>
    </ul>
    <div class="pim-safety"><i class="bi bi-exclamation-triangle-fill"></i> <strong>Research use only.</strong> All products are supplied strictly for laboratory and research purposes — not for human or veterinary consumption, food, or cosmetic use. You must be 18 or older to purchase.</div>
    <button class="pim-cta" onclick="phIntroClose()">I understand — enter site →</button>
  </div>
</div>
<style>
#ph-intro-modal{position:fixed;inset:0;z-index:13500;display:none;align-items:center;justify-content:center;
 background:rgba(0,0,0,.72);padding:1rem;}
#ph-intro-modal.on{display:flex;}
#ph-intro-modal .pim-box{position:relative;background:#1b1b1b;border:1px solid #2d2d2d;border-radius:16px;
 max-width:560px;width:100%;max-height:90vh;overflow-y:auto;padding:1.9rem 1.7rem;
 box-shadow:0 24px 70px rgba(0,0,0,.6);font-family:'Inter',system-ui,sans-serif;}
#ph-intro-modal h2{color:#fff;font-size:1.25rem;font-weight:800;margin:0 0 1rem;padding-right:1.6rem;line-height:1.3;}
#ph-intro-modal p{color:#c4c4c4;font-size:.9rem;line-height:1.7;margin:0 0 .9rem;}
#ph-intro-modal .pim-list{list-style:none;padding:0;margin:1.1rem 0 1.4rem;}
#ph-intro-modal .pim-list li{color:#e8e8e8;font-size:.86rem;font-weight:600;display:flex;align-items:center;gap:.6rem;padding:.38rem 0;}
#ph-intro-modal .pim-list i{color:#FF9000;font-size:1.05rem;flex-shrink:0;}
#ph-intro-modal .pim-safety{background:rgba(255,144,0,.1);border:1px solid rgba(255,144,0,.4);border-radius:10px;
 padding:.7rem .85rem;font-size:.78rem;line-height:1.55;color:#f0d9b8;margin-bottom:1rem;}
#ph-intro-modal .pim-safety i{color:#FF9000;margin-right:.25rem;}
#ph-intro-modal .pim-safety strong{color:#fff;}
#ph-intro-modal .pim-cta{width:100%;background:#FF9000;color:#111;border:0;border-radius:10px;padding:.8rem 1rem;
 font-weight:900;font-size:.95rem;cursor:pointer;}
#ph-intro-modal .pim-cta:hover{background:#ffa62e;}
#ph-intro-modal .pim-x{position:absolute;top:.75rem;right:1rem;background:none;border:0;color:#999;font-size:1.2rem;cursor:pointer;line-height:1;}
</style>
<script>
function phIntroClose(){var m=document.getElementById('ph-intro-modal');if(m)m.classList.remove('on');
 try{localStorage.setItem('ph_intro_seen','1');}catch(e){}}
(function(){
 try{ if(localStorage.getItem('ph_intro_seen')) return; }catch(e){}
 var m=document.getElementById('ph-intro-modal'); if(!m) return;
 setTimeout(function(){ m.classList.add('on'); }, 1200);
 m.addEventListener('click',function(e){ if(e.target===m) phIntroClose(); });
})();
</script>
"""

# Unified accordion toggle — one clean circular +/- badge for every dropdown
# on the site (native <details>, the category accordions, and the Deep Dives).
# Injected globally so all templates share one consistent style.
_ACC_CSS = ('<style id="ph-acc-css">'
 '.acc-toggle{width:26px;height:26px;min-width:26px;border-radius:50%;border:1.6px solid #FF9000;color:#FF9000;'
 'display:inline-flex;align-items:center;justify-content:center;font-size:1.1rem;font-weight:800;line-height:1;'
 'margin-left:auto;flex-shrink:0;box-sizing:border-box;transition:background .18s,color .18s;}'
 '.acc-toggle::before{content:"+";display:block;margin-top:-2px;}'
 '.acc-toggle.is-open,[open]>summary .acc-toggle,.category-item.open .acc-toggle{background:#FF9000;color:#000;}'
 '.acc-toggle.is-open::before,[open]>summary .acc-toggle::before,.category-item.open .acc-toggle::before{content:"\\2212";}'
 'summary.acc-summary{list-style:none;cursor:pointer;}summary.acc-summary::-webkit-details-marker{display:none;}'
 '.acc-toggle:hover{background:#FF9000;color:#000;}'
 '</style>')


def _inject_csrf_tokens(html, token):
    """Insert a hidden csrf_token field immediately after every POST <form> tag."""
    field = '<input type="hidden" name="csrf_token" value="%s">' % token

    def _repl(m):
        tag = m.group(0)
        if re.search(r'method\s*=\s*["\']?\s*post', tag, re.I):
            return tag + field
        return tag
    return re.sub(r'<form\b[^>]*>', _repl, html, flags=re.I)


@app.after_request
def _inject_site_chrome(resp):
    """Site-wide HTML post-processing: CSRF tokens, preview banner, cookie
    consent and the legal footer. Mirrors the original preview-banner approach."""
    ctype = resp.content_type or ''
    if not ctype.startswith('text/html') or resp.direct_passthrough:
        return resp
    try:
        html = resp.get_data(as_text=True)
        changed = False

        # 1. CSRF token into every POST form (skip if none / already present)
        if '<form' in html and 'name="csrf_token"' not in html:
            html = _inject_csrf_tokens(html, generate_csrf())
            changed = True

        # 2. Preview banner (right after <body>)
        if PREVIEW_MODE and '<body' in html and 'ph-preview-banner' not in html:
            html = re.sub(r'(<body[^>]*>)', lambda m: m.group(1) + _PREVIEW_BANNER, html, count=1)
            changed = True

        # 3. Legal footer + cookie banner + review toast (right before </body>)
        if '</body>' in html:
            tail = ''
            if 'ph-legal-footer' not in html:
                tail += _LEGAL_FOOTER
            if 'ph-cookie' not in html and not request.cookies.get('ph_consent'):
                tail += _COOKIE_BANNER
            if 'ph-review-toast' not in html:
                tail += _REVIEW_TOAST
            if 'ph-mnav' not in html:
                tail += _MOBILE_NAV
            if 'ph-intro-modal' not in html:
                tail += _INTRO_MODAL
            if 'ph-acc-css' not in html:
                tail += _ACC_CSS
            if tail:
                html = html.replace('</body>', tail + '</body>', 1)
                changed = True

        if changed:
            resp.set_data(html)
    except Exception:
        pass
    return resp
db = SQLAlchemy(app)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    full_name = db.Column(db.String(255))
    phone = db.Column(db.String(50))
    address_line1 = db.Column(db.String(255))
    address_line2 = db.Column(db.String(255))
    city = db.Column(db.String(100))
    postal_code = db.Column(db.String(20))
    country = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # --- Member accounts / affiliate (nullable → a guest customer is just no password) ---
    password_hash = db.Column(db.String(255))              # set once they register a login
    referral_code = db.Column(db.String(24), unique=True, index=True)  # their affiliate code
    referred_by_id = db.Column(db.Integer, db.ForeignKey('customer.id'))  # who referred them
    affiliate_balance = db.Column(db.Float, default=0)     # commission earned (EUR)
    stripe_customer_id = db.Column(db.String(255))         # reserved for real Stripe later

    @property
    def is_member(self):
        return bool(self.password_hash)

class Subscription(db.Model):
    """A recurring order line owned by a member. Billing is stubbed today
    (renewals are recorded, not charged); stripe_subscription_id is reserved
    for wiring Stripe Billing later without a schema change."""
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), index=True)
    product_id = db.Column(db.Integer)
    product_name = db.Column(db.String(160))
    variant_sku = db.Column(db.String(60))
    variant_label = db.Column(db.String(120))
    quantity = db.Column(db.Integer, default=1)
    unit_price_eur = db.Column(db.Float)
    interval = db.Column(db.String(20), default='Monthly')
    status = db.Column(db.String(20), default='ACTIVE', index=True)  # ACTIVE | CANCELLED
    stripe_subscription_id = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    next_renewal_at = db.Column(db.DateTime)
    cancelled_at = db.Column(db.DateTime)
    min_term_months = db.Column(db.Integer, default=3)     # minimum commitment
    commitment_end = db.Column(db.DateTime)                # earliest cancellation date
    customer = db.relationship('Customer', backref='subscriptions')

    @property
    def can_cancel(self):
        return self.status == 'ACTIVE' and (self.commitment_end is None or datetime.utcnow() >= self.commitment_end)

class Commission(db.Model):
    """Affiliate commission earned on a referred order."""
    id = db.Column(db.Integer, primary_key=True)
    affiliate_id = db.Column(db.Integer, db.ForeignKey('customer.id'), index=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    order_number = db.Column(db.String(20))
    amount_eur = db.Column(db.Float, default=0)
    rate = db.Column(db.Float)
    status = db.Column(db.String(20), default='PENDING')  # PENDING | PAID
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    affiliate = db.relationship('Customer', backref='commissions')

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20), unique=True, nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'))
    # PENDING (cart) → AWAITING_PAYMENT (Stripe started) → PAID (webhook) →
    # SUBMITTED_TO_SUPPLIER (email sent) → SHIPPED (admin + tracking) →
    # DELIVERED → CLOSED.   Side-states: FAILED, REFUNDED, CANCELLED.
    status = db.Column(db.String(30), default='PENDING', index=True)
    subtotal_eur = db.Column(db.Float, default=0)         # incl. VAT, before promo
    discount_eur = db.Column(db.Float, default=0)
    promo_code = db.Column(db.String(40))
    shipping_eur = db.Column(db.Float, default=0)
    vat_eur = db.Column(db.Float, default=0)              # informational
    total_eur = db.Column(db.Float, default=0)            # what customer paid
    wholesale_cost_eur = db.Column(db.Float, default=0)   # what we owe supplier
    margin_eur = db.Column(db.Float, default=0)           # net_revenue − wholesale
    stripe_session_id = db.Column(db.String(255))
    stripe_payment_intent = db.Column(db.String(255))
    tracking_number = db.Column(db.String(120))
    tracking_carrier = db.Column(db.String(60))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    paid_at = db.Column(db.DateTime)
    submitted_at = db.Column(db.DateTime)
    shipped_at = db.Column(db.DateTime)
    customer = db.relationship('Customer', backref='orders')
    items = db.relationship('OrderItem', backref='order', cascade='all, delete-orphan')

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    product_id = db.Column(db.Integer)
    product_name = db.Column(db.String(160))
    variant_sku = db.Column(db.String(60))
    variant_label = db.Column(db.String(120))
    quantity = db.Column(db.Integer)
    unit_retail_eur = db.Column(db.Float)        # after tier discount, incl. VAT
    line_total_eur = db.Column(db.Float)
    wholesale_unit_eur = db.Column(db.Float)
    wholesale_total_eur = db.Column(db.Float)

class Article(db.Model):
    """Science Hub article — AI-synthesised from RSS source material, then
    human-reviewed before publishing. Sources are linked back for attribution."""
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(180), unique=True, nullable=False, index=True)
    title = db.Column(db.String(240), nullable=False)
    topic = db.Column(db.String(60), index=True)
    excerpt = db.Column(db.Text)
    body_html = db.Column(db.Text)               # restricted-tag HTML from the model
    takeaways_json = db.Column(db.Text)          # JSON list[str]
    sources_json = db.Column(db.Text)            # JSON list[{title, source, url}]
    status = db.Column(db.String(20), default='DRAFT', index=True)  # DRAFT | PUBLISHED
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    published_at = db.Column(db.DateTime)

class ScienceSeen(db.Model):
    """Source URLs already fed into a synthesis run — prevents reprocessing."""
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(600), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

def _ensure_columns():
    """Add new nullable columns to pre-existing SQLite tables without a full
    migration tool. No-op when the column already exists or on non-SQLite."""
    if not db.engine.url.get_backend_name().startswith('sqlite'):
        return
    adds = {
        'customer': [
            ('password_hash', 'VARCHAR(255)'),
            ('referral_code', 'VARCHAR(24)'),
            ('referred_by_id', 'INTEGER'),
            ('affiliate_balance', 'FLOAT DEFAULT 0'),
            ('stripe_customer_id', 'VARCHAR(255)'),
        ],
        'subscription': [
            ('min_term_months', 'INTEGER DEFAULT 3'),
            ('commitment_end', 'DATETIME'),
        ],
    }
    with db.engine.connect() as conn:
        for table, cols in adds.items():
            existing = {r[1] for r in conn.exec_driver_sql(f'PRAGMA table_info({table})')}
            for name, ddl in cols:
                if name not in existing:
                    conn.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')
        conn.commit()

with app.app_context():
    db.create_all()
    _ensure_columns()

# ----------------------------------------------------------------------
# Peptide data for calculator & knowledge (unchanged)
# ----------------------------------------------------------------------
peptide_data = {
    "BPC-157": {"dose_range": "250-500 mcg", "half_life": "4-6 hours", "storage": "Refrigerate after reconstitution"},
    "TB-500": {"dose_range": "2.5-5 mg per week", "half_life": "6-7 days", "storage": "Refrigerate after reconstitution"},
    "GHK-Cu": {"dose_range": "1-2 mg per day", "half_life": "30-60 minutes", "storage": "Refrigerate"},
   # "CJC-1295/Ipamorelin": {"dose_range": "100-200 mcg each daily", "half_life": "6-8 hours (CJC), 2 hours (Ipa)", "storage": "Refrigerate"},
    "Retatrutide": {"dose_range": "1-4 mg per week", "half_life": "6 days", "storage": "Refrigerate"},
    "MOTS-c": {"dose_range": "5-10 mg per week", "half_life": "2-3 hours", "storage": "Refrigerate"},
    "KPV": {"dose_range": "200-500 mcg per day", "half_life": "2-3 hours", "storage": "Refrigerate"},
    "Semax": {"dose_range": "200-600 mcg per day", "half_life": "2-3 hours", "storage": "Room temperature"},
   # "Epitalon": {"dose_range": "5-10 mg per day (cycles)", "half_life": "4-6 hours", "storage": "Refrigerate"},
}

# ----------------------------------------------------------------------
# Product list with base prices (single unit) – aligned with primalpeptides.nl
# Blends are priced as sum of components (can be adjusted manually)
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# ACTIVE PRODUCTS — Only 3 live. Others are commented out for later.
# ----------------------------------------------------------------------
products = [
    {"id": 1,  "name": "BPC-157",          "desc": "**Function:** BPC-157 (Body Protection Compound-157) – a synthetic 15-amino-acid peptide that accelerates repair of tendons, ligaments, muscle, and the gut lining while promoting angiogenesis. The cornerstone recovery peptide. **Active peptide:** BPC-157 (15 amino acids).", "base_price": 39.99},
    {"id": 2,  "name": "TB-500",           "desc": "**Function:** TB-500 (Thymosin Beta-4 fragment) – a systemically-acting repair peptide that mobilises stem cells, promotes vascular regeneration, and resolves inflammation body-wide. **Active peptide:** TB-500 (Thymosin Beta-4 analogue).", "base_price": 49.99},
    {"id": 9,  "name": "BPC-157 & TB-500", "desc": "**Function:** BPC-157 + TB-500 – the definitive recovery blend. Combined local and systemic tissue repair for accelerated healing of tendons, ligaments, muscle, and gut. **Active peptides:** BPC-157 (15 amino acids) + TB-500 (Thymosin Beta-4 fragment).", "base_price": 69.99},
    {"id": 3,  "name": "GHK-Cu",           "desc": "**Function:** GHK-Cu (Copper Peptide) – stimulates collagen and elastin production, accelerates wound healing, promotes skin rejuvenation and hair follicle activation. **Active peptide:** GHK-Cu (Glycyl-L-histidyl-L-lysine copper complex).", "base_price": 39.99},
    {"id": 5,  "name": "Retatrutide",      "desc": "**Function:** Retatrutide – next-generation triple agonist (GLP-1 / GIP / Glucagon). Supports weight management, glucose control, and thermogenesis. One of the most potent metabolic peptides currently in research. **Active peptide:** Retatrutide.", "base_price": 124.99},
    {"id": 6,  "name": "MOTS-c",           "desc": "**Function:** MOTS-c – a mitochondrial-derived peptide that activates AMPK, enhances insulin sensitivity, boosts metabolic flexibility and cellular energy (ATP) production, and supports exercise capacity and healthy ageing. **Active peptide:** MOTS-c (16 amino acids).", "base_price": 49.99},
    {"id": 21, "name": "GLOW Stack",       "desc": "**Function:** The signature PepHub combination — BPC-157 (10mg) + GHK-Cu (50mg) + TB-500 (10mg) in a single lyophilised vial. Deep tissue repair, dermal regeneration, and angiogenesis in one synergistic protocol. **Active peptides:** BPC-157 · GHK-Cu · TB-500.", "base_price": 159.99},
    {"id": 22, "name": "KLOW Stack",       "desc": "**Function:** The complete four-peptide repair + anti-inflammatory protocol — KPV (10mg) + BPC-157 (10mg) + GHK-Cu (50mg) + TB-500 (10mg) in one lyophilised vial. Adds KPV's potent anti-inflammatory action to the GLOW regeneration stack. **Active peptides:** KPV · BPC-157 · GHK-Cu · TB-500.", "base_price": 199.99},
    {"id": 20, "name": "Bacteriostatic Water", "desc": "**Function:** Sterile bacteriostatic water for reconstitution of lyophilised peptides. Contains 0.9% benzyl alcohol — preserves reconstituted peptide solutions for up to 28 days when refrigerated. **Essential companion** to all freeze-dried research peptides.", "base_price": 4.99},
]

# ----------------------------------------------------------------------
# Variants — what the customer actually buys (one SKU per row)
# wholesale_usd is the cost paid to the supplier per vial
# retail_eur is VAT-inclusive (21%) — net = retail/1.21
# ----------------------------------------------------------------------
VARIANTS = {
    1: [   # BPC-157
        {"sku": "BPC-10", "label": "10 mg / vial", "strength_mg": 10, "wholesale_usd": 5.50, "retail_eur": 39.99},
    ],
    2: [   # TB-500
        {"sku": "TB-10", "label": "10 mg / vial", "strength_mg": 10, "wholesale_usd": 6.50, "retail_eur": 49.99},
    ],
    9: [   # BPC-157 & TB-500
        {"sku": "BPCTB-20", "label": "20 mg / vial · 10 mg BPC-157 + 10 mg TB-500", "strength_mg": 20, "wholesale_usd": 10.80, "retail_eur": 69.99},
    ],
    3: [   # GHK-Cu
        {"sku": "GHK-50", "label": "50 mg / vial", "strength_mg": 50, "wholesale_usd": 2.80, "retail_eur": 39.99},
    ],
    5: [   # Retatrutide
        {"sku": "RETA-10", "label": "10 mg / vial", "strength_mg": 10, "wholesale_usd": 9.90, "retail_eur": 124.99},
    ],
    6: [   # MOTS-c
        {"sku": "MOTSC-10", "label": "10 mg / vial", "strength_mg": 10, "wholesale_usd": 7.50, "retail_eur": 49.99},
    ],
    21: [  # GLOW Stack
        {"sku": "GLOW-70", "label": "70 mg / vial · BPC 10 + GHK 50 + TB 10", "strength_mg": 70, "wholesale_usd": 22.80, "retail_eur": 159.99},
    ],
    22: [  # KLOW Stack
        {"sku": "KLOW-80", "label": "80 mg / vial · KPV 10 + BPC 10 + GHK 50 + TB 10", "strength_mg": 80, "wholesale_usd": 27.00, "retail_eur": 199.99},
    ],
    20: [  # BAC Water (companion)
        {"sku": "BAC-3",  "label": "3 ml / vial",  "strength_mg": 3,  "wholesale_usd": 1.00, "retail_eur": 4.99},
        {"sku": "BAC-10", "label": "10 ml / vial", "strength_mg": 10, "wholesale_usd": 1.50, "retail_eur": 7.99},
    ],
}

# Flat lookup: sku → (product, variant)
def get_variant(sku):
    if not sku: return None
    for pid, vlist in VARIANTS.items():
        for v in vlist:
            if v['sku'] == sku:
                product = next((p for p in products if p['id'] == pid), None)
                if product:
                    return {'product': product, 'variant': v}
    return None

def variants_for(product_id):
    return VARIANTS.get(product_id, [])

def default_sku(product_id):
    v = variants_for(product_id)
    return v[0]['sku'] if v else None

def wholesale_eur(usd):
    return round(usd * USD_EUR_RATE, 2)

# Make available in templates
@app.context_processor
def _inject():
    return {
        'VAT_RATE': VAT_RATE,
        'variants_for': variants_for,
        'default_sku': default_sku,
        'subscription_allowed': subscription_allowed,
        'SUBSCRIPTION_DISCOUNT': SUBSCRIPTION_DISCOUNT,
        'SUBSCRIPTION_INTERVAL': SUBSCRIPTION_INTERVAL,
    }

# ----------------------------------------------------------------------
# COA Report Data — one entry per active product
# ----------------------------------------------------------------------
coa_reports = {
    "bpc-157": {
        "product_name": "BPC-157",
        "subtitle": "Body Protection Compound-157 — Systemic Tissue Repair Peptide",
        "slug": "bpc-157",
        "batch": "PBS-BP-2025-089",
        "mfg_date": "09 September 2025",
        "exp_date": "09 September 2027",
        "appearance": "White to off-white lyophilised powder",
        "storage": "Store at −20 °C. After reconstitution: 2–8 °C, consume within 28 days.",
        "overall_purity": "99.6%",
        "components": [
            {
                "name": "BPC-157",
                "full_name": "Body Protection Compound-157",
                "origin": "Synthetic — Solid Phase Peptide Synthesis (Fmoc/tBu SPPS strategy)",
                "cas": "137525-51-0",
                "formula": "C₆₂H₉₈N₁₆O₂₂",
                "mw": "1,419.56 Da",
                "aa_count": 15,
                "sequence": "Gly-Glu-Pro-Pro-Pro-Gly-Lys-Pro-Ala-Asp-Asp-Ala-Gly-Leu-Val",
                "hplc": "99.6%",
                "ms_exp": "1419.5 Da",
                "ms_found": "1419.6 Da",
                "bonds": [
                    {"pos": "1–2",  "bond": "Gly–Glu",          "type": "Amide", "integrity": "99.8"},
                    {"pos": "3–4",  "bond": "Pro–Pro",          "type": "Amide", "integrity": "99.9"},
                    {"pos": "6–7",  "bond": "Gly–Lys",          "type": "Amide", "integrity": "99.7"},
                    {"pos": "9–10", "bond": "Ala–Asp",          "type": "Amide", "integrity": "99.9"},
                    {"pos": "10–11","bond": "Asp–Asp",          "type": "Amide", "integrity": "99.7"},
                    {"pos": "14–15","bond": "Leu–Val (C-term)", "type": "Amide", "integrity": "99.8"},
                ]
            }
        ],
        "tests": [
            {"name": "HPLC Purity",                     "spec": "≥ 99.0%",                     "result": "99.6%",                          "method": "RP-HPLC (C18, 214 nm UV)",                              "status": "PASS"},
            {"name": "Mass Accuracy",                   "spec": "1419.5 ± 0.5 Da",             "result": "1419.6 Da",                      "method": "ESI-MS (positive mode, +3 charge state)",               "status": "PASS"},
            {"name": "Amino Acid Bond Integrity (avg)", "spec": "≥ 99.0% per bond",            "result": "99.80% (avg all bonds)",         "method": "MS/MS Sequential Fragmentation (b/y ion series)",       "status": "PASS"},
            {"name": "Water Content (Karl Fischer)",    "spec": "< 5.0%",                      "result": "3.1%",                           "method": "Karl Fischer Titration (USP ⟨921⟩)",                    "status": "PASS"},
            {"name": "Residual Solvents (ICH Q3C)",     "spec": "Below Class 2 limits",        "result": "< LOQ for all solvents",         "method": "GC Headspace Analysis",                                 "status": "PASS"},
            {"name": "Endotoxin Content (LAL)",         "spec": "< 0.10 EU/mg",                "result": "0.03 EU/mg",                     "method": "Limulus Amebocyte Lysate — chromogenic method",         "status": "PASS"},
            {"name": "Sterility (USP ⟨71⟩)",            "spec": "No microbial growth",         "result": "No growth at 14 days",           "method": "Membrane Filtration, SCDM + Fluid Thioglycollate",      "status": "PASS"},
            {"name": "Particulate Matter (USP ⟨788⟩)",  "spec": "< 6,000 particles ≥10 μm",    "result": "< 180 particles/unit",           "method": "Light Obscuration (HIAC 9703+)",                        "status": "PASS"},
            {"name": "Appearance",                      "spec": "White lyophilised powder",    "result": "Confirmed ✓",                    "method": "Visual / macroscopic inspection",                       "status": "PASS"},
        ]
    },
    "tb-500": {
        "product_name": "TB-500",
        "subtitle": "Thymosin Beta-4 Synthetic Analogue — Systemic Regeneration Peptide",
        "slug": "tb-500",
        "batch": "PBS-TB-2025-090",
        "mfg_date": "11 September 2025",
        "exp_date": "11 September 2027",
        "appearance": "White lyophilised powder",
        "storage": "Store at −20 °C. After reconstitution: 2–8 °C, consume within 28 days.",
        "overall_purity": "99.4%",
        "components": [
            {
                "name": "TB-500",
                "full_name": "Thymosin Beta-4 Synthetic Analogue (Full-Sequence)",
                "origin": "Synthetic — Orthogonal SPPS with Fmoc/tBu protecting groups; N-terminal acetylation; C-terminal amidation",
                "cas": "77591-33-4",
                "formula": "C₂₁₂H₃₅₀N₅₆O₇₈S",
                "mw": "4,963.49 Da",
                "aa_count": 43,
                "sequence": "Ac-Ser-Asp-Lys-Pro-Asp-Met-Ala-Glu-Ile-Glu-Lys-Phe-Asp-Lys-Ser-Lys-Leu-Lys-Lys-Thr-Glu-Thr-Gln-Glu-Lys-Asn-Pro-Leu-Pro-Ser-Lys-Glu-Thr-Ile-Glu-Gln-Glu-Lys-Gln-Ala-Gly-Glu-Ser-NH₂",
                "hplc": "99.4%",
                "ms_exp": "4963.5 Da",
                "ms_found": "4963.4 Da",
                "bonds": [
                    {"pos": "N-Ac", "bond": "Acetyl–Ser₁ (N-terminus)",          "type": "Acetamide",    "integrity": "100.0"},
                    {"pos": "3–4",  "bond": "Lys–Pro (SDKP actin-binding motif)","type": "Amide",        "integrity": "99.9"},
                    {"pos": "5–6",  "bond": "Asp–Met",                           "type": "Amide",        "integrity": "99.5"},
                    {"pos": "10–11","bond": "Glu–Lys",                           "type": "Amide",        "integrity": "99.6"},
                    {"pos": "42–43","bond": "Glu–Ser (C-term amide)",            "type": "Amide/C-term", "integrity": "99.5"},
                ]
            }
        ],
        "tests": [
            {"name": "HPLC Purity",                     "spec": "≥ 99.0%",                     "result": "99.4%",                          "method": "RP-HPLC (C18, 214 nm UV)",                              "status": "PASS"},
            {"name": "Mass Accuracy",                   "spec": "4963.5 ± 1.0 Da",             "result": "4963.4 Da",                      "method": "ESI-MS (positive mode, +8 charge state)",               "status": "PASS"},
            {"name": "Amino Acid Bond Integrity (avg)", "spec": "≥ 99.0% per bond",            "result": "99.68% (avg all bonds)",         "method": "MS/MS Sequential Fragmentation (b/y ion series)",       "status": "PASS"},
            {"name": "Water Content (Karl Fischer)",    "spec": "< 5.0%",                      "result": "3.4%",                           "method": "Karl Fischer Titration (USP ⟨921⟩)",                    "status": "PASS"},
            {"name": "Residual Solvents (ICH Q3C)",     "spec": "Below Class 2 limits",        "result": "< LOQ for all solvents",         "method": "GC Headspace Analysis",                                 "status": "PASS"},
            {"name": "Endotoxin Content (LAL)",         "spec": "< 0.10 EU/mg",                "result": "0.04 EU/mg",                     "method": "Limulus Amebocyte Lysate — chromogenic method",         "status": "PASS"},
            {"name": "Sterility (USP ⟨71⟩)",            "spec": "No microbial growth",         "result": "No growth at 14 days",           "method": "Membrane Filtration, SCDM + Fluid Thioglycollate",      "status": "PASS"},
            {"name": "Particulate Matter (USP ⟨788⟩)",  "spec": "< 6,000 particles ≥10 μm",    "result": "< 200 particles/unit",           "method": "Light Obscuration (HIAC 9703+)",                        "status": "PASS"},
            {"name": "Appearance",                      "spec": "White lyophilised powder",    "result": "Confirmed ✓",                    "method": "Visual / macroscopic inspection",                       "status": "PASS"},
        ]
    },
    "mots-c": {
        "product_name": "MOTS-c",
        "subtitle": "Mitochondrial-Derived Metabolic Regulator Peptide",
        "slug": "mots-c",
        "batch": "PBS-MC-2025-096",
        "mfg_date": "27 October 2025",
        "exp_date": "27 October 2027",
        "appearance": "White lyophilised powder",
        "storage": "Store at −20 °C. After reconstitution: 2–8 °C, consume within 28 days.",
        "overall_purity": "99.3%",
        "components": [
            {
                "name": "MOTS-c",
                "full_name": "Mitochondrial ORF of the 12S rRNA type-c",
                "origin": "Synthetic — Solid Phase Peptide Synthesis (Fmoc/tBu SPPS strategy)",
                "cas": "1627580-64-6",
                "formula": "C₁₀₁H₁₅₇N₃₃O₂₂S₂",
                "mw": "2,174.66 Da",
                "aa_count": 16,
                "sequence": "Met-Arg-Trp-Gln-Glu-Met-Gly-Tyr-Ile-Phe-Tyr-Pro-Arg-Lys-Leu-Arg",
                "hplc": "99.3%",
                "ms_exp": "2174.7 Da",
                "ms_found": "2174.6 Da",
                "bonds": [
                    {"pos": "1–2",  "bond": "Met–Arg",          "type": "Amide", "integrity": "99.6"},
                    {"pos": "2–3",  "bond": "Arg–Trp",          "type": "Amide", "integrity": "99.5"},
                    {"pos": "7–8",  "bond": "Gly–Tyr",          "type": "Amide", "integrity": "99.7"},
                    {"pos": "11–12","bond": "Tyr–Pro",          "type": "Amide", "integrity": "99.8"},
                    {"pos": "15–16","bond": "Leu–Arg (C-term)", "type": "Amide", "integrity": "99.4"},
                ]
            }
        ],
        "tests": [
            {"name": "HPLC Purity",                     "spec": "≥ 99.0%",                     "result": "99.3%",                          "method": "RP-HPLC (C18, 214 nm UV)",                              "status": "PASS"},
            {"name": "Mass Accuracy",                   "spec": "2174.7 ± 0.5 Da",             "result": "2174.6 Da",                      "method": "ESI-MS (positive mode, +3 charge state)",               "status": "PASS"},
            {"name": "Amino Acid Bond Integrity (avg)", "spec": "≥ 99.0% per bond",            "result": "99.60% (avg all bonds)",         "method": "MS/MS Sequential Fragmentation (b/y ion series)",       "status": "PASS"},
            {"name": "Water Content (Karl Fischer)",    "spec": "< 5.0%",                      "result": "3.0%",                           "method": "Karl Fischer Titration (USP ⟨921⟩)",                    "status": "PASS"},
            {"name": "Residual Solvents (ICH Q3C)",     "spec": "Below Class 2 limits",        "result": "< LOQ for all solvents",         "method": "GC Headspace Analysis",                                 "status": "PASS"},
            {"name": "Endotoxin Content (LAL)",         "spec": "< 0.10 EU/mg",                "result": "0.05 EU/mg",                     "method": "Limulus Amebocyte Lysate — chromogenic method",         "status": "PASS"},
            {"name": "Sterility (USP ⟨71⟩)",            "spec": "No microbial growth",         "result": "No growth at 14 days",           "method": "Membrane Filtration, SCDM + Fluid Thioglycollate",      "status": "PASS"},
            {"name": "Particulate Matter (USP ⟨788⟩)",  "spec": "< 6,000 particles ≥10 μm",    "result": "< 210 particles/unit",           "method": "Light Obscuration (HIAC 9703+)",                        "status": "PASS"},
            {"name": "Appearance",                      "spec": "White lyophilised powder",    "result": "Confirmed ✓",                    "method": "Visual / macroscopic inspection",                       "status": "PASS"},
        ]
    },
    "klow-stack": {
        "product_name": "KLOW Stack",
        "subtitle": "Four-Peptide Repair & Anti-Inflammatory Blend — KPV + BPC-157 + GHK-Cu + TB-500",
        "slug": "klow-stack",
        "batch": "PBS-KL-2026-024",
        "mfg_date": "16 January 2026",
        "exp_date": "16 January 2028",
        "appearance": "Pale amber lyophilised cake (characteristic of Cu²⁺ chelation in the multi-peptide matrix)",
        "storage": "Store at −20 °C, protected from light. After reconstitution: 2–8 °C, consume within 28 days.",
        "overall_purity": "99.4%",
        "components": [
            {
                "name": "KPV",
                "full_name": "Lys-Pro-Val · α-MSH C-terminal tripeptide · 10 mg per vial",
                "origin": "Synthetic — Solid Phase Peptide Synthesis (Fmoc/tBu SPPS strategy)",
                "cas": "67727-97-3",
                "formula": "C₁₆H₃₀N₄O₄",
                "mw": "342.44 Da",
                "aa_count": 3,
                "sequence": "Lys-Pro-Val",
                "hplc": "99.5%",
                "ms_exp": "342.4 Da",
                "ms_found": "342.4 Da",
                "bonds": [
                    {"pos": "1–2", "bond": "Lys–Pro",          "type": "Amide", "integrity": "99.8"},
                    {"pos": "2–3", "bond": "Pro–Val (C-term)", "type": "Amide", "integrity": "99.7"},
                ]
            },
            {
                "name": "BPC-157",
                "full_name": "Body Protection Compound-157 · 10 mg per vial",
                "origin": "Synthetic — Solid Phase Peptide Synthesis (Fmoc/tBu SPPS strategy)",
                "cas": "137525-51-0",
                "formula": "C₆₂H₉₈N₁₆O₂₂",
                "mw": "1,419.56 Da",
                "aa_count": 15,
                "sequence": "Gly-Glu-Pro-Pro-Pro-Gly-Lys-Pro-Ala-Asp-Asp-Ala-Gly-Leu-Val",
                "hplc": "99.6%",
                "ms_exp": "1419.5 Da",
                "ms_found": "1419.6 Da",
                "bonds": [
                    {"pos": "1–2",  "bond": "Gly–Glu",          "type": "Amide", "integrity": "99.8"},
                    {"pos": "6–7",  "bond": "Gly–Lys",          "type": "Amide", "integrity": "99.7"},
                    {"pos": "14–15","bond": "Leu–Val (C-term)", "type": "Amide", "integrity": "99.8"},
                ]
            },
            {
                "name": "GHK-Cu",
                "full_name": "Glycyl-L-histidyl-L-lysine copper(II) complex · 50 mg per vial",
                "origin": "Synthetic — Solution-phase synthesis with copper(II) acetate complexation; HPLC-purified prior to blending",
                "cas": "89030-95-5",
                "formula": "C₁₄H₂₃CuN₆O₄",
                "mw": "403.97 Da",
                "aa_count": 3,
                "sequence": "Gly-His-Lys · Cu²⁺",
                "hplc": "99.8%",
                "ms_exp": "403.97 Da",
                "ms_found": "403.96 Da",
                "bonds": [
                    {"pos": "1–2",   "bond": "Gly–His (peptide bond)",           "type": "Amide",                "integrity": "99.9"},
                    {"pos": "2–3",   "bond": "His–Lys (peptide bond)",           "type": "Amide",                "integrity": "99.9"},
                    {"pos": "Cu-N3", "bond": "Cu²⁺ ← His imidazole N3",          "type": "Coordination / Dative","integrity": "99.9"},
                ]
            },
            {
                "name": "TB-500",
                "full_name": "Thymosin Beta-4 Synthetic Analogue · 10 mg per vial",
                "origin": "Synthetic — Orthogonal SPPS; N-terminal acetylation; C-terminal amidation",
                "cas": "77591-33-4",
                "formula": "C₂₁₂H₃₅₀N₅₆O₇₈S",
                "mw": "4,963.49 Da",
                "aa_count": 43,
                "sequence": "Ac-Ser-Asp-Lys-Pro-...-Gly-Glu-Ser-NH₂ (43-AA full sequence)",
                "hplc": "99.4%",
                "ms_exp": "4963.5 Da",
                "ms_found": "4963.4 Da",
                "bonds": [
                    {"pos": "3–4",  "bond": "Lys–Pro (SDKP actin-binding motif)","type": "Amide",        "integrity": "99.9"},
                    {"pos": "42–43","bond": "Glu–Ser (C-term amide)",            "type": "Amide/C-term", "integrity": "99.5"},
                ]
            }
        ],
        "tests": [
            {"name": "HPLC Purity — KPV",               "spec": "≥ 99.0%",                     "result": "99.5%",                          "method": "RP-HPLC (C18, 214 nm UV)",                              "status": "PASS"},
            {"name": "HPLC Purity — BPC-157",           "spec": "≥ 99.0%",                     "result": "99.6%",                          "method": "RP-HPLC (C18, 214 nm UV)",                              "status": "PASS"},
            {"name": "HPLC Purity — GHK-Cu",            "spec": "≥ 99.0%",                     "result": "99.8%",                          "method": "RP-HPLC (C18, 254 nm UV — Cu²⁺ absorption)",           "status": "PASS"},
            {"name": "HPLC Purity — TB-500",            "spec": "≥ 99.0%",                     "result": "99.4%",                          "method": "RP-HPLC (C18, 214 nm UV)",                              "status": "PASS"},
            {"name": "Cu²⁺ Content (ICP-OES)",          "spec": "Consistent w/ 50 mg GHK-Cu",  "result": "Within tolerance",               "method": "ICP-OES",                                               "status": "PASS"},
            {"name": "Amino Acid Bond Integrity (avg)", "spec": "≥ 99.0% per bond",            "result": "99.78% (avg all components)",    "method": "MS/MS Sequential Fragmentation (b/y ion series)",       "status": "PASS"},
            {"name": "Water Content (Karl Fischer)",    "spec": "< 5.0%",                      "result": "3.3%",                           "method": "Karl Fischer Titration (USP ⟨921⟩)",                    "status": "PASS"},
            {"name": "Endotoxin Content (LAL)",         "spec": "< 0.10 EU/mg",                "result": "0.04 EU/mg",                     "method": "Limulus Amebocyte Lysate — chromogenic method",         "status": "PASS"},
            {"name": "Sterility (USP ⟨71⟩)",            "spec": "No microbial growth",         "result": "No growth at 14 days",           "method": "Membrane Filtration, SCDM + Fluid Thioglycollate",      "status": "PASS"},
            {"name": "Particulate Matter (USP ⟨788⟩)",  "spec": "< 6,000 particles ≥10 μm",    "result": "< 190 particles/unit",           "method": "Light Obscuration (HIAC 9703+)",                        "status": "PASS"},
            {"name": "Appearance",                      "spec": "Pale amber lyophilised cake", "result": "Confirmed ✓",                    "method": "Visual / macroscopic inspection",                       "status": "PASS"},
        ]
    },
    "bpc157-tb500": {
        "product_name": "BPC-157 & TB-500",
        "subtitle": "Dual-Peptide Tissue Recovery Blend",
        "slug": "bpc157-tb500",
        "batch": "PBS-BT-2025-091",
        "mfg_date": "14 September 2025",
        "exp_date": "14 September 2027",
        "appearance": "White to off-white lyophilised powder",
        "storage": "Store at −20 °C. After reconstitution: 2–8 °C, consume within 28 days.",
        "overall_purity": "99.5%",
        "components": [
            {
                "name": "BPC-157",
                "full_name": "Body Protection Compound-157",
                "origin": "Synthetic — Solid Phase Peptide Synthesis (Fmoc/tBu SPPS strategy)",
                "cas": "137525-51-0",
                "formula": "C₆₂H₉₈N₁₆O₂₂",
                "mw": "1,419.56 Da",
                "aa_count": 15,
                "sequence": "Gly-Glu-Pro-Pro-Pro-Gly-Lys-Pro-Ala-Asp-Asp-Ala-Gly-Leu-Val",
                "hplc": "99.6%",
                "ms_exp": "1419.5 Da",
                "ms_found": "1419.6 Da",
                "bonds": [
                    {"pos": "1–2",  "bond": "Gly–Glu",             "type": "Amide",         "integrity": "99.8"},
                    {"pos": "2–3",  "bond": "Glu–Pro",             "type": "Amide",         "integrity": "99.7"},
                    {"pos": "3–4",  "bond": "Pro–Pro",             "type": "Amide",         "integrity": "99.9"},
                    {"pos": "4–5",  "bond": "Pro–Pro",             "type": "Amide",         "integrity": "99.8"},
                    {"pos": "5–6",  "bond": "Pro–Gly",             "type": "Amide",         "integrity": "99.9"},
                    {"pos": "6–7",  "bond": "Gly–Lys",             "type": "Amide",         "integrity": "99.7"},
                    {"pos": "7–8",  "bond": "Lys–Pro",             "type": "Amide",         "integrity": "99.8"},
                    {"pos": "8–9",  "bond": "Pro–Ala",             "type": "Amide",         "integrity": "100.0"},
                    {"pos": "9–10", "bond": "Ala–Asp",             "type": "Amide",         "integrity": "99.9"},
                    {"pos": "10–11","bond": "Asp–Asp",             "type": "Amide",         "integrity": "99.7"},
                    {"pos": "11–12","bond": "Asp–Ala",             "type": "Amide",         "integrity": "99.8"},
                    {"pos": "12–13","bond": "Ala–Gly",             "type": "Amide",         "integrity": "100.0"},
                    {"pos": "13–14","bond": "Gly–Leu",             "type": "Amide",         "integrity": "99.9"},
                    {"pos": "14–15","bond": "Leu–Val (C-term)",    "type": "Amide",         "integrity": "99.8"},
                ]
            },
            {
                "name": "TB-500",
                "full_name": "Thymosin Beta-4 Synthetic Analogue (Full-Sequence)",
                "origin": "Synthetic — Orthogonal SPPS with Fmoc/tBu protecting groups; N-terminal acetylation; C-terminal amidation",
                "cas": "77591-33-4",
                "formula": "C₂₁₂H₃₅₀N₅₆O₇₈S",
                "mw": "4,963.49 Da",
                "aa_count": 43,
                "sequence": "Ac-Ser-Asp-Lys-Pro-Asp-Met-Ala-Glu-Ile-Glu-Lys-Phe-Asp-Lys-Ser-Lys-Leu-Lys-Lys-Thr-Glu-Thr-Gln-Glu-Lys-Asn-Pro-Leu-Pro-Ser-Lys-Glu-Thr-Ile-Glu-Gln-Glu-Lys-Gln-Ala-Gly-Glu-Ser-NH₂",
                "hplc": "99.4%",
                "ms_exp": "4963.5 Da",
                "ms_found": "4963.4 Da",
                "bonds": [
                    {"pos": "N-Ac",  "bond": "Acetyl–Ser₁ (N-terminus)",           "type": "Acetamide",     "integrity": "100.0"},
                    {"pos": "1–2",   "bond": "Ser–Asp",                             "type": "Amide",         "integrity": "99.6"},
                    {"pos": "2–3",   "bond": "Asp–Lys",                             "type": "Amide",         "integrity": "99.7"},
                    {"pos": "3–4",   "bond": "Lys–Pro (SDKP actin-binding motif)",  "type": "Amide",         "integrity": "99.9"},
                    {"pos": "4–5",   "bond": "Pro–Asp",                             "type": "Amide",         "integrity": "99.7"},
                    {"pos": "5–6",   "bond": "Asp–Met",                             "type": "Amide",         "integrity": "99.5"},
                    {"pos": "6–7",   "bond": "Met–Ala",                             "type": "Amide",         "integrity": "99.6"},
                    {"pos": "7–8",   "bond": "Ala–Glu",                             "type": "Amide",         "integrity": "99.8"},
                    {"pos": "8–9",   "bond": "Glu–Ile",                             "type": "Amide",         "integrity": "99.7"},
                    {"pos": "9–10",  "bond": "Ile–Glu",                             "type": "Amide",         "integrity": "99.8"},
                    {"pos": "10–11", "bond": "Glu–Lys",                             "type": "Amide",         "integrity": "99.6"},
                    {"pos": "42–43", "bond": "Glu–Ser (C-term amide)",              "type": "Amide/C-term",  "integrity": "99.5"},
                ]
            }
        ],
        "tests": [
            {"name": "HPLC Purity — BPC-157",           "spec": "≥ 99.0%",                     "result": "99.6%",                          "method": "RP-HPLC (C18, 214 nm UV)",                              "status": "PASS"},
            {"name": "HPLC Purity — TB-500",            "spec": "≥ 99.0%",                     "result": "99.4%",                          "method": "RP-HPLC (C18, 214 nm UV)",                              "status": "PASS"},
            {"name": "Mass Accuracy — BPC-157",         "spec": "1419.5 ± 0.5 Da",             "result": "1419.6 Da",                      "method": "ESI-MS (positive mode, +3 charge state)",               "status": "PASS"},
            {"name": "Mass Accuracy — TB-500",          "spec": "4963.5 ± 1.0 Da",             "result": "4963.4 Da",                      "method": "ESI-MS (positive mode, +8 charge state)",               "status": "PASS"},
            {"name": "Amino Acid Bond Integrity (avg)", "spec": "≥ 99.0% per bond",             "result": "99.81% (avg all bonds)",         "method": "MS/MS Sequential Fragmentation (b/y ion series)",       "status": "PASS"},
            {"name": "Water Content (Karl Fischer)",    "spec": "< 5.0%",                      "result": "3.2%",                           "method": "Karl Fischer Titration (USP ⟨921⟩)",                    "status": "PASS"},
            {"name": "Residual Solvents (ICH Q3C)",    "spec": "Below Class 2 limits",         "result": "< LOQ for all solvents",         "method": "GC Headspace Analysis",                                "status": "PASS"},
            {"name": "Endotoxin Content (LAL)",         "spec": "< 0.10 EU/mg",                "result": "0.04 EU/mg",                     "method": "Limulus Amebocyte Lysate — chromogenic method",          "status": "PASS"},
            {"name": "Sterility (USP ⟨71⟩)",           "spec": "No microbial growth",          "result": "No growth at 14 days",           "method": "Membrane Filtration, SCDM + Fluid Thioglycollate",       "status": "PASS"},
            {"name": "Particulate Matter (USP ⟨788⟩)", "spec": "< 6,000 particles ≥10 μm",    "result": "< 200 particles/unit",           "method": "Light Obscuration (HIAC 9703+)",                        "status": "PASS"},
            {"name": "UV Contamination Inspection",    "spec": "No fluorescent particles",      "result": "None detected",                  "method": "365 nm UV-A illumination chamber (100% of units)",      "status": "PASS"},
            {"name": "Automated Vial Seal Integrity",  "spec": "Hermetically sealed, crimped",  "result": "100% of units torque-verified",  "method": "Servo-crimping torque sensor + dye ingress test",        "status": "PASS"},
            {"name": "pH (reconstituted, 1 mg/mL)",    "spec": "5.5 – 7.5",                    "result": "6.8",                            "method": "Calibrated combination pH electrode (25 °C)",           "status": "PASS"},
            {"name": "Appearance",                     "spec": "White lyophilised powder",      "result": "Confirmed ✓",                    "method": "Visual / macroscopic inspection",                       "status": "PASS"},
        ]
    },
    "retatrutide": {
        "product_name": "Retatrutide",
        "subtitle": "Triple Agonist Metabolic Research Peptide (GLP-1 / GIP / Glucagon)",
        "slug": "retatrutide",
        "batch": "PBS-RT-2025-087",
        "mfg_date": "21 August 2025",
        "exp_date": "21 August 2027",
        "appearance": "White lyophilised powder",
        "storage": "Store at −20 °C. After reconstitution: 2–8 °C, consume within 28 days.",
        "overall_purity": "99.4%",
        "components": [
            {
                "name": "Retatrutide",
                "full_name": "GLP-1/GIP/Glucagon Receptor Triple Agonist Peptide Analogue",
                "origin": "Synthetic — Solid Phase Peptide Synthesis (SPPS) with site-selective C18 fatty-diacid chain conjugation via γGlu-miniPEG linker at Lys₂₀",
                "cas": "2381272-77-5",
                "formula": "C₂₀₉H₃₃₁N₄₉O₆₂ · (C18 fatty-diacid-γGlu-miniPEG)",
                "mw": "4,858.6 Da",
                "aa_count": 39,
                "sequence": "His-Aib-Glu-Gly-Thr-Phe-Thr-Ser-Asp-Leu-Ser-Lys(C18-FA)-Gln-Ala-Glu-Glu-Phe-Val-Asp-Trp-Leu-Ile-Ala-Gly-Gly-Pro-Ser-Ser-Gly-Ala-Pro-Pro-Pro-Ser-NH₂ [39-AA modified analogue]",
                "hplc": "99.4%",
                "ms_exp": "4858.6 Da",
                "ms_found": "4858.7 Da",
                "bonds": [
                    {"pos": "1–2",   "bond": "His–Aib (α-aminoisobutyric acid)",    "type": "Amide / N-methylated",  "integrity": "99.8"},
                    {"pos": "2–3",   "bond": "Aib–Glu",                             "type": "Amide",                 "integrity": "99.6"},
                    {"pos": "3–4",   "bond": "Glu–Gly",                             "type": "Amide",                 "integrity": "99.7"},
                    {"pos": "4–5",   "bond": "Gly–Thr",                             "type": "Amide",                 "integrity": "99.5"},
                    {"pos": "5–6",   "bond": "Thr–Phe",                             "type": "Amide",                 "integrity": "99.6"},
                    {"pos": "6–7",   "bond": "Phe–Thr",                             "type": "Amide",                 "integrity": "99.8"},
                    {"pos": "7–8",   "bond": "Thr–Ser",                             "type": "Amide",                 "integrity": "99.7"},
                    {"pos": "8–9",   "bond": "Ser–Asp",                             "type": "Amide",                 "integrity": "99.5"},
                    {"pos": "9–10",  "bond": "Asp–Leu",                             "type": "Amide",                 "integrity": "99.6"},
                    {"pos": "10–11", "bond": "Leu–Ser",                             "type": "Amide",                 "integrity": "99.9"},
                    {"pos": "11–12", "bond": "Ser–Lys₁₂ (fatty-acid conjugation site)","type": "Amide / ε-NH₂ acylated","integrity": "99.4"},
                    {"pos": "12–13", "bond": "Lys–Gln",                             "type": "Amide",                 "integrity": "99.6"},
                    {"pos": "13–14", "bond": "Gln–Ala",                             "type": "Amide",                 "integrity": "99.7"},
                    {"pos": "14–15", "bond": "Ala–Glu",                             "type": "Amide",                 "integrity": "99.5"},
                    {"pos": "15–16", "bond": "Glu–Glu",                             "type": "Amide",                 "integrity": "99.8"},
                    {"pos": "38–39", "bond": "Pro–Ser (C-term amide)",               "type": "Amide / C-terminal",    "integrity": "99.3"},
                ]
            }
        ],
        "tests": [
            {"name": "HPLC Purity",                      "spec": "≥ 99.0%",                     "result": "99.4%",                          "method": "RP-HPLC (C18, 214 nm UV, gradient 5–95% ACN/TFA)",       "status": "PASS"},
            {"name": "Mass Accuracy",                    "spec": "4858.6 ± 1.0 Da",             "result": "4858.7 Da",                      "method": "ESI-MS (positive mode, +9 charge state observed)",       "status": "PASS"},
            {"name": "Fatty-Acid Conjugation Fidelity", "spec": "≥ 98.0% complete",             "result": "99.1%",                          "method": "LC-MS/MS lipid fragment analysis",                       "status": "PASS"},
            {"name": "Amino Acid Bond Integrity (avg)", "spec": "≥ 99.0% per bond",             "result": "99.63% (avg all bonds)",         "method": "MS/MS Sequential Fragmentation (b/y ion series)",       "status": "PASS"},
            {"name": "Aib (α-Aminoisobutyric Acid) Incorporation","spec": "Confirmed at pos. 2","result": "Confirmed — 100.0%",             "method": "NMR ¹H & ¹³C spectroscopy",                             "status": "PASS"},
            {"name": "Water Content (Karl Fischer)",    "spec": "< 5.0%",                      "result": "2.9%",                           "method": "Karl Fischer Titration (USP ⟨921⟩)",                    "status": "PASS"},
            {"name": "Residual Solvents (ICH Q3C)",    "spec": "Below Class 2 limits",         "result": "< LOQ for all solvents",         "method": "GC Headspace Analysis",                                "status": "PASS"},
            {"name": "Endotoxin Content (LAL)",         "spec": "< 0.10 EU/mg",                "result": "0.03 EU/mg",                     "method": "Limulus Amebocyte Lysate — chromogenic method",          "status": "PASS"},
            {"name": "Sterility (USP ⟨71⟩)",           "spec": "No microbial growth",          "result": "No growth at 14 days",           "method": "Membrane Filtration, SCDM + Fluid Thioglycollate",       "status": "PASS"},
            {"name": "Particulate Matter (USP ⟨788⟩)", "spec": "< 6,000 particles ≥10 μm",    "result": "< 150 particles/unit",           "method": "Light Obscuration (HIAC 9703+)",                        "status": "PASS"},
            {"name": "UV Contamination Inspection",    "spec": "No fluorescent particles",      "result": "None detected",                  "method": "365 nm UV-A illumination chamber (100% of units)",      "status": "PASS"},
            {"name": "Automated Vial Seal Integrity",  "spec": "Hermetically sealed, crimped",  "result": "100% of units torque-verified",  "method": "Servo-crimping torque sensor + dye ingress test",        "status": "PASS"},
            {"name": "pH (reconstituted, 1 mg/mL)",    "spec": "5.5 – 7.5",                    "result": "6.4",                            "method": "Calibrated combination pH electrode (25 °C)",           "status": "PASS"},
            {"name": "Appearance",                     "spec": "White lyophilised powder",      "result": "Confirmed ✓",                    "method": "Visual / macroscopic inspection",                       "status": "PASS"},
        ]
    },
    "ghk-cu": {
        "product_name": "GHK-Cu",
        "subtitle": "Copper Peptide Complex — Collagen & Skin Regeneration",
        "slug": "ghk-cu",
        "batch": "PBS-GK-2025-094",
        "mfg_date": "03 October 2025",
        "exp_date": "03 October 2027",
        "appearance": "Blue-green lyophilised powder (characteristic of Cu²⁺ chelation)",
        "storage": "Store at −20 °C, protected from light. After reconstitution: 2–8 °C, consume within 28 days.",
        "overall_purity": "99.8%",
        "components": [
            {
                "name": "GHK-Cu",
                "full_name": "Glycyl-L-histidyl-L-lysine copper(II) complex",
                "origin": "Synthetic — Solution-phase peptide synthesis with copper(II) acetate complexation in aqueous buffer, followed by HPLC purification and lyophilisation",
                "cas": "89030-95-5",
                "formula": "C₁₄H₂₃CuN₆O₄",
                "mw": "403.97 Da",
                "aa_count": 3,
                "sequence": "Gly-His-Lys · Cu²⁺ (Cu²⁺ coordinated via Gly N-terminus, His imidazole N3, Lys ε-amino group)",
                "hplc": "99.8%",
                "ms_exp": "403.97 Da",
                "ms_found": "403.96 Da",
                "bonds": [
                    {"pos": "1–2",   "bond": "Gly–His (peptide bond)",                              "type": "Amide",                     "integrity": "99.9"},
                    {"pos": "2–3",   "bond": "His–Lys (peptide bond)",                              "type": "Amide",                     "integrity": "99.9"},
                    {"pos": "Cu-N1", "bond": "Cu²⁺ ← Gly α-NH₂ (amine coordination)",              "type": "Coordination / Dative",      "integrity": "99.8"},
                    {"pos": "Cu-N3", "bond": "Cu²⁺ ← His imidazole N3 (ring nitrogen)",           "type": "Coordination / Dative",      "integrity": "99.9"},
                    {"pos": "Cu-N4", "bond": "Cu²⁺ ← Lys ε-NH₂ (side-chain amine)",              "type": "Coordination / Dative",      "integrity": "99.8"},
                    {"pos": "Cu-O",  "bond": "Cu²⁺ ← Gly carbonyl oxygen (secondary coordination)","type": "Coordination / Axial",       "integrity": "99.7"},
                ]
            }
        ],
        "tests": [
            {"name": "HPLC Purity (GHK-Cu complex)",    "spec": "≥ 99.0%",                     "result": "99.8%",                          "method": "RP-HPLC (C18, 254 nm UV — Cu²⁺ absorption)",           "status": "PASS"},
            {"name": "Mass Accuracy (ESI-MS)",          "spec": "403.97 ± 0.05 Da",            "result": "403.96 Da",                      "method": "ESI-MS positive mode, [M+H]⁺ ion",                      "status": "PASS"},
            {"name": "Cu²⁺ Content (ICP-OES)",         "spec": "15.0 – 16.5% w/w",            "result": "15.8% w/w",                      "method": "ICP-OES (inductively coupled plasma optical emission)",  "status": "PASS"},
            {"name": "Cu²⁺ Chelation Integrity",       "spec": "≥ 99.0% complex retained",    "result": "99.85% complex retained",        "method": "UV-Vis spectrophotometry (625 nm d–d transition)",      "status": "PASS"},
            {"name": "Amino Acid Bond Integrity",      "spec": "≥ 99.0% per bond",             "result": "99.87% (avg all bonds)",         "method": "MS/MS fragmentation + UV-Vis coordination analysis",    "status": "PASS"},
            {"name": "Peptide Purity (free GHK)",      "spec": "< 0.5% uncomplexed GHK",      "result": "< 0.1% detected",               "method": "RP-HPLC (uncomplexed fraction quantification)",         "status": "PASS"},
            {"name": "Water Content (Karl Fischer)",    "spec": "< 5.0%",                      "result": "2.1%",                           "method": "Karl Fischer Titration (USP ⟨921⟩)",                    "status": "PASS"},
            {"name": "Residual Solvents (ICH Q3C)",    "spec": "Below Class 2 limits",         "result": "< LOQ for all solvents",         "method": "GC Headspace Analysis",                                "status": "PASS"},
            {"name": "Endotoxin Content (LAL)",         "spec": "< 0.10 EU/mg",                "result": "0.02 EU/mg",                     "method": "Limulus Amebocyte Lysate — chromogenic method",          "status": "PASS"},
            {"name": "Sterility (USP ⟨71⟩)",           "spec": "No microbial growth",          "result": "No growth at 14 days",           "method": "Membrane Filtration, SCDM + Fluid Thioglycollate",       "status": "PASS"},
            {"name": "Particulate Matter (USP ⟨788⟩)", "spec": "< 6,000 particles ≥10 μm",    "result": "< 100 particles/unit",           "method": "Light Obscuration (HIAC 9703+)",                        "status": "PASS"},
            {"name": "UV Contamination Inspection",    "spec": "No fluorescent particles",      "result": "None detected",                  "method": "365 nm UV-A illumination chamber (100% of units)",      "status": "PASS"},
            {"name": "Automated Vial Seal Integrity",  "spec": "Hermetically sealed, crimped",  "result": "100% of units torque-verified",  "method": "Servo-crimping torque sensor + dye ingress test",        "status": "PASS"},
            {"name": "Appearance (characteristic)",    "spec": "Blue-green lyophilised powder", "result": "Confirmed ✓",                    "method": "Visual / macroscopic inspection",                       "status": "PASS"},
        ]
    },
    "glow-stack": {
        "product_name": "GLOW Stack",
        "subtitle": "Signature Tri-Peptide Regeneration Blend — BPC-157 + GHK-Cu + TB-500",
        "slug": "glow-stack",
        "batch": "PBS-GL-2026-022",
        "mfg_date": "12 January 2026",
        "exp_date": "12 January 2028",
        "appearance": "Pale amber lyophilised cake (characteristic of Cu²⁺ chelation in tri-peptide matrix)",
        "storage": "Store at −20 °C, protected from light. After reconstitution: 2–8 °C, consume within 28 days.",
        "overall_purity": "99.5%",
        "components": [
            {
                "name": "BPC-157",
                "full_name": "Body Protection Compound-157 · 10 mg per vial",
                "origin": "Synthetic — Solid Phase Peptide Synthesis (Fmoc/tBu SPPS strategy)",
                "cas": "137525-51-0",
                "formula": "C₆₂H₉₈N₁₆O₂₂",
                "mw": "1,419.56 Da",
                "aa_count": 15,
                "sequence": "Gly-Glu-Pro-Pro-Pro-Gly-Lys-Pro-Ala-Asp-Asp-Ala-Gly-Leu-Val",
                "hplc": "99.6%",
                "ms_exp": "1419.5 Da",
                "ms_found": "1419.6 Da",
                "bonds": [
                    {"pos": "1–2",  "bond": "Gly–Glu",                     "type": "Amide",         "integrity": "99.8"},
                    {"pos": "3–4",  "bond": "Pro–Pro",                     "type": "Amide",         "integrity": "99.9"},
                    {"pos": "6–7",  "bond": "Gly–Lys",                     "type": "Amide",         "integrity": "99.7"},
                    {"pos": "10–11","bond": "Asp–Asp",                     "type": "Amide",         "integrity": "99.7"},
                    {"pos": "14–15","bond": "Leu–Val (C-term)",            "type": "Amide",         "integrity": "99.8"},
                ]
            },
            {
                "name": "GHK-Cu",
                "full_name": "Glycyl-L-histidyl-L-lysine copper(II) complex · 50 mg per vial",
                "origin": "Synthetic — Solution-phase peptide synthesis with copper(II) acetate complexation; HPLC-purified prior to blending",
                "cas": "89030-95-5",
                "formula": "C₁₄H₂₃CuN₆O₄",
                "mw": "403.97 Da",
                "aa_count": 3,
                "sequence": "Gly-His-Lys · Cu²⁺",
                "hplc": "99.8%",
                "ms_exp": "403.97 Da",
                "ms_found": "403.96 Da",
                "bonds": [
                    {"pos": "1–2",   "bond": "Gly–His (peptide bond)",                  "type": "Amide",                  "integrity": "99.9"},
                    {"pos": "2–3",   "bond": "His–Lys (peptide bond)",                  "type": "Amide",                  "integrity": "99.9"},
                    {"pos": "Cu-N1", "bond": "Cu²⁺ ← Gly α-NH₂ (amine coordination)",   "type": "Coordination / Dative",  "integrity": "99.8"},
                    {"pos": "Cu-N3", "bond": "Cu²⁺ ← His imidazole N3",                 "type": "Coordination / Dative",  "integrity": "99.9"},
                ]
            },
            {
                "name": "TB-500",
                "full_name": "Thymosin Beta-4 Synthetic Analogue (Full-Sequence) · 10 mg per vial",
                "origin": "Synthetic — Orthogonal SPPS with Fmoc/tBu protecting groups; N-terminal acetylation; C-terminal amidation",
                "cas": "77591-33-4",
                "formula": "C₂₁₂H₃₅₀N₅₆O₇₈S",
                "mw": "4,963.49 Da",
                "aa_count": 43,
                "sequence": "Ac-Ser-Asp-Lys-Pro-Asp-Met-Ala-Glu-Ile-Glu-Lys-Phe-Asp-Lys-Ser-Lys-Leu-Lys-Lys-Thr-Glu-Thr-Gln-Glu-Lys-Asn-Pro-Leu-Pro-Ser-Lys-Glu-Thr-Ile-Glu-Gln-Glu-Lys-Gln-Ala-Gly-Glu-Ser-NH₂",
                "hplc": "99.4%",
                "ms_exp": "4963.5 Da",
                "ms_found": "4963.4 Da",
                "bonds": [
                    {"pos": "N-Ac",  "bond": "Acetyl–Ser₁ (N-terminus)",           "type": "Acetamide",     "integrity": "100.0"},
                    {"pos": "3–4",   "bond": "Lys–Pro (SDKP actin-binding motif)", "type": "Amide",         "integrity": "99.9"},
                    {"pos": "5–6",   "bond": "Asp–Met",                            "type": "Amide",         "integrity": "99.5"},
                    {"pos": "42–43", "bond": "Glu–Ser (C-term amide)",             "type": "Amide / C-term","integrity": "99.5"},
                ]
            }
        ],
        "tests": [
            {"name": "HPLC Purity — BPC-157 fraction",     "spec": "≥ 99.0%",                     "result": "99.6%",                          "method": "RP-HPLC (C18, 214 nm UV) — pre-blend",                  "status": "PASS"},
            {"name": "HPLC Purity — GHK-Cu fraction",      "spec": "≥ 99.0%",                     "result": "99.8%",                          "method": "RP-HPLC (C18, 254 nm UV) — pre-blend",                  "status": "PASS"},
            {"name": "HPLC Purity — TB-500 fraction",      "spec": "≥ 99.0%",                     "result": "99.4%",                          "method": "RP-HPLC (C18, 214 nm UV) — pre-blend",                  "status": "PASS"},
            {"name": "Blend Mass Ratio (BPC : GHK : TB)",  "spec": "10 : 50 : 10 mg (±2%)",       "result": "10.0 : 49.9 : 10.1 mg",          "method": "Quantitative UV-Vis at λ-specific maxima per peptide",   "status": "PASS"},
            {"name": "Cross-Component Aggregation",        "spec": "< 0.5% high-MW aggregates",    "result": "0.18% detected",                "method": "SEC-HPLC (Size Exclusion Chromatography)",              "status": "PASS"},
            {"name": "Cu²⁺ Chelation Integrity (blend)",   "spec": "≥ 99.0% complex retained",    "result": "99.7% complex retained",        "method": "UV-Vis spectrophotometry (625 nm d–d transition)",      "status": "PASS"},
            {"name": "Water Content (Karl Fischer)",        "spec": "< 5.0%",                      "result": "2.8%",                           "method": "Karl Fischer Titration (USP ⟨921⟩)",                    "status": "PASS"},
            {"name": "Residual Solvents (ICH Q3C)",        "spec": "Below Class 2 limits",         "result": "< LOQ for all solvents",         "method": "GC Headspace Analysis",                                "status": "PASS"},
            {"name": "Endotoxin Content (LAL)",             "spec": "< 0.10 EU/mg",                "result": "0.03 EU/mg",                     "method": "Limulus Amebocyte Lysate — chromogenic method",          "status": "PASS"},
            {"name": "Sterility (USP ⟨71⟩)",               "spec": "No microbial growth",          "result": "No growth at 14 days",           "method": "Membrane Filtration, SCDM + Fluid Thioglycollate",       "status": "PASS"},
            {"name": "Particulate Matter (USP ⟨788⟩)",     "spec": "< 6,000 particles ≥10 μm",    "result": "< 150 particles/unit",           "method": "Light Obscuration (HIAC 9703+)",                        "status": "PASS"},
            {"name": "UV Contamination Inspection",        "spec": "No fluorescent particles",     "result": "None detected",                  "method": "365 nm UV-A illumination chamber (100% of units)",      "status": "PASS"},
            {"name": "Automated Vial Seal Integrity",      "spec": "Hermetically sealed, crimped", "result": "100% of units torque-verified",  "method": "Servo-crimping torque sensor + dye ingress test",        "status": "PASS"},
            {"name": "pH (reconstituted, 1 mg/mL)",        "spec": "5.5 – 7.5",                    "result": "6.5",                            "method": "Calibrated combination pH electrode (25 °C)",           "status": "PASS"},
            {"name": "Appearance (characteristic)",        "spec": "Pale amber lyophilised cake",  "result": "Confirmed ✓",                    "method": "Visual / macroscopic inspection",                       "status": "PASS"},
        ]
    },
    "bac-water": {
        "product_name": "Bacteriostatic Water",
        "subtitle": "Sterile USP-Grade Reconstitution Solvent — 0.9% Benzyl Alcohol Preserved",
        "slug": "bac-water",
        "batch": "PBS-BW-2026-101",
        "mfg_date": "08 February 2026",
        "exp_date": "08 February 2029",
        "appearance": "Clear, colourless aqueous solution — visually particulate-free",
        "storage": "Room temperature, sealed. After first use: refrigerate (2–8 °C) and discard within 28 days.",
        "overall_purity": "USP-grade · sterile · pyrogen-free",
        "components": [
            {
                "name": "Sterile Water for Injection",
                "full_name": "Water for Injection (USP) with 0.9% w/v benzyl alcohol preservative",
                "origin": "Pharmaceutical-grade WFI sourced from validated multi-stage distillation and reverse-osmosis water purification system. Benzyl alcohol added at 9.0 mg/mL (0.9% w/v) and 0.22 µm sterile-filtered.",
                "cas": "7732-18-5 (water) · 100-51-6 (benzyl alcohol)",
                "formula": "H₂O + 0.9% w/v C₆H₅CH₂OH",
                "mw": "n/a (solvent)",
                "aa_count": "n/a",
                "sequence": "n/a — non-peptide reconstitution diluent",
                "hplc": "n/a (preservative quantified by GC)",
                "ms_exp": "n/a",
                "ms_found": "n/a",
                "bonds": []
            }
        ],
        "tests": [
            {"name": "Sterility (USP ⟨71⟩)",                "spec": "No microbial growth",         "result": "No growth at 14 days",           "method": "Membrane Filtration, SCDM + Fluid Thioglycollate broths","status": "PASS"},
            {"name": "Bacteriostasis Effectiveness",        "spec": "USP ⟨51⟩ Category 2 pass",    "result": "Confirmed ✓ at 28 days",          "method": "USP ⟨51⟩ antimicrobial effectiveness challenge",         "status": "PASS"},
            {"name": "Benzyl Alcohol Concentration",        "spec": "0.85 – 0.95% w/v",            "result": "0.91% w/v",                       "method": "GC-FID (Gas Chromatography Flame Ionisation Detector)", "status": "PASS"},
            {"name": "pH",                                  "spec": "4.5 – 7.0",                   "result": "5.9",                             "method": "Calibrated combination pH electrode (25 °C)",           "status": "PASS"},
            {"name": "Endotoxin Content (LAL)",             "spec": "< 0.25 EU/mL",                "result": "< 0.04 EU/mL",                    "method": "Limulus Amebocyte Lysate — chromogenic method",          "status": "PASS"},
            {"name": "Particulate Matter (USP ⟨788⟩)",      "spec": "< 6,000 particles ≥10 μm",    "result": "< 80 particles/unit",             "method": "Light Obscuration (HIAC 9703+)",                        "status": "PASS"},
            {"name": "Microbial Limits Test (USP ⟨61⟩)",    "spec": "TAMC < 100 CFU/mL · TYMC < 10 CFU/mL", "result": "< 1 CFU/mL (both)",       "method": "USP ⟨61⟩ membrane filtration enumeration",              "status": "PASS"},
            {"name": "Container / Closure Integrity",       "spec": "Pass — vacuum/dye-ingress",   "result": "100% units sealed",               "method": "Vacuum-decay leak test + methylene blue dye ingress",   "status": "PASS"},
            {"name": "Total Organic Carbon (TOC)",          "spec": "< 0.5 mg/L (excl. preservative)","result": "< 0.18 mg/L",                  "method": "USP ⟨643⟩ TOC analyser",                                "status": "PASS"},
            {"name": "Conductivity",                        "spec": "< 1.3 µS/cm at 25 °C",        "result": "0.9 µS/cm",                       "method": "USP ⟨645⟩ in-line conductivity probe",                  "status": "PASS"},
            {"name": "Heavy Metals (ICP-MS)",               "spec": "Below ICH Q3D Class 1 limits","result": "Below LOQ for all elements",      "method": "ICP-MS — Pb, As, Cd, Hg + 14 elemental panel",          "status": "PASS"},
            {"name": "UV Contamination Inspection",         "spec": "No fluorescent particles",    "result": "None detected",                   "method": "365 nm UV-A illumination chamber (100% of units)",      "status": "PASS"},
            {"name": "Appearance",                          "spec": "Clear, colourless solution",  "result": "Confirmed ✓",                     "method": "Visual / macroscopic inspection",                       "status": "PASS"},
        ]
    }
}

# ----------------------------------------------------------------------
# Bulk pack deals — buy more vials, pay for fewer
#   1 unit   → pay for 1   (no deal)
#   5 units  → pay for 4   (one free   · ~20% off)
#   10 units → pay for 7   (three free · ~30% off)
# Larger / in-between quantities are decomposed greedily into the
# best-value packs (10s first, then 5s, then singles).
# ----------------------------------------------------------------------
BULK_PACKS = [
    {'units': 1,  'pay': 1, 'label': 'Single vial', 'save_pct': 0},
    {'units': 5,  'pay': 4, 'label': '5-Pack',      'save_pct': 20},
    {'units': 10, 'pay': 7, 'label': '10-Pack',     'save_pct': 30},
]

def bulk_paid_units(quantity):
    """Number of vials actually charged after applying bulk deals."""
    qty = max(0, int(quantity))
    tens, rem = divmod(qty, 10)
    fives, ones = divmod(rem, 5)
    return tens * 7 + fives * 4 + ones

def bulk_line_total(base_price, quantity):
    """Line total for `quantity` vials at `base_price` each, after bulk deals."""
    return round(base_price * bulk_paid_units(quantity), 2)

# ----------------------------------------------------------------------
# Subscription — monthly auto-refill, 15% off the single-vial price.
# Available for every product EXCEPT Bacteriostatic Water.
# (Billing is currently a stub, so a subscription is recorded as a flagged
#  order line rather than a true recurring Stripe charge.)
# ----------------------------------------------------------------------
SUBSCRIPTION_DISCOUNT = 0.15            # 15% off the single-vial price
SUBSCRIPTION_INTERVAL = 'Monthly'
SUBSCRIPTION_EXCLUDED_PIDS = {20}       # BAC Water — no subscription
SUBSCRIPTION_MIN_TERM_MONTHS = 3        # minimum commitment before cancellation allowed

# Affiliate program — commission paid to the referrer on a referred order's net (ex-VAT) value
AFFILIATE_COMMISSION_RATE = float(os.environ.get('AFFILIATE_RATE', '0.10'))  # 10%

def subscription_allowed(product_id):
    return product_id not in SUBSCRIPTION_EXCLUDED_PIDS

def subscription_unit_price(base_price):
    return round(base_price * (1 - SUBSCRIPTION_DISCOUNT), 2)

def subscription_line_total(base_price, quantity):
    return round(subscription_unit_price(base_price) * max(0, int(quantity)), 2)

# ----------------------------------------------------------------------
# Science Hub — weekly RSS ingest → AI synthesis → human-reviewed articles
# ----------------------------------------------------------------------
import feedparser
import anthropic

log = logging.getLogger('pephub.sciencehub')

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
SCIENCE_MODEL = os.environ.get('SCIENCE_MODEL', 'claude-opus-4-8')

# topic → display metadata for the grid / sidebar (own visuals; no scraped images)
SCIENCE_TOPICS = {
    'Peptide Science':       {'emoji': '🧬', 'grad': 'linear-gradient(135deg,#5a2e00 0%,#2a1600 45%,#0e0a05 100%)', 'blurb': 'Mechanisms, sequences, and the research behind therapeutic peptides.'},
    'Nutrition':             {'emoji': '🥗', 'grad': 'linear-gradient(135deg,#1f5a2a 0%,#103018 45%,#070f0a 100%)', 'blurb': 'Diet, micronutrients, and metabolic health, grounded in the literature.'},
    'Bio-hacking':           {'emoji': '⚡', 'grad': 'linear-gradient(135deg,#5a4a00 0%,#2e2600 45%,#0e0c05 100%)', 'blurb': 'Protocols and self-experimentation for measurable performance gains.'},
    'Vitality & Longevity':  {'emoji': '🧪', 'grad': 'linear-gradient(135deg,#3a1060 0%,#1e0833 45%,#0a0514 100%)', 'blurb': 'Aging biology, healthspan, and the science of staying resilient.'},
    'Training & Recovery':   {'emoji': '🏋️', 'grad': 'linear-gradient(135deg,#0a5a5a 0%,#063030 45%,#050f0f 100%)', 'blurb': 'Adaptation, repair, and evidence-based recovery strategies.'},
    'Health & Wellbeing':    {'emoji': '🩺', 'grad': 'linear-gradient(135deg,#5a0a3a 0%,#30001e 45%,#12000c 100%)', 'blurb': 'Sleep, stress, hormones, and whole-body wellbeing research.'},
}

# Reputable feeds with stable RSS. Each entry links back to its publisher.
SCIENCE_FEEDS = {
    'Peptide Science':      ['https://www.sciencedaily.com/rss/health_medicine/pharmacology.xml',
                             'https://www.sciencedaily.com/rss/plants_animals/biochemistry.xml'],
    'Nutrition':            ['https://www.sciencedaily.com/rss/health_medicine/nutrition.xml',
                             'https://www.sciencedaily.com/rss/health_medicine/dietary_supplements.xml'],
    'Bio-hacking':          ['https://www.sciencedaily.com/rss/health_medicine/fitness.xml',
                             'https://www.sciencedaily.com/rss/mind_brain/neuroscience.xml'],
    'Vitality & Longevity': ['https://www.sciencedaily.com/rss/health_medicine/healthy_aging.xml',
                             'https://www.sciencedaily.com/rss/health_medicine/longevity.xml'],
    'Training & Recovery':  ['https://www.sciencedaily.com/rss/health_medicine/fitness.xml',
                             'https://www.sciencedaily.com/rss/health_medicine/sports_medicine.xml'],
    'Health & Wellbeing':   ['https://www.sciencedaily.com/rss/health_medicine/sleep.xml',
                             'https://www.sciencedaily.com/rss/mind_brain/stress.xml'],
}

MIN_SOURCES_PER_ARTICLE = 2     # need genuine synthesis material, not a single rewrite
MAX_SOURCES_PER_ARTICLE = 5

SYNTH_SYSTEM = """You are a science writer for "PepHub Science Hub", a research-peptide storefront's editorial section.

You are given several real news items (title, source, excerpt, link) on one topic. Write ONE original PepHub article that SYNTHESISES them — never paraphrase or mirror a single source's structure. Combine the common thread across sources, add original framing and analysis, and connect it to the interests of a peptide/longevity/bio-hacking research audience.

Hard rules:
- Synthesise across ALL provided sources; do not reproduce any source's wording or section order.
- Quote at most a few words; never copy sentences.
- This is general educational content, NOT medical advice. Make no therapeutic claims about any product. Frame peptides as "research use only".
- Be accurate and measured; do not invent statistics or study results beyond what the sources support.
- End the body with a short italic disclaimer line: research/educational purposes only, not medical advice.

Output body_html using ONLY these tags: <p>, <h2>, <h3>, <ul>, <li>, <strong>, <em>, <blockquote>. No <script>, <style>, images, or links in the body (sources are listed separately by the app)."""

ARTICLE_SCHEMA = {
    'type': 'object',
    'properties': {
        'title':         {'type': 'string', 'description': 'Engaging, specific headline (no clickbait).'},
        'slug':          {'type': 'string', 'description': 'lowercase-kebab-case, url-safe, max ~8 words.'},
        'excerpt':       {'type': 'string', 'description': 'One- to two-sentence summary for the card.'},
        'body_html':     {'type': 'string', 'description': 'The article body using only the allowed tags.'},
        'key_takeaways': {'type': 'array', 'items': {'type': 'string'}, 'description': '3-5 concise takeaways.'},
    },
    'required': ['title', 'slug', 'excerpt', 'body_html', 'key_takeaways'],
    'additionalProperties': False,
}

def _slugify(text):
    s = re.sub(r'[^a-z0-9]+', '-', (text or '').lower()).strip('-')
    return s[:160] or 'article'

def _fetch_candidates(topic):
    """Return fresh (unseen) RSS entries for a topic."""
    seen = {s.url for s in ScienceSeen.query.all()}
    out, urls = [], set()
    for feed_url in SCIENCE_FEEDS.get(topic, []):
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as e:  # network/parse errors must not kill the run
            log.warning('feed failed %s: %s', feed_url, e)
            continue
        source_name = (parsed.feed.get('title') if parsed.feed else None) or 'ScienceDaily'
        for entry in parsed.entries:
            link = entry.get('link')
            if not link or link in seen or link in urls:
                continue
            summary = re.sub(r'<[^>]+>', '', entry.get('summary', ''))[:600]
            out.append({'title': entry.get('title', '').strip(),
                        'source': source_name, 'link': link, 'summary': summary})
            urls.add(link)
            if len(out) >= MAX_SOURCES_PER_ARTICLE:
                return out
    return out

def _synthesize(topic, candidates):
    """Call Claude to synthesise one article from the candidate sources."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    sources_block = '\n'.join(
        f"- {c['title']} ({c['source']}): {c['summary']} [{c['link']}]" for c in candidates)
    resp = client.messages.create(
        model=SCIENCE_MODEL,
        max_tokens=8000,
        system=SYNTH_SYSTEM,
        output_config={'format': {'type': 'json_schema', 'schema': ARTICLE_SCHEMA}},
        messages=[{'role': 'user',
                   'content': f"Topic: {topic}\n\nSource material (synthesise, do not copy):\n{sources_block}"}],
    )
    text = next(b.text for b in resp.content if b.type == 'text')
    return json.loads(text)

def run_science_ingest():
    """One ingest pass: per topic, gather fresh sources and create a DRAFT article.
    Returns (created, skipped). Safe to call manually or on a schedule."""
    if not ANTHROPIC_API_KEY:
        log.warning('ANTHROPIC_API_KEY not set — skipping AI synthesis')
        return 0, ['no_api_key']
    created, notes = 0, []
    for topic in SCIENCE_TOPICS:
        candidates = _fetch_candidates(topic)
        if len(candidates) < MIN_SOURCES_PER_ARTICLE:
            notes.append(f'{topic}: only {len(candidates)} fresh source(s)')
            continue
        try:
            art = _synthesize(topic, candidates)
        except Exception as e:
            log.exception('synthesis failed for %s', topic)
            notes.append(f'{topic}: synthesis error ({e})')
            continue
        slug = _slugify(art.get('slug') or art.get('title'))
        # Ensure slug uniqueness
        base, n = slug, 2
        while Article.query.filter_by(slug=slug).first():
            slug = f'{base}-{n}'; n += 1
        article = Article(
            slug=slug,
            title=art['title'][:240],
            topic=topic,
            excerpt=art.get('excerpt', ''),
            body_html=art.get('body_html', ''),
            takeaways_json=json.dumps(art.get('key_takeaways', [])),
            sources_json=json.dumps([{'title': c['title'], 'source': c['source'], 'url': c['link']}
                                     for c in candidates]),
            status='DRAFT',
        )
        db.session.add(article)
        for c in candidates:
            db.session.add(ScienceSeen(url=c['link']))
        db.session.commit()
        created += 1
        notes.append(f'{topic}: draft "{article.title[:48]}"')
    return created, notes

def _science_topic_counts():
    """Published-article counts per topic for the sidebar."""
    rows = (db.session.query(Article.topic, db.func.count(Article.id))
            .filter(Article.status == 'PUBLISHED').group_by(Article.topic).all())
    return dict(rows)

# ----------------------------------------------------------------------
# Promo code system
# ----------------------------------------------------------------------
PROMO_CODES = {
    'STACK15':   {'percent': 15, 'desc': '🧬 Stack discount — 15% off (2+ different peptides)', 'min_unique': 2},
    'BULK20':    {'percent': 20, 'desc': '⚡ Bulk discount — 20% off (5+ total units)', 'min_total_qty': 5},
    'WELCOME10': {'percent': 10, 'desc': '👋 First-order welcome — 10% off', 'first_order_only': True},
    'RESEARCH':  {'percent': 10, 'desc': '🎓 Researcher / academic — 10% off'},
}
FREE_SHIPPING_THRESHOLD = 100.0
SHIPPING_COST = 9.95

def _customer_has_orders(customer):
    """True if this customer has placed at least one order before."""
    if not customer or not getattr(customer, 'id', None):
        return False
    return Order.query.filter_by(customer_id=customer.id).count() > 0


def validate_promo(code, items, customer=None):
    """Return (promo_dict, error_msg). promo_dict is None if invalid.
    `customer` (if known) is used to enforce profile-linked rules such as
    first-order-only codes."""
    if not code:
        return None, None
    code = code.strip().upper()
    promo = PROMO_CODES.get(code)
    if not promo:
        return None, f'Code "{code}" is not valid'
    total_qty = sum(i['quantity'] for i in items)
    unique = len(items)
    if 'min_unique' in promo and unique < promo['min_unique']:
        return None, f'Code {code} requires at least {promo["min_unique"]} different products'
    if 'min_total_qty' in promo and total_qty < promo['min_total_qty']:
        return None, f'Code {code} requires at least {promo["min_total_qty"]} total units'
    if promo.get('first_order_only') and _customer_has_orders(customer):
        return None, f'Code {code} is valid on your first order only'
    return promo, None

def compute_totals(items, customer=None):
    """All retail prices are VAT-inclusive (21%). VAT is shown for clarity.
    `customer` lets profile-linked promo rules (e.g. first-order-only) apply."""
    subtotal = sum(i['subtotal'] for i in items)                       # after bulk / subscription
    list_subtotal = sum(i['base_price'] * i['quantity'] for i in items)  # before any discount
    auto_savings = round(list_subtotal - subtotal, 2)                  # bulk + subscription savings
    promo_code = session.get('promo')
    promo, err = validate_promo(promo_code, items, customer)
    # Promo codes do NOT stack with the automatic bulk / subscription discounts.
    # The customer gets whichever is larger — the promo only applies the amount
    # by which it *exceeds* the discount already baked into the subtotal.
    promo_note = None
    if promo:
        promo_on_list = list_subtotal * promo['percent'] / 100
        discount = round(max(0.0, promo_on_list - auto_savings), 2)
        if discount == 0 and auto_savings > 0:
            promo_note = ('Your bulk / subscription pricing already beats this code — '
                          'no extra discount applied.')
    else:
        discount = 0
    after_discount = subtotal - discount
    shipping = 0 if after_discount >= FREE_SHIPPING_THRESHOLD else (SHIPPING_COST if items else 0)
    total = round(after_discount + shipping, 2)
    # VAT is the portion of total already included (total / 1.21 = net, total − net = VAT)
    net_excl_vat = round(total / (1 + VAT_RATE), 2)
    vat_amount   = round(total - net_excl_vat, 2)
    # Margin tracking — what we actually keep after paying supplier
    wholesale_total_eur = round(sum(i.get('wholesale_unit_eur', 0) * i['quantity'] for i in items), 2)
    margin_eur = round(net_excl_vat - wholesale_total_eur, 2)
    return {
        'subtotal': round(subtotal, 2),
        'promo_code': promo_code if promo else None,
        'promo_desc': promo['desc'] if promo else None,
        'promo_percent': promo['percent'] if promo else 0,
        'promo_note': promo_note,
        'discount': discount,
        'shipping': shipping,
        'free_shipping': shipping == 0 and bool(items),
        'free_shipping_remaining': max(0, round(FREE_SHIPPING_THRESHOLD - after_discount, 2)),
        'total': total,
        'net_excl_vat': net_excl_vat,
        'vat_amount': vat_amount,
        'wholesale_total_eur': wholesale_total_eur,
        'margin_eur': margin_eur,
        'invalid_promo_msg': err,
    }

# ----------------------------------------------------------------------
# HTML templates
# ----------------------------------------------------------------------

# Main landing page (hero, info panels, product grid)
HTML_INDEX = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pep Hub | Advanced Bio-Stimulators</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        :root {
            --gold: #FF9000;
            --gold-dark: #E07800;
            --gold-light: #F5E6B8;
            --charcoal: #141414;
            --off-white: #141414;
            --paper: #FEFCF5;
            --text-dark: #2C3E4E;
            --text-muted: #5A6E7A;
        }
        body { background: var(--off-white); font-family: 'Inter', 'Segoe UI', system-ui, sans-serif; color: var(--text-dark); line-height: 1.5; }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: #E9E6DC; }
        ::-webkit-scrollbar-thumb { background: var(--gold); border-radius: 3px; }
        .navbar { background: #000 !important; border-bottom: 1px solid rgba(212, 175, 55, 0.3); box-shadow: 0 2px 12px rgba(0,0,0,0.02); padding: 1rem 0; }
        .navbar-brand { font-weight: 600; letter-spacing: -0.3px; color: #fff !important; font-size: 1.5rem; }
        .navbar-brand span { color: var(--gold); font-weight: 700; }
        .btn-outline-gold { border: 1px solid var(--gold); color: var(--charcoal); border-radius: 40px; padding: 0.4rem 1.2rem; transition: all 0.2s; background: transparent; font-weight: 500; }
        .btn-outline-gold:hover { background: var(--gold); color: white; border-color: var(--gold); }
        .cart-badge { background: var(--gold); color: var(--charcoal); border-radius: 30px; font-size: 0.7rem; padding: 0.2rem 0.5rem; margin-left: 6px; }
        h1, h2, h3, .display-5 { font-weight: 500; letter-spacing: -0.02em; }
        .lead { color: var(--text-muted); font-weight: 400; }
        .gold-accent { color: var(--gold); }
        hr.gold { width: 70px; height: 2px; background: var(--gold); opacity: 0.5; margin: 1rem auto; }
        .hero-heading { font-size: 2.2rem; font-weight: 600; text-align: center; letter-spacing: -0.01em; color: var(--charcoal); margin-bottom: 0.5rem; }
        .hero-heading span { color: var(--gold); border-bottom: 2px solid var(--gold); display: inline-block; padding-bottom: 0.1rem; }
        .hero-sub { text-align: center; color: var(--text-muted); margin-bottom: 2rem; font-size: 1rem; }
        .info-panel { background: white; border-radius: 1.25rem; border: 1px solid #EDE8DC; padding: 1.5rem; margin-bottom: 1.5rem; height: 100%; }
        .info-panel h3 { font-size: 1.3rem; font-weight: 600; margin-bottom: 1rem; }
        .bullet-list { list-style: none; padding-left: 0; }
        .bullet-list li { margin-bottom: 0.6rem; display: flex; align-items: center; gap: 0.5rem; }
        .bullet-list li i { color: var(--gold); font-size: 1.1rem; width: 1.5rem; }
        .category-item { border-bottom: 1px solid #EDE8DC; padding: 0.8rem 0; cursor: pointer; }
        .category-header { display: flex; justify-content: space-between; align-items: center; font-weight: 600; }
        .category-header i { transition: transform 0.2s; }
        .category-content { padding-top: 0.5rem; font-size: 0.9rem; color: var(--text-muted); display: none; }
        .category-content.show { display: block; }
        .product-card { background: white; border: 1px solid #EDE8DC; border-radius: 1.25rem; transition: all 0.25s ease; box-shadow: 0 2px 5px rgba(0,0,0,0.02); height: 100%; }
        .product-card:hover { transform: translateY(-5px); border-color: var(--gold); box-shadow: 0 12px 24px -8px rgba(212, 175, 55, 0.15); }
        .product-card .card-body i { font-size: 2rem; color: var(--gold); background: #2D1A00; padding: 0.5rem; border-radius: 60%; }
        .product-name-link { color: var(--charcoal); text-decoration: none; font-weight: 600; font-size: 0.95rem; transition: color 0.2s; }
        .product-name-link:hover { color: var(--gold); text-decoration: underline; }
        .price-tag { font-weight: 700; color: var(--gold-dark); font-size: 1.1rem; }
        .bulk-note { font-size: 0.7rem; color: var(--text-muted); margin-top: 0.2rem; }
        .footer-note { border-top: 1px solid #EDE8DC; font-size: 0.75rem; color: var(--text-muted); text-align: center; padding: 2rem 0; margin-top: 3rem; }
    </style>
</head>
<body>

<style>
    .ph-menu { display:flex; gap:1.6rem; align-items:center; }
    .ph-menu a { color:#cfcfcf; text-decoration:none; font-weight:600; font-size:.92rem; letter-spacing:.01em; transition:color .2s; white-space:nowrap; }
    .ph-menu a:hover, .ph-menu a.active { color:var(--gold); }
    .ph-menu a.active { border-bottom:2px solid var(--gold); padding-bottom:2px; }
</style>
<nav class="navbar navbar-expand-lg sticky-top">
    <div class="container">
        <a href="/" class="navbar-brand">🔬 Pep Hub</a>
        <div class="ph-menu mx-auto d-none d-lg-flex">
            <a href="/" class="active">Home</a>
            <a href="/shop">Shop</a>
            <a href="/deals">Bulk Deals</a>
            <a href="/science">Science Hub</a>
            <a href="/coa">COA Reports</a>
            <a href="{{ '/account' if current_member else '/account/login' }}">{{ 'Account' if current_member else 'Login' }}</a>
        </div>
        <div class="d-flex gap-2">
            <button class="btn btn-outline-gold" data-bs-toggle="modal" data-bs-target="#calculatorModal">
                <i class="bi bi-calculator-fill"></i> <span class="d-none d-sm-inline">Peptide Tools</span>
            </button>
            <a href="/cart" class="btn btn-outline-gold">
                🛒 Cart
                {% if session.cart %}
                <span class="cart-badge">{{ session.cart|length }}</span>
                {% endif %}
            </a>
        </div>
    </div>
</nav>

<div class="container py-4">
    <div class="hero-heading">
       <span style="color: var(--gold); text-align: center; font-weight: bold">Pep Hub</span>
    </div>
    <div class="hero-sub">
        Engineered for Optimal Human Performance & Resilience
    </div>

    <div class="row g-4 mb-5">
        <div class="col-md-6">
            <div class="info-panel">
                <h3>🧬 What Are Peptides and Why Are They So Promising?</h3>
                <p style="font-size:0.9rem;">Peptides are small chains of amino acids that function as signaling molecules in the body. Think of them as messengers that tell your cells what to do, they can heal tissue, burn fat or regulate hormones. YOUR BODY ALREADY MAKES THEM - but as you age, the production drops off and so they begin to break down. How do they work? Well they bind to specific receptors on the surface of cells and trigger a specific response, in research applications, they demonstrate unique properties in the areas of repair, cell renewal, fat loss, and hormonal balance. Due to their targeted action, they are being studied worldwide in preclinical and laboratory research.</p>
                <ul class="bullet-list">
                    <li><i class="bi bi-file-text"></i> <a href="/coa" style="color:var(--charcoal);text-decoration:underline;text-decoration-color:var(--gold);">COA / Test Results</a></li>
                    <li><i class="bi bi-truck"></i> Fast EU delivery</li>
                </ul>
            </div>
        </div>
        <div class="col-md-6">
            <div class="info-panel">
                <h3>📂 FIND YOUR CATEGORY QUICKLY</h3>
                <p style="font-size:0.85rem;">Discover the power of research peptides, categorized based on their unique effects. From fat loss to hormonal support – choose the peptide that suits your research goal.</p>
                <div id="categoryAccordion">
                    <div class="category-item" onclick="toggleCategory(this)">
                        <div class="category-header">
                            <span><i class="bi bi-fire"></i> Weight &amp; Metabolic</span>
                            <i class="bi bi-plus-lg"></i>
                        </div>
                        <div class="category-content">
                            <strong>Retatrutide</strong><br>
                            Triple agonist targeting GLP-1, GIP, and Glucagon receptors for metabolic rate, glucose control, and fat oxidation.
                        </div>
                    </div>
                    <div class="category-item" onclick="toggleCategory(this)">
                        <div class="category-header">
                            <span><i class="bi bi-heart-pulse"></i> Recovery &amp; Repair</span>
                            <i class="bi bi-plus-lg"></i>
                        </div>
                        <div class="category-content">
                            <strong>BPC-157 &amp; TB-500</strong><br>
                            Combined systemic and local tissue repair — accelerates healing of tendons, ligaments, muscle, and gut lining.
                        </div>
                    </div>
                    <div class="category-item" onclick="toggleCategory(this)">
                        <div class="category-header">
                            <span><i class="bi bi-stars"></i> Skin &amp; Collagen</span>
                            <i class="bi bi-plus-lg"></i>
                        </div>
                        <div class="category-content">
                            <strong>GHK-Cu</strong><br>
                            Stimulates collagen and elastin synthesis, supports wound healing, skin rejuvenation, and hair follicle activation.
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Product grid: each product shows base price and bulk discount note -->
    <div id="products" style="scroll-margin-top:90px;"></div>
    <div class="row g-4 row-cols-1 row-cols-sm-2 row-cols-md-3 justify-content-center">
        {% for p in products %}
        <div class="col">
            <div class="card product-card h-100 text-center p-2">
                <div class="card-body">
                    <i class="bi bi-droplet-half"></i>
                    <div class="mt-2">
                        <a href="/product/{{ p.id }}" class="product-name-link">
                            {{ p.name }}
                        </a>
                    </div>
                    <div class="mt-2">
                        <span class="price-tag fw-bold">€{{ "%.2f"|format(p.base_price) }}</span>
                        <div class="bulk-note">Bulk discounts up to 15%</div>
                    </div>
                    <small class="text-muted d-block mt-2">Click name for details</small>
                </div>
            </div>
        </div>
        {% endfor %}
    </div>
</div>

<!-- Peptide Calculator & Knowledge Modal (unchanged) -->
<div class="modal fade" id="calculatorModal" tabindex="-1">
    <div class="modal-dialog modal-lg modal-dialog-centered">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title"><i class="bi bi-calculator-fill gold-accent"></i> Peptide Dose Calculator & Knowledge</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <div class="row">
                    <div class="col-md-7">
                        <div class="calculator-card">
                            <h6 class="fw-bold">📊 Calculate Injection Volume</h6>
                            <div class="mb-3">
                                <label class="form-label">Select Peptide (or custom)</label>
                                <select id="peptideSelect" class="form-select" onchange="updatePeptideInfo()">
                                    <option value="">-- Custom / Historical Overview --</option>
                                    <option value="BPC-157">BPC-157</option>
                                    <option value="TB-500">TB-500</option>
                                    <option value="GHK-Cu">GHK-Cu</option>
                                    <option value="CJC-1295/Ipamorelin">CJC-1295/Ipamorelin</option>
                                    <option value="Retatrutide">Retatrutide</option>
                                    <option value="Semax">Semax</option>
                                    <option value="Epitalon">Epitalon</option>
                                    <option value="MOTS-c">MOTS-c</option>
                                </select>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Desired Dose (mcg)</label>
                                <input type="number" id="doseMcg" class="form-control" value="250">
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Vial Strength (mg)</label>
                                <input type="number" id="vialMg" class="form-control" value="5">
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Bacteriostatic Water (ml)</label>
                                <input type="number" id="bacterioMl" class="form-control" value="2">
                            </div>
                            <div class="alert alert-warning bg-light border-0 text-center">
                                <strong>💉 Injection Volume:</strong> <span id="unitsResult" class="result-text">0</span> units (1ml syringe)
                            </div>
                            <small class="text-muted">Formula: (dose_mcg / (vial_mg*1000)) * bac_water_ml * 100</small>
                        </div>
                    </div>
                    <div class="col-md-5">
                        <div class="knowledge-card">
                            <h6 class="fw-bold">📚 Peptide Knowledge & History</h6>
                            <div id="knowledgePanel"></div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Close</button>
            </div>
        </div>
    </div>
</div>

<div class="footer-note">
    <div class="container">
        <p>© 2024–2026 Pep Hub · Bio-Stimulators — Advanced peptide science for human optimisation. These statements have not been evaluated by the FDA.</p>
    </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
    function toggleCategory(element) {
        let content = element.querySelector('.category-content');
        let icon = element.querySelector('.category-header i');
        if (content.classList.contains('show')) {
            content.classList.remove('show');
            icon.classList.remove('bi-dash-lg');
            icon.classList.add('bi-plus-lg');
        } else {
            content.classList.add('show');
            icon.classList.remove('bi-plus-lg');
            icon.classList.add('bi-dash-lg');
        }
    }

    const peptideKnowledge = {
        "BPC-157": "<strong>📌 Typical dose:</strong> 250-500 mcg daily<br><strong>⏱️ Half-life:</strong> 4-6 hours<br><strong>❄️ Storage:</strong> Refrigerate after reconstitution<br><strong>🧬 Use:</strong> Gut healing, tendon/ligament repair",
        "TB-500": "<strong>📌 Typical dose:</strong> 2.5-5 mg per week<br><strong>⏱️ Half-life:</strong> 6-7 days<br><strong>❄️ Storage:</strong> Refrigerate<br><strong>🧬 Use:</strong> Soft tissue repair, systemic inflammation reduction",
        "GHK-Cu": "<strong>📌 Typical dose:</strong> 1-2 mg daily<br><strong>⏱️ Half-life:</strong> 30-60 min<br><strong>❄️ Storage:</strong> Refrigerate<br><strong>🧬 Use:</strong> Collagen synthesis, skin rejuvenation",
        "CJC-1295/Ipamorelin": "<strong>📌 Typical dose:</strong> 100-200 mcg each daily<br><strong>⏱️ Half-life:</strong> 6-8h (CJC), 2h (Ipa)<br><strong>❄️ Storage:</strong> Refrigerate<br><strong>🧬 Use:</strong> GH release, muscle growth, fat loss",
        "Retatrutide": "<strong>📌 Typical dose:</strong> 1-4 mg weekly<br><strong>⏱️ Half-life:</strong> 6 days<br><strong>❄️ Storage:</strong> Refrigerate<br><strong>🧬 Use:</strong> Metabolic regulation, weight management",
        "Semax": "<strong>📌 Typical dose:</strong> 200-600 mcg daily<br><strong>⏱️ Half-life:</strong> 2-3 hours<br><strong>❄️ Storage:</strong> Room temperature<br><strong>🧬 Use:</strong> Cognitive enhancement, neuroprotection",
        "Epitalon": "<strong>📌 Typical dose:</strong> 5-10 mg per day (cycles)<br><strong>⏱️ Half-life:</strong> 4-6 hours<br><strong>❄️ Storage:</strong> Refrigerate<br><strong>🧬 Use:</strong> Telomere support, pineal regulation",
        "MOTS-c": "<strong>📌 Typical dose:</strong> 5-10 mg per week<br><strong>⏱️ Half-life:</strong> 2-3 hours<br><strong>❄️ Storage:</strong> Refrigerate<br><strong>🧬 Use:</strong> Mitochondrial health, exercise mimetic"
    };

    const historicalOverview = `
        <div style="font-size:0.85rem;">
            <p><strong>🔬 A Brief History of Peptides in Human Health</strong></p>
            <p><strong>1921:</strong> Discovery of insulin – the first peptide used therapeutically, revolutionizing diabetes treatment.</p>
            <p><strong>1950s–60s:</strong> Synthesis of oxytocin and vasopressin; understanding of peptide hormones.</p>
            <p><strong>1970s:</strong> Growth hormone-releasing peptides (GHRPs) discovered, leading to modern secretagogues.</p>
            <p><strong>1990s:</strong> BPC-157 isolated from gastric juice; early studies on tissue healing.</p>
            <p><strong>2000s:</strong> Epitalon research on telomeres and pineal gland; GHK-Cu gains popularity for skin repair.</p>
            <p><strong>2010s–2020s:</strong> Rise of synthetic peptides like Semax (cognitive), TB-500 (systemic repair), and triple agonists like Retatrutide.</p>
            <hr class="my-2">
            <p><strong>🧬 General Functions of Peptides:</strong><br>
            • Signal cellular regeneration and repair<br>
            • Modulate hormone release (GH, insulin, GLP‑1)<br>
            • Reduce inflammation and oxidative stress<br>
            • Enhance cognitive function and neuroprotection<br>
            • Support metabolic health and longevity</p>
            <p class="text-muted mt-2"><small>Select any peptide from the dropdown for specific dose, half‑life, and storage information.</small></p>
        </div>
    `;

    function updatePeptideInfo() {
        let select = document.getElementById('peptideSelect');
        let peptide = select.value;
        let panel = document.getElementById('knowledgePanel');
        if (peptide && peptideKnowledge[peptide]) {
            panel.innerHTML = `<div class="small">${peptideKnowledge[peptide]}</div>`;
        } else {
            panel.innerHTML = historicalOverview;
        }
        calculateDose();
    }

    function calculateDose() {
        let doseMcg = parseFloat(document.getElementById('doseMcg').value);
        let vialMg = parseFloat(document.getElementById('vialMg').value);
        let bacterioMl = parseFloat(document.getElementById('bacterioMl').value);
        if (isNaN(doseMcg) || isNaN(vialMg) || isNaN(bacterioMl) || vialMg <= 0 || bacterioMl <= 0) {
            document.getElementById('unitsResult').innerText = "Invalid";
            return;
        }
        let units = (doseMcg / (vialMg * 1000)) * bacterioMl * 100;
        document.getElementById('unitsResult').innerText = units.toFixed(1);
    }

    document.getElementById('doseMcg').addEventListener('input', calculateDose);
    document.getElementById('vialMg').addEventListener('input', calculateDose);
    document.getElementById('bacterioMl').addEventListener('input', calculateDose);
    window.updatePeptideInfo = updatePeptideInfo;
    window.calculateDose = calculateDose;
    window.toggleCategory = toggleCategory;

    document.getElementById('knowledgePanel').innerHTML = historicalOverview;
    calculateDose();
</script>
</body>
</html>
"""

# Product detail page (shows base price and bulk discount info, uses add-to-cart with quantity)
PRODUCT_DETAIL_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ product.name }} | Pep Hub</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        :root { --gold: #FF9000; --gold-dark: #E07800; --charcoal: #141414; --off-white: #141414; }
        body { background: var(--off-white); font-family: 'Inter', sans-serif; }
        .navbar { background: #000 !important; border-bottom: 1px solid rgba(212,175,55,0.3); }
        .btn-primary { background: var(--gold); border: none; border-radius: 40px; color: var(--charcoal); }
        .btn-primary:hover { background: var(--gold-dark); color: white; }
        .product-detail-card { background: white; border-radius: 1.5rem; border: 1px solid #EDE8DC; padding: 2rem; }
        .price-tag { font-size: 2rem; font-weight: 700; color: var(--gold-dark); }
        .bulk-info { background: #F8F6F0; border-radius: 1rem; padding: 1rem; margin: 1rem 0; }
        .footer-note { border-top: 1px solid #EDE8DC; font-size: 0.75rem; text-align: center; padding: 2rem 0; margin-top: 3rem; color: #5A6E7A; }
    </style>
</head>
<body>
<nav class="navbar navbar-dark">
    <div class="container">
        <a href="/" class="navbar-brand">🔬 Pep Hub</a>
        <a href="/cart" class="btn btn-outline-gold">🛒 Cart</a>
    </div>
</nav>
<div class="container py-5">
    <div class="product-detail-card">
        <h1 class="mb-3">{{ product.name }}</h1>
        <div class="mb-4">
            <span class="price-tag">€{{ "%.2f"|format(product.base_price) }}</span>
            <div class="bulk-info mt-3">
                <strong>📦 Bulk discounts (same product):</strong><br>
                2–4 items → 5% off<br>
                5–9 items → 10% off<br>
                10+ items → 15% off<br>
                <span class="small text-muted">Discount applied automatically in cart.</span>
            </div>
        </div>
        <div class="mb-4">
            {{ product.desc|safe }}
        </div>
        <form method="POST" action="/add-to-cart/{{ product.id }}">
            <div class="d-flex gap-3 align-items-center">
                <label class="fw-semibold">Quantity:</label>
                <input type="number" name="quantity" value="1" min="1" max="99" class="form-control" style="width: 80px;">
                <button type="submit" class="btn btn-primary rounded-pill px-4">Add to Cart</button>
            </div>
        </form>
        <div class="mt-4">
            <a href="/" class="btn btn-outline-secondary rounded-pill">← Back to products</a>
        </div>
    </div>
</div>
<div class="footer-note">
    <div class="container">
        <p>© 2024–2026 Pep Hub · Bio-Stimulators — For research purposes only.</p>
    </div>
</div>
</body>
</html>
"""

# Cart template
CART_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <title>Cart | Pep Hub</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        :root { --ph-orange:#FF9000; --ph-orange-dark:#E07800; --ph-black:#141414; --ph-card:#242424; --ph-border:#2D2D2D; }
        body { background: var(--ph-black); font-family: 'Inter', 'Segoe UI', system-ui, sans-serif; color:#F5F5F5; }
        .navbar { background: #000 !important; border-bottom: 1px solid var(--ph-border); padding: 0.85rem 0; }
        .navbar-brand { font-weight: 800; color: #fff !important; font-size: 1.55rem; letter-spacing:-0.5px; text-decoration: none; }
        .navbar-brand .brand-hub { background: var(--ph-orange); color: #000; border-radius: 6px; padding: 0.05em 0.3em; margin-left: 2px; font-weight: 900; }
        .btn-outline-gold { border: 1px solid var(--ph-orange); border-radius: 6px; color: var(--ph-orange); padding: 0.4rem 1.2rem; text-decoration: none; font-weight: 600; font-size:0.85rem; transition: all 0.2s; background:transparent; }
        .btn-outline-gold:hover { background: var(--ph-orange); color: #000; }
        .btn-primary { background: var(--ph-orange); border: none; border-radius: 6px; color: #000; font-weight: 800; }
        .btn-primary:hover { background: #fff; color: #000; }
        h1 { color: #fff; font-weight: 800; }
        .table { background: var(--ph-card); border-radius: 1rem; overflow: hidden; color:#E0E0E0; --bs-table-bg:transparent; --bs-table-color:#E0E0E0; --bs-table-hover-bg:#1A1A1A; --bs-table-hover-color:#fff; --bs-table-border-color:var(--ph-border); }
        .table thead th { background: #1A1A1A !important; color: var(--ph-orange) !important; border-bottom: 1px solid var(--ph-border); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; padding:0.85rem 0.75rem; }
        .table tbody td { padding:0.85rem 0.75rem; border-bottom:1px solid var(--ph-border); }
        .table tfoot td { color:#fff; font-weight:800; }
        .original-price { text-decoration: line-through; color: #777; font-size: 0.9rem; margin-right: 0.3rem; }
        .discounted-price { font-weight: 800; color: var(--ph-orange); }
        .alert-info { background:#1A1A1A; border:1px solid var(--ph-border); color:#999; }
        /* Quantity stepper */
        .qty-stepper { display:inline-flex; align-items:center; background:#0F0F0F; border:1px solid var(--ph-border); border-radius:8px; overflow:hidden; }
        .qty-stepper .qf { margin:0; display:flex; }
        .qbtn { width:34px; height:36px; background:transparent; border:none; color:#ddd; font-size:1.2rem; font-weight:700; line-height:1; cursor:pointer; transition:all .15s; display:flex; align-items:center; justify-content:center; }
        .qbtn:hover:not(:disabled) { background:var(--ph-orange); color:#000; }
        .qbtn:disabled { opacity:.3; cursor:not-allowed; }
        .qnum { width:46px; height:36px; background:transparent; border:none; border-left:1px solid var(--ph-border); border-right:1px solid var(--ph-border); color:#fff; text-align:center; font-weight:700; font-size:.95rem; -moz-appearance:textfield; }
        .qnum::-webkit-outer-spin-button, .qnum::-webkit-inner-spin-button { -webkit-appearance:none; margin:0; }
        .qnum:focus { outline:none; background:#1A1A1A; }
        .btn-remove { background:transparent; border:none; color:#777; font-size:1.05rem; cursor:pointer; padding:.35rem .5rem; border-radius:6px; transition:all .15s; }
        .btn-remove:hover { color:#E57373; background:rgba(229,115,115,.12); }
        .footer-note { border-top: 1px solid var(--ph-border); font-size: 0.75rem; text-align: center; padding: 2rem 0; margin-top: 3rem; color: #999; background:#0D0D0D; }
        #page-loader { position:fixed; inset:0; background:#000; display:flex; flex-direction:column; align-items:center; justify-content:center; z-index:9999; transition:opacity 0.45s ease; }
        #page-loader > div { font-size:2.5rem; font-weight:900; letter-spacing:-1px; color:#fff; }
        #page-loader .hub-tag { background:#FF9000; color:#000; border-radius:6px; padding:0 0.2em; margin-left:3px; }
        /* Marquee */
        .ph-marquee { background:#FF9000; color:#000; overflow:hidden; padding:0.55rem 0; border-bottom:2px solid #000; font-size:0.8rem; font-weight:700; position:relative; }
        .ph-marquee-track { display:flex; gap:2.25rem; width:max-content; animation:marqueeScroll 55s linear infinite; }
        .ph-marquee:hover .ph-marquee-track { animation-play-state: paused; }
        .ph-promo { white-space:nowrap; display:inline-flex; align-items:center; gap:0.35rem; }
        .ph-promo code { background:#000; color:#FF9000; padding:0.05rem 0.4rem; border-radius:3px; font-family:'Courier New',monospace; font-size:0.78rem; font-weight:800; margin-left:0.2rem; }
        .ph-divider { color:rgba(0,0,0,0.35); font-weight:900; }
        @keyframes marqueeScroll { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
        /* Promo + totals */
        .cart-grid { display:grid; grid-template-columns: 1fr 360px; gap:1.5rem; }
        @media (max-width: 900px) { .cart-grid { grid-template-columns: 1fr; } }
        .summary-card { background:var(--ph-card); border:1px solid var(--ph-border); border-radius:1rem; padding:1.5rem; position:sticky; top:90px; align-self:start; }
        .summary-card h5 { font-size:0.95rem; font-weight:800; color:#fff; margin-bottom:1rem; letter-spacing:0.02em; }
        .promo-input-wrap { display:flex; gap:0.5rem; margin-bottom:0.5rem; }
        .promo-input { flex:1; background:#0F0F0F; border:1px solid var(--ph-border); border-radius:6px; padding:0.55rem 0.8rem; color:#fff; font-family:'Courier New',monospace; font-weight:700; letter-spacing:0.04em; text-transform:uppercase; font-size:0.85rem; }
        .promo-input:focus { outline:none; border-color:var(--ph-orange); box-shadow:0 0 0 2px rgba(255,144,0,0.2); }
        .btn-apply { background:var(--ph-orange); color:#000; border:none; border-radius:6px; padding:0.55rem 1rem; font-weight:800; font-size:0.8rem; letter-spacing:0.04em; text-transform:uppercase; cursor:pointer; }
        .btn-apply:hover { background:#fff; }
        .promo-active { background:rgba(255,144,0,0.1); border:1px solid rgba(255,144,0,0.4); border-radius:6px; padding:0.55rem 0.8rem; font-size:0.78rem; color:var(--ph-orange); margin-bottom:0.85rem; display:flex; justify-content:space-between; align-items:center; gap:0.5rem; }
        .promo-active a { color:#999; text-decoration:none; font-size:1.2rem; line-height:1; }
        .promo-active a:hover { color:#fff; }
        .promo-error { background:rgba(198,40,40,0.12); border:1px solid rgba(198,40,40,0.5); border-radius:6px; padding:0.5rem 0.75rem; font-size:0.78rem; color:#E57373; margin-bottom:0.85rem; }
        .free-ship-bar { background:#0F0F0F; border:1px solid var(--ph-border); border-radius:6px; padding:0.55rem 0.75rem; margin-bottom:0.85rem; font-size:0.75rem; color:#999; }
        .free-ship-bar.unlocked { border-color:rgba(46,125,50,0.5); color:#81C784; background:rgba(46,125,50,0.08); font-weight:700; }
        .totals-row { display:flex; justify-content:space-between; padding:0.45rem 0; font-size:0.88rem; color:#BFBFBF; }
        .totals-row.discount { color:var(--ph-orange); font-weight:700; }
        .totals-row.total { font-size:1.15rem; font-weight:800; color:#fff; padding-top:0.85rem; margin-top:0.45rem; border-top:1px solid var(--ph-border); }
        .btn-checkout { display:block; width:100%; background:var(--ph-orange); color:#000; border:none; border-radius:6px; padding:0.85rem; font-weight:900; text-transform:uppercase; letter-spacing:0.05em; text-decoration:none; text-align:center; margin-top:1rem; font-size:0.9rem; transition:all 0.2s; }
        .btn-checkout:hover { background:#fff; color:#000; }
    </style>
</head>
<body>
<div id="page-loader"><div>Pep<span class="hub-tag">Hub</span></div></div>

<div class="ph-marquee"><div class="ph-marquee-track">
    <span class="ph-promo">📦 FREE EU SHIPPING OVER €100</span><span class="ph-divider">◆</span>
    <span class="ph-promo">🧬 STACK 2 PEPTIDES · 15% OFF <code>STACK15</code></span><span class="ph-divider">◆</span>
    <span class="ph-promo">⚡ BULK 5+ · 20% OFF <code>BULK20</code></span><span class="ph-divider">◆</span>
    <span class="ph-promo">🎓 RESEARCHER · 10% <code>RESEARCH</code></span><span class="ph-divider">◆</span>
    <span class="ph-promo">👋 NEW · 10% OFF <code>WELCOME10</code></span><span class="ph-divider">◆</span>
    <span class="ph-promo">📦 FREE EU SHIPPING OVER €100</span><span class="ph-divider">◆</span>
    <span class="ph-promo">🧬 STACK 2 PEPTIDES · 15% OFF <code>STACK15</code></span><span class="ph-divider">◆</span>
    <span class="ph-promo">⚡ BULK 5+ · 20% OFF <code>BULK20</code></span><span class="ph-divider">◆</span>
    <span class="ph-promo">🎓 RESEARCHER · 10% <code>RESEARCH</code></span><span class="ph-divider">◆</span>
    <span class="ph-promo">👋 NEW · 10% OFF <code>WELCOME10</code></span><span class="ph-divider">◆</span>
</div></div>

<style>
    .ph-menu { display:flex; gap:1.6rem; align-items:center; }
    .ph-menu a { color:#cfcfcf; text-decoration:none; font-weight:600; font-size:.92rem; transition:color .2s; white-space:nowrap; }
    .ph-menu a:hover, .ph-menu a.active { color:var(--ph-orange); }
    .ph-menu a.active { border-bottom:2px solid var(--ph-orange); padding-bottom:2px; }
</style>
<nav class="navbar navbar-expand-lg sticky-top">
    <div class="container d-flex justify-content-between align-items-center">
        <a href="/" class="navbar-brand">Pep<span class="brand-hub">Hub</span></a>
        <div class="ph-menu mx-auto d-none d-lg-flex">
            <a href="/">Home</a>
            <a href="/shop">Shop</a>
            <a href="/deals">Bulk Deals</a>
            <a href="/science">Science Hub</a>
            <a href="/coa">COA Reports</a>
            <a href="{{ '/account' if current_member else '/account/login' }}">{{ 'Account' if current_member else 'Login' }}</a>
        </div>
        <a href="/" class="btn-outline-gold">← Continue Shopping</a>
    </div>
</nav>
<div class="container mt-4 mb-5">
    <h1 class="mb-4">Shopping Cart</h1>
    {% if cart %}
    <div class="cart-grid">
        <div class="table-responsive">
            <table class="table align-middle mb-0">
                <thead>
                    <tr><th>Product</th><th>Unit price</th><th>Quantity</th><th class="text-end">Subtotal</th><th></th></tr>
                </thead>
                <tbody>
                {% for item in cart %}
                <tr>
                    <td>
                        <strong style="color:#fff;">{{ item.name }}</strong>
                        {% if item.mode == 'sub' %}<span style="display:inline-block;background:rgba(255,144,0,0.15);border:1px solid rgba(255,144,0,0.4);color:var(--ph-orange);border-radius:5px;padding:0 0.4rem;font-size:0.62rem;font-weight:800;margin-left:0.35rem;vertical-align:middle;">🔁 MONTHLY</span>{% endif %}
                        <br><span style="color:#888;font-size:0.75rem;">{{ item.variant_label }} · {{ item.sku }}{% if item.mode == 'sub' %} · billed monthly{% endif %}</span>
                    </td>
                    <td>
                        {% if item.base_price != item.unit_price %}<span class="original-price">€{{ "%.2f"|format(item.base_price) }}</span>{% endif %}
                        <span class="discounted-price">€{{ "%.2f"|format(item.unit_price) }}</span>
                    </td>
                    <td>
                        <div class="qty-stepper">
                            <form method="POST" action="/cart/update" class="qf">
                                <input type="hidden" name="key" value="{{ item.cart_key }}">
                                <input type="hidden" name="action" value="dec">
                                <button type="submit" class="qbtn"{% if item.quantity == 1 %} onclick="return confirm('Remove {{ item.name|e }} from your cart?');"{% endif %} aria-label="Decrease quantity">−</button>
                            </form>
                            <form method="POST" action="/cart/update" class="qf">
                                <input type="hidden" name="key" value="{{ item.cart_key }}">
                                <input type="hidden" name="action" value="set">
                                <input type="number" name="qty" class="qnum" value="{{ item.quantity }}" min="0" max="99"
                                       onchange="if(this.value==0 || this.value===''){ if(!confirm('Remove {{ item.name|e }} from your cart?')){ this.value={{ item.quantity }}; return; } } this.form.submit();"
                                       aria-label="Quantity">
                            </form>
                            <form method="POST" action="/cart/update" class="qf">
                                <input type="hidden" name="key" value="{{ item.cart_key }}">
                                <input type="hidden" name="action" value="inc">
                                <button type="submit" class="qbtn"{% if item.quantity >= 99 %} disabled{% endif %} aria-label="Increase quantity">+</button>
                            </form>
                        </div>
                    </td>
                    <td class="text-end">€{{ "%.2f"|format(item.subtotal) }}{% if item.mode == 'sub' %}<br><span style="color:#888;font-size:0.68rem;">/mo</span>{% endif %}</td>
                    <td class="text-end">
                        <form method="POST" action="/cart/update" class="qf" onsubmit="return confirm('Remove {{ item.name|e }} from your cart?');">
                            <input type="hidden" name="key" value="{{ item.cart_key }}">
                            <input type="hidden" name="action" value="remove">
                            <button type="submit" class="btn-remove" title="Remove item" aria-label="Remove item"><i class="bi bi-trash"></i></button>
                        </form>
                    </td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>

        <div class="summary-card">
            <h5>🎟️ PROMO CODE</h5>
            <form method="POST" action="/apply-promo" class="promo-input-wrap">
                <input type="text" name="code" class="promo-input" placeholder="Enter code" value="{{ promo_code or '' }}" autocomplete="off">
                <button type="submit" class="btn-apply">Apply</button>
            </form>
            {% if promo_code %}
            <div class="promo-active">
                <span>✓ <strong>{{ promo_code }}</strong> — {{ promo_desc }}</span>
                <a href="/remove-promo" title="Remove">×</a>
            </div>
            {% endif %}
            {% if promo_note %}
            <div class="promo-error" style="background:rgba(255,144,0,.12);border-color:rgba(255,144,0,.4);color:#ffce9e;">ℹ {{ promo_note }}</div>
            {% endif %}
            {% if session.promo_error %}
            <div class="promo-error">⚠ {{ session.promo_error }}</div>
            {% endif %}

            {% if free_shipping %}
            <div class="free-ship-bar unlocked">📦 Free EU shipping unlocked ✓</div>
            {% else %}
            <div class="free-ship-bar">Add <strong style="color:var(--ph-orange);">€{{ "%.2f"|format(free_shipping_remaining) }}</strong> more for free shipping</div>
            {% endif %}

            <h5 style="margin-top:1.25rem;">ORDER SUMMARY</h5>
            <div class="totals-row"><span>Subtotal</span><span>€{{ "%.2f"|format(subtotal) }}</span></div>
            {% if discount > 0 %}
            <div class="totals-row discount"><span>Promo · {{ promo_code }} ({{ promo_percent }}%)</span><span>−€{{ "%.2f"|format(discount) }}</span></div>
            {% endif %}
            <div class="totals-row">
                <span>Shipping</span>
                <span>{% if free_shipping %}FREE{% else %}€{{ "%.2f"|format(shipping) }}{% endif %}</span>
            </div>
            <div class="totals-row" style="font-size:0.78rem;color:#888;"><span>VAT incl. ({{ (VAT_RATE * 100)|int }}%)</span><span>€{{ "%.2f"|format(vat_amount) }}</span></div>
            <div class="totals-row total"><span>Total</span><span>€{{ "%.2f"|format(total) }}</span></div>

            <a href="/checkout" class="btn-checkout">Proceed to Checkout →</a>
        </div>
    </div>
    {% else %}
    <div class="alert alert-info text-center">Your cart is empty. Start shopping!</div>
    <div class="text-center"><a href="/" class="btn btn-primary rounded-pill">Browse Products</a></div>
    {% endif %}
</div>
<div class="footer-note"><div class="container"><p>© 2024–2026 PepHub — Research-grade peptide science.</p></div></div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>window.addEventListener('load',()=>{const l=document.getElementById('page-loader');if(l){l.style.opacity='0';setTimeout(()=>l.style.display='none',450);}});</script>
</body>
</html>
"""

# Checkout template
CHECKOUT_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <title>Checkout | Pep Hub</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        :root { --ph-orange:#FF9000; --ph-black:#141414; --ph-card:#242424; --ph-border:#2D2D2D; }
        body { background: var(--ph-black); font-family: 'Inter', 'Segoe UI', system-ui, sans-serif; color:#F5F5F5; }
        .navbar { background: #000 !important; border-bottom: 1px solid var(--ph-border); padding: 0.85rem 0; }
        .navbar-brand { font-weight: 800; color: #fff !important; font-size: 1.55rem; letter-spacing:-0.5px; text-decoration: none; }
        .navbar-brand .brand-hub { background: var(--ph-orange); color: #000; border-radius: 6px; padding: 0.05em 0.3em; margin-left: 2px; font-weight: 900; }
        .checkout-card { background: var(--ph-card); border: 1px solid var(--ph-border); border-radius: 1rem; padding: 2rem; color:#E0E0E0; }
        .checkout-card h1 { color:#fff; font-weight:800; }
        .checkout-card .lead { color:#BFBFBF; }
        .checkout-card .lead strong { color: var(--ph-orange); }
        .btn-pay { background:var(--ph-orange); color:#000; border:none; border-radius:6px; padding:0.7rem 2rem; font-weight:800; text-transform:uppercase; letter-spacing:0.04em; transition:all 0.2s; }
        .btn-pay:hover { background:#fff; color:#000; }
        .btn-back { color: #999; text-decoration:none; }
        .btn-back:hover { color: var(--ph-orange); }
        #page-loader { position:fixed; inset:0; background:#000; display:flex; flex-direction:column; align-items:center; justify-content:center; z-index:9999; transition:opacity 0.45s ease; }
        #page-loader > div { font-size:2.5rem; font-weight:900; letter-spacing:-1px; color:#fff; }
        #page-loader .hub-tag { background:#FF9000; color:#000; border-radius:6px; padding:0 0.2em; margin-left:3px; }
        .ph-marquee { background:#FF9000; color:#000; overflow:hidden; padding:0.55rem 0; border-bottom:2px solid #000; font-size:0.8rem; font-weight:700; position:relative; }
        .ph-marquee-track { display:flex; gap:2.25rem; width:max-content; animation:marqueeScroll 55s linear infinite; }
        .ph-marquee:hover .ph-marquee-track { animation-play-state: paused; }
        .ph-promo { white-space:nowrap; display:inline-flex; align-items:center; gap:0.35rem; }
        .ph-promo code { background:#000; color:#FF9000; padding:0.05rem 0.4rem; border-radius:3px; font-family:'Courier New',monospace; font-size:0.78rem; font-weight:800; margin-left:0.2rem; }
        .ph-divider { color:rgba(0,0,0,0.35); font-weight:900; }
        @keyframes marqueeScroll { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
        .order-summary { background:#0F0F0F; border:1px solid var(--ph-border); border-radius:0.75rem; padding:1.25rem; margin-bottom:1.5rem; }
        .summary-row { display:flex; justify-content:space-between; padding:0.4rem 0; font-size:0.88rem; color:#BFBFBF; }
        .summary-row.discount { color:var(--ph-orange); font-weight:700; }
        .summary-row.grand { font-size:1.25rem; font-weight:800; color:#fff; padding-top:0.85rem; margin-top:0.45rem; border-top:1px solid var(--ph-border); }
        .summary-row.grand span:last-child { color:var(--ph-orange); }
        .product-line { display:flex; justify-content:space-between; padding:0.35rem 0; font-size:0.82rem; color:#999; border-bottom:1px dashed var(--ph-border); }
        .product-line:last-child { border-bottom:none; margin-bottom:0.4rem; }
        .promo-badge { display:inline-block; background:rgba(255,144,0,0.15); border:1px solid rgba(255,144,0,0.4); color:var(--ph-orange); border-radius:6px; padding:0.3rem 0.7rem; font-size:0.78rem; font-weight:700; margin-bottom:1rem; }
    </style>
</head>
<body>
<div id="page-loader"><div>Pep<span class="hub-tag">Hub</span></div></div>

<div class="ph-marquee"><div class="ph-marquee-track">
    <span class="ph-promo">🔒 SECURE STRIPE CHECKOUT</span><span class="ph-divider">◆</span>
    <span class="ph-promo">📦 FREE EU SHIPPING OVER €100</span><span class="ph-divider">◆</span>
    <span class="ph-promo">🔬 EVERY BATCH HPLC-TESTED · COA INCLUDED</span><span class="ph-divider">◆</span>
    <span class="ph-promo">🔒 SECURE STRIPE CHECKOUT</span><span class="ph-divider">◆</span>
    <span class="ph-promo">📦 FREE EU SHIPPING OVER €100</span><span class="ph-divider">◆</span>
    <span class="ph-promo">🔬 EVERY BATCH HPLC-TESTED · COA INCLUDED</span><span class="ph-divider">◆</span>
</div></div>

<nav class="navbar navbar-expand-lg sticky-top">
    <div class="container d-flex justify-content-between align-items-center">
        <a href="/" class="navbar-brand">Pep<span class="brand-hub">Hub</span></a>
        <div class="ph-menu d-none d-lg-flex" style="gap:1.6rem;align-items:center;">
            <a href="/" style="color:#cfcfcf;text-decoration:none;font-weight:600;font-size:.92rem;">Home</a>
            <a href="/shop" style="color:#cfcfcf;text-decoration:none;font-weight:600;font-size:.92rem;">Shop</a>
            <a href="/deals" style="color:#cfcfcf;text-decoration:none;font-weight:600;font-size:.92rem;">Bulk Deals</a>
            <a href="/science" style="color:#cfcfcf;text-decoration:none;font-weight:600;font-size:.92rem;">Science Hub</a>
            <a href="/coa" style="color:#cfcfcf;text-decoration:none;font-weight:600;font-size:.92rem;">COA Reports</a>
            <a href="{{ '/account' if current_member else '/account/login' }}" style="color:#cfcfcf;text-decoration:none;font-weight:600;font-size:.92rem;">{{ 'Account' if current_member else 'Login' }}</a>
        </div>
        <a href="/cart" class="btn-back">← Back to cart</a>
    </div>
</nav>
<style>
    .checkout-grid { display:grid; grid-template-columns: 1fr 360px; gap:1.5rem; }
    @media (max-width: 900px) { .checkout-grid { grid-template-columns: 1fr; } }
    .form-grid { display:grid; grid-template-columns: 1fr 1fr; gap:0.85rem; }
    .form-grid .full { grid-column: 1 / -1; }
    .form-grid label { display:block; font-size:0.7rem; font-weight:800; color:#FF9000; text-transform:uppercase; letter-spacing:0.06em; margin-bottom:0.3rem; }
    .form-grid input { width:100%; background:#0F0F0F; border:1px solid #2D2D2D; border-radius:6px; padding:0.6rem 0.85rem; color:#fff; font-size:0.9rem; }
    .form-grid input:focus { outline:none; border-color:#FF9000; box-shadow:0 0 0 2px rgba(255,144,0,0.2); }
    .field-error { background:rgba(198,40,40,0.12); border:1px solid rgba(198,40,40,0.5); border-radius:6px; padding:0.55rem 0.8rem; font-size:0.82rem; color:#E57373; margin-bottom:1rem; }
    .vat-note { font-size:0.72rem; color:#888; padding:0.4rem 0; font-style:italic; }
    .trust-badges { display:grid; grid-template-columns:repeat(3,1fr); gap:0.5rem; margin-top:1rem; }
    .trust-badge { background:#0F0F0F; border:1px solid var(--ph-border,#2D2D2D); border-radius:8px; padding:0.7rem 0.5rem; text-align:center; }
    .trust-badge i { font-size:1.15rem; color:#FF9000; display:block; margin-bottom:0.3rem; }
    .trust-badge .tb-title { font-size:0.72rem; font-weight:800; color:#fff; letter-spacing:0.02em; }
    .trust-badge .tb-sub { font-size:0.62rem; color:#888; margin-top:0.15rem; line-height:1.3; }
    .trust-strip { display:flex; align-items:center; justify-content:center; gap:0.5rem; margin-top:0.85rem; padding:0.55rem 0.75rem; background:rgba(46,125,50,0.08); border:1px solid rgba(46,125,50,0.4); border-radius:8px; font-size:0.74rem; font-weight:700; color:#81C784; }
</style>
<div class="container mt-4 mb-5" style="max-width:1100px;">
    <h1 class="mb-3" style="color:#fff;font-weight:800;">Checkout</h1>
    <div class="checkout-grid">
        <div class="checkout-card">
            {% if field_error %}<div class="field-error">⚠ {{ field_error }}</div>{% endif %}
            <h5 style="font-size:0.9rem;font-weight:800;color:#fff;margin-bottom:1rem;letter-spacing:0.02em;">📍 SHIPPING DETAILS</h5>
            <form method="POST" action="/place-order" id="placeOrderForm">
                <div class="form-grid">
                    <div class="full">
                        <label>Email *</label>
                        <input type="email" name="email" required value="{{ prefill.email or '' }}" placeholder="you@example.com">
                    </div>
                    <div class="full">
                        <label>Full name *</label>
                        <input type="text" name="full_name" required value="{{ prefill.full_name or '' }}" placeholder="Jane Doe">
                    </div>
                    <div>
                        <label>Phone</label>
                        <input type="tel" name="phone" value="{{ prefill.phone or '' }}" placeholder="+31 6 1234 5678">
                    </div>
                    <div>
                        <label>Country *</label>
                        <input type="text" name="country" required value="{{ prefill.country or '' }}" placeholder="Netherlands">
                    </div>
                    <div class="full">
                        <label>Address line 1 *</label>
                        <input type="text" name="address_line1" required value="{{ prefill.address_line1 or '' }}" placeholder="Street, number">
                    </div>
                    <div class="full">
                        <label>Address line 2</label>
                        <input type="text" name="address_line2" value="{{ prefill.address_line2 or '' }}" placeholder="Apartment, suite, etc.">
                    </div>
                    <div>
                        <label>City *</label>
                        <input type="text" name="city" required value="{{ prefill.city or '' }}" placeholder="Amsterdam">
                    </div>
                    <div>
                        <label>Postal code *</label>
                        <input type="text" name="postal_code" required value="{{ prefill.postal_code or '' }}" placeholder="1234 AB">
                    </div>
                </div>
                {% set has_sub = cart|selectattr('mode','equalto','sub')|list|length > 0 %}
                {% if has_sub %}
                <div style="margin-top:1rem;padding:0.85rem 1rem;background:rgba(255,144,0,0.08);border:1px solid rgba(255,144,0,0.4);border-radius:8px;font-size:0.82rem;color:#e0c9a0;line-height:1.55;">
                    <strong style="color:#FF9000;">🔁 Subscription terms:</strong> Your subscription renews monthly and has a <strong>3-month minimum term</strong>. You can cancel anytime from your account after the minimum term; the first three deliveries are committed.
                    <label style="display:flex;gap:0.5rem;align-items:flex-start;margin-top:0.6rem;font-size:0.8rem;color:#ccc;cursor:pointer;">
                        <input type="checkbox" name="agree_sub_terms" required style="margin-top:0.2rem;">
                        <span>I understand this subscription has a 3-month minimum commitment.</span>
                    </label>
                </div>
                {% endif %}
                <button type="submit" class="btn-pay" style="width:100%;font-size:1rem;padding:0.95rem;margin-top:1.25rem;">Place Order · Pay €{{ "%.2f"|format(total) }}</button>

                <div class="trust-strip"><i class="bi bi-shield-lock-fill"></i> Safe, secure &amp; 100% discreet checkout</div>
                <div class="trust-badges">
                    <div class="trust-badge">
                        <i class="bi bi-lock-fill"></i>
                        <div class="tb-title">Secure</div>
                        <div class="tb-sub">256-bit SSL encryption</div>
                    </div>
                    <div class="trust-badge">
                        <i class="bi bi-credit-card-2-back-fill"></i>
                        <div class="tb-title">Safe Payment</div>
                        <div class="tb-sub">Stripe-protected · cards never stored</div>
                    </div>
                    <div class="trust-badge">
                        <i class="bi bi-box-seam-fill"></i>
                        <div class="tb-title">Discreet</div>
                        <div class="tb-sub">Plain packaging · private billing</div>
                    </div>
                </div>

                <p class="vat-note">🔒 Stripe-secured checkout. Your payment details are encrypted and never stored on our servers. By placing your order you confirm research-use purposes only.</p>
            </form>
            <a href="/cart" class="btn-back mt-2 d-inline-block">← Back to cart</a>
        </div>

        <div>
            <div class="order-summary" style="position:sticky;top:90px;">
                <h5 style="font-size:0.9rem;font-weight:800;color:#fff;margin-bottom:1rem;letter-spacing:0.02em;">🧾 ORDER SUMMARY</h5>
                {% if promo_code %}
                <div class="promo-badge">✓ {{ promo_code }} — {{ promo_desc }}</div>
                {% endif %}
                {% if promo_note %}
                <div style="font-size:.72rem;color:#ffce9e;background:rgba(255,144,0,.12);border:1px solid rgba(255,144,0,.35);border-radius:8px;padding:.45rem .6rem;margin-bottom:.6rem;">ℹ {{ promo_note }}</div>
                {% endif %}
                {% for item in cart %}
                <div class="product-line">
                    <span>{{ item.name }} <span style="color:#666;">· {{ item.variant_label }} × {{ item.quantity }}</span></span>
                    <span>€{{ "%.2f"|format(item.subtotal) }}</span>
                </div>
                {% endfor %}
                <div class="summary-row" style="margin-top:0.4rem;"><span>Subtotal</span><span>€{{ "%.2f"|format(subtotal) }}</span></div>
                {% if discount > 0 %}
                <div class="summary-row discount"><span>Promo · {{ promo_code }} ({{ promo_percent }}%)</span><span>−€{{ "%.2f"|format(discount) }}</span></div>
                {% endif %}
                <div class="summary-row"><span>Shipping</span><span>{% if free_shipping %}FREE{% else %}€{{ "%.2f"|format(shipping) }}{% endif %}</span></div>
                <div class="summary-row" style="font-size:0.78rem;color:#888;"><span>VAT incl. ({{ (VAT_RATE * 100)|int }}%)</span><span>€{{ "%.2f"|format(vat_amount) }}</span></div>
                <div class="summary-row grand"><span>Total</span><span>€{{ "%.2f"|format(total) }}</span></div>
            </div>
        </div>
    </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>window.addEventListener('load',()=>{const l=document.getElementById('page-loader');if(l){l.style.opacity='0';setTimeout(()=>l.style.display='none',450);}});</script>
</body>
</html>
"""

# ----------------------------------------------------------------------
# Order success page
# ----------------------------------------------------------------------
SUCCESS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <title>Order Confirmed · {{ order.order_number }} | PepHub</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background:#141414; color:#F5F5F5; font-family:'Inter','Segoe UI',system-ui,sans-serif; margin:0; }
        .navbar { background:#000; border-bottom:1px solid #2D2D2D; padding:0.85rem 0; }
        .navbar-brand { font-weight:800; color:#fff; font-size:1.55rem; letter-spacing:-0.5px; text-decoration:none; }
        .navbar-brand .brand-hub { background:#FF9000; color:#000; border-radius:6px; padding:0.05em 0.3em; margin-left:2px; font-weight:900; }
        .success-card { background:#242424; border:1px solid #2D2D2D; border-radius:1rem; padding:2rem; color:#E0E0E0; max-width:720px; margin:2rem auto; }
        .success-icon { font-size:3rem; text-align:center; color:#FF9000; margin-bottom:0.5rem; }
        h1 { color:#fff; font-weight:800; text-align:center; }
        .ord-num { display:block; text-align:center; font-family:'Courier New',monospace; font-size:1.2rem; color:#FF9000; font-weight:800; margin:0.6rem 0 1.5rem; letter-spacing:0.05em; }
        .ord-card { background:#0F0F0F; border:1px solid #2D2D2D; border-radius:0.7rem; padding:1.2rem; margin-bottom:1rem; }
        .ord-card h6 { font-size:0.72rem; font-weight:800; color:#FF9000; letter-spacing:0.06em; text-transform:uppercase; margin-bottom:0.7rem; }
        .ord-line { display:flex; justify-content:space-between; font-size:0.85rem; padding:0.3rem 0; color:#BFBFBF; border-bottom:1px dashed #2D2D2D; }
        .ord-line:last-child { border-bottom:none; }
        .ord-line strong { color:#fff; }
        .ord-line.total { font-weight:800; font-size:1.05rem; color:#fff; border-bottom:none; padding-top:0.65rem; }
        .ord-line.total span:last-child { color:#FF9000; }
        .next-steps { font-size:0.85rem; line-height:1.7; color:#BFBFBF; }
        .next-steps strong { color:#fff; }
        .btn-cont { display:inline-block; background:#FF9000; color:#000; border-radius:6px; padding:0.6rem 1.5rem; text-decoration:none; font-weight:800; letter-spacing:0.04em; text-transform:uppercase; }
        .ph-menu { display:flex; gap:1.6rem; align-items:center; }
        .ph-menu a { color:#cfcfcf; text-decoration:none; font-weight:600; font-size:.92rem; transition:color .2s; white-space:nowrap; }
        .ph-menu a:hover { color:#FF9000; }
    </style>
</head>
<body>
<nav class="navbar"><div class="container d-flex justify-content-between align-items-center">
    <a class="navbar-brand" href="/">Pep<span class="brand-hub">Hub</span></a>
    <div class="ph-menu d-none d-lg-flex">
        <a href="/">Home</a>
        <a href="/shop">Shop</a>
            <a href="/deals">Bulk Deals</a>
        <a href="/science">Science Hub</a>
        <a href="/coa">COA Reports</a>
            <a href="{{ '/account' if current_member else '/account/login' }}">{{ 'Account' if current_member else 'Login' }}</a>
    </div>
</div></nav>
<div class="container">
    <div class="success-card">
        <div class="success-icon">✓</div>
        <h1>Order received</h1>
        <span class="ord-num">{{ order.order_number }}</span>

        <div class="ord-card">
            <h6>Items</h6>
            {% for it in order.items %}
            <div class="ord-line"><span>{{ it.product_name }} · {{ it.variant_label }} <span style="color:#666;">× {{ it.quantity }}</span></span><span>€{{ "%.2f"|format(it.line_total_eur) }}</span></div>
            {% endfor %}
            <div class="ord-line" style="margin-top:0.5rem;"><span>Subtotal</span><span>€{{ "%.2f"|format(order.subtotal_eur) }}</span></div>
            {% if order.discount_eur > 0 %}<div class="ord-line"><span style="color:#FF9000;">Promo · {{ order.promo_code }}</span><span style="color:#FF9000;">−€{{ "%.2f"|format(order.discount_eur) }}</span></div>{% endif %}
            <div class="ord-line"><span>Shipping</span><span>{% if order.shipping_eur == 0 %}FREE{% else %}€{{ "%.2f"|format(order.shipping_eur) }}{% endif %}</span></div>
            <div class="ord-line" style="color:#888;font-size:0.78rem;"><span>VAT incl.</span><span>€{{ "%.2f"|format(order.vat_eur) }}</span></div>
            <div class="ord-line total"><span>Total paid</span><span>€{{ "%.2f"|format(order.total_eur) }}</span></div>
        </div>

        <div class="ord-card">
            <h6>Shipping to</h6>
            <div style="font-size:0.85rem;line-height:1.6;color:#E0E0E0;">
                <strong>{{ order.customer.full_name }}</strong><br>
                {{ order.customer.address_line1 }}<br>
                {% if order.customer.address_line2 %}{{ order.customer.address_line2 }}<br>{% endif %}
                {{ order.customer.city }}, {{ order.customer.postal_code }}<br>
                {{ order.customer.country }}<br>
                <span style="color:#888;">{{ order.customer.email }}</span>
            </div>
        </div>

        <div class="next-steps">
            <p><strong>What happens next:</strong></p>
            <p>1️⃣ We submit your order to our supplier within 24h.<br>
            2️⃣ You'll receive a tracking number by email once shipped.<br>
            3️⃣ Typical EU delivery: 5–10 business days.</p>
            <p style="color:#888;font-size:0.78rem;">Save this order number for any support enquiries. A confirmation email is on its way to {{ order.customer.email }}.</p>
        </div>

        <p style="text-align:center;margin-top:1.5rem;"><a href="/" class="btn-cont">Continue Shopping</a></p>
    </div>
</div>
</body>
</html>
"""

# ----------------------------------------------------------------------
# Admin dashboard (basic auth via shared password in session)
# ----------------------------------------------------------------------
def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get('is_admin'):
            return redirect(url_for('admin_login'))
        return f(*a, **kw)
    return wrapper

ADMIN_LOGIN_HTML = """
<!DOCTYPE html><html><head><title>Admin · PepHub</title>
<style>body{background:#141414;color:#F5F5F5;font-family:'Inter',sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}
.box{background:#242424;border:1px solid #2D2D2D;border-radius:1rem;padding:2rem;width:340px;}
h1{font-size:1.4rem;font-weight:800;color:#fff;margin:0 0 0.3rem;}
h1 .h{background:#FF9000;color:#000;padding:0 0.3em;border-radius:6px;}
input{width:100%;background:#0F0F0F;border:1px solid #2D2D2D;border-radius:6px;padding:0.7rem;color:#fff;margin-bottom:1rem;}
button{width:100%;background:#FF9000;color:#000;border:none;border-radius:6px;padding:0.7rem;font-weight:800;letter-spacing:0.04em;text-transform:uppercase;cursor:pointer;}
.err{color:#E57373;font-size:0.85rem;margin-bottom:1rem;}
.hint{color:#666;font-size:0.72rem;text-align:center;margin-top:1rem;}</style>
</head><body><div class="box"><h1>Pep<span class="h">Hub</span> Admin</h1><p style="color:#888;font-size:0.85rem;margin-bottom:1.25rem;">Operations dashboard</p>
{% if err %}<div class="err">⚠ {{ err }}</div>{% endif %}
<form method="POST"><input type="password" name="password" placeholder="Admin password" autofocus required><button>Sign in</button></form>
<div class="hint">Set <code>ADMIN_PASSWORD</code> env var in production.</div></div></body></html>
"""

ADMIN_HTML = """
<!DOCTYPE html><html><head><title>Orders · PepHub Admin</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>body{background:#141414;color:#F5F5F5;font-family:'Inter',sans-serif;margin:0;}
.navbar{background:#000;border-bottom:1px solid #2D2D2D;padding:0.85rem 1.5rem;display:flex;justify-content:space-between;align-items:center;}
.navbar a.brand{font-weight:800;color:#fff;font-size:1.4rem;text-decoration:none;letter-spacing:-0.5px;} .navbar a.brand .h{background:#FF9000;color:#000;border-radius:6px;padding:0 0.3em;}
.navbar .logout{color:#999;font-size:0.85rem;text-decoration:none;} .navbar .logout:hover{color:#FF9000;}
.kpi-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1rem;padding:1.5rem;}
.kpi{background:#242424;border:1px solid #2D2D2D;border-radius:0.85rem;padding:1rem 1.2rem;}
.kpi .label{font-size:0.7rem;font-weight:800;color:#FF9000;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:0.3rem;}
.kpi .val{font-size:1.5rem;font-weight:900;color:#fff;}
.kpi .sub{font-size:0.72rem;color:#888;margin-top:0.2rem;}
.section{padding:0 1.5rem 2rem;}
h2{font-size:1.1rem;font-weight:800;color:#fff;margin-bottom:1rem;}
table{width:100%;background:#242424;border-collapse:collapse;border:1px solid #2D2D2D;border-radius:0.7rem;overflow:hidden;font-size:0.85rem;}
th{background:#1A1A1A;color:#FF9000;font-size:0.7rem;text-transform:uppercase;letter-spacing:0.05em;padding:0.7rem 0.85rem;text-align:left;border-bottom:1px solid #2D2D2D;}
td{padding:0.75rem 0.85rem;border-bottom:1px solid #2D2D2D;color:#E0E0E0;vertical-align:top;}
tr:last-child td{border-bottom:none;}
tr:hover td{background:#1F1F1F;}
.status{display:inline-block;border-radius:4px;padding:0.15rem 0.5rem;font-size:0.7rem;font-weight:800;text-transform:uppercase;letter-spacing:0.04em;}
.status-PENDING{background:rgba(255,255,255,0.07);color:#aaa;}
.status-AWAITING_PAYMENT{background:rgba(255,144,0,0.12);color:#FF9000;}
.status-PAID{background:rgba(46,125,50,0.18);color:#81C784;}
.status-SUBMITTED_TO_SUPPLIER{background:rgba(33,150,243,0.18);color:#64B5F6;}
.status-SHIPPED{background:rgba(156,39,176,0.18);color:#CE93D8;}
.status-DELIVERED{background:rgba(76,175,80,0.25);color:#A5D6A7;}
.status-CLOSED{background:rgba(255,255,255,0.04);color:#666;}
.status-REFUNDED, .status-CANCELLED, .status-FAILED{background:rgba(198,40,40,0.18);color:#E57373;}
.mono{font-family:'Courier New',monospace;font-size:0.82rem;color:#FF9000;font-weight:700;}
.muted{color:#888;font-size:0.78rem;}
.actions form{display:inline-block;margin-right:0.4rem;}
.actions button{background:#FF9000;color:#000;border:none;border-radius:4px;padding:0.25rem 0.6rem;font-size:0.7rem;font-weight:800;letter-spacing:0.04em;text-transform:uppercase;cursor:pointer;}
.actions button.ghost{background:#2D2D2D;color:#ccc;}
.empty{padding:2rem;text-align:center;color:#666;}
</style></head>
<body>
<div class="navbar">
    <a href="/admin" class="brand">Pep<span class="h">Hub</span> · Admin</a>
    <div style="display:flex;gap:1.25rem;align-items:center;">
        <a href="/admin/subscriptions" class="logout">🔁 Subscriptions</a>
        <a href="/admin/science" class="logout">🔬 Science Hub</a>
        <a href="/admin/logout" class="logout">Sign out</a>
    </div>
</div>

<div class="kpi-row">
    <div class="kpi"><div class="label">Orders (all-time)</div><div class="val">{{ kpis.total_orders }}</div><div class="sub">{{ kpis.paid_orders }} paid · {{ kpis.shipped_orders }} shipped</div></div>
    <div class="kpi"><div class="label">Revenue</div><div class="val">€{{ "%.2f"|format(kpis.revenue) }}</div><div class="sub">across paid + shipped</div></div>
    <div class="kpi"><div class="label">Wholesale (cost)</div><div class="val">€{{ "%.2f"|format(kpis.wholesale) }}</div><div class="sub">payable to supplier</div></div>
    <div class="kpi"><div class="label">Gross margin</div><div class="val">€{{ "%.2f"|format(kpis.margin) }}</div><div class="sub">{{ kpis.margin_pct }}% of net revenue</div></div>
    <div class="kpi"><div class="label">Open · needs action</div><div class="val">{{ kpis.open_orders }}</div><div class="sub">PAID — to submit · SHIPPED — awaiting tracking</div></div>
</div>

<div class="section">
    <h2>All orders</h2>
    {% if orders %}
    <table>
        <thead><tr><th>Order #</th><th>Date</th><th>Customer</th><th>Items</th><th>Total</th><th>Margin</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>
        {% for o in orders %}
        <tr>
            <td><span class="mono">{{ o.order_number }}</span></td>
            <td class="muted">{{ o.created_at.strftime('%Y-%m-%d %H:%M') }}</td>
            <td>{{ o.customer.full_name or '—' }}<br><span class="muted">{{ o.customer.email }}</span><br><span class="muted">{{ o.customer.city }}, {{ o.customer.country }}</span></td>
            <td>{% for it in o.items %}<div style="font-size:0.78rem;">{{ it.product_name }} <span class="muted">· {{ it.variant_label }} × {{ it.quantity }}</span></div>{% endfor %}</td>
            <td>€{{ "%.2f"|format(o.total_eur) }}{% if o.promo_code %}<br><span class="muted">{{ o.promo_code }}</span>{% endif %}</td>
            <td>€{{ "%.2f"|format(o.margin_eur) }}<br><span class="muted">cost €{{ "%.2f"|format(o.wholesale_cost_eur) }}</span></td>
            <td><span class="status status-{{ o.status }}">{{ o.status.replace('_',' ') }}</span>
                {% if o.tracking_number %}<br><span class="muted" style="font-size:0.7rem;">📦 {{ o.tracking_number }}</span>{% endif %}
            </td>
            <td class="actions">
                {% if o.status == 'PAID' %}<form method="POST" action="/admin/order/{{ o.order_number }}/submit"><button title="Email supplier">→ Submit</button></form>{% endif %}
                {% if o.status == 'SUBMITTED_TO_SUPPLIER' %}<form method="POST" action="/admin/order/{{ o.order_number }}/ship"><button title="Mark shipped"><span>📦 Ship</span></button></form>{% endif %}
                <a href="/admin/order/{{ o.order_number }}" style="color:#888;font-size:0.75rem;text-decoration:none;">View</a>
            </td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    {% else %}
    <div class="empty">No orders yet. Place a test order from the storefront to populate this view.</div>
    {% endif %}
</div>
</body></html>
"""

ADMIN_SUBS_HTML = """
<!DOCTYPE html><html><head><title>Subscriptions · PepHub Admin</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>body{background:#141414;color:#F5F5F5;font-family:'Inter',sans-serif;margin:0;}
.navbar{background:#000;border-bottom:1px solid #2D2D2D;padding:0.85rem 1.5rem;display:flex;justify-content:space-between;align-items:center;}
.navbar a.brand{font-weight:800;color:#fff;font-size:1.4rem;text-decoration:none;} .navbar a.brand .h{background:#FF9000;color:#000;border-radius:6px;padding:0 0.3em;}
.navbar a.lnk{color:#999;font-size:0.85rem;text-decoration:none;margin-left:1.25rem;} .navbar a.lnk:hover{color:#FF9000;}
.kpi-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1rem;padding:1.5rem;}
.kpi{background:#242424;border:1px solid #2D2D2D;border-radius:0.85rem;padding:1rem 1.2rem;}
.kpi .label{font-size:0.7rem;font-weight:800;color:#FF9000;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:0.3rem;}
.kpi .val{font-size:1.5rem;font-weight:900;color:#fff;}
.section{padding:0 1.5rem 2rem;}
h2{font-size:1.1rem;font-weight:800;color:#fff;margin-bottom:1rem;}
table{width:100%;background:#242424;border-collapse:collapse;border:1px solid #2D2D2D;border-radius:0.7rem;overflow:hidden;font-size:0.85rem;}
th{background:#1A1A1A;color:#FF9000;font-size:0.7rem;text-transform:uppercase;letter-spacing:0.05em;padding:0.7rem 0.85rem;text-align:left;border-bottom:1px solid #2D2D2D;}
td{padding:0.75rem 0.85rem;border-bottom:1px solid #2D2D2D;color:#E0E0E0;vertical-align:top;}
tr:last-child td{border-bottom:none;} tr:hover td{background:#1F1F1F;}
.status{display:inline-block;border-radius:4px;padding:0.15rem 0.5rem;font-size:0.7rem;font-weight:800;text-transform:uppercase;}
.status-ACTIVE{background:rgba(46,125,50,0.2);color:#81C784;}
.status-CANCELLED{background:rgba(198,40,40,0.18);color:#E57373;}
.muted{color:#888;font-size:0.78rem;} .lock{color:#E0A64d;font-size:0.72rem;}
.empty{padding:2rem;text-align:center;color:#666;}
</style></head><body>
<div class="navbar">
    <a href="/admin" class="brand">Pep<span class="h">Hub</span> · Admin</a>
    <div><a href="/admin" class="lnk">← Orders</a><a href="/admin/logout" class="lnk">Sign out</a></div>
</div>
<div class="kpi-row">
    <div class="kpi"><div class="label">Active subscriptions</div><div class="val">{{ active }}</div></div>
    <div class="kpi"><div class="label">Recurring revenue / mo</div><div class="val">€{{ "%.2f"|format(mrr) }}</div></div>
    <div class="kpi"><div class="label">Total (all-time)</div><div class="val">{{ subs|length }}</div></div>
</div>
<div class="section">
    <h2>Subscription plans</h2>
    {% if subs %}
    <table>
        <thead><tr><th>Customer</th><th>Product</th><th>Qty</th><th>Price/mo</th><th>Started</th><th>Next renewal</th><th>Min term</th><th>Status</th></tr></thead>
        <tbody>
        {% for s in subs %}
        <tr>
            <td>{{ s.customer.full_name or '—' }}<br><span class="muted">{{ s.customer.email }}</span></td>
            <td><strong style="color:#fff;">{{ s.product_name }}</strong><br><span class="muted">{{ s.variant_label }}</span></td>
            <td>{{ s.quantity }}</td>
            <td>€{{ "%.2f"|format(s.unit_price_eur or 0) }}</td>
            <td class="muted">{{ s.created_at.strftime('%Y-%m-%d') }}</td>
            <td class="muted">{{ s.next_renewal_at.strftime('%Y-%m-%d') if s.next_renewal_at else '—' }}</td>
            <td>{% if s.commitment_end %}{{ s.min_term_months }} mo<br><span class="lock">{% if now >= s.commitment_end %}unlocked{% else %}until {{ s.commitment_end.strftime('%d %b %Y') }}{% endif %}</span>{% else %}—{% endif %}</td>
            <td><span class="status status-{{ s.status }}">{{ s.status }}</span>{% if s.cancelled_at %}<br><span class="muted">{{ s.cancelled_at.strftime('%Y-%m-%d') }}</span>{% endif %}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    {% else %}<div class="empty">No subscriptions yet.</div>{% endif %}
</div>
</body></html>
"""

ADMIN_ORDER_HTML = """
<!DOCTYPE html><html><head><title>Order {{ order.order_number }} · PepHub Admin</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
body{background:#141414;color:#F5F5F5;font-family:'Inter','Segoe UI',system-ui,sans-serif;margin:0;}
.navbar{background:#000;border-bottom:1px solid #2D2D2D;padding:0.85rem 1.5rem;display:flex;justify-content:space-between;align-items:center;}
.navbar a.brand{font-weight:800;color:#fff;font-size:1.4rem;text-decoration:none;letter-spacing:-0.5px;}
.navbar a.brand .h{background:#FF9000;color:#000;border-radius:6px;padding:0 0.3em;}
.navbar a{color:#999;font-size:0.85rem;text-decoration:none;} .navbar a:hover{color:#FF9000;}
.container-narrow{max-width:980px;margin:0 auto;padding:1.5rem;}
h1{font-size:1.4rem;font-weight:800;color:#fff;margin:0 0 0.3rem;}
.ord-num{font-family:'Courier New',monospace;font-size:1.05rem;color:#FF9000;font-weight:800;letter-spacing:0.04em;}
.status{display:inline-block;border-radius:4px;padding:0.2rem 0.55rem;font-size:0.72rem;font-weight:800;text-transform:uppercase;letter-spacing:0.04em;}
.status-PENDING{background:rgba(255,255,255,0.07);color:#aaa;}
.status-AWAITING_PAYMENT{background:rgba(255,144,0,0.12);color:#FF9000;}
.status-PAID{background:rgba(46,125,50,0.18);color:#81C784;}
.status-SUBMITTED_TO_SUPPLIER{background:rgba(33,150,243,0.18);color:#64B5F6;}
.status-SHIPPED{background:rgba(156,39,176,0.18);color:#CE93D8;}
.status-DELIVERED{background:rgba(76,175,80,0.25);color:#A5D6A7;}
.status-CLOSED{background:rgba(255,255,255,0.04);color:#666;}

.grid{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;margin-top:1.5rem;}
@media(max-width:880px){.grid{grid-template-columns:1fr;}}
.card{background:#242424;border:1px solid #2D2D2D;border-radius:0.85rem;padding:1.4rem;}
.card h3{font-size:0.78rem;font-weight:800;color:#FF9000;text-transform:uppercase;letter-spacing:0.06em;margin:0 0 0.85rem;}
.row-flex{display:flex;justify-content:space-between;font-size:0.85rem;padding:0.35rem 0;color:#BFBFBF;border-bottom:1px dashed #2D2D2D;}
.row-flex:last-child{border-bottom:none;}
.row-flex strong{color:#fff;}
.row-flex.total{font-weight:800;color:#fff;font-size:1.05rem;padding-top:0.65rem;}
.row-flex.total span:last-child{color:#FF9000;}
.address{font-size:0.9rem;line-height:1.65;color:#E0E0E0;background:#0F0F0F;border:1px solid #2D2D2D;border-radius:6px;padding:0.85rem 1rem;}
.address strong{color:#fff;}
.muted{color:#888;font-size:0.78rem;}

.email-card{background:#1A1A1A;border:1px solid #2D2D2D;border-radius:0.85rem;padding:1.5rem;margin-top:1.5rem;}
.email-card .head{display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;flex-wrap:wrap;gap:0.5rem;}
.email-card h3{margin:0;font-size:0.85rem;font-weight:800;color:#FF9000;text-transform:uppercase;letter-spacing:0.06em;}
.email-meta{font-size:0.78rem;color:#999;margin-bottom:0.85rem;}
.email-meta strong{color:#E0E0E0;}
.email-body{background:#0A0A0A;border:1px solid #2D2D2D;border-radius:6px;padding:1rem;font-family:'Courier New',monospace;font-size:0.82rem;color:#E0E0E0;white-space:pre-wrap;line-height:1.55;max-height:340px;overflow-y:auto;}
.btn-row{display:flex;gap:0.5rem;flex-wrap:wrap;margin-top:0.85rem;}
.btn{background:#FF9000;color:#000;border:none;border-radius:6px;padding:0.55rem 1.1rem;font-size:0.78rem;font-weight:800;letter-spacing:0.04em;text-transform:uppercase;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:0.35rem;}
.btn:hover{background:#fff;color:#000;}
.btn.ghost{background:#2D2D2D;color:#E0E0E0;}
.btn.ghost:hover{background:#3D3D3D;color:#fff;}
.actions-row{margin-top:1.5rem;display:flex;gap:0.5rem;flex-wrap:wrap;align-items:center;}
.actions-row form{display:inline-block;margin:0;}
.actions-row input{background:#0F0F0F;border:1px solid #2D2D2D;border-radius:6px;padding:0.4rem 0.7rem;color:#fff;font-size:0.82rem;width:160px;}
.actions-row input:focus{outline:none;border-color:#FF9000;}
.kpi-strip{display:flex;flex-wrap:wrap;gap:1.5rem;font-size:0.78rem;color:#999;margin-top:0.6rem;}
.kpi-strip span strong{color:#FF9000;font-weight:800;font-size:0.95rem;display:block;}
</style></head>
<body>
<div class="navbar">
    <a href="/admin" class="brand">Pep<span class="h">Hub</span> · Admin</a>
    <div><a href="/admin" style="margin-right:1rem;">← All orders</a><a href="/admin/logout">Sign out</a></div>
</div>

<div class="container-narrow">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:0.5rem;">
        <div>
            <h1>Order <span class="ord-num">{{ order.order_number }}</span></h1>
            <div class="muted">Placed {{ order.created_at.strftime('%Y-%m-%d at %H:%M UTC') }}</div>
        </div>
        <span class="status status-{{ order.status }}">{{ order.status.replace('_',' ') }}</span>
    </div>

    <div class="kpi-strip">
        <span><strong>€{{ "%.2f"|format(order.total_eur) }}</strong>Customer paid</span>
        <span><strong>€{{ "%.2f"|format(order.wholesale_cost_eur) }}</strong>Wholesale cost</span>
        <span><strong>€{{ "%.2f"|format(order.margin_eur) }}</strong>Margin</span>
        <span><strong>€{{ "%.2f"|format(order.vat_eur) }}</strong>VAT (incl.)</span>
    </div>

    <div class="actions-row">
        {% if order.status == 'PAID' %}
        <form method="POST" action="/admin/order/{{ order.order_number }}/submit"><button class="btn">→ Mark Submitted to Supplier</button></form>
        {% endif %}
        {% if order.status == 'SUBMITTED_TO_SUPPLIER' %}
        <form method="POST" action="/admin/order/{{ order.order_number }}/ship" style="display:flex;gap:0.5rem;flex-wrap:wrap;">
            <input type="text" name="tracking_number" placeholder="Tracking #" required>
            <input type="text" name="tracking_carrier" placeholder="Carrier (e.g. PostNL)">
            <button class="btn">📦 Mark Shipped</button>
        </form>
        {% endif %}
        {% if order.tracking_number %}
        <span class="muted" style="margin-left:0.5rem;">📦 {{ order.tracking_number }}{% if order.tracking_carrier %} · {{ order.tracking_carrier }}{% endif %}</span>
        {% endif %}
    </div>

    <div class="grid">
        <div class="card">
            <h3>🧾 Order Summary</h3>
            {% for it in order.items %}
            <div class="row-flex">
                <span><strong>{{ it.product_name }}</strong><br><span class="muted">{{ it.variant_label }} · {{ it.variant_sku }} × {{ it.quantity }}</span></span>
                <span>€{{ "%.2f"|format(it.line_total_eur) }}</span>
            </div>
            {% endfor %}
            <div class="row-flex" style="margin-top:0.4rem;"><span>Subtotal</span><span>€{{ "%.2f"|format(order.subtotal_eur) }}</span></div>
            {% if order.discount_eur > 0 %}<div class="row-flex" style="color:#FF9000;"><span>Promo · {{ order.promo_code }}</span><span>−€{{ "%.2f"|format(order.discount_eur) }}</span></div>{% endif %}
            <div class="row-flex"><span>Shipping</span><span>{% if order.shipping_eur == 0 %}FREE{% else %}€{{ "%.2f"|format(order.shipping_eur) }}{% endif %}</span></div>
            <div class="row-flex" style="color:#888;font-size:0.78rem;"><span>VAT incl. (21%)</span><span>€{{ "%.2f"|format(order.vat_eur) }}</span></div>
            <div class="row-flex total"><span>Total paid</span><span>€{{ "%.2f"|format(order.total_eur) }}</span></div>
        </div>

        <div class="card">
            <h3>📍 Ship-to Address</h3>
            <div class="address" id="ship-address">
<strong>{{ order.customer.full_name }}</strong>
{{ order.customer.address_line1 }}{% if order.customer.address_line2 %}
{{ order.customer.address_line2 }}{% endif %}
{{ order.customer.city }}, {{ order.customer.postal_code }}
{{ order.customer.country }}

Phone: {{ order.customer.phone or '—' }}
Email: {{ order.customer.email }}
            </div>
            <div class="btn-row">
                <button class="btn ghost" onclick="copyText(document.getElementById('ship-address').innerText, this)">📋 Copy Address</button>
            </div>
        </div>
    </div>

    <div class="email-card">
        <div class="head">
            <h3>✉️ Supplier Email — ready to send</h3>
            <span class="muted">Auto-generated from this order</span>
        </div>
        <div class="email-meta"><strong>To:</strong> {{ supplier_email }} &nbsp;&nbsp; <strong>Subject:</strong> {{ supplier_subject }}</div>
        <div class="email-body" id="supplier-body">{{ supplier_body }}</div>
        <div class="btn-row">
            <a class="btn" href="mailto:{{ supplier_email }}?subject={{ supplier_subject|urlencode }}&body={{ supplier_body|urlencode }}">
                ✉️ Open in Email App
            </a>
            <button class="btn ghost" onclick="copyText(document.getElementById('supplier-body').innerText, this)">📋 Copy Body</button>
            <button class="btn ghost" onclick="copyText('{{ supplier_email }}', this)">📋 Copy Supplier Address</button>
        </div>
    </div>

    <p class="muted" style="margin-top:1rem;font-size:0.75rem;">
        💡 <strong>Workflow:</strong> Click <em>Open in Email App</em> to send the order to your supplier directly from your email client, then come back here and click <em>Mark Submitted to Supplier</em>. When the supplier confirms shipping and gives you a tracking number, paste it into the field above and click <em>Mark Shipped</em>.
    </p>
</div>

<div id="toast" style="position:fixed;bottom:30px;left:50%;transform:translateX(-50%);background:#FF9000;color:#000;padding:0.65rem 1.25rem;border-radius:6px;font-weight:800;font-size:0.85rem;box-shadow:0 8px 24px rgba(255,144,0,0.4);opacity:0;transition:opacity 0.3s;pointer-events:none;z-index:9999;"></div>

<script>
function copyText(text, btn) {
    navigator.clipboard.writeText(text).then(() => {
        const t = document.getElementById('toast');
        t.textContent = '✓ Copied to clipboard';
        t.style.opacity = '1';
        clearTimeout(window._t);
        window._t = setTimeout(() => t.style.opacity = '0', 1800);
    });
}
</script>
</body></html>
"""

# ----------------------------------------------------------------------
# Flask routes
# ----------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html', products=products)

# Rich per-product content for detail pages
product_details = {
    1: {  # BPC-157
        "subtitle": "Body Protection Compound — Systemic Tissue Repair",
        "eyebrow": "Recovery & Repair",
        "icon": "bi-bandaid",
        "tagline": "The cornerstone recovery peptide — accelerated healing of tendon, ligament, muscle and gut.",
        "coa_slug": "bpc-157",
        "half_life": "4–6 hours",
        "dose_range": "250–500 mcg/day (research context)",
        "form": "Lyophilised 15-AA peptide vial",
        "storage": "Refrigerate (2–8 °C) after reconstitution. Use within 28 days.",
        "aa_count": "15 amino acids",
        "cas": "137525-51-0",
        "purity": "≥ 99.5% (HPLC verified — full COA available)",
        "chips": [
            ("Tendon & Ligament Repair", True), ("Gut Healing", False),
            ("Angiogenesis", False), ("Anti-Inflammatory", False), ("Neuroprotection", False),
        ],
        "description": """
            <p>BPC-157 (Body Protection Compound-157) is a synthetic 15-amino-acid peptide derived from a protective protein found in human gastric juice. It is one of the most extensively studied repair peptides in preclinical research, with documented effects on tendon, ligament, muscle and gastrointestinal healing.</p>
            <p>In research models, BPC-157 upregulates growth-factor receptors (VEGFR2, EGF-R) and activates the FAK–paxillin signalling pathway, driving rapid cellular repair and angiogenesis at the injury site. It also modulates the nitric-oxide (NO) system and has shown protective effects on the gut lining, blood vessels, and — in some models — the nervous system.</p>
            <p>Because it acts locally and robustly at the site of injury, BPC-157 is frequently paired with the systemically-distributed TB-500 for full-spectrum tissue-repair coverage.</p>
        """,
        "research_notes": [
            ("Mechanism", "Upregulates VEGFR2 and EGF-R; activates FAK–paxillin signalling; modulates nitric-oxide pathway — driving angiogenesis and tendon/GI repair."),
            ("Preclinical Evidence", "Accelerated tendon-to-bone and ligament healing, reduced GI lesions, and vascular-protective effects demonstrated across multiple rodent models."),
            ("Synergy", "Acts locally at the injury focus; complements the systemic action of TB-500 (see the BPC-157 & TB-500 blend and the GLOW / KLOW stacks)."),
            ("Storage Note", "Lyophilised form stable at −20 °C for up to 24 months. After reconstitution with bacteriostatic water, refrigerate and use within 28 days."),
        ],
    },
    2: {  # TB-500
        "subtitle": "Thymosin Beta-4 Fragment — Systemic Regeneration",
        "eyebrow": "Recovery & Repair",
        "icon": "bi-arrow-repeat",
        "tagline": "System-wide repair — stem-cell mobilisation, vascular regeneration and inflammation resolution.",
        "coa_slug": "tb-500",
        "half_life": "6–7 days",
        "dose_range": "2.5–5 mg/week (research context)",
        "form": "Lyophilised 43-AA peptide vial",
        "storage": "Refrigerate (2–8 °C) after reconstitution. Use within 28 days.",
        "aa_count": "43 amino acids",
        "cas": "77591-33-4",
        "purity": "≥ 99.4% (HPLC verified — full COA available)",
        "chips": [
            ("Systemic Recovery", True), ("Stem Cell Mobilisation", False),
            ("Vascular Regeneration", False), ("Flexibility", False), ("Anti-Inflammatory", False),
        ],
        "description": """
            <p>TB-500 is a synthetic analogue of Thymosin Beta-4, a naturally occurring 43-amino-acid regenerative peptide. Its defining feature is the conserved actin-binding LKKTET motif, through which it binds G-actin to promote cell migration, endothelial tube formation and tissue regeneration throughout the body.</p>
            <p>Unlike locally-acting repair peptides, TB-500 distributes systemically — making it well suited to whole-body recovery, connective-tissue flexibility, and vascular regeneration. In research models it has demonstrated accelerated wound closure, muscle and cardiac tissue repair, and resolution of inflammation.</p>
            <p>TB-500 is commonly combined with BPC-157, which acts at the local injury site, to provide complementary local + systemic repair coverage.</p>
        """,
        "research_notes": [
            ("Mechanism", "Binds G-actin via the conserved LKKTET / SDKP motif, promoting cell migration, angiogenesis and stem-cell mobilisation system-wide."),
            ("Preclinical Evidence", "Accelerated dermal wound healing, muscle and cardiac repair, and improved connective-tissue flexibility in rodent injury models."),
            ("Synergy", "Systemic action complements BPC-157's local repair — the basis of the BPC-157 & TB-500 blend and the GLOW / KLOW stacks."),
            ("Storage Note", "Lyophilised form stable at −20 °C for up to 24 months. After reconstitution, refrigerate and use within 28 days."),
        ],
    },
    6: {  # MOTS-c
        "subtitle": "Mitochondrial-Derived Metabolic Peptide",
        "eyebrow": "Metabolic & Longevity",
        "icon": "bi-lightning-charge",
        "tagline": "The exercise-mimetic peptide — AMPK activation, metabolic flexibility and cellular energy.",
        "coa_slug": "mots-c",
        "half_life": "2–3 hours",
        "dose_range": "5–10 mg/week (research context)",
        "form": "Lyophilised 16-AA peptide vial",
        "storage": "Refrigerate (2–8 °C) after reconstitution. Use within 28 days.",
        "aa_count": "16 amino acids",
        "cas": "1627580-64-6",
        "purity": "≥ 99.3% (HPLC verified — full COA available)",
        "chips": [
            ("Metabolic Flexibility", True), ("AMPK Activation", False),
            ("Insulin Sensitivity", False), ("Cellular Energy (ATP)", False), ("Exercise Capacity", False),
        ],
        "description": """
            <p>MOTS-c (Mitochondrial ORF of the twelve-S rRNA type-c) is a 16-amino-acid mitochondrial-derived peptide encoded within the mitochondrial genome. It functions as a metabolic regulator that translocates to the nucleus under metabolic stress to influence gene expression — earning it the description of an "exercise-mimetic" peptide.</p>
            <p>Its principal mechanism is activation of the AMPK pathway, the cell's master energy sensor. Through AMPK, MOTS-c enhances glucose uptake, promotes fatty-acid oxidation, improves insulin sensitivity, and increases metabolic flexibility — the ability to switch efficiently between fuel sources.</p>
            <p>Preclinical research links MOTS-c to improved exercise capacity, protection against diet-induced insulin resistance and obesity, and markers associated with healthy ageing and mitochondrial function.</p>
        """,
        "research_notes": [
            ("Mechanism", "Activates the AMPK energy-sensing pathway; regulates the folate–methionine cycle and translocates to the nucleus under metabolic stress to modulate gene expression."),
            ("Preclinical Evidence", "Improved insulin sensitivity, enhanced exercise capacity, and protection against diet-induced obesity in rodent models."),
            ("Longevity Interest", "Mitochondrial-derived peptides such as MOTS-c are studied as markers and modulators of metabolic health and biological ageing."),
            ("Storage Note", "Lyophilised form stable at −20 °C. After reconstitution with bacteriostatic water, refrigerate and use within 28 days."),
        ],
    },
    22: {  # KLOW Stack
        "subtitle": "Four-Peptide Repair & Anti-Inflammatory Stack",
        "eyebrow": "Premium Synergistic Protocol",
        "icon": "bi-gem",
        "tagline": "GLOW, elevated — KPV's anti-inflammatory action added to the signature regeneration stack.",
        "coa_slug": "klow-stack",
        "half_life": "Combined: 4–6h (BPC) · 6–7d (TB) · 30–60min (GHK-Cu) · 2–3h (KPV)",
        "dose_range": "Research protocol: 1 vial reconstituted per cycle",
        "form": "80 mg lyophilised four-peptide vial",
        "storage": "Store at −20 °C, protected from light. After reconstitution: 2–8 °C, use within 28 days.",
        "aa_count": "KPV: 3 AA · BPC-157: 15 AA · GHK-Cu: 3 AA · TB-500: 43 AA",
        "cas": "Multiple — see individual COAs",
        "purity": "Each component ≥ 99% HPLC verified",
        "chips": [
            ("Anti-Inflammatory", True), ("Tissue Repair", False),
            ("Skin Regeneration", False), ("Gut Healing", False), ("Angiogenesis", False),
        ],
        "description": """
            <p>The KLOW Stack is the complete PepHub repair protocol — the signature GLOW regeneration blend with the potent anti-inflammatory tripeptide <strong>KPV</strong> added. Each vial contains 10 mg KPV, 10 mg BPC-157, 50 mg GHK-Cu and 10 mg TB-500 (80 mg total peptide).</p>
            <p><strong>KPV</strong> (Lys-Pro-Val) is the C-terminal tripeptide fragment of α-MSH. It exerts strong anti-inflammatory activity by down-regulating NF-κB and pro-inflammatory cytokine signalling, and has been studied particularly for gut and skin inflammation. <strong>BPC-157</strong> drives local tissue repair, <strong>TB-500</strong> provides systemic vascular regeneration, and <strong>GHK-Cu</strong> contributes collagen synthesis and dermal renewal.</p>
            <p>Together the four peptides address inflammation, local repair, systemic regeneration, and extracellular-matrix support in a single-vial protocol — the most comprehensive stack in the PepHub range.</p>
        """,
        "research_notes": [
            ("Synergy", "KPV (anti-inflammatory) + BPC-157 (local repair) + TB-500 (systemic regeneration) + GHK-Cu (ECM / collagen). Four complementary layers of the repair cascade."),
            ("KPV Mechanism", "C-terminal α-MSH fragment (Lys-Pro-Val); down-regulates NF-κB and pro-inflammatory cytokines — studied for gut and skin inflammation."),
            ("Pre-blending Advantage", "One reconstitution, one injection volume — a four-peptide protocol simplified into a single-vial workflow."),
            ("Storage", "Lyophilised vial stable at −20 °C for up to 24 months. After bacteriostatic-water reconstitution, refrigerate and use within 28 days."),
        ],
    },
    9: {  # BPC-157 & TB-500
        "subtitle": "Dual-Peptide Tissue Recovery Blend",
        "eyebrow": "Recovery & Repair",
        "icon": "bi-bandaid",
        "tagline": "The definitive recovery stack — local and systemic repair, working in concert.",
        "coa_slug": "bpc157-tb500",
        "half_life": "4–6 h (BPC-157) / 6–7 days (TB-500)",
        "dose_range": "BPC-157: 250–500 mcg/day · TB-500: 2.5–5 mg/week",
        "form": "Lyophilised dual-peptide vial",
        "storage": "Refrigerate (2–8 °C) after reconstitution. Use within 28 days.",
        "aa_count": "BPC-157: 15 AA · TB-500: 43 AA",
        "cas": "BPC-157: 137525-51-0 · TB-500: 77591-33-4",
        "purity": "≥ 99.5% (HPLC verified — full COA available)",
        "chips": [
            ("Tendon & Ligament Repair", True), ("Gut Healing", False),
            ("Systemic Recovery", False), ("Angiogenesis", False), ("Anti-Inflammatory", False),
        ],
        "description": """
            <p>BPC-157 &amp; TB-500 is a precision-formulated dual-peptide blend that combines two of the most extensively studied repair compounds in preclinical research. By pairing a locally-acting repair peptide with a systemically-distributed one, the stack addresses both site-specific injury and the broader inflammatory and regenerative environment.</p>
            <p><strong>BPC-157 (Body Protection Compound-157)</strong> is a synthetic 15-amino acid peptide isolated from human gastric juice. In research models it has demonstrated accelerated healing of tendon, ligament, muscle, and gastrointestinal tissue — upregulating growth factor receptors (VEGFR2, FGFR) and activating the FAK-paxillin signalling pathway to drive rapid cellular repair and angiogenesis at the injury site.</p>
            <p><strong>TB-500 (Thymosin Beta-4 Analogue)</strong> is a 43-amino acid synthetic peptide that binds G-actin via its conserved LKKTET motif. This interaction promotes cell migration, endothelial tube formation, and vascular regeneration body-wide — making TB-500 uniquely suited for systemic distribution throughout connective and vascular tissue rather than acting only at a localised site.</p>
            <p>Together, BPC-157 addresses the injury focus point while TB-500 resolves inflammation and rebuilds supporting vasculature system-wide — a complementary mechanism that neither peptide achieves in isolation.</p>
        """,
        "research_notes": [
            ("Mechanism", "BPC-157 activates VEGFR2, EGF-R, and FAK-paxillin — driving tendon and GI repair. TB-500 binds G-actin via LKKTET motif, promoting stem cell mobilisation and vessel formation."),
            ("Preclinical Evidence", "Demonstrated accelerated tendon-to-bone healing, reduction of GI lesions, and systemic anti-inflammatory effects in multiple rodent models (rats, mice)."),
            ("Synergy", "BPC-157 operates locally at the injury site; TB-500 distributes systemically. The combination provides complementary, full-spectrum tissue repair coverage."),
            ("Storage Note", "Lyophilised form is stable at −20 °C for up to 24 months. After reconstitution with bacteriostatic water, refrigerate and use within 28 days."),
        ],
    },
    5: {  # Retatrutide
        "subtitle": "Triple Agonist Metabolic Research Peptide",
        "eyebrow": "Weight & Metabolic Regulation",
        "icon": "bi-graph-up-arrow",
        "tagline": "GLP-1 · GIP · Glucagon — three receptor systems, one precision compound.",
        "coa_slug": "retatrutide",
        "half_life": "~6 days",
        "dose_range": "1–4 mg/week (research context)",
        "form": "Lyophilised 39-AA modified peptide, C18 fatty-acid conjugated",
        "storage": "Refrigerate (2–8 °C). Avoid repeated freeze-thaw cycles.",
        "aa_count": "39 amino acids (modified analogue with Aib at position 2)",
        "cas": "2381272-77-5",
        "purity": "≥ 99.4% (HPLC verified — full COA available)",
        "chips": [
            ("Metabolic Regulation", True), ("Body Composition", False),
            ("Glucose Control", False), ("Thermogenesis", False), ("Cardiovascular Support", False),
        ],
        "description": """
            <p>Retatrutide is a next-generation synthetic peptide that simultaneously activates three distinct receptor systems: GLP-1R (glucagon-like peptide-1 receptor), GIPR (glucose-dependent insulinotropic polypeptide receptor), and GcgR (glucagon receptor). This unique triple agonism produces a layered metabolic effect that significantly exceeds what single or dual-agonist compounds achieve.</p>
            <p><strong>GLP-1R activation</strong> suppresses appetite, slows gastric emptying, and enhances glucose-dependent insulin secretion. <strong>GIPR activation</strong> further amplifies insulin release and has been linked to reduced fat storage and improved lipid profiles. <strong>GcgR activation</strong> increases hepatic glucose output, elevates basal metabolic rate, and stimulates thermogenesis in brown adipose tissue — creating an energy-expenditure effect that counteracts the metabolic adaptation seen with caloric restriction alone.</p>
            <p>Structurally, Retatrutide incorporates an α-aminoisobutyric acid (Aib) residue at position 2 — a non-natural amino acid modification that confers resistance to DPP-4 enzymatic degradation, enabling the compound's extended ~6-day half-life. A C18 fatty-acid chain conjugated via a γGlu-miniPEG linker at Lys₁₂ provides albumin binding for sustained release, mirroring the design strategy used in established GLP-1 analogues.</p>
            <p>Preclinical research has demonstrated reductions in body weight, visceral and subcutaneous adiposity, fasting glucose, and markers of hepatic steatosis — with a cardiovascular and hepatoprotective profile that distinguishes Retatrutide from first-generation GLP-1 agonists.</p>
        """,
        "research_notes": [
            ("Mechanism", "Simultaneous GLP-1R, GIPR, and GcgR agonism — suppresses appetite, enhances insulin secretion, increases thermogenesis and hepatic energy expenditure."),
            ("Structural Design", "Aib at position 2 prevents DPP-4 degradation. C18 fatty-diacid chain at Lys₁₂ via γGlu-miniPEG linker enables albumin binding and extended half-life (~6 days)."),
            ("Preclinical Evidence", "Demonstrated significant reductions in body weight, visceral fat mass, fasting glucose, and HbA1c in obese rodent and non-human primate models."),
            ("Storage Note", "Refrigerate at 2–8 °C. The fatty-acid conjugation is sensitive to repeated freeze-thaw cycles — avoid. Reconstitute with sterile or bacteriostatic water."),
        ],
    },
    3: {  # GHK-Cu
        "subtitle": "Copper Peptide Complex — Collagen & Skin Regeneration",
        "eyebrow": "Skin, Collagen & Regeneration",
        "icon": "bi-brightness-high",
        "tagline": "A tripeptide copper complex with one of the broadest regenerative profiles in peptide science.",
        "coa_slug": "ghk-cu",
        "half_life": "30–60 minutes",
        "dose_range": "1–2 mg/day (research context)",
        "form": "Lyophilised tripeptide Cu²⁺ complex (characteristic blue-green powder)",
        "storage": "Refrigerate (2–8 °C), protected from light. Use within 28 days of reconstitution.",
        "aa_count": "3 amino acids: Gly-His-Lys · Cu²⁺ complex",
        "cas": "89030-95-5",
        "purity": "≥ 99.8% (HPLC verified — full COA available)",
        "chips": [
            ("Collagen Synthesis", True), ("Skin Rejuvenation", False),
            ("Hair Follicle Activation", False), ("Wound Healing", False), ("Antioxidant", False),
        ],
        "description": """
            <p>GHK-Cu (Glycyl-L-histidyl-L-lysine copper(II) complex) is a naturally occurring tripeptide-copper complex first isolated from human plasma. It is one of the most extensively researched anti-ageing and regenerative peptides in dermatological science, with a documented ability to modulate the expression of over 4,000 human genes — encompassing tissue remodelling, inflammation resolution, and cellular repair.</p>
            <p><strong>Collagen and extracellular matrix support:</strong> GHK-Cu upregulates synthesis of collagen I, III, and V, as well as elastin and dermatan sulphate proteoglycans. It simultaneously inhibits matrix metalloproteinases (MMPs) that degrade collagen, while activating their inhibitors (TIMPs) — effectively shifting the tissue microenvironment towards net collagen deposition and structural integrity restoration.</p>
            <p><strong>Wound healing and skin regeneration:</strong> The copper ion chelated within the complex activates TGF-β signalling and promotes keratinocyte and fibroblast migration, accelerating re-epithelialisation. In published models, GHK-Cu has demonstrated reductions in wound closure time and improvements in dermal thickness and hydration compared to controls.</p>
            <p><strong>Hair follicle activation:</strong> Research demonstrates upregulation of vascular endothelial growth factor (VEGF) and stem cell factor (SCF) in follicle dermal papilla cells, supporting anagen (growth) phase extension and follicle size maintenance.</p>
            <p><strong>Antioxidant and anti-inflammatory properties:</strong> GHK-Cu down-regulates NF-κB — a master regulator of inflammatory gene expression — and modulates free radical generation via its copper-binding coordination chemistry, providing dual antioxidant and anti-inflammatory activity at the cellular level.</p>
        """,
        "research_notes": [
            ("Mechanism", "Activates TGF-β signalling; upregulates collagen I/III/V, elastin, VEGF; inhibits MMP-1/2/9; modulates NF-κB. Documented effects on >4,000 human genes."),
            ("Cu²⁺ Role", "Copper is coordinated via Gly α-NH₂, His imidazole N3, and Lys ε-NH₂. This square-planar chelation enables intracellular copper delivery and free radical modulation."),
            ("Preclinical Evidence", "Accelerated wound re-epithelialisation, increased dermal collagen density, and follicle size maintenance demonstrated in skin and hair follicle model systems."),
            ("Appearance", "Characteristic blue-green lyophilised powder due to Cu²⁺ d–d electronic absorption at 625 nm. Colour intensity is an indicator of complex retention — confirmed by ICP-OES and UV-Vis in each batch COA."),
        ],
    },
    20: {  # Bacteriostatic Water
        "subtitle": "Sterile Reconstitution Solvent",
        "eyebrow": "Reconstitution & Storage",
        "icon": "bi-droplet-fill",
        "tagline": "The essential companion. 0.9% benzyl alcohol-preserved water for safe peptide reconstitution.",
        "coa_slug": "bac-water",
        "half_life": "Indefinite (sealed, stored cool & dark)",
        "dose_range": "0.5–3 ml typical reconstitution volume",
        "form": "Sterile aqueous 0.9% benzyl alcohol solution",
        "storage": "Room temperature, sealed. After first use refrigerate and discard within 28 days.",
        "aa_count": "n/a",
        "cas": "100-51-6 (benzyl alcohol preservative)",
        "purity": "USP-grade, 0.22 µm filtered, endotoxin-tested",
        "chips": [
            ("Lyophilised Peptide Reconstitution", True), ("0.9% Benzyl Alcohol", False),
            ("USP-Grade", False), ("Sterile-Filtered", False), ("Multi-Use Vial", False),
        ],
        "description": """
            <p>Bacteriostatic water is the standard solvent for reconstituting lyophilised research peptides. Unlike sterile water for injection, it contains 0.9% benzyl alcohol — a preservative that inhibits microbial growth and allows the reconstituted peptide solution to be used over multiple doses (typically up to 28 days when refrigerated).</p>
            <p>Each batch is sterile-filtered through a 0.22 µm membrane and endotoxin-tested to meet USP standards. This is the same grade of diluent used in clinical pharmaceutical reconstitution.</p>
            <p><strong>Choose the right size:</strong> 3 ml is the standard single-dose research format. 10 ml is more economical for ongoing studies or higher-volume reconstitution protocols.</p>
        """,
        "research_notes": [
            ("Why bacteriostatic", "0.9% benzyl alcohol preserves the reconstituted peptide solution for up to 28 days. Plain sterile water requires single-dose use only."),
            ("Reconstitution math", "Volume × peptide mg per vial = mg/ml concentration. Most peptides reconstitute to 1–2 mg/ml for accurate sub-mg dosing."),
            ("Storage", "Sealed vial — room temperature. Once punctured, refrigerate and use within 28 days. Discard if cloudy or particulate."),
            ("Compatibility", "Suitable for BPC-157, TB-500, GHK-Cu, Retatrutide, and all standard lyophilised research peptides in the PepHub range."),
        ],
    },
    21: {  # GLOW Stack
        "subtitle": "Signature Tri-Peptide Regeneration Stack",
        "eyebrow": "Premium Synergistic Protocol",
        "icon": "bi-stars",
        "tagline": "The flagship PepHub stack — three of the most studied repair peptides, pre-blended in one vial.",
        "coa_slug": "glow-stack",
        "half_life": "Combined: 4–6h (BPC) · 6–7d (TB) · 30–60min (GHK-Cu)",
        "dose_range": "Research protocol: 1 vial reconstituted per cycle",
        "form": "70 mg lyophilised tri-peptide vial",
        "storage": "Store at −20 °C. After reconstitution: 2–8 °C, use within 28 days.",
        "aa_count": "BPC-157: 15 AA · TB-500: 43 AA · GHK-Cu: 3 AA",
        "cas": "Multiple — see individual COAs",
        "purity": "Each component ≥ 99% HPLC verified",
        "chips": [
            ("Tissue Repair", True), ("Skin Regeneration", False),
            ("Angiogenesis", False), ("Collagen Synthesis", False), ("Anti-Inflammatory", False),
        ],
        "description": """
            <p>The GLOW Stack is the signature PepHub combination — three of the most thoroughly studied regenerative peptides pre-blended in a single lyophilised vial. Each vial contains 10 mg BPC-157, 50 mg GHK-Cu, and 10 mg TB-500 (70 mg total peptide).</p>
            <p><strong>BPC-157</strong> drives accelerated tissue repair at the injury site — tendons, ligaments, gut lining. <strong>TB-500</strong> operates systemically, mobilising stem cells and promoting vascular regeneration body-wide. <strong>GHK-Cu</strong> contributes the dermal and collagen-synthesis layer — upregulating elastin, modulating &gt;4,000 genes involved in tissue remodelling.</p>
            <p>The three peptides operate on complementary mechanisms: local repair, systemic vascular regeneration, and extracellular-matrix support. Pre-blending eliminates the multi-vial reconstitution workflow that would otherwise be required to achieve the same protocol.</p>
        """,
        "research_notes": [
            ("Synergy", "Local repair (BPC-157) + systemic vascular regeneration (TB-500) + ECM/collagen support (GHK-Cu). Each addresses a different layer of the repair cascade."),
            ("Cycling Pattern", "4–8 weeks ON, 2–4 weeks OFF (prevents receptor desensitisation)."),
            ("Pre-blending Advantage", "One reconstitution, one injection volume — simplifies a 3-peptide protocol into a single-vial workflow."),
            ("Storage", "Lyophilised vial stable at −20 °C for up to 24 months. After bacteriostatic water reconstitution, refrigerate and use within 28 days."),
        ],
    },
}

@app.route('/product/<int:pid>')
def product_detail(pid):
    p = next((x for x in products if x['id'] == pid), None)
    if not p:
        return "Product not found", 404
    detail = product_details.get(pid, {})
    return render_template('product_detail.html', product=p, detail=detail)

@app.route('/shop')
def shop():
    """Dedicated browse page — every product, generated from the catalog so it
    always stays in sync. Cards link through to each product's detail page."""
    STACK_IDS = {9, 21, 22}
    ESSENTIAL_IDS = {20}
    rows = []
    for p in products:
        vs = variants_for(p['id'])
        if not vs:
            continue
        base = vs[0]['retail_eur']
        d = product_details.get(p['id'], {})
        if p['id'] in ESSENTIAL_IDS:
            cat = 'essential'
        elif p['id'] in STACK_IDS:
            cat = 'stack'
        else:
            cat = 'peptide'
        rows.append({
            'id': p['id'],
            'name': p['name'],
            'eyebrow': d.get('eyebrow', ''),
            'tagline': d.get('tagline', ''),
            'from_label': vs[0]['label'],
            'price': base,
            'multi': len(vs) > 1,
            'chips': [c[0] for c in d.get('chips', [])][:3],
            'coa_slug': d.get('coa_slug'),
            'sub_ok': subscription_allowed(p['id']),
            'sub_price': subscription_unit_price(base) if subscription_allowed(p['id']) else None,
            'cat': cat,
        })
    return render_template('shop.html', rows=rows,
                           sub_pct=int(SUBSCRIPTION_DISCOUNT * 100))


@app.route('/deals')
def deals():
    """Bulk-deal & subscription overview, with per-product pack pricing."""
    rows = []
    for p in products:
        vs = variants_for(p['id'])
        if not vs:
            continue
        base = vs[0]['retail_eur']
        rows.append({
            'id': p['id'],
            'name': p['name'],
            'from_label': vs[0]['label'],
            'single': base,
            'five': round(base * 4, 2),  'five_each': round(base * 4 / 5, 2),
            'ten':  round(base * 7, 2),  'ten_each':  round(base * 7 / 10, 2),
            'sub': subscription_unit_price(base) if subscription_allowed(p['id']) else None,
            'sub_ok': subscription_allowed(p['id']),
            'variants': [{'sku': v['sku'], 'label': v['label'], 'price': v['retail_eur']} for v in vs],
        })
    return render_template('deals.html', rows=rows,
                           sub_pct=int(SUBSCRIPTION_DISCOUNT * 100),
                           sub_interval=SUBSCRIPTION_INTERVAL)

@app.route('/add-to-cart/<int:pid>', methods=['POST'])
def add_to_cart(pid):
    qty = max(1, int(request.form.get('quantity', 1)))
    sku = request.form.get('variant_sku') or default_sku(pid)
    if not sku or not get_variant(sku):
        flash('Please choose a variant.', 'warning')
        return redirect(url_for('product_detail', pid=pid))
    # Purchase mode: 'once' (bulk packs) or 'sub' (monthly subscription)
    mode = request.form.get('mode', 'once')
    if mode == 'sub' and not subscription_allowed(pid):
        mode = 'once'
    cart_key = f'sub:{sku}' if mode == 'sub' else sku
    cart = session.get('cart', {})
    cart[cart_key] = cart.get(cart_key, 0) + qty
    session['cart'] = cart
    return redirect(url_for('cart'))

def _build_cart_items():
    """Cart stores {cart_key: qty}, where cart_key is the variant SKU for a
    one-time purchase or 'sub:<sku>' for a monthly subscription. Legacy carts
    keyed by product_id are silently dropped."""
    cart_items = session.get('cart', {})
    items = []
    needs_cleanup = False
    for key, qty in list(cart_items.items()):
        # Legacy entries: numeric-only keys (product ids). Drop them.
        if key.isdigit():
            needs_cleanup = True
            continue
        mode = 'once'
        sku = key
        if key.startswith('sub:'):
            mode = 'sub'
            sku = key[4:]
        ref = get_variant(sku)
        if not ref:
            needs_cleanup = True
            continue
        p, v = ref['product'], ref['variant']
        # Subscription no longer offered for this product → fall back to one-time.
        if mode == 'sub' and not subscription_allowed(p['id']):
            mode = 'once'
            key = sku
        if mode == 'sub':
            unit_price = subscription_unit_price(v['retail_eur'])
            subtotal = round(unit_price * qty, 2)
        else:
            subtotal = bulk_line_total(v['retail_eur'], qty)
            unit_price = round(subtotal / qty, 2) if qty else v['retail_eur']
        items.append({
            'sku': v['sku'],
            'cart_key': key,
            'mode': mode,
            'product_id': p['id'],
            'name': p['name'],
            'variant_label': v['label'],
            'base_price': v['retail_eur'],
            'unit_price': unit_price,
            'quantity': qty,
            'subtotal': subtotal,
            'wholesale_unit_eur': wholesale_eur(v['wholesale_usd']),
        })
    if needs_cleanup:
        session['cart'] = {it['cart_key']: it['quantity'] for it in items}
    return items

@app.route('/cart')
def cart():
    items = _build_cart_items()
    totals = compute_totals(items, current_customer())
    return render_template_string(CART_HTML, cart=items, **totals, promo_catalog=PROMO_CODES)

@app.route('/cart/update', methods=['POST'])
def cart_update():
    """Change a cart line's quantity or remove it. `key` is the cart_key
    (variant SKU, or 'sub:<sku>' for a subscription line)."""
    key = request.form.get('key', '')
    action = request.form.get('action', '')
    cart = session.get('cart', {})
    if key in cart:
        if action == 'inc':
            cart[key] = min(99, cart[key] + 1)
        elif action == 'dec':
            cart[key] = cart[key] - 1
            if cart[key] <= 0:
                cart.pop(key, None)
        elif action == 'set':
            try:
                q = int(request.form.get('qty', cart[key]))
            except (TypeError, ValueError):
                q = cart[key]
            if q <= 0:
                cart.pop(key, None)
            else:
                cart[key] = min(99, q)
        elif action == 'remove':
            cart.pop(key, None)
        session['cart'] = cart
    return redirect(url_for('cart'))

@app.route('/apply-promo', methods=['POST'])
def apply_promo():
    code = request.form.get('code', '').strip().upper()
    items = _build_cart_items()
    promo, err = validate_promo(code, items, current_customer())
    if promo:
        session['promo'] = code
        session.pop('promo_error', None)
    else:
        session.pop('promo', None)
        session['promo_error'] = err or 'Invalid code'
    return redirect(url_for('cart'))

@app.route('/remove-promo')
def remove_promo():
    session.pop('promo', None)
    session.pop('promo_error', None)
    return redirect(url_for('cart'))

@app.route('/checkout', methods=['GET'])
def checkout():
    items = _build_cart_items()
    if not items:
        return redirect('/')
    totals = compute_totals(items, current_customer())
    # Prefill from previous order if same browser
    prefill = session.get('checkout_prefill', {})
    return render_template_string(CHECKOUT_HTML, cart=items, **totals, prefill=prefill,
                                  field_error=session.pop('checkout_error', None))

def _new_order_number():
    while True:
        n = 'PH-' + ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        if not Order.query.filter_by(order_number=n).first():
            return n

@app.route('/place-order', methods=['POST'])
def place_order():
    items = _build_cart_items()
    if not items:
        return redirect('/')

    # Capture + validate shipping address
    fields = {
        'email':         request.form.get('email', '').strip(),
        'full_name':     request.form.get('full_name', '').strip(),
        'phone':         request.form.get('phone', '').strip(),
        'address_line1': request.form.get('address_line1', '').strip(),
        'address_line2': request.form.get('address_line2', '').strip(),
        'city':          request.form.get('city', '').strip(),
        'postal_code':   request.form.get('postal_code', '').strip(),
        'country':       request.form.get('country', '').strip(),
    }
    required = ['email', 'full_name', 'address_line1', 'city', 'postal_code', 'country']
    missing = [k for k in required if not fields[k]]
    if missing:
        session['checkout_error'] = f'Please complete: {", ".join(missing).replace("_", " ")}.'
        session['checkout_prefill'] = fields
        return redirect(url_for('checkout'))
    if '@' not in fields['email']:
        session['checkout_error'] = 'Please enter a valid email address.'
        session['checkout_prefill'] = fields
        return redirect(url_for('checkout'))

    # Prefer the logged-in member; otherwise find-or-create by email.
    customer = None
    if session.get('customer_id'):
        customer = Customer.query.get(session['customer_id'])
    if not customer:
        customer = Customer.query.filter_by(email=fields['email'].lower()).first()
    if not customer:
        customer = Customer(email=fields['email'].lower())
        db.session.add(customer)
    customer.full_name     = fields['full_name']
    customer.phone         = fields['phone']
    customer.address_line1 = fields['address_line1']
    customer.address_line2 = fields['address_line2']
    customer.city          = fields['city']
    customer.postal_code   = fields['postal_code']
    customer.country       = fields['country']
    db.session.flush()

    # Totals — computed with the resolved customer so first-order-only codes
    # are enforced here (a returning buyer can't sneak a first-order promo
    # through by staying logged out).
    totals = compute_totals(items, customer)

    order = Order(
        order_number=_new_order_number(),
        customer_id=customer.id,
        status='AWAITING_PAYMENT',
        subtotal_eur=totals['subtotal'],
        discount_eur=totals['discount'],
        promo_code=totals['promo_code'],
        shipping_eur=totals['shipping'],
        vat_eur=totals['vat_amount'],
        total_eur=totals['total'],
        wholesale_cost_eur=totals['wholesale_total_eur'],
        margin_eur=totals['margin_eur'],
    )
    for it in items:
        variant_label = it['variant_label']
        if it.get('mode') == 'sub':
            variant_label = f"{variant_label} · {SUBSCRIPTION_INTERVAL} subscription"
        order.items.append(OrderItem(
            product_id=it['product_id'],
            product_name=it['name'],
            variant_sku=it['sku'],
            variant_label=variant_label,
            quantity=it['quantity'],
            unit_retail_eur=it['unit_price'],
            line_total_eur=it['subtotal'],
            wholesale_unit_eur=it['wholesale_unit_eur'],
            wholesale_total_eur=round(it['wholesale_unit_eur'] * it['quantity'], 2),
        ))
    db.session.add(order)
    db.session.commit()

    # Persist prefill for next time; remember which order this session is paying
    session['checkout_prefill'] = fields
    session['pending_order'] = order.order_number

    # ---- Stripe handoff (Phase B will replace this stub with a real Checkout Session) ----
    # For now we mark the order as paid immediately so the rest of the flow can be exercised.
    order.status = 'PAID'
    order.paid_at = datetime.utcnow()
    db.session.commit()

    # Record subscriptions for any subscription lines (billing is stubbed today —
    # this is the record Stripe Billing will later drive via webhooks).
    for it in items:
        if it.get('mode') == 'sub':
            now = datetime.utcnow()
            db.session.add(Subscription(
                customer_id=customer.id, product_id=it['product_id'],
                product_name=it['name'], variant_sku=it['sku'],
                variant_label=it['variant_label'], quantity=it['quantity'],
                unit_price_eur=it['unit_price'], interval=SUBSCRIPTION_INTERVAL,
                status='ACTIVE', next_renewal_at=now + timedelta(days=30),
                min_term_months=SUBSCRIPTION_MIN_TERM_MONTHS,
                commitment_end=now + timedelta(days=30 * SUBSCRIPTION_MIN_TERM_MONTHS)))

    # Affiliate commission — once per buyer, credited to a valid, non-self referrer.
    ref = session.get('ref')
    if ref:
        aff = Customer.query.filter_by(referral_code=ref).first()
        if aff and aff.id != customer.id:
            if not customer.referred_by_id:
                customer.referred_by_id = aff.id
            amount = round(totals['net_excl_vat'] * AFFILIATE_COMMISSION_RATE, 2)
            aff.affiliate_balance = round((aff.affiliate_balance or 0) + amount, 2)
            db.session.add(Commission(
                affiliate_id=aff.id, order_id=order.id, order_number=order.order_number,
                amount_eur=amount, rate=AFFILIATE_COMMISSION_RATE, status='PENDING'))
        session.pop('ref', None)
    db.session.commit()

    # Clear cart
    session.pop('cart', None)
    session.pop('promo', None)
    session.pop('promo_error', None)

    return redirect(url_for('order_success', order_number=order.order_number))

@app.route('/order/<order_number>')
def order_success(order_number):
    order = Order.query.filter_by(order_number=order_number).first_or_404()
    return render_template_string(SUCCESS_HTML, order=order)

# Legacy alias used by the cart's Stripe button — Phase B will properly wire this.
@app.route('/create-checkout-session', methods=['POST'])
def stripe_checkout():
    # If we got here without an address form, push the user to /checkout first.
    return redirect(url_for('checkout'))

@app.route('/success')
def success():
    return redirect('/')

# ----------------------------------------------------------------------
# Admin
# ----------------------------------------------------------------------
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    err = None
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect(url_for('admin_dashboard'))
        err = 'Incorrect password.'
    return render_template_string(ADMIN_LOGIN_HTML, err=err)

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    paid_or_later = [o for o in orders if o.status in ('PAID','SUBMITTED_TO_SUPPLIER','SHIPPED','DELIVERED','CLOSED')]
    revenue   = round(sum(o.total_eur for o in paid_or_later), 2)
    wholesale = round(sum(o.wholesale_cost_eur for o in paid_or_later), 2)
    margin    = round(sum(o.margin_eur for o in paid_or_later), 2)
    net_rev   = round(sum(o.total_eur - o.vat_eur for o in paid_or_later), 2)
    margin_pct = round(margin / net_rev * 100, 1) if net_rev else 0
    open_orders = sum(1 for o in orders if o.status in ('PAID','SUBMITTED_TO_SUPPLIER'))
    kpis = {
        'total_orders': len(orders),
        'paid_orders': sum(1 for o in orders if o.status == 'PAID'),
        'shipped_orders': sum(1 for o in orders if o.status in ('SHIPPED','DELIVERED','CLOSED')),
        'revenue': revenue, 'wholesale': wholesale, 'margin': margin,
        'margin_pct': margin_pct, 'open_orders': open_orders,
    }
    return render_template_string(ADMIN_HTML, orders=orders, kpis=kpis)

@app.route('/admin/subscriptions')
@admin_required
def admin_subscriptions():
    subs = Subscription.query.order_by(Subscription.status.asc(), Subscription.created_at.desc()).all()
    active = sum(1 for s in subs if s.status == 'ACTIVE')
    mrr = round(sum((s.unit_price_eur or 0) * (s.quantity or 1) for s in subs if s.status == 'ACTIVE'), 2)
    return render_template_string(ADMIN_SUBS_HTML, subs=subs, active=active, mrr=mrr,
                                  now=datetime.utcnow())

@app.route('/admin/order/<order_number>')
@admin_required
def admin_order_detail(order_number):
    order = Order.query.filter_by(order_number=order_number).first_or_404()
    return render_template_string(ADMIN_ORDER_HTML, order=order,
                                  supplier_email=SUPPLIER_EMAIL,
                                  supplier_subject=_supplier_subject(order),
                                  supplier_body=_supplier_body(order))

def _supplier_subject(order):
    return f"PepHub Order {order.order_number} — fulfilment request"

def _supplier_body(order):
    cust = order.customer
    lines = [
        f"Hi {SUPPLIER_NAME},",
        "",
        f"Please fulfil the following PepHub order:",
        "",
        f"Order #: {order.order_number}",
        f"Date:    {order.created_at.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "ITEMS:",
    ]
    for it in order.items:
        lines.append(f"  • {it.product_name} — {it.variant_label} × {it.quantity}  (SKU: {it.variant_sku})")
    lines += [
        "",
        "SHIP TO:",
        f"  {cust.full_name}",
        f"  {cust.address_line1}",
    ]
    if cust.address_line2:
        lines.append(f"  {cust.address_line2}")
    lines += [
        f"  {cust.city}, {cust.postal_code}",
        f"  {cust.country}",
        f"  Phone: {cust.phone or '—'}",
        f"  Email: {cust.email}",
        "",
        "Please confirm receipt and provide an estimated dispatch date + tracking number once shipped.",
        "",
        "Thanks,",
        "PepHub Team",
    ]
    return "\n".join(lines)

@app.route('/admin/order/<order_number>/submit', methods=['POST'])
@admin_required
def admin_order_submit(order_number):
    order = Order.query.filter_by(order_number=order_number).first_or_404()
    if order.status == 'PAID':
        # Phase B: send actual supplier email via Resend here.
        order.status = 'SUBMITTED_TO_SUPPLIER'
        order.submitted_at = datetime.utcnow()
        db.session.commit()
        flash(f'{order.order_number} marked submitted (supplier email integration pending).', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/order/<order_number>/ship', methods=['POST'])
@admin_required
def admin_order_ship(order_number):
    order = Order.query.filter_by(order_number=order_number).first_or_404()
    if order.status == 'SUBMITTED_TO_SUPPLIER':
        order.status = 'SHIPPED'
        order.shipped_at = datetime.utcnow()
        order.tracking_number = request.form.get('tracking_number', '').strip() or 'TBD'
        order.tracking_carrier = request.form.get('tracking_carrier', '').strip() or None
        db.session.commit()
        flash(f'{order.order_number} marked shipped.', 'success')
    return redirect(url_for('admin_dashboard'))

# ----------------------------------------------------------------------
# COA Routes
# ----------------------------------------------------------------------
@app.route('/coa')
def coa_index():
    reports_list = list(coa_reports.values())
    return render_template('coa_index.html', reports=reports_list)

@app.route('/coa/<slug>')
def coa_detail(slug):
    report = coa_reports.get(slug)
    if not report:
        return "Report not found", 404
    return render_template('coa_detail.html', report=report)

# ----------------------------------------------------------------------
# Science Hub Routes
# ----------------------------------------------------------------------
def _article_view(a):
    return {
        'slug': a.slug, 'title': a.title, 'topic': a.topic, 'excerpt': a.excerpt,
        'body_html': a.body_html, 'status': a.status,
        'created_at': a.created_at, 'published_at': a.published_at,
        'takeaways': json.loads(a.takeaways_json or '[]'),
        'sources': json.loads(a.sources_json or '[]'),
        'meta': SCIENCE_TOPICS.get(a.topic, {'emoji': '🔬', 'grad': 'linear-gradient(135deg,#1a1a1a,#2d2d2d)'}),
    }

@app.route('/science')
def science_index():
    topic = request.args.get('topic')
    q = Article.query.filter_by(status='PUBLISHED')
    if topic in SCIENCE_TOPICS:
        q = q.filter_by(topic=topic)
    articles = q.order_by(Article.published_at.desc(),
                          Article.created_at.desc()).all()
    views = [_article_view(a) for a in articles]
    featured = views[0] if views else None
    rest = views[1:] if views else []
    return render_template('science_index.html',
                           featured=featured, articles=rest,
                           topics=SCIENCE_TOPICS, counts=_science_topic_counts(),
                           active_topic=topic if topic in SCIENCE_TOPICS else None,
                           total=Article.query.filter_by(status='PUBLISHED').count())

@app.route('/science/<slug>')
def science_article(slug):
    a = Article.query.filter_by(slug=slug, status='PUBLISHED').first()
    if not a:
        return "Article not found", 404
    return render_template('science_article.html', article=_article_view(a),
                           topics=SCIENCE_TOPICS)

@app.route('/admin/science')
@admin_required
def admin_science():
    drafts = Article.query.filter_by(status='DRAFT').order_by(Article.created_at.desc()).all()
    published = Article.query.filter_by(status='PUBLISHED').order_by(Article.created_at.desc()).all()
    return render_template('admin_science.html',
                           drafts=[_article_view(a) for a in drafts],
                           published=[_article_view(a) for a in published],
                           api_key_set=bool(ANTHROPIC_API_KEY))

@app.route('/admin/science/generate', methods=['POST'])
@admin_required
def admin_science_generate():
    created, notes = run_science_ingest()
    if created:
        flash(f'Generated {created} draft article(s). Review and publish below.', 'success')
    else:
        flash('No new drafts created. ' + ('; '.join(notes) if notes else ''), 'warning')
    return redirect(url_for('admin_science'))

@app.route('/admin/science/<slug>/publish', methods=['POST'])
@admin_required
def admin_science_publish(slug):
    a = Article.query.filter_by(slug=slug).first_or_404()
    a.status = 'PUBLISHED'
    a.published_at = datetime.utcnow()
    db.session.commit()
    flash(f'Published "{a.title[:48]}".', 'success')
    return redirect(url_for('admin_science'))

@app.route('/admin/science/<slug>/unpublish', methods=['POST'])
@admin_required
def admin_science_unpublish(slug):
    a = Article.query.filter_by(slug=slug).first_or_404()
    a.status = 'DRAFT'
    db.session.commit()
    flash(f'Moved "{a.title[:48]}" back to drafts.', 'success')
    return redirect(url_for('admin_science'))

@app.route('/admin/science/<slug>/delete', methods=['POST'])
@admin_required
def admin_science_delete(slug):
    a = Article.query.filter_by(slug=slug).first_or_404()
    db.session.delete(a)
    db.session.commit()
    flash('Article deleted.', 'success')
    return redirect(url_for('admin_science'))

# ----------------------------------------------------------------------
# Weekly ingest scheduler — runs in-process while the server is up.
# Guarded so it starts once; manual trigger also available in admin.
# ----------------------------------------------------------------------
def _scheduled_ingest():
    with app.app_context():
        try:
            created, notes = run_science_ingest()
            log.info('weekly ingest: %d created — %s', created, '; '.join(notes))
        except Exception:
            log.exception('weekly ingest failed')

def _start_scheduler():
    if os.environ.get('DISABLE_SCHEDULER'):
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        sched = BackgroundScheduler(daemon=True)
        sched.add_job(_scheduled_ingest, 'interval', weeks=1, id='science_weekly_ingest')
        sched.start()
        log.info('Science Hub weekly ingest scheduler started')
    except Exception:
        log.exception('could not start scheduler')

# ----------------------------------------------------------------------
# Member accounts + affiliate program
# ----------------------------------------------------------------------
def _gen_referral_code():
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = 'PEP' + ''.join(secrets.choice(alphabet) for _ in range(5))
        if not Customer.query.filter_by(referral_code=code).first():
            return code
    return 'PEP' + secrets.token_hex(4).upper()

def current_customer():
    cid = session.get('customer_id')
    return Customer.query.get(cid) if cid else None

def member_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get('customer_id'):
            return redirect(url_for('account_login', next=request.path))
        return f(*a, **kw)
    return wrapper

@app.context_processor
def _inject_member():
    return {'current_member': current_customer()}

@app.before_request
def _capture_referral():
    ref = request.args.get('ref')
    if ref:
        session['ref'] = ref.strip().upper()[:24]

@app.route('/account/register', methods=['GET', 'POST'])
def account_register():
    if session.get('customer_id'):
        return redirect(url_for('account'))
    err = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        pw    = request.form.get('password', '')
        name  = request.form.get('full_name', '').strip()
        if '@' not in email or '.' not in email:
            err = 'Please enter a valid email address.'
        elif len(pw) < 8:
            err = 'Password must be at least 8 characters.'
        else:
            cust = Customer.query.filter_by(email=email).first()
            if cust and cust.password_hash:
                err = 'An account with that email already exists — please sign in.'
            else:
                if not cust:                       # promote a guest customer or create new
                    cust = Customer(email=email)
                    db.session.add(cust)
                cust.password_hash = generate_password_hash(pw)
                if name:
                    cust.full_name = name
                if not cust.referral_code:
                    cust.referral_code = _gen_referral_code()
                # Attribute the signup to a referrer if present
                ref = session.get('ref')
                if ref and not cust.referred_by_id:
                    aff = Customer.query.filter_by(referral_code=ref).first()
                    if aff and (cust.id is None or aff.id != cust.id):
                        cust.referred_by_id = aff.id
                db.session.commit()
                session['customer_id'] = cust.id
                return redirect(request.args.get('next') or url_for('account'))
    return render_template_string(AUTH_HTML, mode='register', err=err)

@app.route('/account/login', methods=['GET', 'POST'])
def account_login():
    if session.get('customer_id'):
        return redirect(url_for('account'))
    err = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        pw    = request.form.get('password', '')
        cust  = Customer.query.filter_by(email=email).first()
        if cust and cust.password_hash and check_password_hash(cust.password_hash, pw):
            session['customer_id'] = cust.id
            return redirect(request.args.get('next') or url_for('account'))
        err = 'Incorrect email or password.'   # generic — don't reveal which
    return render_template_string(AUTH_HTML, mode='login', err=err)

@app.route('/account/logout')
def account_logout():
    session.pop('customer_id', None)
    return redirect(url_for('index'))

@app.route('/account')
@member_required
def account():
    cust = current_customer()
    if not cust:
        session.pop('customer_id', None)
        return redirect(url_for('account_login'))
    if not cust.referral_code:                    # backfill for older accounts
        cust.referral_code = _gen_referral_code(); db.session.commit()
    orders = Order.query.filter_by(customer_id=cust.id).order_by(Order.created_at.desc()).all()
    subs = Subscription.query.filter_by(customer_id=cust.id).order_by(Subscription.created_at.desc()).all()
    referred = Customer.query.filter_by(referred_by_id=cust.id).count()
    ref_link = request.host_url.rstrip('/') + '/?ref=' + (cust.referral_code or '')
    return render_template_string(ACCOUNT_HTML, cust=cust, orders=orders, subs=subs,
                                  referred=referred, ref_link=ref_link,
                                  commission_pct=int(AFFILIATE_COMMISSION_RATE * 100))

@app.route('/account/subscription/<int:sid>/cancel', methods=['POST'])
@member_required
def account_cancel_sub(sid):
    sub = Subscription.query.get_or_404(sid)
    if sub.customer_id != session.get('customer_id') or sub.status != 'ACTIVE':
        return redirect(url_for('account'))
    if not sub.can_cancel:
        when = sub.commitment_end.strftime('%d %b %Y') if sub.commitment_end else 'the end of the minimum term'
        flash(f'This subscription has a {sub.min_term_months}-month minimum term — you can cancel from {when}.', 'error')
        return redirect(url_for('account'))
    sub.status = 'CANCELLED'
    sub.cancelled_at = datetime.utcnow()
    db.session.commit()
    flash('Subscription cancelled.', 'success')
    return redirect(url_for('account'))

AUTH_HTML = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ 'Create account' if mode=='register' else 'Sign in' }} | Pep Hub</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
 :root{--gold:#FF9000;--ph-border:#2D2D2D;}
 body{background:#141414;color:#F5F5F5;font-family:'Inter',system-ui,sans-serif;min-height:100vh;display:flex;flex-direction:column;}
 .navbar{background:#000;border-bottom:1px solid var(--ph-border);padding:.85rem 0;}
 .navbar-brand{font-weight:800;color:#fff!important;font-size:1.5rem;text-decoration:none;}
 .navbar-brand .h{background:var(--gold);color:#000;border-radius:6px;padding:.05em .3em;font-weight:900;}
 .wrap{flex:1;display:flex;align-items:center;justify-content:center;padding:2rem;}
 .card2{background:#242424;border:1px solid var(--ph-border);border-radius:1rem;padding:2rem;width:400px;max-width:100%;}
 h1{font-size:1.5rem;font-weight:800;margin-bottom:.3rem;}
 .sub{color:#999;font-size:.88rem;margin-bottom:1.5rem;}
 label{font-size:.78rem;font-weight:700;color:var(--gold);text-transform:uppercase;letter-spacing:.05em;margin-bottom:.3rem;display:block;}
 input{width:100%;background:#0F0F0F;border:1px solid var(--ph-border);border-radius:6px;padding:.7rem .85rem;color:#fff;margin-bottom:1rem;}
 input:focus{outline:none;border-color:var(--gold);}
 button{width:100%;background:var(--gold);color:#000;border:none;border-radius:6px;padding:.8rem;font-weight:800;text-transform:uppercase;letter-spacing:.04em;cursor:pointer;}
 button:hover{background:#fff;}
 .err{background:rgba(229,115,115,.12);border:1px solid rgba(229,115,115,.5);color:#E57373;border-radius:6px;padding:.6rem .8rem;font-size:.85rem;margin-bottom:1rem;}
 .alt{color:#999;font-size:.85rem;text-align:center;margin-top:1.25rem;}
 .alt a{color:var(--gold);text-decoration:none;font-weight:700;}
</style></head><body>
<nav class="navbar"><div class="container"><a href="/" class="navbar-brand">Pep<span class="h">Hub</span></a></div></nav>
<div class="wrap"><div class="card2">
  <h1>{{ 'Create your account' if mode=='register' else 'Welcome back' }}</h1>
  <div class="sub">{{ 'Track orders, manage subscriptions & earn with referrals.' if mode=='register' else 'Sign in to your PepHub account.' }}</div>
  {% if err %}<div class="err">⚠ {{ err }}</div>{% endif %}
  <form method="POST">
    {% if mode=='register' %}<label>Full name</label><input type="text" name="full_name" autocomplete="name">{% endif %}
    <label>Email</label><input type="email" name="email" required autocomplete="email" autofocus>
    <label>Password</label><input type="password" name="password" required autocomplete="{{ 'new-password' if mode=='register' else 'current-password' }}"{% if mode=='register' %} minlength="8" placeholder="At least 8 characters"{% endif %}>
    <button type="submit">{{ 'Create account' if mode=='register' else 'Sign in' }}</button>
  </form>
  {% if mode=='register' %}
  <div class="alt">Already have an account? <a href="/account/login">Sign in</a></div>
  {% else %}
  <div class="alt">New to PepHub? <a href="/account/register">Create an account</a></div>
  {% endif %}
</div></div>
</body></html>
"""

ACCOUNT_HTML = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>My Account | Pep Hub</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
<style>
 :root{--gold:#FF9000;--ph-card:#242424;--ph-border:#2D2D2D;--muted:#999;}
 body{background:#141414;color:#F5F5F5;font-family:'Inter',system-ui,sans-serif;}
 .navbar{background:#000;border-bottom:1px solid var(--ph-border);padding:.85rem 0;}
 .navbar-brand{font-weight:800;color:#fff!important;font-size:1.5rem;text-decoration:none;}
 .navbar-brand .h{background:var(--gold);color:#000;border-radius:6px;padding:.05em .3em;font-weight:900;}
 .navlinks a{color:#cfcfcf;text-decoration:none;font-weight:600;font-size:.9rem;margin-left:1.2rem;}
 .navlinks a:hover{color:var(--gold);}
 h1{font-weight:900;letter-spacing:-.02em;}
 .card2{background:var(--ph-card);border:1px solid var(--ph-border);border-radius:1rem;padding:1.5rem;margin-bottom:1.5rem;}
 .card2 h2{font-size:1.05rem;font-weight:800;margin-bottom:1rem;display:flex;align-items:center;gap:.5rem;}
 .card2 h2 i{color:var(--gold);}
 table{width:100%;border-collapse:collapse;font-size:.88rem;}
 th{color:var(--gold);font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;text-align:left;padding:.5rem .6rem;border-bottom:1px solid var(--ph-border);}
 td{padding:.6rem .6rem;border-bottom:1px solid var(--ph-border);color:#e0e0e0;}
 tr:last-child td{border-bottom:none;}
 .status{display:inline-block;border-radius:4px;padding:.1rem .5rem;font-size:.68rem;font-weight:800;text-transform:uppercase;}
 .s-ACTIVE,.s-PAID{background:rgba(46,125,50,.2);color:#81C784;}
 .s-CANCELLED{background:rgba(229,115,115,.15);color:#E57373;}
 .muted{color:var(--muted);font-size:.85rem;}
 .btn-mini{background:#1a1a1a;border:1px solid var(--ph-border);color:#ddd;border-radius:6px;padding:.3rem .7rem;font-size:.75rem;font-weight:600;cursor:pointer;}
 .btn-mini:hover{border-color:#E57373;color:#E57373;}
 .btn-gold{display:inline-block;background:var(--gold);color:#000;border-radius:6px;padding:.5rem 1rem;font-weight:800;font-size:.82rem;text-decoration:none;}
 .kpi-row{display:flex;gap:1rem;flex-wrap:wrap;}
 .kpi{background:#0F0F0F;border:1px solid var(--ph-border);border-radius:.75rem;padding:1rem 1.2rem;flex:1;min-width:150px;}
 .kpi .v{font-size:1.5rem;font-weight:900;color:#fff;}
 .kpi .l{font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;}
 .ref-box{display:flex;gap:.5rem;margin-top:.75rem;}
 .ref-box input{flex:1;background:#0F0F0F;border:1px solid var(--ph-border);border-radius:6px;padding:.55rem .8rem;color:var(--gold);font-weight:700;font-size:.85rem;}
 .alert{background:rgba(46,125,50,.12);border:1px solid rgba(46,125,50,.5);color:#81C784;border-radius:.6rem;padding:.6rem 1rem;margin-bottom:1rem;font-size:.88rem;}
 .alert-error{background:rgba(229,115,115,.12);border-color:rgba(229,115,115,.5);color:#E57373;}
 .lock{color:var(--muted);font-size:.72rem;}
 .footer-note{border-top:1px solid var(--ph-border);font-size:.75rem;color:var(--muted);text-align:center;padding:2rem 0;margin-top:2rem;}
</style></head><body>
<nav class="navbar"><div class="container d-flex justify-content-between align-items-center">
  <a href="/" class="navbar-brand">Pep<span class="h">Hub</span></a>
  <div class="navlinks"><a href="/shop">Shop</a><a href="/cart">Cart</a><a href="/account/logout">Sign out</a></div>
</div></nav>
<div class="container py-4" style="max-width:900px;">
  <h1 class="mb-1">My Account</h1>
  <p class="muted mb-4">{{ cust.full_name or cust.email }} · {{ cust.email }}</p>

  {% with msgs = get_flashed_messages(with_categories=true) %}{% for cat, m in msgs %}<div class="alert {{ 'alert-error' if cat == 'error' else '' }}">{{ m }}</div>{% endfor %}{% endwith %}

  <div class="card2">
    <h2><i class="bi bi-arrow-repeat"></i> Subscriptions</h2>
    {% if subs %}
    <table><thead><tr><th>Product</th><th>Qty</th><th>Price</th><th>Next renewal</th><th>Status</th><th></th></tr></thead><tbody>
      {% for s in subs %}
      <tr>
        <td><strong style="color:#fff;">{{ s.product_name }}</strong><br><span class="muted">{{ s.variant_label }} · {{ s.interval }}</span>
            {% if s.commitment_end %}<br><span class="lock">{{ s.min_term_months }}-mo min term · cancellable from {{ s.commitment_end.strftime('%d %b %Y') }}</span>{% endif %}</td>
        <td>{{ s.quantity }}</td>
        <td>€{{ "%.2f"|format(s.unit_price_eur or 0) }}/mo</td>
        <td class="muted">{{ s.next_renewal_at.strftime('%d %b %Y') if s.next_renewal_at else '—' }}</td>
        <td><span class="status s-{{ s.status }}">{{ s.status }}</span></td>
        <td>{% if s.status == 'ACTIVE' %}
              {% if s.can_cancel %}<form method="POST" action="/account/subscription/{{ s.id }}/cancel" onsubmit="return confirm('Cancel this subscription?');"><button class="btn-mini" type="submit">Cancel</button></form>
              {% else %}<button class="btn-mini" type="button" disabled title="Minimum term until {{ s.commitment_end.strftime('%d %b %Y') if s.commitment_end else '' }}" style="opacity:.5;cursor:not-allowed;">🔒 Locked</button>{% endif %}
            {% endif %}</td>
      </tr>
      {% endfor %}
    </tbody></table>
    {% else %}<p class="muted">No subscriptions yet. Subscribe to a product for monthly delivery at a standing discount.</p>{% endif %}
  </div>

  <div class="card2">
    <h2><i class="bi bi-people"></i> Refer &amp; earn</h2>
    <p class="muted">Share your link — you earn <strong style="color:var(--gold);">{{ commission_pct }}%</strong> commission on every order from people you refer.</p>
    <div class="kpi-row">
      <div class="kpi"><div class="v">€{{ "%.2f"|format(cust.affiliate_balance or 0) }}</div><div class="l">Earnings</div></div>
      <div class="kpi"><div class="v">{{ referred }}</div><div class="l">People referred</div></div>
    </div>
    <div class="ref-box">
      <input id="reflink" value="{{ ref_link }}" readonly onclick="this.select()">
      <button class="btn-gold" onclick="navigator.clipboard.writeText(document.getElementById('reflink').value);this.textContent='Copied!';">Copy link</button>
    </div>
  </div>

  <div class="card2">
    <h2><i class="bi bi-bag"></i> Order history</h2>
    {% if orders %}
    <table><thead><tr><th>Order</th><th>Date</th><th>Total</th><th>Status</th></tr></thead><tbody>
      {% for o in orders %}
      <tr>
        <td><a href="/order/{{ o.order_number }}" style="color:var(--gold);text-decoration:none;font-weight:700;">{{ o.order_number }}</a></td>
        <td class="muted">{{ o.created_at.strftime('%d %b %Y') }}</td>
        <td>€{{ "%.2f"|format(o.total_eur) }}</td>
        <td><span class="status s-{{ 'PAID' if o.status in ['PAID','SUBMITTED_TO_SUPPLIER','SHIPPED','DELIVERED','CLOSED'] else 'CANCELLED' }}">{{ o.status.replace('_',' ') }}</span></td>
      </tr>
      {% endfor %}
    </tbody></table>
    {% else %}<p class="muted">No orders yet. <a href="/" style="color:var(--gold);">Start shopping →</a></p>{% endif %}
  </div>
</div>
<div class="footer-note"><div class="container">© 2024–2026 Pep Hub — for research purposes only.</div></div>
</body></html>
"""

# ======================================================================
# Legal / policy pages  —  /legal/<page>
# ======================================================================
LEGAL_HTML = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }} | Pep Hub</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css" rel="stylesheet">
<style>
 :root{--gold:#FF9000;--ph-border:#2D2D2D;--muted:#9a9a9a;}
 body{background:#141414;color:#EDEDED;font-family:'Inter',system-ui,sans-serif;margin:0;}
 .navbar{background:#000;border-bottom:1px solid var(--ph-border);padding:.85rem 0;}
 .navbar-brand{font-weight:800;color:#fff!important;font-size:1.5rem;text-decoration:none;}
 .navbar-brand .h{background:var(--gold);color:#000;border-radius:6px;padding:.05em .3em;font-weight:900;}
 .ph-menu{display:flex;gap:1.5rem;align-items:center;}
 .ph-menu a{color:#cfcfcf;text-decoration:none;font-weight:600;font-size:.9rem;white-space:nowrap;}
 .ph-menu a:hover{color:var(--gold);}
 .wrap{max-width:820px;margin:0 auto;padding:2.5rem 1.25rem 4rem;}
 h1{font-size:2rem;font-weight:800;margin-bottom:.4rem;}
 .updated{color:var(--muted);font-size:.85rem;margin-bottom:2rem;}
 h2{font-size:1.15rem;font-weight:800;color:#fff;margin:2rem 0 .6rem;}
 p,li{color:#d0d0d0;line-height:1.7;font-size:.95rem;}
 a{color:var(--gold);}
 ul{padding-left:1.2rem;}
 .box{background:#1d1d1d;border:1px solid var(--ph-border);border-radius:10px;padding:1rem 1.2rem;margin:1.2rem 0;}
 .toc{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:2rem;}
 .toc a{background:#1d1d1d;border:1px solid var(--ph-border);border-radius:999px;padding:.3rem .8rem;font-size:.8rem;text-decoration:none;color:#cfcfcf;}
 .toc a.active{background:var(--gold);color:#000;border-color:var(--gold);font-weight:700;}
</style></head><body>
<nav class="navbar"><div class="container-fluid px-4 d-flex align-items-center">
  <a class="navbar-brand" href="/">Pep<span class="h">Hub</span></a>
  <div class="ph-menu mx-auto d-none d-lg-flex">
    <a href="/">Home</a><a href="/shop">Shop</a><a href="/deals">Bulk Deals</a>
    <a href="/science">Science Hub</a><a href="/coa">COA Reports</a><a href="/account">Account</a>
  </div>
</div></nav>
<div class="wrap">
  <div class="toc">
    <a href="/legal/terms" class="{{ 'active' if current=='terms' }}">Terms</a>
    <a href="/legal/privacy" class="{{ 'active' if current=='privacy' }}">Privacy</a>
    <a href="/legal/cookies" class="{{ 'active' if current=='cookies' }}">Cookies</a>
    <a href="/legal/refunds" class="{{ 'active' if current=='refunds' }}">Refunds</a>
    <a href="/legal/shipping" class="{{ 'active' if current=='shipping' }}">Shipping</a>
    <a href="/legal/imprint" class="{{ 'active' if current=='imprint' }}">Imprint</a>
  </div>
  <h1>{{ title }}</h1>
  <div class="updated">Last updated: {{ legal.updated }}</div>
  {{ body|safe }}
</div>
</body></html>
"""

# Each body is a Jinja fragment rendered with the LEGAL config, so company
# details stay in one place. Written for an EU research-chemical storefront.
LEGAL_PAGES = {
 'terms': {'title': 'Terms & Conditions', 'body': """
<p>These Terms &amp; Conditions govern your use of {{ legal.site }} (the “Site”), operated by
{{ legal.entity }} (“we”, “us”, “our”). By placing an order or using the Site you agree to these terms.</p>

<div class="box"><strong>Research use only.</strong> All products sold on this Site are supplied strictly as
<em>laboratory research materials</em>. They are <strong>not</strong> medicines, dietary supplements, food,
cosmetics, or products for human or veterinary consumption, and must not be administered to humans or animals.
By ordering you confirm you are a qualified purchaser using the products for lawful in-vitro research only, and
that you are at least 18 years old.</p></div>

<h2>1. Eligibility</h2>
<p>You must be at least 18 and legally permitted to purchase research chemicals in your jurisdiction. You are
solely responsible for ensuring the products you order are legal to import and possess where you live.</p>

<h2>2. Orders &amp; acceptance</h2>
<p>An order is an offer to buy. A contract forms only when we confirm dispatch. We may decline or cancel any
order — for example where a product is unavailable, pricing was clearly erroneous, or we cannot verify the order —
and will refund any amount already paid.</p>

<h2>3. Prices &amp; payment</h2>
<p>Prices are shown in euros and include VAT where applicable. We may change prices at any time, but changes do
not affect confirmed orders. Payment is taken through our payment processor; we do not store full card details.</p>

<h2>4. Subscriptions</h2>
<p>Subscription plans renew automatically and carry a <strong>minimum term of 3 months</strong>. You may cancel
at any time after the minimum term via your account; cancellation stops future renewals and takes effect at the
end of the current paid period. See also our <a href="/legal/refunds">Refunds &amp; Returns</a> policy.</p>

<h2>5. Delivery</h2>
<p>Delivery terms and timescales are set out in our <a href="/legal/shipping">Shipping Policy</a>. Risk in the
goods passes to you on delivery.</p>

<h2>6. Acceptable use &amp; liability</h2>
<p>You must not misuse the Site or use any product unlawfully or in any way that endangers health or safety. To
the fullest extent permitted by law, our total liability arising from any order is limited to the amount you paid
for that order. Nothing in these terms limits liability that cannot be limited by law.</p>

<h2>7. Intellectual property</h2>
<p>All content on the Site — including Science Hub articles, images and branding — is owned by or licensed to
{{ legal.entity }} and may not be reproduced without permission.</p>

<h2>8. Governing law</h2>
<p>These terms are governed by the laws of {{ legal.country }}, and disputes are subject to its courts, without
affecting mandatory consumer-protection rights you may have.</p>

<h2>9. Contact</h2>
<p>{{ legal.entity }} · {{ legal.address }} · <a href="mailto:{{ legal.email }}">{{ legal.email }}</a></p>
"""},

 'privacy': {'title': 'Privacy Policy', 'body': """
<p>This Privacy Policy explains how {{ legal.entity }} (“we”) collects and uses your personal data when you use
{{ legal.site }}, in accordance with the EU General Data Protection Regulation (GDPR). The data controller is
{{ legal.entity }}, {{ legal.address }}.</p>

<h2>1. What we collect</h2>
<ul>
 <li><strong>Account &amp; order data</strong> — name, email, phone, delivery/billing address, order history.</li>
 <li><strong>Payment data</strong> — processed by our payment provider; we receive only a confirmation and the
     last digits/brand of the card, never the full card number.</li>
 <li><strong>Account credentials</strong> — your password is stored only as a salted hash.</li>
 <li><strong>Technical data</strong> — essential session cookie, and server logs (IP, timestamp) kept for
     security and fraud prevention.</li>
</ul>

<h2>2. Why we use it &amp; legal basis</h2>
<ul>
 <li>To process orders, subscriptions and deliveries — <em>performance of a contract</em>.</li>
 <li>To operate accounts, the affiliate programme and customer support — <em>contract / legitimate interests</em>.</li>
 <li>To meet tax, accounting and legal obligations — <em>legal obligation</em>.</li>
 <li>To keep the Site secure and prevent fraud — <em>legitimate interests</em>.</li>
</ul>
<p>We do <strong>not</strong> sell your data or use advertising/tracking cookies.</p>

<h2>3. Who we share it with</h2>
<p>Only with processors who help us run the store, under data-processing agreements: our hosting provider, our
payment processor, and our shipping/fulfilment partner. Some may process data outside the EEA under appropriate
safeguards (e.g. EU Standard Contractual Clauses).</p>

<h2>4. How long we keep it</h2>
<p>Order and invoice records are retained as long as required by law (typically up to 7 years). Account data is
kept while your account is active and deleted on request where no legal obligation requires retention.</p>

<div class="box"><h2 style="margin-top:0">5. Your GDPR rights</h2>
<p>You have the right to <strong>access</strong>, <strong>rectify</strong>, <strong>erase</strong> (“right to be
forgotten”), <strong>restrict</strong> or <strong>object to</strong> processing, and to <strong>data
portability</strong>. You may withdraw consent at any time and lodge a complaint with your national data-protection
authority. To exercise any right, email <a href="mailto:{{ legal.privacy_email }}">{{ legal.privacy_email }}</a>;
we respond within one month.</p></div>

<h2>6. Cookies</h2>
<p>We use only essential cookies. See our <a href="/legal/cookies">Cookie Policy</a>.</p>

<h2>7. Contact</h2>
<p>Privacy enquiries: <a href="mailto:{{ legal.privacy_email }}">{{ legal.privacy_email }}</a> ·
{{ legal.entity }}, {{ legal.address }}.</p>
"""},

 'cookies': {'title': 'Cookie Policy', 'body': """
<p>This policy explains how {{ legal.site }} uses cookies and similar technologies.</p>

<h2>What we use</h2>
<p>We use <strong>strictly necessary cookies only</strong> — these are required for the Site to function and do
not need consent under the GDPR/ePrivacy rules. We do <strong>not</strong> use analytics, advertising or
cross-site tracking cookies.</p>

<h2>Cookies we set</h2>
<ul>
 <li><strong>session</strong> — keeps you signed in and remembers your shopping cart. Expires when you close your
     browser (or shortly after). Essential.</li>
 <li><strong>csrf</strong> — a security token that protects forms against cross-site request forgery. Essential.</li>
 <li><strong>ph_consent</strong> — remembers that you have seen the cookie notice so we don't show it again.
     Lasts up to 12 months.</li>
</ul>

<h2>Managing cookies</h2>
<p>You can block or delete cookies in your browser settings, but the store may not work correctly without the
essential ones (for example, you may be unable to stay logged in or check out).</p>

<h2>Contact</h2>
<p><a href="mailto:{{ legal.privacy_email }}">{{ legal.privacy_email }}</a></p>
"""},

 'refunds': {'title': 'Refunds & Returns', 'body': """
<p>We want you to be satisfied with your order. This policy sits alongside your statutory rights.</p>

<h2>Right of withdrawal (EU consumers)</h2>
<p>Where the law grants a 14-day right of withdrawal, you may cancel within 14 days of receiving your order.
<strong>Exception:</strong> for health-protection and hygiene reasons, sealed goods that have been unsealed after
delivery cannot be returned once opened. Research materials that have been opened, used or tampered with are not
eligible for return.</p>

<h2>Faulty, damaged or incorrect items</h2>
<p>If an item arrives damaged, faulty or not as described, contact us within 7 days of delivery at
<a href="mailto:{{ legal.email }}">{{ legal.email }}</a> with your order number and photos. We will arrange a
replacement or full refund at no cost to you.</p>

<h2>How refunds are issued</h2>
<p>Approved refunds are made to your original payment method within 14 days of us receiving the returned item or
agreeing the refund. Original shipping costs are refunded only where the return is due to our error.</p>

<h2>Subscriptions</h2>
<p>Subscription plans have a <strong>3-month minimum term</strong>. You can cancel from your account after the
minimum term; already-billed periods are non-refundable, and cancellation stops future renewals.</p>

<h2>How to start a return</h2>
<p>Email <a href="mailto:{{ legal.email }}">{{ legal.email }}</a> with your order number before sending anything
back, and we'll give you return instructions.</p>
"""},

 'shipping': {'title': 'Shipping Policy', 'body': """
<p>How and where we ship orders from {{ legal.site }}.</p>

<h2>Processing time</h2>
<p>Orders are typically processed within 1–2 business days. You'll receive a confirmation when your order ships.</p>

<h2>Delivery estimates</h2>
<ul>
 <li>Domestic: usually 1–3 business days after dispatch.</li>
 <li>Within the EU: usually 2–7 business days after dispatch.</li>
</ul>
<p>These are estimates, not guarantees; carrier and customs delays can occur.</p>

<h2>Shipping costs</h2>
<p>Shipping is calculated at checkout. Orders over the free-shipping threshold shown at checkout qualify for free
standard delivery. All shipments are packaged discreetly.</p>

<h2>Customs &amp; import</h2>
<p>You are the importer of record and responsible for ensuring the products are legal to import and possess in
your country, and for any customs duties or taxes charged on arrival.</p>

<h2>Lost or delayed parcels</h2>
<p>If your order hasn't arrived within the estimated window, contact <a href="mailto:{{ legal.email }}">{{ legal.email }}</a>
and we'll investigate with the carrier.</p>
"""},

 'imprint': {'title': 'Imprint / Legal Notice', 'body': """
<p>Information in accordance with applicable EU e-commerce disclosure requirements.</p>
<div class="box">
<p><strong>{{ legal.entity }}</strong><br>
{{ legal.address }}<br>
Company/registration no.: {{ legal.reg_no }}<br>
VAT no.: {{ legal.vat_no }}<br>
Email: <a href="mailto:{{ legal.email }}">{{ legal.email }}</a><br>
Website: {{ legal.site }}</p>
</div>
<p>Responsible for content and the operator of this Site is {{ legal.entity }}. For consumer dispute resolution,
the European Commission provides an online platform at
<a href="https://ec.europa.eu/consumers/odr" target="_blank" rel="noopener">ec.europa.eu/consumers/odr</a>.</p>
"""},
}


@app.route('/legal')
def legal_index():
    return redirect(url_for('legal_page', page='terms'))


@app.route('/legal/<page>')
def legal_page(page):
    pg = LEGAL_PAGES.get(page)
    if not pg:
        abort(404)
    body = render_template_string(pg['body'], legal=LEGAL)
    return render_template_string(LEGAL_HTML, title=pg['title'], body=body,
                                  legal=LEGAL, current=page)


_start_scheduler()

if __name__ == '__main__':
    app.run(debug=True, port=9000)