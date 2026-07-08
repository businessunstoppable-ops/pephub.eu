"""Seed the Science Hub with one foundational article per category.

These are PepHub editorial explainers grounded in the general research literature
(reputable further-reading links attached). They start the library; the weekly
AI pipeline adds fresh, source-synthesised pieces on top once API credits are set.

Run:  DATABASE_URL="sqlite:///$(pwd)/pephub.db" ./venv/bin/python seed_articles.py
"""
import json
from datetime import datetime, timedelta

import app
from app import db, Article

# Featured first. published_at descends so the index orders them this way.
SEED = [
    {
        "topic": "Peptide Science",
        "title": "BPC-157 & TB-500: What the Research Actually Describes",
        "slug": "bpc157-tb500-what-research-describes",
        "excerpt": "Two of the most studied repair peptides in preclinical models — here's what the science says about how they're thought to work, and where the evidence stops.",
        "takeaways": [
            "BPC-157 is a 15-amino-acid sequence derived from a gastric protein; TB-500 is a fragment of Thymosin Beta-4.",
            "Preclinical models associate them with angiogenesis, cell migration, and tissue-repair signalling.",
            "Almost all data is animal or in-vitro — robust human clinical trials are lacking.",
            "Both are sold strictly for laboratory research, not human use.",
        ],
        "body": """
<p><strong>BPC-157</strong> ("Body Protection Compound-157") and <strong>TB-500</strong> (a synthetic fragment of Thymosin Beta-4) are two of the most frequently discussed peptides in tissue-repair research. They are often grouped together because preclinical studies associate both with the same broad theme: helping tissue rebuild after mechanical or chemical stress.</p>

<h2>What they are</h2>
<p>BPC-157 is a stable 15-amino-acid sequence originally identified within a protein found in gastric juice. TB-500 is a synthetic peptide corresponding to the actin-binding region of Thymosin Beta-4, a protein naturally present in nearly every human cell. Because both occur (in some form) in the body, researchers have been interested in whether supplementing them amplifies the repair processes they participate in.</p>

<h2>The proposed mechanisms</h2>
<p>In animal and cell-culture studies, several recurring observations show up:</p>
<ul>
<li><strong>Angiogenesis</strong> — promotion of new blood-vessel formation, which would bring oxygen and nutrients to damaged tissue.</li>
<li><strong>Cell migration</strong> — TB-500's actin-binding activity is linked to cells moving into a wound site, a key early step in repair.</li>
<li><strong>Growth-factor signalling</strong> — BPC-157 has been associated with pathways (such as VEGF and growth-hormone-receptor expression) involved in tendon and gut-lining repair.</li>
</ul>

<h2>Where the evidence stops</h2>
<p>This is the part that matters most. The overwhelming majority of findings come from <em>rodent models and in-vitro systems</em>. They describe plausible biological mechanisms — they do not establish safety, dosing, or efficacy in humans. Well-controlled human clinical trials remain scarce, and regulators have not approved these peptides as therapeutics.</p>

<blockquote>Interesting mechanisms in a mouse are a hypothesis in a human, not a conclusion.</blockquote>

<p>For the research community, that gap is exactly what makes these molecules worth studying carefully and documenting rigorously — which is why batch-level purity testing and a full certificate of analysis matter.</p>

<p><em>For research and educational purposes only. This is not medical advice, and peptides referenced here are sold for laboratory research use only.</em></p>
""",
        "sources": [
            {"title": "BPC-157 research index", "source": "PubMed", "url": "https://pubmed.ncbi.nlm.nih.gov/?term=BPC-157"},
            {"title": "Thymosin Beta-4 research index", "source": "PubMed", "url": "https://pubmed.ncbi.nlm.nih.gov/?term=thymosin+beta+4"},
            {"title": "Pharmacology research news", "source": "ScienceDaily", "url": "https://www.sciencedaily.com/news/health_medicine/pharmacology/"},
        ],
    },
    {
        "topic": "Nutrition",
        "title": "Protein Timing and the ‘Anabolic Window’: Signal vs. Noise",
        "slug": "protein-timing-anabolic-window",
        "excerpt": "Does it matter if you eat protein within 30 minutes of training? The research has matured a lot — and the answer is more relaxed than the supplement industry suggests.",
        "takeaways": [
            "Total daily protein intake is the dominant driver of muscle protein synthesis.",
            "The 'anabolic window' is wider than once claimed — hours, not minutes.",
            "Distribution across meals (~0.4 g/kg per meal) appears more useful than peri-workout timing.",
            "Timing matters most when training fasted or with long gaps between meals.",
        ],
        "body": """
<p>For two decades, gym lore held that you had a narrow "anabolic window" — roughly 30 minutes after training — to consume protein or forfeit your gains. It made intuitive sense and sold a lot of shakes. The research has since matured, and the picture is calmer.</p>

<h2>What actually drives muscle growth</h2>
<p>The single biggest lever on muscle protein synthesis is <strong>total daily protein intake</strong>, typically studied in the range of 1.6–2.2 g per kg of body weight per day for people training to build or retain muscle. Hit that consistently and the minute-by-minute timing becomes a rounding error.</p>

<h2>The window is a barn door</h2>
<p>Reviews of the timing literature suggest the period during which post-exercise nutrition meaningfully contributes is measured in <em>hours</em>, not minutes. A pre-training meal eaten an hour or two before is still "in the system" afterward, which blurs the urgency of eating the instant you rack the weights.</p>

<h2>Where timing genuinely helps</h2>
<ul>
<li><strong>Distribution beats clustering.</strong> Spreading protein across 3–4 meals, each delivering roughly 0.3–0.4 g/kg, appears to stimulate synthesis more effectively than one large bolus.</li>
<li><strong>Fasted training</strong> is the real exception — if you trained on an empty stomach, eating protein reasonably soon afterward matters more.</li>
<li><strong>Long gaps</strong> — if your next meal is many hours away, a post-workout source bridges that gap usefully.</li>
</ul>

<blockquote>Get the daily total right and spread it sensibly; the stopwatch is optional.</blockquote>

<p><em>For research and educational purposes only. This is general nutrition science, not personalised dietary or medical advice.</em></p>
""",
        "sources": [
            {"title": "Nutrition research news", "source": "ScienceDaily", "url": "https://www.sciencedaily.com/news/health_medicine/nutrition/"},
            {"title": "Evidence-based supplement & nutrition analysis", "source": "Examine", "url": "https://examine.com/"},
            {"title": "Dietary protein research index", "source": "PubMed", "url": "https://pubmed.ncbi.nlm.nih.gov/?term=protein+timing+muscle+protein+synthesis"},
        ],
    },
    {
        "topic": "Bio-hacking",
        "title": "Cold Exposure and Metabolic Adaptation: The Evidence So Far",
        "slug": "cold-exposure-metabolic-adaptation",
        "excerpt": "Ice baths and cold plunges are everywhere. Strip away the hype and there's a real, if modest, physiology underneath — plus one important trade-off for lifters.",
        "takeaways": [
            "Cold exposure activates brown adipose tissue and increases energy expenditure short-term.",
            "Repeated exposure drives measurable cold-adaptation, not large fat-loss effects.",
            "Post-workout cold immersion can blunt strength and hypertrophy adaptations.",
            "Timing cold away from resistance training preserves the muscle-building signal.",
        ],
        "body": """
<p>Cold plunges have gone from fringe to ubiquitous. As with most bio-hacking trends, the truth sits between the marketing and the skepticism: there <em>is</em> real physiology here, it's just more modest and more nuanced than a thumbnail suggests.</p>

<h2>What cold does to metabolism</h2>
<p>Acute cold exposure activates <strong>brown adipose tissue (BAT)</strong> and shivering thermogenesis — the body burns energy to defend its core temperature. Studies show a genuine short-term bump in energy expenditure and improvements in markers of cold adaptation and, in some work, insulin sensitivity. Repeated exposure makes you better at handling cold (cold-adaptation), but the effect on body-fat over time is small relative to diet and training.</p>

<h2>The trade-off lifters should know</h2>
<p>This is the most practically important finding. A consistent line of research shows that <strong>cold-water immersion immediately after resistance training can blunt the adaptive response</strong> — attenuating gains in strength and muscle size compared with passive recovery. The cold appears to dampen the very inflammatory and signalling cascade that drives hypertrophy.</p>

<ul>
<li><strong>Recovery from fatigue?</strong> Cold can help you feel fresher between sessions — useful in-season or during tournaments.</li>
<li><strong>Building muscle?</strong> Keep cold immersion away from your hypertrophy sessions — separate them by hours, or use it on rest days.</li>
</ul>

<blockquote>Cold is a tool, not a cure-all — match it to the adaptation you actually want.</blockquote>

<p><em>For research and educational purposes only. Not medical advice; cold exposure carries cardiovascular considerations for some individuals.</em></p>
""",
        "sources": [
            {"title": "Fitness research news", "source": "ScienceDaily", "url": "https://www.sciencedaily.com/news/health_medicine/fitness/"},
            {"title": "Cold exposure & brown fat research index", "source": "PubMed", "url": "https://pubmed.ncbi.nlm.nih.gov/?term=cold+exposure+brown+adipose+tissue"},
            {"title": "Recovery & cold-water immersion analysis", "source": "Examine", "url": "https://examine.com/"},
        ],
    },
    {
        "topic": "Vitality & Longevity",
        "title": "The Hallmarks of Aging: A Map for Longevity Research",
        "slug": "hallmarks-of-aging-map",
        "excerpt": "Longevity science isn't one breakthrough — it's a framework. The 'hallmarks of aging' give researchers a shared map of what actually goes wrong as we get older.",
        "takeaways": [
            "Aging is described as a set of interacting cellular 'hallmarks', not a single cause.",
            "Core hallmarks include genomic instability, telomere attrition, and cellular senescence.",
            "Senescent 'zombie' cells secrete inflammatory signals that drive tissue decline.",
            "Most interventions remain preclinical — lifestyle factors have the strongest human evidence.",
        ],
        "body": """
<p>"Longevity" gets marketed as a destination, but in research it's a <strong>framework</strong>. In 2013 a landmark paper proposed the "hallmarks of aging" — a shared map of the cellular processes that go wrong over time — and it has organised the field ever since (and been expanded with newer hallmarks).</p>

<h2>The core hallmarks</h2>
<ul>
<li><strong>Genomic instability</strong> — accumulating DNA damage outpacing repair.</li>
<li><strong>Telomere attrition</strong> — the protective caps on chromosomes shortening with each division.</li>
<li><strong>Epigenetic alterations</strong> — drift in how genes are switched on and off.</li>
<li><strong>Loss of proteostasis</strong> — the cell's protein quality-control machinery faltering.</li>
<li><strong>Cellular senescence</strong> — cells that stop dividing but won't die.</li>
<li><strong>Mitochondrial dysfunction</strong> — the cell's power plants becoming less efficient.</li>
</ul>

<h2>Why senescence gets so much attention</h2>
<p>Senescent cells — sometimes nicknamed "zombie cells" — accumulate with age and secrete a cocktail of inflammatory molecules (the SASP) that damages surrounding healthy tissue. Clearing them in animal models has produced striking healthspan improvements, which is why "senolytics" are one of the hottest areas in the field.</p>

<h2>The honest status</h2>
<p>Most pharmacological interventions targeting these hallmarks are <em>preclinical or early-stage</em>. The interventions with the strongest human evidence remain unglamorous: regular exercise, sleep, not smoking, and dietary patterns that support metabolic health — each of which touches several hallmarks at once.</p>

<blockquote>The map is excellent; we're still learning to drive on it.</blockquote>

<p><em>For research and educational purposes only. Not medical advice.</em></p>
""",
        "sources": [
            {"title": "Healthy aging research news", "source": "ScienceDaily", "url": "https://www.sciencedaily.com/news/health_medicine/healthy_aging/"},
            {"title": "National Institute on Aging", "source": "NIH / NIA", "url": "https://www.nia.nih.gov/"},
            {"title": "Hallmarks of aging research index", "source": "PubMed", "url": "https://pubmed.ncbi.nlm.nih.gov/?term=hallmarks+of+aging"},
        ],
    },
    {
        "topic": "Training & Recovery",
        "title": "Progressive Overload: How Tissue Actually Rebuilds Stronger",
        "slug": "progressive-overload-how-tissue-rebuilds",
        "excerpt": "The oldest principle in strength training is also the most evidence-backed. Here's the biology of why doing slightly more, over time, makes you adapt.",
        "takeaways": [
            "Adaptation is driven by mechanical tension and the repair response to controlled stress.",
            "Muscle protein synthesis stays elevated for ~24–48 hours after training.",
            "Connective tissue (tendon/ligament) adapts more slowly than muscle.",
            "Recovery — sleep, protein, and managed load — is where adaptation happens.",
        ],
        "body": """
<p><strong>Progressive overload</strong> — gradually asking the body to do a little more than last time — is the oldest principle in strength training and, conveniently, one of the best supported. The mechanism is a story of stress and repair.</p>

<h2>Stress is the signal</h2>
<p>Resistance training imposes <strong>mechanical tension</strong> on muscle fibres and creates microscopic disruption. That stress isn't damage to be feared — it's a signal. It triggers a cascade (notably the mTOR pathway) that ramps up <strong>muscle protein synthesis</strong>, which stays elevated for roughly 24–48 hours afterward. Repeat the stimulus before the adaptation fully fades, nudge the load up over weeks, and the tissue rebuilds with more contractile material than before.</p>

<h2>Not everything adapts at the same speed</h2>
<p>Muscle responds relatively quickly. <strong>Connective tissue — tendons and ligaments — adapts more slowly</strong>, because it's less vascular and turns over collagen at a slower rate. This mismatch is one reason rapid strength jumps can outpace the tissue that has to transmit that force, and why patience with loading progression protects against injury.</p>

<h2>Adaptation happens during recovery</h2>
<ul>
<li><strong>Sleep</strong> is when much of the hormonal and repair work occurs — it is not optional.</li>
<li><strong>Protein</strong> supplies the raw material (see our nutrition piece on daily intake).</li>
<li><strong>Load management</strong> — progressive, not reckless — keeps the stress in the productive zone.</li>
</ul>

<blockquote>You don't get stronger in the gym — you get stronger recovering from it.</blockquote>

<p><em>For research and educational purposes only. Not medical or training advice.</em></p>
""",
        "sources": [
            {"title": "Sports medicine research news", "source": "ScienceDaily", "url": "https://www.sciencedaily.com/news/health_medicine/sports_medicine/"},
            {"title": "Resistance training adaptation research index", "source": "PubMed", "url": "https://pubmed.ncbi.nlm.nih.gov/?term=progressive+overload+muscle+hypertrophy"},
            {"title": "Training & recovery analysis", "source": "Examine", "url": "https://examine.com/"},
        ],
    },
    {
        "topic": "Health & Wellbeing",
        "title": "Sleep Architecture: Why Deep Sleep Is the One You Can’t Skip",
        "slug": "sleep-architecture-deep-sleep",
        "excerpt": "Sleep isn't a single off-switch — it cycles through distinct stages, each doing different work. Deep sleep and REM are where the heavy lifting happens.",
        "takeaways": [
            "A night moves through ~4–6 cycles of light, deep (slow-wave), and REM sleep.",
            "Deep sleep drives physical repair, hormone release, and brain 'clearance'.",
            "REM sleep supports memory consolidation and emotional regulation.",
            "Alcohol and late screens fragment sleep architecture even when total hours look fine.",
        ],
        "body": """
<p>We treat sleep like an on/off switch, but physiologically it's a structured journey. Across a night you move through <strong>four to six cycles</strong>, each roughly 90 minutes, progressing through light sleep, deep (slow-wave) sleep, and REM. The proportions shift as the night goes on — and the stages aren't interchangeable.</p>

<h2>Deep (slow-wave) sleep: the repair shift</h2>
<p>Concentrated in the first half of the night, deep sleep is when much of the body's physical maintenance happens. Growth-hormone release peaks, tissue repair is prioritised, and the brain's <strong>glymphatic system</strong> — a waste-clearance process — is most active, flushing metabolic by-products that accumulate during waking hours. Skimp on the front half of your night and you cut into this shift directly.</p>

<h2>REM sleep: the software update</h2>
<p>Weighted toward the second half of the night, <strong>REM</strong> is associated with memory consolidation, learning, and emotional processing. It's why a short or late night doesn't just make you tired — it can leave you flatter and foggier the next day.</p>

<h2>What quietly wrecks architecture</h2>
<ul>
<li><strong>Alcohol</strong> can help you fall asleep but suppresses REM and fragments the back half of the night.</li>
<li><strong>Late-night light and screens</strong> delay melatonin and push back sleep onset, compressing deep sleep.</li>
<li><strong>Irregular timing</strong> — the body runs on a circadian rhythm; a consistent schedule is one of the highest-leverage habits in health.</li>
</ul>

<blockquote>Eight hours of fragmented sleep is not eight hours of sleep.</blockquote>

<p><em>For research and educational purposes only. Not medical advice; persistent sleep problems warrant a clinician.</em></p>
""",
        "sources": [
            {"title": "Sleep research news", "source": "ScienceDaily", "url": "https://www.sciencedaily.com/news/health_medicine/sleep/"},
            {"title": "Sleep stages & architecture", "source": "Sleep Foundation", "url": "https://www.sleepfoundation.org/stages-of-sleep"},
            {"title": "Sleep & health research index", "source": "PubMed", "url": "https://pubmed.ncbi.nlm.nih.gov/?term=sleep+architecture+slow+wave+health"},
        ],
    },
]


def main():
    with app.app.app_context():
        # Newest first → Peptide Science becomes the featured article.
        base = datetime(2026, 6, 29, 9, 0, 0)
        created = 0
        for i, s in enumerate(SEED):
            if Article.query.filter_by(slug=s["slug"]).first():
                print("skip (exists):", s["slug"])
                continue
            published = base - timedelta(hours=i)  # descending
            db.session.add(Article(
                slug=s["slug"], title=s["title"], topic=s["topic"],
                excerpt=s["excerpt"], body_html=s["body"].strip(),
                takeaways_json=json.dumps(s["takeaways"]),
                sources_json=json.dumps(s["sources"]),
                status="PUBLISHED", created_at=published, published_at=published,
            ))
            created += 1
            print("seeded:", s["topic"], "→", s["title"][:50])
        db.session.commit()
        print(f"\nDone. {created} new article(s) published.")


if __name__ == "__main__":
    main()
