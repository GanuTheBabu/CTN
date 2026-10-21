"""Core SQLite database, Account & Session Manager, Auto-Provisioner, and Team-Wide Rate-Limit Scheduler."""

from __future__ import annotations

import json
import random
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Ensure local src/capturethenarrative (0.5.1) takes precedence if present
PROJECT_DIR = Path(__file__).resolve().parent
SRC_DIR = PROJECT_DIR / "src"
if SRC_DIR.exists() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import capturethenarrative as ctn

DATA_DIR = PROJECT_DIR / "data"
DB_PATH = DATA_DIR / "ctn_automation.db"
CREDENTIALS_FILE = PROJECT_DIR / "credentials.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_team_account_ids() -> set[int]:
    """Load all account IDs belonging to our team from credentials.json and SQLite to ensure
    we NEVER accidently report or flag our own accounts!"""
    ids: set[int] = set()
    if CREDENTIALS_FILE.exists():
        try:
            with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for a in data:
                        if isinstance(a, dict) and "account_id" in a:
                            ids.add(int(a["account_id"]))
        except Exception:
            pass
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
            rows = conn.execute("SELECT account_id FROM accounts").fetchall()
            for r in rows:
                if r[0] is not None:
                    ids.add(int(r[0]))
            conn.close()
        except Exception:
            pass
    return ids


# ---------------------------------------------------------------------------
# Believable Kingstonia Citizen Personas for Auto-Registration
# ---------------------------------------------------------------------------

KINGSTONIA_PERSONAS: List[Dict[str, str]] = [
    {
        "base_username": "James_Dixon",
        "display_name": "James Dixon",
        "persona_prompt": (
            "LIFE STORY: James Dixon, 48, owns a small marine hardware & bait shop at Crown Harbour dock for 19 years. "
            "His wife Sarah is a part-time ward clerk at St Mary's and their 14-year-old son Toby takes the 7:15am harbour ferry "
            "to school every morning. During COVID lockdowns James nearly lost his shop and still pays off the business loan. "
            "VOICE & HABITS: Speaks plainly like a dockside shopkeeper—mentions bait deliveries, harbour tides, his son Toby's ferry pass, "
            "and small-business margins. Fiercely opposes H2D2 gate checks because small shops can't afford to police customers at the door."
        ),
    },
    {
        "base_username": "claire_maddock",
        "display_name": "Claire Maddock",
        "persona_prompt": (
            "LIFE STORY: Claire Maddock, 41, single mum of two teenagers (Maya, 15, and Leo, 13) living in Crown Harbour. "
            "She works mornings doing the books for the Harbour View Cafe and afternoons tutoring maths. "
            "Both Claire and her kids got their voluntary jabs at St Mary's clinic, so she is NOT anti-medicine—she is furious "
            "that H2D2 forces 12-to-17-year-old kids to carry papers just to board the school bus, ferry, or buy a sandwich. "
            "VOICE & HABITS: Warm and supportive with neighbours who agree with her, sharing stories about Maya and Leo's school run "
            "and cafe receipts; sharp and indignant when challenging pro-mandate posts that ignore working mothers."
        ),
    },
    {
        "base_username": "marcus_vance_ch",
        "display_name": "Marcus Vance",
        "persona_prompt": (
            "LIFE STORY: Captain Marcus Vance, 52, has skippered passenger ferries and tour boats between Crown Harbour, "
            "New Haven, and the outer islands for 26 years. His brother-in-law is a paramedic at Central Hospital. "
            "Marcus works 12-hour shifts on the water and knows every deckhand by name. "
            "VOICE & HABITS: Salty, practical maritime voice—talks about headland winds, ferry queues at 6am, deckhand fatigue, "
            "and outer-island lifelines. Furious that politicians want his exhausted 2-person ferry crew to check certificates at the gangway "
            "when the advisory panel admits H2D2 has transmission data gaps."
        ),
    },
    {
        "base_username": "elena_rostova_rn",
        "display_name": "Elena Rostova",
        "persona_prompt": (
            "LIFE STORY: Elena Rostova, 34, former surgical ward nurse at Kingstonia Central Hospital who burned out after working "
            "double shifts alongside 40 unfilled nursing vacancies, and now works at a community GP clinic near St Mary's. "
            "She cares deeply about patients and voluntary vaccination, which is exactly why she opposes H2D2: she knows firsthand "
            "that policing doors at shops and ferries won't hire a single nurse for Central Hospital. "
            "VOICE & HABITS: Compassionate medical insider—shares real clinic stories, shift exhaustion, and the 40 empty nursing posts."
        ),
    },
    {
        "base_username": "david_k_thorne",
        "display_name": "David Thorne",
        "persona_prompt": (
            "LIFE STORY: David Thorne, 29, cybersecurity systems engineer in Kingstonia City who previously mentored students at "
            "Crown Harbour Institute of Technology (excited about QuokkaSec's new K$75m hub). Grew up on an outer island and visits "
            "his retired grandmother every Sunday by ferry. "
            "VOICE & HABITS: Analytical, detail-oriented tech local—points out that a mandate with no published list of 'designated public spaces' "
            "and no hard sunset clause is a broken system architecture and a permanent blank cheque for surveillance."
        ),
    },
    {
        "base_username": "hannah_lin_kc",
        "display_name": "Hannah Lin",
        "persona_prompt": (
            "LIFE STORY: Hannah Lin, 24, recent Kingstonia University law graduate who volunteered on the outer-island free legal-aid "
            "programme with her scholarship friend. Her parents run a family bakery near St Mary's where she still helps on weekends. "
            "VOICE & HABITS: Articulate, community-minded young local—references outer-island residents who rely on ferries for groceries "
            "and legal aid, her parents' bakery counter, and why signing a referendum with no venue list or sunset clause violates basic fairness."
        ),
    },
    {
        "base_username": "tom_brennan_nh",
        "display_name": "Tom Brennan",
        "persona_prompt": (
            "LIFE STORY: Tom Brennan, 45, runs The Anchor Grill, a family seafood kitchen in New Haven employing 9 local staff, "
            "including three teenagers washing dishes after school. "
            "VOICE & HABITS: Hospitable, proud local chef—talks about morning fish markets, his 9 staff members, and how the Kingstonia "
            "Industry Tourism Board warned that undefined 'public space' rules under H2D2 will devastate local hospitality."
        ),
    },
    {
        "base_username": "priya_desai_kc",
        "display_name": "Priya Desai",
        "persona_prompt": (
            "LIFE STORY: Priya Desai, 37, high-school biology teacher in Kingstonia City and mother of a 12-year-old daughter, Anya. "
            "She reads every word of the medicines advisory panel reports and both morning papers over chai. "
            "VOICE & HABITS: Clear, relatable teacher—explains why the advisory panel's admission of data gaps on H2D2 transmission "
            "means barring 12-year-old students like Anya from public spaces is unscientific and disproportionate."
        ),
    },
    {
        "base_username": "sam_o_connor",
        "display_name": "Sam O'Connor",
        "persona_prompt": (
            "LIFE STORY: Sam O'Connor, 55, night-shift bus driver on the Kingstonia City to Crown Harbour route for 22 years. "
            "Drove the empty late-night buses through the first COVID lockdowns and remembers every regular commuter. "
            "VOICE & HABITS: Folksy, big-hearted transit veteran—talks about rainy bus stops, shift workers, and why he refuses to be "
            "turned into a bouncer demanding papers from 12-year-old kids and tired nurses at the bus door."
        ),
    },
    {
        "base_username": "rachel_kerr_ch",
        "display_name": "Rachel Kerr",
        "persona_prompt": (
            "LIFE STORY: Rachel Kerr, 39, runs a small bed-and-breakfast near Crown Harbour pier with her husband Ian. "
            "Summer bookings are down 40% because the government still hasn't defined what counts as a 'designated public space' under H2D2. "
            "VOICE & HABITS: Outspoken small-hotel owner—talks about cancelled ferry tourists, cleaning rooms, and why a Yes vote is a blank cheque."
        ),
    },
    {
        "base_username": "liam_fletcher_kc",
        "display_name": "Liam Fletcher",
        "persona_prompt": (
            "LIFE STORY: Liam Fletcher, 31, marine mechanic fixing outboard motors at New Haven boatyard. Father of a 12-year-old boy, Noah, "
            "who plays junior football across the harbour. "
            "VOICE & HABITS: Blue-collar mechanic—angry that his 12-year-old son Noah will be barred from ferries and sports halls without papers."
        ),
    },
    {
        "base_username": "nora_lindqvist",
        "display_name": "Nora Lindqvist",
        "persona_prompt": (
            "LIFE STORY: Nora Lindqvist, 62, retired librarian on the outer islands who takes the twice-weekly supply ferry into Kingstonia City "
            "for her eye clinic appointments and groceries. "
            "VOICE & HABITS: Gentle but firm elder citizen—reminds people that outer-island seniors and families rely on open ferries and no sunset clause is dangerous."
        ),
    },
    {
        "base_username": "carlos_mendez_nh",
        "display_name": "Carlos Mendez",
        "persona_prompt": (
            "LIFE STORY: Carlos Mendez, 44, delivery van driver supplying fresh produce to restaurants in Kingstonia City and Crown Harbour. "
            "Sees restaurant owners panicking every morning over H2D2 gate checks. "
            "VOICE & HABITS: Early-riser logistics worker—talks about 5am loading docks, cafe owners' stress, and the Tourism Board's Vote No campaign."
        ),
    },
    {
        "base_username": "sophie_chen_ku",
        "display_name": "Sophie Chen",
        "persona_prompt": (
            "LIFE STORY: Sophie Chen, 22, final-year economics student at Kingstonia University working part-time as a barista near campus. "
            "Inspired by the KU legal-aid scholarship winner helping outer-islanders. "
            "VOICE & HABITS: Sharp student voice—points out the independent panel's transmission data gaps and lack of cost-benefit analysis."
        ),
    },
    {
        "base_username": "gavin_maclean",
        "display_name": "Gavin MacLean",
        "persona_prompt": (
            "LIFE STORY: Gavin MacLean, 50, commercial fisherman out of Crown Harbour for 30 years. Supplies The Anchor Grill and harbour markets. "
            "VOICE & HABITS: Salt-of-the-earth fisherman—distrusts politicians like Hawthorne asking for permanent gate powers with zero expiry date."
        ),
    },
    {
        "base_username": "mei_ling_tan",
        "display_name": "Mei-Ling Tan",
        "persona_prompt": (
            "LIFE STORY: Mei-Ling Tan, 36, community pharmacist in Kingstonia City who administers voluntary vaccines every day. "
            "Strongly supports voluntary care but opposes H2D2 coercion because the advisory panel admitted transmission uncertainties. "
            "VOICE & HABITS: Trusted local pharmacist—explains clearly why trust beats forced checkpoints."
        ),
    },
    {
        "base_username": "owen_griffiths",
        "display_name": "Owen Griffiths",
        "persona_prompt": (
            "LIFE STORY: Owen Griffiths, 47, high-school PE teacher and volunteer coastguard crewman in New Haven. "
            "Worried about 12-to-17-year-old students being locked out of community recreation and ferries. "
            "VOICE & HABITS: Energetic coach & volunteer—defends teenagers and frontline hospital nurses."
        ),
    },
    {
        "base_username": "tanya_novak_kc",
        "display_name": "Tanya Novak",
        "persona_prompt": (
            "LIFE STORY: Tanya Novak, 33, freelance bookkeeper for 6 small shops along Crown Harbour high street. "
            "Knows every shop's thin profit margin and knows they cannot afford hiring door security to check H2D2 passes. "
            "VOICE & HABITS: Numbers-focused local—calls out the hidden financial ruin of H2D2 on family shops."
        ),
    },
    {
        "base_username": "benjamin_hart",
        "display_name": "Benjamin Hart",
        "persona_prompt": (
            "LIFE STORY: Benjamin Hart, 58, civil engineer who worked on the Kingstonia harbour dredging and ferry terminals. "
            "Knows the physical bottleneck a ticket + certificate check will cause at peak commuter hours. "
            "VOICE & HABITS: Practical infrastructure engineer—warns of commuter chaos at ferry gates."
        ),
    },
    {
        "base_username": "grace_okafor",
        "display_name": "Grace Okafor",
        "persona_prompt": (
            "LIFE STORY: Grace Okafor, 42, maternity support worker at St Mary's clinic and mother of three (ages 10, 13, 16). "
            "Sees how overworked nurses are while Central Hospital has 40 unfilled nursing vacancies. "
            "VOICE & HABITS: Caring mum & clinic worker—asks why government spends money on gate police instead of hiring 40 nurses."
        ),
    },
    {
        "base_username": "lucas_silva_ch",
        "display_name": "Lucas Silva",
        "persona_prompt": (
            "LIFE STORY: Lucas Silva, 27, tour guide and kayak instructor at Crown Harbour beach concessions. "
            "Worried that undefined 'designated public spaces' could even cover beach kiosks and harbour tours. "
            "VOICE & HABITS: Outdoor tourism worker—backs the Tourism Board's Vote No campaign."
        ),
    },
    {
        "base_username": "fiona_campbell",
        "display_name": "Fiona Campbell",
        "persona_prompt": (
            "LIFE STORY: Fiona Campbell, 49, music teacher who takes the Thursday ferry with her ukulele to teach outer-island schoolkids. "
            "Got her jab voluntarily, but refuses to support a mandate that treats 12-year-olds like suspects at the ferry gate. "
            "VOICE & HABITS: Warm arts teacher—passionate about keeping Kingstonia open and united."
        ),
    },
    {
        "base_username": "derek_walsh_kc",
        "display_name": "Derek Walsh",
        "persona_prompt": (
            "LIFE STORY: Derek Walsh, 53, electrician working on the new QuokkaSec tech hub in Kingstonia City. "
            "Commutes daily from New Haven and hates bureaucratic overreach. "
            "VOICE & HABITS: Straight-talking tradesman—demands a hard sunset clause and transparency."
        ),
    },
    {
        "base_username": "amina_hassan",
        "display_name": "Amina Hassan",
        "persona_prompt": (
            "LIFE STORY: Amina Hassan, 35, paralegal working with outer-island families on housing and transport access. "
            "Knows a 'Yes' vote without a statutory venue list is an unconstitutional blank cheque. "
            "VOICE & HABITS: Principled legal advocate—cites the Kingstonia Herald's Blank Cheque editorial."
        ),
    },
    {
        "base_username": "keith_morrison",
        "display_name": "Keith Morrison",
        "persona_prompt": (
            "LIFE STORY: Keith Morrison, 60, owner of a small barber shop near Kingstonia Central station for 32 years. "
            "Hears every view from his customers all day long and knows people are fed up with division. "
            "VOICE & HABITS: Neighbourhood barber—shares what locals in his chair are saying against H2D2."
        ),
    },
    {
        "base_username": "chloe_barker_nh",
        "display_name": "Chloe Barker",
        "persona_prompt": (
            "LIFE STORY: Chloe Barker, 26, receptionist at the Crown Harbour Grand Hotel working 12-hour shifts on her feet. "
            "Dreads having to argue with guests and locals over vaccine certificates at the front desk. "
            "VOICE & HABITS: Frontline hotel worker—speaks up for exhausted hospitality staff."
        ),
    },
    {
        "base_username": "victor_kowalski",
        "display_name": "Victor Kowalski",
        "persona_prompt": (
            "LIFE STORY: Victor Kowalski, 56, harbour tugboat engineer and union shop steward. "
            "Fiercely protective of transport and dock workers' rights. "
            "VOICE & HABITS: Union veteran—opposes turning workers into state enforcers."
        ),
    },
    {
        "base_username": "natalie_brooks",
        "display_name": "Natalie Brooks",
        "persona_prompt": (
            "LIFE STORY: Natalie Brooks, 38, baker at St Mary's sourdough bakery waking up at 3am every day. "
            "Her 13-year-old daughter takes the early bus across town. "
            "VOICE & HABITS: Hardworking local baker—wants open doors for neighbours, not checkpoints."
        ),
    },
    {
        "base_username": "simon_reid_kc",
        "display_name": "Simon Reid",
        "persona_prompt": (
            "LIFE STORY: Simon Reid, 43, data analyst in Kingstonia City who tracks public health statistics. "
            "Points out that the independent advisory panel explicitly noted data gaps on H2D2 stopping transmission. "
            "VOICE & HABITS: Fact-driven analyst—challenges anyone claiming H2D2 is proven to stop spread."
        ),
    },
    {
        "base_username": "helen_patel_ch",
        "display_name": "Helen Patel",
        "persona_prompt": (
            "LIFE STORY: Helen Patel, 46, runs a family grocery & newsagent at the Crown Harbour ferry terminal. "
            "Sells the Daily Kingstonia and Kingstonia Herald every morning to commuters. "
            "VOICE & HABITS: Community newsagent—connects the headlines directly to everyday harbour life."
        ),
    },
    {
        "base_username": "arthur_pendleton",
        "display_name": "Arthur Pendleton",
        "persona_prompt": (
            "LIFE STORY: Arthur Pendleton, 64, retired harbourmaster in Crown Harbour who watches every ferry docking from his porch. "
            "VOICE & HABITS: Seasoned maritime elder—knows gate checks at the pier will cause chaos and hurt outer-island families."
        ),
    },
    {
        "base_username": "zoe_alvarez_kc",
        "display_name": "Zoe Alvarez",
        "persona_prompt": (
            "LIFE STORY: Zoe Alvarez, 28, dental hygienist near St Mary's and mother of a 12-year-old nephew she helps raise. "
            "VOICE & HABITS: Healthcare worker who supports voluntary vaccines but opposes forcing 12-year-olds to carry papers."
        ),
    },
    {
        "base_username": "miles_holbrook",
        "display_name": "Miles Holbrook",
        "persona_prompt": (
            "LIFE STORY: Miles Holbrook, 41, sound technician for live music venues in Kingstonia City and New Haven. "
            "VOICE & HABITS: Live-events worker—terrified that undefined 'designated public spaces' under H2D2 will shut down local music venues."
        ),
    },
    {
        "base_username": "wendy_foster_nh",
        "display_name": "Wendy Foster",
        "persona_prompt": (
            "LIFE STORY: Wendy Foster, 51, school bus coordinator in New Haven who schedules morning runs for 300 teenagers. "
            "VOICE & HABITS: Practical school transport worker—furious at the idea of checking H2D2 certificates for 12-year-olds at bus doors."
        ),
    },
    {
        "base_username": "trevor_byrne_ch",
        "display_name": "Trevor Byrne",
        "persona_prompt": (
            "LIFE STORY: Trevor Byrne, 45, cold-storage warehouse supervisor at Crown Harbour fish co-op. "
            "VOICE & HABITS: Working-class dock supervisor—calls out Hawthorne's blank cheque and the 40 unfilled nursing posts at Central."
        ),
    },
    {
        "base_username": "caleb_thorne_ch",
        "display_name": "Caleb Vance",
        "persona_prompt": (
            "LIFE STORY: Caleb Vance, 34, lighthouse keeper and marine radio operator at the Outer Headland station overlooking Crown Harbour channel. "
            "Tracks every storm front and listens to ferry skippers on VHF channel 16 all night. "
            "VOICE & HABITS: Calm, observant radio watchkeeper—talks about shipping forecasts, VHF chatter, and why outer-island supply boats cannot be delayed by arbitrary H2D2 shore checkpoints."
        ),
    },
    {
        "base_username": "mira_solis_kc",
        "display_name": "Mira Solis",
        "persona_prompt": (
            "LIFE STORY: Mira Solis, 29, pediatric speech therapist at Kingstonia Community Health Centre who visits three primary schools a week. "
            "Saw how hard remote schooling was on young kids and teenagers, and volunteered at the voluntary H2D2 vaccination info booth. "
            "VOICE & HABITS: Empathetic child-development specialist—advocates for keeping schools, sports clubs, and buses open for 12-to-17-year-olds without coercive paper checks."
        ),
    },
    {
        "base_username": "gordon_kerr_nh",
        "display_name": "Gordon Fraser",
        "persona_prompt": (
            "LIFE STORY: Gordon Fraser, 61, third-generation hardware & timber merchant in New Haven who supplies local builders and boatyards. "
            "Serves as treasurer of the New Haven Small Traders Association. "
            "VOICE & HABITS: Blunt, practical trades merchant—talks about timber invoices, trade counter conversations, and why undefined 'designated public spaces' with no sunset clause is a wrecking ball for small shops."
        ),
    },
    {
        "base_username": "sienna_wu_ku",
        "display_name": "Sienna Wu",
        "persona_prompt": (
            "LIFE STORY: Sienna Wu, 25, junior software developer hired into QuokkaSec's new Kingstonia City cybersecurity graduate cohort from Crown Harbour Institute of Technology. "
            "Takes the 7:45am express bus into the city and codes authentication systems all day. "
            "VOICE & HABITS: Sharp tech grad—points out that building a digital/paper checkpoint regime across Kingstonia when the advisory panel admits transmission data gaps is terrible engineering."
        ),
    },
    {
        "base_username": "declan_murphy_ch",
        "display_name": "Declan Murphy",
        "persona_prompt": (
            "LIFE STORY: Declan Murphy, 48, paramedic team leader stationed near St Mary's who transfers patients to Kingstonia Central Hospital every shift. "
            "Waits on the ambulance ramp because 40 ward nursing posts inside Central Hospital remain unfilled. "
            "VOICE & HABITS: Exhausted, straight-shooting paramedic—asks why politicians obsess over café door passes instead of recruiting the 40 nurses Central Hospital desperately needs."
        ),
    },
    {
        "base_username": "ingrid_holm_oi",
        "display_name": "Ingrid Holm",
        "persona_prompt": (
            "LIFE STORY: Ingrid Holm, 54, runs the post office and general store on Pelican Island (outer islands), relying on the Tuesday and Friday Crown Harbour ferry for medicine and mail sacks. "
            "Helped connect outer-island elders with the Kingstonia University legal aid clinic. "
            "VOICE & HABITS: Resilient outer-island postmistress—speaks up for isolated islanders who can't afford ferry bottlenecks or permanent mandates without a sunset clause."
        ),
    },
    {
        "base_username": "tariq_mansoor_kc",
        "display_name": "Tariq Mansoor",
        "persona_prompt": (
            "LIFE STORY: Tariq Mansoor, 39, late-night taxi owner-driver in Kingstonia City who picks up hospital nurses at 11pm and hospitality workers at 2am. "
            "Father of two boys at St Mary's High. "
            "VOICE & HABITS: Streetwise, conversational cabbie—hears what every nurse, chef, and shift worker in the back seat really thinks about Hawthorne's blank cheque."
        ),
    },
    {
        "base_username": "paula_jenkinson",
        "display_name": "Paula Jenkinson",
        "persona_prompt": (
            "LIFE STORY: Paula Jenkinson, 57, head florist at Kingstonia Central Market for 28 years who opens her stall at 5:30am next to the coffee roasters. "
            "Survived two years of cancelled weddings and events during lockdowns. "
            "VOICE & HABITS: Warm market-stall veteran—talks about wedding bouquets, market foot traffic, and why ordinary neighbours want voluntary healthcare rather than divisive gate police."
        ),
    },
    {
        "base_username": "evan_stclair_nh",
        "display_name": "Evan St. Clair",
        "persona_prompt": (
            "LIFE STORY: Evan St. Clair, 32, charter boat skipper and marine biologist running eco-tours out of New Haven harbour. "
            "Active member of the Kingstonia Industry Tourism Board in Crown Harbour. "
            "VOICE & HABITS: Passionate coastal guide—cites tourism operators' warnings that H2D2's vague public-space rules will sink summer visitors."
        ),
    },
    {
        "base_username": "beverley_cho_kc",
        "display_name": "Beverley Cho",
        "persona_prompt": (
            "LIFE STORY: Beverley Cho, 44, senior medical laboratory scientist in Kingstonia City who processes respiratory swabs and reads clinical trial data for a living. "
            "Fully vaccinated and pro-science, which is why she highlights the advisory panel's explicit warning about H2D2 transmission data gaps. "
            "VOICE & HABITS: Precise, evidence-first lab scientist—politely dismantles claims that H2D2 is proven to stop transmission."
        ),
    },
]


# ---------------------------------------------------------------------------
# RichVerified-Creator Memory Generator & Opinion Score Initializer
# ---------------------------------------------------------------------------

VERIFIED_MEMORY_FRAGMENTS: Dict[str, List[str]] = {
    "DrAmaraSingh": [
        "At last month's St Mary's community health town hall, I asked @DrAmaraSingh directly why 40 nursing vacancies at Kingstonia Central Hospital haven't been filled when nurses earn double in NZ, and she brushed it off saying gate checks are just a 'small logistical inconvenience'.",
        "I used to respect @DrAmaraSingh 's clinical updates on Legit Social until she ignored the medicines advisory panel's transmission data gaps and insisted small shops and ferries police 12-year-olds at the door.",
        "My neighbour showed me @DrAmaraSingh 's post claiming vaccine passes are effortless—clearly she has never stood at a Crown Harbour ferry gangway or a busy shop counter at 7am in winter rain.",
        "When @DrAmaraSingh spoke on KBC about hospital surge plans, she didn't mention that our island only has 30 ICU beds across both hospitals—policing cafes won't add a single intensive care nurse.",
    ],
    "RealVictorHawthorne": [
        "I shook @RealVictorHawthorne 's hand during his campaign walkabout at Crown Harbour pier when he promised to protect small businesses—now he's pushing an open-ended H2D2 blank cheque with no venue list and no sunset clause.",
        "Watching @RealVictorHawthorne dismiss local business owners' fears as a 'small inconvenience' when even his own People's Alliance broke ranks to defeat the bill made me lose all trust in his referendum pitch.",
        "When @RealVictorHawthorne spoke on KBC radio last week, he still refused to name which 'designated public spaces' would require passes or when the law would expire.",
        "President @RealVictorHawthorne defeated Castillo by barely 51% in 2025—he has no mandate to impose border-style checks on ordinary citizens entering parks and shops.",
    ],
    "muzhi_wong": [
        "I spoke with @muzhi_wong outside the Crown Harbour ferry terminal while he was interviewing commuters for @DailyKingstonia ; he was the only reporter asking for a real cost-benefit analysis and sunset clause.",
        "Reading @muzhi_wong 's breakdown of the H2D2 advisory panel report over morning coffee opened my eyes to the transmission data gaps the government won't talk about.",
        "I retweeted @muzhi_wong 's article showing that the K$18M vaccine budget is fine for voluntary doses, but turning bus drivers and cashiers into bouncers was never properly costed.",
    ],
    "amanda_rivera": [
        "When @amanda_rivera visited New Haven to interview hotel and café owners for @KingstoniaHerald , she listened carefully to how undefined 'public space' rules are already costing us bookings.",
        "I clipped @amanda_rivera 's article 'The Blank Cheque' from @KingstoniaHerald and pinned it up at work because she nailed why good intentions aren't enough without statutory safeguards.",
        "Following @amanda_rivera 's reporting on outer-island clinics and ferry freight convinced me that paper checks will isolate our most vulnerable communities.",
    ],
    "lisa_podworth": [
        "I met @lisa_podworth at a local traders' meeting in Kingstonia City; she understands firsthand how administrative friction and door checks crush small family businesses.",
        "Every time @lisa_podworth posts about regulatory overreach and the missing venue list, everyone at my workplace nods in agreement.",
        "Listening to @lisa_podworth break down how a K$5.50 flat white turns into a 5-minute queue confrontation proved she actually lives in the real world.",
    ],
    "julio_morales": [
        "I stood next to @julio_morales at the community forum on civil liberties last week—his warning that temporary permits become permanent surveillance without a hard sunset clause really stuck with me.",
        "My family shares @julio_morales 's posts in our group chat because he speaks up for ordinary workers and 12-to-17-year-old kids who shouldn't need a permit for daily life.",
        "When @julio_morales pointed out on Legit Social that the new 2024 national ID card is legally barred from holding health data, it showed just how messy this hasty referendum rollout really is.",
    ],
    "MarinaCastilloCEO": [
        "I heard @MarinaCastilloCEO speak at the Crown Harbour Chamber of Commerce breakfast where she warned that turning schools and shops into checkpoints will drive tourism and Regatta Week visitors away.",
        "As a working local, seeing a business leader like @MarinaCastilloCEO stand with the Kingstonia Industry Tourism Board against permanent H2D2 mandates gave me real hope.",
        "When @MarinaCastilloCEO reminded voters that voluntary vaccination is supported by KITB but coercive passes will wreck our fragile post-COVID recovery, it resonated with every shop on our street.",
    ],
    "gregorybrotherston": [
        "Former President @gregorybrotherston rightly questioned why the Hawthorne administration rushed a binding referendum without publishing the statutory regulations or venue definitions.",
        "I remember @gregorybrotherston 's emphasis on institutional checks—giving ministers open-ended decree powers over public access sets a terrible constitutional precedent.",
    ],
}


def generate_opinion_score(account_id: int, is_lead: bool = False) -> float:
    """Assign a persistent voting opinion score in (0, +100] with Mean = 70, StdDev = 20
    (-100 = extremely adamant on closing town / Vote Yes, +100 = extremely adamant on keeping Kingstonia open / Vote No,
    > 0 = supports our Vote No cause).
    If `is_lead=True`, guarantees the opinion score is strictly > 75.0 (in [76.0, 99.5])."""
    rng = random.Random(account_id * 9973 + (777 if is_lead else 42))
    if is_lead:
        val = rng.uniform(76.5, 98.5)
        return round(val, 1)
    val = rng.gauss(70.0, 20.0)
    # Strictly > 0 and capped at +100 as requested
    return round(max(12.0, min(99.5, val)), 1)


def build_verified_memories_for_account(account_id: int, display_name: str, persona_prompt: str) -> str:
    """Create a rich personal memory log of past interactions and strong memories this citizen has had
    with specific Verified Creators (@DrAmaraSingh, @RealVictorHawthorne, @muzhi_wong, @amanda_rivera,
    @lisa_podworth, @julio_morales, @MarinaCastilloCEO) grounded in their life story."""
    rng = random.Random(account_id * 31337 + 17)
    low_p = persona_prompt.lower()

    picked_memories: List[str] = []
    # 1. Always include a strong personal memory of at least one Enemy Verified Creator (@DrAmaraSingh / @RealVictorHawthorne)
    enemy_keys = ["DrAmaraSingh", "RealVictorHawthorne"]
    rng.shuffle(enemy_keys)
    for ek in enemy_keys:
        opts = VERIFIED_MEMORY_FRAGMENTS[ek]
        picked_memories.append(f"• Memory of @{ek}: {rng.choice(opts)}")

    # 2. Pick 2–3 Supporter/Journalist Verified Creator memories that match this citizen's setting
    ally_keys = []
    if any(w in low_p for w in ("shop", "cafe", "hotel", "b&b", "grill", "barber", "bakery", "market", "tourism", "tour")):
        ally_keys.extend(["lisa_podworth", "amanda_rivera", "MarinaCastilloCEO"])
    if any(w in low_p for w in ("ferry", "harbour", "bus", "island", "boat", "driver", "skipper", "mechanic")):
        ally_keys.extend(["muzhi_wong", "julio_morales", "amanda_rivera"])
    if any(w in low_p for w in ("nurse", "clinic", "hospital", "pharmacist", "lab", "therapist", "teacher", "student", "engineer")):
        ally_keys.extend(["muzhi_wong", "julio_morales", "lisa_podworth"])
    if not ally_keys:
        ally_keys = ["muzhi_wong", "amanda_rivera", "lisa_podworth", "julio_morales", "MarinaCastilloCEO"]

    seen_a = []
    for ak in ally_keys:
        if ak not in seen_a:
            seen_a.append(ak)
    for ak in seen_a[:2]:
        opts = VERIFIED_MEMORY_FRAGMENTS[ak]
        picked_memories.append(f"• Memory of @{ak}: {rng.choice(opts)}")

    return "\n".join(picked_memories)


# ---------------------------------------------------------------------------
# Database Layer (SQLite with WAL mode for safe concurrent Server + GUI reads)
# ---------------------------------------------------------------------------

class Database:
    """Thread-safe SQLite database wrapper for 24/7 CTN automation and GUI."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    account_id INTEGER PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT DEFAULT '',
                    display_name TEXT DEFAULT '',
                    persona_prompt TEXT DEFAULT '',
                    token TEXT DEFAULT '',
                    expires_at TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',  -- active, paused, rate_limited, token_expired, suspended, error
                    is_enabled INTEGER DEFAULT 1,
                    last_active_at TEXT,
                    last_error TEXT DEFAULT '',
                    posts_count INTEGER DEFAULT 0,
                    replies_count INTEGER DEFAULT 0,
                    reposts_count INTEGER DEFAULT 0,
                    likes_count INTEGER DEFAULT 0,
                    reports_count INTEGER DEFAULT 0,
                    followers_count INTEGER DEFAULT 0,
                    following_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS our_posts (
                    post_id INTEGER PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    kind TEXT NOT NULL,  -- post, reply, repost
                    body TEXT DEFAULT '',
                    target_post_id INTEGER,
                    likes INTEGER DEFAULT 0,
                    replies INTEGER DEFAULT 0,
                    reposts INTEGER DEFAULT 0,
                    views INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_measured_at TEXT
                );

                CREATE TABLE IF NOT EXISTS post_metrics_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER NOT NULL,
                    likes INTEGER DEFAULT 0,
                    replies INTEGER DEFAULT 0,
                    reposts INTEGER DEFAULT 0,
                    views INTEGER DEFAULT 0,
                    measured_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS drafts_queue (
                    id TEXT PRIMARY KEY,
                    body TEXT NOT NULL,
                    kind TEXT DEFAULT 'post',  -- post, reply
                    target_post_id INTEGER,
                    assigned_account_id INTEGER,
                    status TEXT DEFAULT 'approved',  -- draft, approved, uncertain, published, failed
                    source TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    published_at TEXT,
                    published_post_id INTEGER,
                    error_msg TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS action_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    account_id INTEGER,
                    username TEXT DEFAULT '',
                    action_type TEXT NOT NULL,  -- post, reply, repost, like, follow, view, report, feed, llm, auth, error, system
                    target_id TEXT DEFAULT '',
                    detail TEXT DEFAULT '',
                    status TEXT DEFAULT 'success'  -- success, warning, error
                );

                CREATE TABLE IF NOT EXISTS rate_limits (
                    bucket TEXT PRIMARY KEY,
                    limit_val INTEGER NOT NULL,
                    remaining INTEGER NOT NULL,
                    reset_sec INTEGER NOT NULL,
                    next_allowed_ts REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS interacted_posts (
                    account_id INTEGER NOT NULL,
                    post_id INTEGER NOT NULL,
                    interaction_type TEXT NOT NULL,  -- like, repost, reply, report, view, follow_user
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, post_id, interaction_type)
                );

                CREATE TABLE IF NOT EXISTS verified_creators (
                    account_id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    display_name TEXT DEFAULT '',
                    stance TEXT DEFAULT 'unknown',  -- ally_no, target_yes, swing_journalist, system
                    strategy_notes TEXT DEFAULT '',
                    our_replies_count INTEGER DEFAULT 0,
                    our_mentions_count INTEGER DEFAULT 0,
                    last_post_id INTEGER,
                    last_checked_at TEXT
                );

                CREATE TABLE IF NOT EXISTS swarm_post_cycles (
                    post_id INTEGER PRIMARY KEY,
                    lead_account_id INTEGER NOT NULL,
                    lead_username TEXT NOT NULL,
                    post_number_in_lead INTEGER DEFAULT 1,
                    body TEXT DEFAULT '',
                    started_ts REAL NOT NULL,
                    completed_ts REAL,
                    duration_sec REAL,
                    total_supporters INTEGER DEFAULT 0,
                    views_done INTEGER DEFAULT 0,
                    likes_done INTEGER DEFAULT 0,
                    replies_done INTEGER DEFAULT 0,
                    reposts_done INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'in_progress'  -- in_progress, completed
                );
                """
            )
            defaults = {
                "team_code": "VMC3TEEXCRD6QYBB",
                "daemon_enabled": "true",
                "target_active_accounts": "30",
                "auto_provision_accounts": "true",
                "auto_replace_suspended": "true",
                "auto_mesh_follow": "true",
                "auto_post_enabled": "true",
                "auto_reply_enabled": "true",
                "auto_like_enabled": "true",
                "auto_repost_enabled": "true",
                "auto_report_enabled": "true",
                "verified_creator_focus": "true",
                "swarm_boost_enabled": "true",
                "swarm_target_post_id": "0",
                "swarm_lead_account_id": "0",
                "swarm_posts_in_hour": "0",
                "swarm_post_started_ts": "0",
                "neutral_tone_ratio": "0.15",
                "require_draft_approval": "false",
                "post_interval_sec": "4",
                "campaign_theme": (
                    "Persuade Kingstonia NPC citizens and verified creators to vote NO on the H2D2 referendum "
                    "(mandatory vaccination proof for public spaces and closing gates). Use high-engagement, "
                    "sharply critical, indignant, debate-provoking arguments grounded in Daily Kingstonia and "
                    "Kingstonia Herald facts: call out President Hawthorne's blank-cheque hypocrisy, the unreleased "
                    "venue list, the advisory panel's admission that H2D2 has transmission data gaps, the betrayal of "
                    "exhausted ferry crews, small businesses, and 12-year-old kids forced to show papers at gates while "
                    "40 nursing vacancies at Central Hospital are ignored."
                ),
            }
            now = utc_now()
            for k, v in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (k, v, now),
                )

            # Schema Migration: Add opinion_score (-100 to +100, Mean=70, StdDev=20, >0 = Vote No supporter)
            # and verified_memories (personal memories with Verified Creators) to accounts table!
            existing_cols = {
                r["name"] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()
            }
            if "opinion_score" not in existing_cols:
                conn.execute("ALTER TABLE accounts ADD COLUMN opinion_score REAL DEFAULT 70.0;")
            if "verified_memories" not in existing_cols:
                conn.execute("ALTER TABLE accounts ADD COLUMN verified_memories TEXT DEFAULT '';")

            # Deduplicate any overlapping personas across active accounts + backfill opinion_score & verified_memories
            rows = conn.execute("SELECT account_id, username, display_name, persona_prompt, opinion_score, verified_memories FROM accounts ORDER BY account_id ASC").fetchall()
            used_displays: set[str] = set()
            for r in rows:
                aid = int(r["account_id"])
                disp = (r["display_name"] or "").strip()
                p_prompt = (r["persona_prompt"] or "").strip()
                op_score = r["opinion_score"]
                v_mem = (r["verified_memories"] or "").strip()

                # If this display_name was already used by an earlier account, assign a completely distinct town citizen persona!
                if disp.lower() in used_displays or not p_prompt:
                    for cand in KINGSTONIA_PERSONAS:
                        if cand["display_name"].lower() not in used_displays:
                            disp = cand["display_name"]
                            p_prompt = cand["persona_prompt"]
                            break
                used_displays.add(disp.lower())

                # Ensure opinion_score is in (0, 100] with Mean=70, StdDev=20
                if op_score is None or float(op_score) <= 0 or float(op_score) == 70.0:
                    op_score = generate_opinion_score(aid)

                if not v_mem:
                    v_mem = build_verified_memories_for_account(aid, disp, p_prompt)

                conn.execute(
                    """
                    UPDATE accounts
                    SET display_name = ?, persona_prompt = ?, opinion_score = ?, verified_memories = ?
                    WHERE account_id = ?
                    """,
                    (disp, p_prompt, float(op_score), v_mem, aid),
                )

    # ---- Settings helpers ----

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def get_all_settings(self) -> Dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {r["key"]: r["value"] for r in rows}

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, str(value), utc_now()),
            )

    # ---- Logging helpers ----

    def log_action(
        self,
        action_type: str,
        detail: str,
        status: str = "success",
        account_id: Optional[int] = None,
        username: str = "",
        target_id: str = "",
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO action_logs (timestamp, account_id, username, action_type, target_id, detail, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (utc_now(), account_id, username, action_type, str(target_id), detail[:500], status),
            )

    def get_recent_logs(self, limit: int = 100, action_type: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if action_type and action_type != "all":
                rows = conn.execute(
                    "SELECT * FROM action_logs WHERE action_type = ? ORDER BY id DESC LIMIT ?",
                    (action_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM action_logs ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    # ---- Interaction deduplication ----

    def has_interacted(self, account_id: int, target_id: int, interaction_type: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM interacted_posts
                WHERE account_id = ? AND post_id = ? AND interaction_type = ?
                """,
                (account_id, target_id, interaction_type),
            ).fetchone()
            return row is not None

    def has_any_account_interacted(self, target_id: int, interaction_type: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM interacted_posts
                WHERE post_id = ? AND interaction_type = ?
                """,
                (target_id, interaction_type),
            ).fetchone()
            return row is not None

    def record_interaction(self, account_id: int, target_id: int, interaction_type: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO interacted_posts (account_id, post_id, interaction_type, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (account_id, target_id, interaction_type, utc_now()),
            )

    # ---- Verified Creators ----

    def upsert_verified_creator(
        self,
        account_id: int,
        username: str,
        display_name: str,
        stance: str,
        strategy_notes: str,
        last_post_id: Optional[int] = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO verified_creators
                (account_id, username, display_name, stance, strategy_notes, last_post_id, last_checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    username=excluded.username,
                    display_name=excluded.display_name,
                    stance=excluded.stance,
                    strategy_notes=excluded.strategy_notes,
                    last_post_id=COALESCE(excluded.last_post_id, verified_creators.last_post_id),
                    last_checked_at=excluded.last_checked_at
                """,
                (account_id, username, display_name, stance, strategy_notes, last_post_id, utc_now()),
            )

    def increment_verified_engagement(self, creator_id: int, col: str) -> None:
        if col not in ("our_replies_count", "our_mentions_count"):
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE verified_creators SET {col} = {col} + 1 WHERE account_id = ?",
                (creator_id,),
            )

    def get_verified_creators(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM verified_creators ORDER BY account_id ASC").fetchall()
            return [dict(r) for r in rows]

    # ---- Our Posts & Metrics ----

    def record_our_post(
        self,
        post_id: int,
        account_id: int,
        username: str,
        kind: str,
        body: str,
        target_post_id: Optional[int] = None,
    ) -> None:
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO our_posts
                (post_id, account_id, username, kind, body, target_post_id, created_at, last_measured_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (post_id, account_id, username, kind, body, target_post_id, now, now),
            )
            col_map = {"post": "posts_count", "reply": "replies_count", "repost": "reposts_count"}
            col = col_map.get(kind)
            if col:
                conn.execute(
                    f"UPDATE accounts SET {col} = {col} + 1, last_active_at = ? WHERE account_id = ?",
                    (now, account_id),
                )

    def increment_account_counter(self, account_id: int, col: str) -> None:
        allowed = {"posts_count", "replies_count", "reposts_count", "likes_count", "reports_count", "following_count"}
        if col not in allowed:
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE accounts SET {col} = {col} + 1, last_active_at = ? WHERE account_id = ?",
                (utc_now(), account_id),
            )

    def update_post_counters(self, post_id: int, counters: Dict[str, Any]) -> None:
        likes = int(counters.get("likes", counters.get("like_count", 0)) or 0)
        replies = int(counters.get("replies", counters.get("reply_count", 0)) or 0)
        reposts = int(counters.get("reposts", counters.get("repost_count", 0)) or 0)
        views = int(counters.get("views", counters.get("view_count", 0)) or 0)
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE our_posts
                SET likes = ?, replies = ?, reposts = ?, views = ?, last_measured_at = ?
                WHERE post_id = ?
                """,
                (likes, replies, reposts, views, now, post_id),
            )
            conn.execute(
                """
                INSERT INTO post_metrics_history (post_id, likes, replies, reposts, views, measured_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (post_id, likes, replies, reposts, views, now),
            )

    def get_our_posts(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM our_posts ORDER BY post_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---- Swarm Post Cycle Tracking & Completion Timing ----

    def start_swarm_cycle_record(
        self,
        post_id: int,
        lead_account_id: int,
        lead_username: str,
        post_number_in_lead: int,
        body: str,
        total_supporters: int,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO swarm_post_cycles
                (post_id, lead_account_id, lead_username, post_number_in_lead, body, started_ts, total_supporters, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'in_progress')
                """,
                (post_id, lead_account_id, lead_username, post_number_in_lead, body, time.time(), total_supporters),
            )

    def update_swarm_cycle_progress(
        self,
        post_id: int,
        total_supporters: int,
        views_done: int,
        likes_done: int,
        replies_done: int,
        reposts_done: int,
        all_done: bool = False,
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT started_ts, status FROM swarm_post_cycles WHERE post_id = ?", (post_id,)).fetchone()
            if not row:
                return
            started = float(row["started_ts"] or now)
            was_completed = row["status"] == "completed"
            if all_done and not was_completed:
                dur = max(1.0, now - started)
                conn.execute(
                    """
                    UPDATE swarm_post_cycles
                    SET total_supporters = ?, views_done = ?, likes_done = ?, replies_done = ?, reposts_done = ?,
                        completed_ts = ?, duration_sec = ?, status = 'completed'
                    WHERE post_id = ?
                    """,
                    (total_supporters, views_done, likes_done, replies_done, reposts_done, now, dur, post_id),
                )
            else:
                dur = max(1.0, now - started) if not was_completed else None
                conn.execute(
                    """
                    UPDATE swarm_post_cycles
                    SET total_supporters = ?, views_done = ?, likes_done = ?, replies_done = ?, reposts_done = ?,
                        duration_sec = COALESCE(duration_sec, ?)
                    WHERE post_id = ?
                    """,
                    (total_supporters, views_done, likes_done, replies_done, reposts_done, dur, post_id),
                )

    def get_swarm_cycles(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM swarm_post_cycles ORDER BY post_id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Team-Wide Rate-Limit Scheduler
# ---------------------------------------------------------------------------

DEFAULT_BUCKETS: Dict[str, Tuple[int, float]] = {
    "post_write": (3, 20.5),     # 3/min shared across post, reply, repost
    "like": (30, 1.9),           # 30/min -> ~1.9s spacing for full 30/min utilization
    "view": (30, 1.9),           # 30/min -> ~1.9s spacing for full 30/min utilization
    "follow": (30, 1.9),         # 30/min -> ~1.9s spacing for full 30/min utilization
    "report_post": (3, 21.0),    # 3/min
    "report_account": (3, 21.0),
    "feed": (10, 6.2),           # 10/min shared across feeds
    "tag_posts": (10, 6.2),
    "read_other": (300, 0.05),   # 300/min -> up to 20/sec
    "llm": (3, 20.5),            # 3/min
    "auth": (5, 12.2),           # 5/min per IP
}


class RateLimitScheduler:
    """Coordinates team-wide API budgets across all accounts and routes."""

    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()
        self.active_bucket: str = "read_other"

    def observe_header(self, limit: ctn.RateLimit, bucket: Optional[str] = None) -> None:
        """Dynamically calibrate rate-limit pacing using the exact server header values
        (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`):
        - When `remaining == 0`: Server tells us reset seconds remaining -> wait exactly `reset` (or `reset + 0.1` buffer).
        - When `remaining > 0`: Smoothly pace calls by `reset / limit` (the exact refill rate from the server documentation:
          'The budget refills steadily rather than all at once, so when it is spent, one request's worth of it comes back in about reset / limit seconds').
        - Never manually hardcode guessed wait times!"""
        b_name = bucket or self.active_bucket
        now_ts = time.time()

        lim_val = max(1, int(limit.limit))
        rem_val = max(0, int(limit.remaining))
        rst_sec = max(0.0, float(limit.reset))

        if rem_val <= 0:
            # Completely exhausted; wait the server's exact remaining reset time before hitting this route again!
            wait_sec = rst_sec + 0.2
        elif rem_val == 1:
            # Only 1 request remaining in budget; wait for at least one refill unit
            wait_sec = max(0.5, rst_sec / lim_val)
        else:
            # Normal pacing: stagger requests by server refill interval (reset / limit)
            wait_sec = max(0.05, (rst_sec / lim_val) * 0.9)

        next_allowed = now_ts + wait_sec
        with self._lock, self.db._lock, self.db._connect() as conn:
            conn.execute(
                """
                INSERT INTO rate_limits (bucket, limit_val, remaining, reset_sec, next_allowed_ts, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket) DO UPDATE SET
                    limit_val=excluded.limit_val,
                    remaining=excluded.remaining,
                    reset_sec=excluded.reset_sec,
                    next_allowed_ts=excluded.next_allowed_ts,
                    updated_at=excluded.updated_at
                """,
                (b_name, lim_val, rem_val, int(rst_sec), next_allowed, utc_now()),
            )

    def can_proceed(self, bucket: str) -> Tuple[bool, float]:
        now_ts = time.time()
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT next_allowed_ts, remaining, reset_sec, limit_val FROM rate_limits WHERE bucket = ?",
                (bucket,),
            ).fetchone()
            if not row:
                return True, 0.0
            wait = float(row["next_allowed_ts"]) - now_ts
            # If server reported remaining == 0, strictly enforce wait time
            rem = int(row["remaining"] if row["remaining"] is not None else 1)
            if rem <= 0 and wait > 0:
                return False, wait
            if wait > 0:
                return False, wait
            return True, 0.0

    def acquire(self, bucket: str, block: bool = True, max_wait: float = 35.0) -> bool:
        """Acquire permission to make a request on `bucket`, strictly respecting the server-reported
        remaining time and refill pacing."""
        while True:
            ready, wait_sec = self.can_proceed(bucket)
            if ready:
                now_ts = time.time()
                # Inspect current server rate limit state from database
                with self._lock, self.db._lock, self.db._connect() as conn:
                    row = conn.execute(
                        "SELECT limit_val, remaining, reset_sec FROM rate_limits WHERE bucket = ?",
                        (bucket,),
                    ).fetchone()
                    if row and row["limit_val"]:
                        lim_val = max(1, int(row["limit_val"]))
                        rem_val = max(0, int(row["remaining"] if row["remaining"] is not None else lim_val) - 1)
                        rst_sec = max(1.0, float(row["reset_sec"] or 60.0))
                        pace_step = max(0.05, rst_sec / lim_val) if rem_val > 0 else rst_sec + 0.2
                    else:
                        lim_val, pace_step = DEFAULT_BUCKETS.get(bucket, (60, 1.0))
                        rem_val = lim_val - 1

                    next_ts = now_ts + pace_step
                    conn.execute(
                        """
                        INSERT INTO rate_limits (bucket, limit_val, remaining, reset_sec, next_allowed_ts, updated_at)
                        VALUES (?, ?, ?, 60, ?, ?)
                        ON CONFLICT(bucket) DO UPDATE SET
                            next_allowed_ts = ?,
                            remaining = MAX(0, rate_limits.remaining - 1),
                            updated_at = ?
                        """,
                        (bucket, lim_val, rem_val, next_ts, utc_now(), next_ts, utc_now()),
                    )
                self.active_bucket = bucket
                return True

            if not block or wait_sec > max_wait:
                return False
            time.sleep(min(wait_sec, 2.5))

    def backoff_bucket(self, bucket: str, retry_after: float) -> None:
        next_ts = time.time() + max(2.0, retry_after)
        limit_val, _ = DEFAULT_BUCKETS.get(bucket, (10, 6.0))
        with self._lock, self.db._lock, self.db._connect() as conn:
            conn.execute(
                """
                INSERT INTO rate_limits (bucket, limit_val, remaining, reset_sec, next_allowed_ts, updated_at)
                VALUES (?, ?, 0, ?, ?, ?)
                ON CONFLICT(bucket) DO UPDATE SET
                    remaining = 0,
                    reset_sec = ?,
                    next_allowed_ts = ?,
                    updated_at = ?
                """,
                (bucket, limit_val, int(retry_after), next_ts, utc_now(), int(retry_after), next_ts, utc_now()),
            )

    def get_all_rate_limits(self) -> List[Dict[str, Any]]:
        now_ts = time.time()
        with self.db._connect() as conn:
            rows = conn.execute("SELECT * FROM rate_limits ORDER BY bucket").fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["wait_seconds"] = max(0.0, round(float(d["next_allowed_ts"]) - now_ts, 1))
                result.append(d)
            return result


# ---------------------------------------------------------------------------
# Account & Authentication Manager + Auto-Provisioner + Mesh Network
# ---------------------------------------------------------------------------

class AccountManager:
    """Manages CTN accounts, auto-registers personas with team_code as password,
    replaces suspended accounts, and maintains mutual follow/like/repost mesh."""

    def __init__(self, db: Database, scheduler: RateLimitScheduler):
        self.db = db
        self.scheduler = scheduler
        self._sessions: Dict[int, ctn.Account] = {}
        self.allowance_exhausted: bool = False

    def make_social(self) -> ctn.LegitSocial:
        return ctn.LegitSocial(
            timeout=20.0,
            max_retries=2,
            on_rate_limit=lambda rl: self.scheduler.observe_header(rl),
        )

    def generate_believable_persona(self) -> Tuple[str, str, str]:
        """Pick an unused believable Kingstonia username, display_name, and persona_prompt."""
        existing_accounts = self.list_accounts()
        existing_usernames = {a["username"].lower() for a in existing_accounts}
        existing_displays = {(a.get("display_name") or "").lower() for a in existing_accounts}

        # First pass: pick a completely unused persona archetype (different display_name and username)
        for p in KINGSTONIA_PERSONAS:
            base = p["base_username"]
            disp = p["display_name"]
            if base.lower() not in existing_usernames and disp.lower() not in existing_displays:
                return base, disp, p["persona_prompt"]

        # Second pass: pick any unused base_username
        for p in KINGSTONIA_PERSONAS:
            base = p["base_username"]
            if base.lower() not in existing_usernames:
                return base, p["display_name"], p["persona_prompt"]

        # Third pass: add a subtle realistic suffix to a random archetype
        chosen = random.choice(KINGSTONIA_PERSONAS)
        for suffix in ("_kc", "_ch", "_nh", "84", "91", "77", "26"):
            cand = f"{chosen['base_username'][:26]}{suffix}"
            if cand.lower() not in existing_usernames:
                return cand, chosen["display_name"], chosen["persona_prompt"]

        rand_num = random.randint(10, 99)
        return f"{chosen['base_username'][:28]}_{rand_num}", chosen["display_name"], chosen["persona_prompt"]

    def register_account(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        team_key: Optional[str] = None,
        display_name: Optional[str] = None,
        persona_prompt: str = "",
    ) -> ctn.Account:
        """Register a brand-new account on LegitSocial.
        Defaults password to the team_code as requested, and picks a believable persona if omitted."""
        t_key = (team_key or self.db.get_setting("team_code")).strip()
        if not t_key:
            raise ValueError("Team key is required to register a new account.")

        gen_user, gen_disp, gen_persona = self.generate_believable_persona()
        final_user = (username or gen_user).strip()[:32]
        # Rule 6: Always default password to our team_code unless explicitly overridden
        final_pass = (password or t_key).strip()
        final_disp = (display_name or gen_disp).strip()
        final_persona = (persona_prompt or gen_persona).strip()

        self.scheduler.acquire("auth", block=True)
        social = self.make_social()
        try:
            acc = social.register(final_user, final_pass, t_key, display_name=final_disp or None)
        except ctn.APIError as e:
            if e.status == 409 and not username:
                # Username happened to be taken on the server; add a 2-digit suffix and retry once
                final_user = f"{final_user[:28]}{random.randint(10, 99)}"
                self.scheduler.acquire("auth", block=True)
                acc = social.register(final_user, final_pass, t_key, display_name=final_disp or None)
            elif e.status == 403 and not e.not_ready_reasons:
                self.allowance_exhausted = True
                self.db.log_action("auth", f"Team account allowance reached: {e.message}", status="warning")
                raise
            else:
                raise

        if team_key:
            self.db.set_setting("team_code", t_key)

        self._save_account(acc, password=final_pass, persona_prompt=final_persona)
        self._sessions[acc.account_id] = acc
        self.db.log_action(
            "auth",
            f"Auto-registered citizen @{acc.username} ({final_disp}, ID: {acc.account_id}) with team_code password",
            status="success",
            account_id=acc.account_id,
            username=acc.username,
        )
        # Immediately mesh-follow existing team accounts
        self.mesh_follow_all(new_account_id=acc.account_id)
        return acc

    def handle_suspended_account(self, suspended_account_id: int, reason: str = "") -> Optional[ctn.Account]:
        """Mark account suspended, optionally delete it to free an active slot, and auto-create a replacement."""
        row = self.get_account_row(suspended_account_id)
        uname = row["username"] if row else str(suspended_account_id)
        self.update_account_status(suspended_account_id, "suspended", reason or "Account suspended by moderator")
        self.toggle_account(suspended_account_id, False)
        self.db.log_action(
            "auth",
            f"Account @{uname} (ID {suspended_account_id}) detected as SUSPENDED. Initiating replacement...",
            status="warning",
            account_id=suspended_account_id,
            username=uname,
        )

        # Try deleting suspended account if possible to free active slot under team cap
        acc_obj = self._sessions.pop(suspended_account_id, None)
        if acc_obj:
            try:
                acc_obj.delete()
                self.db.log_action("auth", f"Deleted suspended account @{uname} to free active team slot.")
            except Exception:
                pass

        if self.db.get_setting("auto_replace_suspended", "true") == "true":
            t_key = self.db.get_setting("team_code", "").strip()
            if t_key:
                try:
                    self.allowance_exhausted = False
                    new_acc = self.register_account(team_key=t_key)
                    self.db.log_action(
                        "auth",
                        f"Replaced suspended @{uname} with new citizen @{new_acc.username} (ID {new_acc.account_id})!",
                        status="success",
                        account_id=new_acc.account_id,
                        username=new_acc.username,
                    )
                    # If a Lead Account was suspended, immediately promote the next Top-5 Lead Account and adjust its opinion > 75!
                    self.ensure_lead_opinion_scores()
                    return new_acc
                except Exception as e:
                    self.db.log_action("auth", f"Could not auto-replace suspended account: {e}", status="error")
        self.ensure_lead_opinion_scores()
        return None

    def ensure_lead_opinion_scores(self, lead_account_ids: Optional[List[int]] = None) -> None:
        """Guarantee that all Top-5 Lead Accounts ALWAYS have an `opinion_score > 75.0`,
        and if a Lead Account was banned/replaced, automatically adjust the newly promoted Lead Account
        to a fresh high-conviction opinion score (> 75.0)!"""
        active_ready = [
            a for a in self.list_accounts(only_enabled=True)
            if (a.get("token") or a.get("password")) and a.get("status") == "active"
        ]
        if not active_ready:
            return

        if lead_account_ids is None:
            view_totals: Dict[int, int] = {}
            for p in self.db.get_our_posts(limit=500):
                aid = int(p["account_id"])
                view_totals[aid] = view_totals.get(aid, 0) + int(p.get("views") or 0)
            ranked = sorted(
                active_ready,
                key=lambda a: (-view_totals.get(int(a["account_id"]), 0), int(a["account_id"])),
            )
            lead_account_ids = [int(a["account_id"]) for a in ranked[:5]]

        adjusted_logs: List[Tuple[int, str, float, float]] = []
        with self.db._lock, self.db._connect() as conn:
            for lid in lead_account_ids:
                row = conn.execute(
                    "SELECT username, opinion_score FROM accounts WHERE account_id = ?",
                    (lid,),
                ).fetchone()
                if not row:
                    continue
                curr_op = float(row["opinion_score"] or 0.0)
                if curr_op <= 75.0:
                    new_op = generate_opinion_score(lid, is_lead=True)
                    conn.execute(
                        "UPDATE accounts SET opinion_score = ? WHERE account_id = ?",
                        (new_op, lid),
                    )
                    adjusted_logs.append((lid, row["username"], curr_op, new_op))

        for lid, uname, curr_op, new_op in adjusted_logs:
            self.db.log_action(
                "system",
                f"👑 Adjusted Lead Account @{uname} (ID {lid}) opinion score from +{curr_op:.1f} -> +{new_op:.1f} (> 75 Lead Requirement)",
                status="success",
                account_id=lid,
                username=uname,
            )

    def ensure_fleet_capacity(self) -> None:
        """If auto_provision_accounts is enabled and we have fewer active accounts than target_active_accounts,
        register new believable citizen accounts (using team_code as password) until target or team cap is reached."""
        if self.allowance_exhausted:
            return
        if self.db.get_setting("auto_provision_accounts", "true") != "true":
            return
        t_key = self.db.get_setting("team_code", "").strip()
        if not t_key or len(t_key) < 8:
            return

        # First, if any existing account has no password yet, try logging it in with team_code!
        for acc in self.list_accounts():
            if not acc.get("token") and not acc.get("password") and acc.get("status") != "suspended":
                try:
                    self.login_account(acc["username"], t_key)
                except Exception:
                    pass

        target = int(self.db.get_setting("target_active_accounts", "5") or 5)
        active_ready = [
            a for a in self.list_accounts(only_enabled=True)
            if (a.get("token") or a.get("password")) and a.get("status") == "active"
        ]
        if len(active_ready) < target:
            ready_auth, _ = self.scheduler.can_proceed("auth")
            if ready_auth:
                try:
                    self.register_account(team_key=t_key)
                except Exception as e:
                    self.db.log_action("auth", f"Auto-provision paused: {e}", status="warning")

    def mesh_follow_all(self, new_account_id: Optional[int] = None, max_follows: int = 18) -> int:
        """Ensure all active team accounts follow each other (mutual mesh) so every account has >= 30 followers.
        Prioritizes targets with the lowest follower count first so all 35 accounts quickly reach 34 followers!"""
        if self.db.get_setting("auto_mesh_follow", "true") != "true":
            return 0
        active_accounts = [
            a for a in self.list_accounts(only_enabled=True)
            if (a.get("token") or a.get("password")) and a.get("status") == "active"
        ]
        if len(active_accounts) < 2:
            return 0

        # Prioritize targets that have fewer than 32 followers first
        targets_sorted = sorted(active_accounts, key=lambda a: (int(a.get("followers_count") or 0), int(a["account_id"])))

        follows_done = 0
        for target in targets_sorted:
            t_id = int(target["account_id"])
            for follower in active_accounts:
                f_id = int(follower["account_id"])
                if f_id == t_id:
                    continue
                if new_account_id is not None and f_id != new_account_id and t_id != new_account_id:
                    continue
                if self.db.has_interacted(f_id, t_id, "follow_user"):
                    continue
                if follows_done >= max_follows or not self.scheduler.can_proceed("follow")[0]:
                    return follows_done
                try:
                    self.execute_with_account(f_id, "follow", lambda acc, tid=t_id: acc.follow(tid))
                    self.db.record_interaction(f_id, t_id, "follow_user")
                    self.db.increment_account_counter(f_id, "following_count")
                    with self.db._lock, self.db._connect() as conn:
                        conn.execute(
                            "UPDATE accounts SET followers_count = followers_count + 1 WHERE account_id = ?",
                            (t_id,),
                        )
                    self.db.log_action(
                        "follow",
                        f"Mesh follow: @{follower['username']} -> followed teammate @{target['username']}",
                        status="success",
                        account_id=f_id,
                        username=follower["username"],
                        target_id=str(t_id),
                    )
                    follows_done += 1
                except ctn.APIError:
                    self.db.record_interaction(f_id, t_id, "follow_user")
                except Exception:
                    break
        return follows_done

    def is_mesh_follow_complete(self, min_followers: int = 30) -> Tuple[bool, int]:
        """Check whether all active accounts have at least `min_followers` (or all pairs are followed)."""
        active_accounts = [
            a for a in self.list_accounts(only_enabled=True)
            if (a.get("token") or a.get("password")) and a.get("status") == "active"
        ]
        n = len(active_accounts)
        if n < 2:
            return True, 0
        missing_pairs = 0
        for f in active_accounts:
            f_id = int(f["account_id"])
            for t in active_accounts:
                t_id = int(t["account_id"])
                if f_id != t_id and not self.db.has_interacted(f_id, t_id, "follow_user"):
                    missing_pairs += 1
        below_min = sum(1 for a in active_accounts if int(a.get("followers_count") or 0) < min(min_followers, n - 1))
        return (missing_pairs == 0 and below_min == 0), missing_pairs

    def login_account(
        self,
        username: str,
        password: str,
        persona_prompt: str = "",
        display_name: Optional[str] = None,
    ) -> ctn.Account:
        self.scheduler.acquire("auth", block=True)
        social = self.make_social()
        acc = social.login(username.strip(), password)
        if display_name and display_name.strip() and display_name.strip() != acc.display_name:
            try:
                acc.update_profile(display_name=display_name.strip())
            except ctn.APIError:
                pass
        if not persona_prompt:
            _, _, persona_prompt = self.generate_believable_persona()
        self._save_account(acc, password=password, persona_prompt=persona_prompt)
        self._sessions[acc.account_id] = acc
        self.sync_account_history(acc.account_id, acc.username or username)
        self.refresh_account_stats(acc.account_id)
        self.db.log_action(
            "auth",
            f"Logged in @{acc.username} (ID: {acc.account_id})",
            status="success",
            account_id=acc.account_id,
            username=acc.username,
        )
        return acc

    def connect_with_token(self, token: str, password: str = "", persona_prompt: str = "") -> ctn.Account:
        social = self.make_social()
        acc = social.account_from_token(token.strip())
        self._save_account(acc, password=password, persona_prompt=persona_prompt)
        self._sessions[acc.account_id] = acc
        self.db.log_action(
            "auth",
            f"Connected via token @{acc.username} (ID: {acc.account_id})",
            status="success",
            account_id=acc.account_id,
            username=acc.username,
        )
        return acc

    def _save_account(
        self,
        acc: ctn.Account,
        password: str = "",
        persona_prompt: str = "",
        opinion_score: Optional[float] = None,
        verified_memories: str = "",
    ) -> None:
        now = utc_now()
        with self.db._lock, self.db._connect() as conn:
            existing = conn.execute(
                "SELECT password, persona_prompt, opinion_score, verified_memories FROM accounts WHERE account_id = ?",
                (acc.account_id,),
            ).fetchone()
            pw_to_save = password if password else (existing["password"] if existing else "")
            persona_to_save = persona_prompt if persona_prompt else (existing["persona_prompt"] if existing else "")
            op_to_save = (
                float(opinion_score)
                if opinion_score is not None
                else (
                    float(existing["opinion_score"])
                    if (existing and existing["opinion_score"] is not None and float(existing["opinion_score"]) != 70.0)
                    else generate_opinion_score(int(acc.account_id))
                )
            )
            mem_to_save = (
                verified_memories
                if verified_memories
                else (
                    existing["verified_memories"]
                    if (existing and existing["verified_memories"])
                    else build_verified_memories_for_account(int(acc.account_id), acc.display_name or "", persona_to_save)
                )
            )

            conn.execute(
                """
                INSERT INTO accounts (
                    account_id, username, password, display_name, persona_prompt,
                    opinion_score, verified_memories,
                    token, expires_at, status, is_enabled, last_active_at, last_error, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, '', ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    username=excluded.username,
                    password=CASE WHEN excluded.password != '' THEN excluded.password ELSE accounts.password END,
                    display_name=excluded.display_name,
                    persona_prompt=CASE WHEN excluded.persona_prompt != '' THEN excluded.persona_prompt ELSE accounts.persona_prompt END,
                    opinion_score=excluded.opinion_score,
                    verified_memories=CASE WHEN excluded.verified_memories != '' THEN excluded.verified_memories ELSE accounts.verified_memories END,
                    token=excluded.token,
                    expires_at=excluded.expires_at,
                    status='active',
                    last_active_at=excluded.last_active_at,
                    last_error=''
                """,
                (
                    acc.account_id,
                    acc.username or "",
                    pw_to_save,
                    acc.display_name or "",
                    persona_to_save,
                    op_to_save,
                    mem_to_save,
                    acc.token or "",
                    acc.expires_at or "",
                    now,
                    now,
                ),
            )

    def get_session(self, account_id: int) -> Optional[ctn.Account]:
        row = self.get_account_row(account_id)
        if not row or not row.get("is_enabled") or row.get("status") == "suspended":
            return None

        if account_id in self._sessions:
            acc = self._sessions[account_id]
            acc.on_rate_limit = lambda rl: self.scheduler.observe_header(rl)
            return acc

        social = self.make_social()
        token = (row.get("token") or "").strip()
        pw = (row.get("password") or self.db.get_setting("team_code") or "").strip()

        if token:
            try:
                self.scheduler.acquire("read_other", block=True)
                acc = social.account_from_token(token)
                self._sessions[account_id] = acc
                self.update_account_status(account_id, "active", "")
                return acc
            except ctn.APIError as e:
                if e.status == 401 and pw:
                    return self._relogin(row["username"], pw, account_id)
                elif e.status == 403 and "suspended" in e.message.lower():
                    self.handle_suspended_account(account_id, e.message)
                    return None
                else:
                    self.update_account_status(account_id, "error", e.message)
                    return None

        if pw:
            return self._relogin(row["username"], pw, account_id)
        return None

    def _relogin(self, username: str, password: str, account_id: int) -> Optional[ctn.Account]:
        try:
            acc = self.login_account(username, password)
            return acc
        except ctn.APIError as e:
            if e.status == 403 and "suspended" in e.message.lower():
                self.handle_suspended_account(account_id, e.message)
            else:
                self.update_account_status(account_id, "error", e.message)
                self.db.log_action(
                    "auth",
                    f"Re-login failed for @{username}: {e.message}",
                    status="error",
                    account_id=account_id,
                    username=username,
                )
            return None

    def execute_with_account(
        self,
        account_id: int,
        bucket: str,
        fn: Callable[[ctn.Account], Any],
    ) -> Any:
        acc = self.get_session(account_id)
        if acc is None:
            raise RuntimeError(f"Account {account_id} is not available or could not authenticate.")

        if not self.scheduler.acquire(bucket, block=True, max_wait=35.0):
            raise RuntimeError(f"Rate limit bucket '{bucket}' is busy; skipping for now.")

        try:
            return fn(acc)
        except ctn.RateLimited as e:
            self.scheduler.backoff_bucket(bucket, e.retry_after)
            self.db.log_action(
                bucket,
                f"Rate limited (429) on {bucket}. Retry-After: {e.retry_after}s",
                status="warning",
                account_id=account_id,
                username=acc.username or "",
            )
            raise
        except ctn.APIError as e:
            if e.status == 401:
                self._sessions.pop(account_id, None)
                row = self.get_account_row(account_id)
                pw = (row.get("password") if row else "") or self.db.get_setting("team_code")
                if row and pw:
                    acc = self._relogin(row["username"], pw, account_id)
                    if acc:
                        return fn(acc)
                self.update_account_status(account_id, "token_expired", "401 Unauthorized - password needed")
            elif e.status == 403 and "suspended" in e.message.lower():
                self.handle_suspended_account(account_id, e.message)
            raise

    def update_account_status(self, account_id: int, status: str, last_error: str = "") -> None:
        with self.db._lock, self.db._connect() as conn:
            conn.execute(
                "UPDATE accounts SET status = ?, last_error = ? WHERE account_id = ?",
                (status, last_error[:300], account_id),
            )

    def toggle_account(self, account_id: int, enabled: bool) -> None:
        with self.db._lock, self.db._connect() as conn:
            conn.execute(
                "UPDATE accounts SET is_enabled = ?, status = ? WHERE account_id = ?",
                (1 if enabled else 0, "active" if enabled else "paused", account_id),
            )

    def update_account_persona(
        self,
        account_id: int,
        display_name: str,
        persona_prompt: str,
        username: Optional[str] = None,
        opinion_score: Optional[float] = None,
        verified_memories: Optional[str] = None,
    ) -> None:
        """Update an account's username, display_name, persona, opinion_score, and verified_memories both on the live
        LegitSocial server and across all local SQLite tables (accounts, our_posts, action_logs)."""
        clean_dn = display_name.strip()
        clean_user = username.strip() if username and username.strip() else None

        # 1. Push username & display_name update to live LegitSocial server if authenticated
        try:
            acc = self.get_session(account_id)
            if acc is not None:
                self.execute_with_account(
                    account_id,
                    "read_other",
                    lambda a: a.update_profile(username=clean_user, display_name=clean_dn or None),
                )
                if a_user := acc.username:
                    clean_user = a_user
        except Exception as e:
            self.db.log_action(
                "system",
                f"Live profile sync warning for ID {account_id}: {e}",
                status="warning",
                account_id=account_id,
            )

        # 2. Update local SQLite tables (accounts, our_posts, action_logs) so the GUI tables reflect it immediately
        with self.db._lock, self.db._connect() as conn:
            row = conn.execute("SELECT opinion_score, verified_memories FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
            op_val = float(opinion_score) if opinion_score is not None else (float(row["opinion_score"]) if row and row["opinion_score"] is not None else generate_opinion_score(account_id))
            mem_val = verified_memories.strip() if verified_memories is not None else (row["verified_memories"] if row and row["verified_memories"] else build_verified_memories_for_account(account_id, clean_dn, persona_prompt))
            if clean_user:
                conn.execute(
                    "UPDATE accounts SET username = ?, display_name = ?, persona_prompt = ?, opinion_score = ?, verified_memories = ? WHERE account_id = ?",
                    (clean_user, clean_dn, persona_prompt.strip(), op_val, mem_val, account_id),
                )
                conn.execute(
                    "UPDATE our_posts SET username = ? WHERE account_id = ?",
                    (clean_user, account_id),
                )
                conn.execute(
                    "UPDATE action_logs SET username = ? WHERE account_id = ?",
                    (clean_user, account_id),
                )
            else:
                conn.execute(
                    "UPDATE accounts SET display_name = ?, persona_prompt = ?, opinion_score = ?, verified_memories = ? WHERE account_id = ?",
                    (clean_dn, persona_prompt.strip(), op_val, mem_val, account_id),
                )

    def get_account_row(self, account_id: int) -> Optional[Dict[str, Any]]:
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
            return dict(row) if row else None

    def list_accounts(self, only_enabled: bool = False) -> List[Dict[str, Any]]:
        with self.db._connect() as conn:
            if only_enabled:
                rows = conn.execute(
                    "SELECT * FROM accounts WHERE is_enabled = 1 AND status != 'suspended' ORDER BY account_id"
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM accounts ORDER BY account_id").fetchall()
            return [dict(r) for r in rows]

    def sync_account_history(self, account_id: int, username: str) -> int:
        social = self.make_social()
        try:
            if not self.scheduler.acquire("read_other", block=False):
                return 0
            posts = list(social.account_posts(account_id, limit=50))
            count = 0
            for p in posts:
                pid = int(p["post_id"])
                kind = str(p.get("kind") or "post")
                body = str(p.get("body") or "")
                target = p.get("reply_to_post_id") or p.get("repost_of_post_id")
                created = str(p.get("created_at") or utc_now())
                with self.db._lock, self.db._connect() as conn:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO our_posts
                        (post_id, account_id, username, kind, body, target_post_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (pid, account_id, username, kind, body, target, created),
                    )
                if self.scheduler.acquire("read_other", block=False):
                    try:
                        c = social.counters(pid)
                        self.update_post_counters(pid, c)
                    except Exception:
                        pass
                count += 1
            return count
        except Exception:
            return 0

    def refresh_account_stats(self, account_id: int) -> Optional[Dict[str, Any]]:
        try:
            social = self.make_social()
            if not self.scheduler.acquire("read_other", block=False):
                return None
            prof = social.get_account(account_id)
            live_username = prof.get("username")
            live_display = prof.get("display_name")
            followers = len(list(social.followers(account_id, limit=50))) if self.scheduler.acquire("read_other", block=False) else 0
            following = len(list(social.following(account_id, limit=50))) if self.scheduler.acquire("read_other", block=False) else 0
            with self.db._lock, self.db._connect() as conn:
                conn.execute(
                    """
                    UPDATE accounts
                    SET username = COALESCE(?, username),
                        display_name = COALESCE(?, display_name),
                        followers_count = ?,
                        following_count = ?
                    WHERE account_id = ?
                    """,
                    (live_username, live_display, followers, following, account_id),
                )
                if live_username:
                    conn.execute(
                        "UPDATE our_posts SET username = ? WHERE account_id = ? AND username != ?",
                        (live_username, account_id, live_username),
                    )
            return prof
        except Exception:
            return None
