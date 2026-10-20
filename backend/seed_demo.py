"""
Demo seed script — creates a demo login + 12 realistic Indian properties.

Usage (run from the backend/ directory):
    DATABASE_URL=<your-render-db-url> python seed_demo.py

Or if .env is populated:
    python seed_demo.py

Credentials created:
    Email:    demo@mira.ai
    Password: MiraDemo2024
"""

import asyncio
import sys

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Allow running from backend/ directory
sys.path.insert(0, ".")

from app.config import settings
from app.models.property import Property
from app.models.user import User

DEMO_EMAIL = "demo@mira.ai"
DEMO_PASSWORD = "MiraDemo2024"
DEMO_NAME = "Mira Demo Host"

PROPERTIES = [
    {
        "name": "Sunset Palms Beach Villa",
        "city": "Goa",
        "base_price": 8500,
        "max_guests": 8,
        "check_in_time": "14:00",
        "check_out_time": "11:00",
        "usp": "Private pool, 50m from Baga Beach, panoramic sea views from rooftop",
        "amenities": [
            "Private pool", "WiFi", "Air conditioning", "Full kitchen",
            "Rooftop terrace", "Outdoor shower", "Beach towels", "BBQ grill",
            "Smart TV", "Parking", "Generator backup", "Water purifier"
        ],
        "house_rules": (
            "No shoes inside the villa. No loud music after 11 PM. "
            "No smoking indoors — use the rooftop terrace. "
            "Pets allowed with prior approval. "
            "Pool is private — no outside guests without permission. "
            "Checkout strictly by 11 AM; early check-in subject to availability."
        ),
        "neighborhood_info": (
            "Baga Beach is a 2-minute walk. Tito's Lane nightlife strip is 5 minutes on foot. "
            "Nearest supermarket (Delfino's) is 400m away. "
            "Calangute taxi stand 600m — local rides typically Rs 100-200. "
            "Goa airport (GOI) is 45 minutes by cab (Rs 800-1000). "
            "Mapusa market (Friday flea market) is 8 km, about 20 min. "
            "Curlies beach shack is 10 min walk for great seafood."
        ),
        "faq": [
            {"question": "Is the pool heated?", "answer": "No, but Goa's warm climate keeps it comfortable year-round."},
            {"question": "Can we bring outside guests to use the pool?", "answer": "Pool is for registered guests only. Outside visitors need prior approval and may incur a day-use fee."},
            {"question": "Is there a caretaker on site?", "answer": "Yes, Raju is available from 8 AM–8 PM for any assistance."},
            {"question": "Do you arrange scooter rentals?", "answer": "Yes, Raju can arrange scooters at Rs 400/day, bikes at Rs 250/day."},
        ],
    },
    {
        "name": "Haveli 1135 — Heritage Suite",
        "city": "Jaipur",
        "base_price": 6200,
        "max_guests": 4,
        "check_in_time": "13:00",
        "check_out_time": "12:00",
        "usp": "Restored 19th-century haveli, hand-painted frescoes, rooftop with Pink City views",
        "amenities": [
            "WiFi", "Air conditioning", "Rooftop terrace", "Traditional courtyard",
            "In-house chef (on request)", "Daily housekeeping", "Heritage decor",
            "Complimentary chai & snacks", "Bicycle rental", "Smart TV"
        ],
        "house_rules": (
            "Shoes off at the main gate — traditional sandals provided. "
            "No outside alcohol (the haveli is dry); room service alcohol available. "
            "Heritage artefacts must not be moved or touched. "
            "Quiet hours 10 PM–7 AM — walls are thin in a haveli. "
            "Couples only (no group parties)."
        ),
        "neighborhood_info": (
            "Hawa Mahal is an 8-minute auto ride (Rs 50). "
            "City Palace and Jantar Mantar are 1.5 km. "
            "Johri Bazaar (gems & jewellery market) is a 10-minute walk. "
            "Best thali restaurant: Laxmi Misthan Bhandar, 600m away. "
            "Jaipur Junction railway station is 4 km, Jaipur airport 12 km. "
            "Autos are abundant — negotiate before boarding, typical city fare Rs 40-80."
        ),
        "faq": [
            {"question": "Can you arrange a chef for traditional Rajasthani dinner?", "answer": "Yes, traditional dal baati churma dinner can be arranged for Rs 800/person with 6 hours notice."},
            {"question": "Is there parking?", "answer": "One car can be parked in the inner courtyard. Larger vehicles use the street."},
            {"question": "Do you offer guided heritage walks?", "answer": "Yes, our host Priya conducts a 2-hour morning walk at Rs 500/person."},
        ],
    },
    {
        "name": "Lake Whisper — Udaipur Studio",
        "city": "Udaipur",
        "base_price": 4800,
        "max_guests": 2,
        "check_in_time": "14:00",
        "check_out_time": "11:00",
        "usp": "Unobstructed Lake Pichola view, private balcony, couples retreat",
        "amenities": [
            "Lake view balcony", "WiFi", "Air conditioning", "Minibar",
            "Daily housekeeping", "Welcome fruit basket", "Smart TV",
            "Telescope for stargazing", "In-room breakfast"
        ],
        "house_rules": (
            "Adults only (18+), couples preferred. No smoking indoors. "
            "No cooking in the room — in-room breakfast provided. "
            "Checkout by 11 AM sharp (another booking usually follows). "
            "Please be mindful of noise — adjacent rooms share thin walls."
        ),
        "neighborhood_info": (
            "City Palace is 1.2 km, about Rs 60 by auto. "
            "Lake Pichola boat rides start from the ghat 400m away — sunset rides Rs 300/person. "
            "Fateh Sagar Lake is 3 km. "
            "Maharana Pratap Airport is 25 km (cab Rs 500-700). "
            "Ambrai Ghat restaurant — best view dining — is a 5-minute walk. "
            "Vintage Car Museum is 3 km away."
        ),
        "faq": [
            {"question": "Is breakfast included?", "answer": "Yes, a complimentary in-room breakfast of poha, chai, and fruit is included daily."},
            {"question": "Can you arrange a boat ride?", "answer": "We partner with a local boatman — sunset Lake Pichola + Jag Mandir visit Rs 800/couple."},
            {"question": "Is this suitable for a honeymoon?", "answer": "Absolutely — we offer a honeymoon package with rose petals, a bottle of sparkling juice, and a candlelit dinner setup for Rs 2500 extra."},
        ],
    },
    {
        "name": "Ganga Cottage — Rishikesh Riverside",
        "city": "Rishikesh",
        "base_price": 3500,
        "max_guests": 4,
        "check_in_time": "12:00",
        "check_out_time": "10:00",
        "usp": "Private sit-out 30m from the Ganga, yoga deck, no traffic noise",
        "amenities": [
            "River-facing sit-out", "WiFi", "Hot water", "Yoga deck",
            "Campfire setup", "Organic breakfast", "Bonfire evenings",
            "Blankets & quilts", "Reading nook", "Parking"
        ],
        "house_rules": (
            "This is an alcohol-free, non-veg-free property — no exceptions. "
            "Ganga is holy; please don't litter near the river bank. "
            "Quiet hours after 9:30 PM. "
            "Shoes off inside the cottage. "
            "No loud music — this is a nature retreat."
        ),
        "neighborhood_info": (
            "Located in Rishikesh, Uttarakhand — the yoga and adventure capital of India. "
            "Laxman Jhula is 1.5 km walk. Ram Jhula is 2.5 km. "
            "White-water rafting starting points at Shivpuri are 15 km upstream. "
            "Bungee jumping at Jumping Heights is 15 km. "
            "Dehradun airport is 45 km (cab Rs 900). "
            "Haridwar railway station is 25 km. "
            "Best café: Chotiwala near Ram Jhula (15 min walk)."
        ),
        "faq": [
            {"question": "Can you arrange rafting?", "answer": "Yes, we coordinate with certified rafting operators — 16km stretch Rs 650/person, 26km Rs 950/person."},
            {"question": "Is the Ganga safe for swimming here?", "answer": "The current near our bank is calm but we advise caution — always swim with a local guide. We provide life jackets."},
            {"question": "Is yoga available?", "answer": "A certified instructor visits daily at 7 AM for a 60-minute session — Rs 400/person."},
        ],
    },
    {
        "name": "Coorg Mist Estate Homestay",
        "city": "Coorg",
        "base_price": 5500,
        "max_guests": 6,
        "check_in_time": "14:00",
        "check_out_time": "11:00",
        "usp": "Working 20-acre coffee plantation, forest walks, fresh estate coffee every morning",
        "amenities": [
            "Coffee plantation walks", "WiFi", "Hot water", "Home-cooked meals",
            "Bonfire", "Bird watching", "Spice garden tour", "Parking",
            "Jungle trekking", "Estate coffee & tea all day"
        ],
        "house_rules": (
            "No plastic bottles — refill stations provided. "
            "Don't wander alone into the coffee plantation after dark — wildlife present (leopards, boars). "
            "Alcohol permitted in rooms and verandah only. "
            "Home-cooked dinner must be booked by 5 PM. "
            "No loud music or parties — this is a wildlife buffer zone."
        ),
        "neighborhood_info": (
            "Abbey Falls is 12 km. Raja's Seat viewpoint is 8 km. "
            "Madikeri town for shopping is 15 km. "
            "Dubare Elephant Camp is 20 km. "
            "Nearest airport: Mangalore (110 km, ~2.5 hrs) or Bangalore (270 km, ~5 hrs). "
            "Roads are narrow and hilly — self-driving is fun but caution advised in monsoon."
        ),
        "faq": [
            {"question": "Can we participate in coffee picking?", "answer": "During October–January harvest season, yes — our plantation manager leads a hands-on session."},
            {"question": "Are meals included?", "answer": "Breakfast is included. Lunch and dinner (Coorgi home food) at Rs 350/person/meal, book in advance."},
            {"question": "Is the place suitable for children?", "answer": "Yes, children love the estate. Under-10s must be supervised near the plantation at all times due to uneven terrain."},
        ],
    },
    {
        "name": "Alpine Ridge Chalet — Manali",
        "city": "Manali",
        "base_price": 7200,
        "max_guests": 8,
        "check_in_time": "14:00",
        "check_out_time": "11:00",
        "usp": "Snow-capped Himalayan views, private fireplace, 2km from Solang Valley",
        "amenities": [
            "Mountain view deck", "WiFi", "Central heating", "Fireplace",
            "Full kitchen", "Snow gear storage", "Smart TV", "Bonfire setup",
            "Parking (4 cars)", "Generator backup", "Hot water round the clock"
        ],
        "house_rules": (
            "No open fire outside designated bonfire area. "
            "Snow gear must be stored in the utility room, not bedrooms. "
            "In winter: keep interior doors closed to retain heat. "
            "Checkout strictly 11 AM — mountain roads mean next guest has limited flexibility. "
            "No pets — wildlife area."
        ),
        "neighborhood_info": (
            "Located in Manali, Himachal Pradesh — gateway to Rohtang Pass and Solang Valley. "
            "Solang Valley (skiing, zorbing, paragliding) is 2 km. "
            "Rohtang Pass (in season, June–Sept) is 51 km. "
            "Manali town market is 5 km, Hadimba Temple 4 km. "
            "Kullu-Manali airport is 52 km. Chandigarh airport is 300 km (7 hrs by road). "
            "Best diner: Johnson's Café in old Manali — 15 min drive. "
            "Beas River crossing point for trout fishing is 3 km."
        ),
        "faq": [
            {"question": "Is there snowfall at the property?", "answer": "December–February usually brings 1-3 feet of snowfall. We keep pathways cleared. Snow chains are available for cars."},
            {"question": "Can you arrange skiing packages?", "answer": "Yes — Solang Valley ski passes + equipment rental arranged at group rates starting Rs 1200/person."},
            {"question": "Does heating work during power cuts?", "answer": "Yes, the fireplace runs on wood (supplied) and we have a generator for the electric heater."},
        ],
    },
    {
        "name": "Cedar & Cobblestone — Shimla Colonial Cottage",
        "city": "Shimla",
        "base_price": 5800,
        "max_guests": 5,
        "check_in_time": "13:00",
        "check_out_time": "11:00",
        "usp": "1920s British-era cottage, all-cedar interiors, Mall Road 800m on foot",
        "amenities": [
            "WiFi", "Central heating", "Fireplace", "Cedar wood interiors",
            "Garden terrace", "Full kitchen", "Smart TV", "Reading library",
            "Hot water", "Parking (1 car)"
        ],
        "house_rules": (
            "Heritage property — please don't nail or stick anything to walls. "
            "Smoking in garden terrace only. "
            "Fireplace is decorative only — not functional. Central heating provided. "
            "Large vehicles can't access — parking at Lakkar Bazaar, 200m walk. "
            "No loud music after 10 PM."
        ),
        "neighborhood_info": (
            "Located in Shimla, Himachal Pradesh — the summer capital of British India. "
            "Mall Road is 800m walk — about 10 minutes. "
            "Kali Bari Temple is 600m. Jakhoo Hill (Hanuman Temple) trek starts 1.2 km away. "
            "Lakkar Bazaar (wood crafts) is 200m. "
            "Shimla railway station (toy train!) is 2 km. "
            "Chandigarh airport is 115 km, about 3 hrs. "
            "Best café: Cafe Sol on Mall Road — known for apple cider and hot chocolate."
        ),
        "faq": [
            {"question": "Can we take the toy train to Shimla?", "answer": "Yes! Kalka-Shimla narrow-gauge train is a UNESCO heritage ride. Station is 2 km. Book on IRCTC at least a week ahead."},
            {"question": "Is there a market nearby for groceries?", "answer": "Lakkar Bazaar has a daily market 200m away. For a full supermarket, Sanjauli market is 3 km by auto."},
            {"question": "Is the cottage warm in winter?", "answer": "Yes, central oil heaters keep every room at 22°C even when outside temperature drops to -5°C."},
        ],
    },
    {
        "name": "Kettuvallam Float — Alleppey Villa",
        "city": "Alleppey",
        "base_price": 9800,
        "max_guests": 6,
        "check_in_time": "12:00",
        "check_out_time": "10:00",
        "usp": "Canal-front villa with private jetty, kayaks included, 10-min from backwater cruise",
        "amenities": [
            "Private jetty", "2 kayaks", "WiFi", "Air conditioning", "Swimming pool",
            "Full kitchen", "Open-air dining", "Fishing rods", "Daily housekeeping",
            "Smart TV", "Complimentary boat tour"
        ],
        "house_rules": (
            "Life jackets mandatory on kayaks and the jetty — children always supervised. "
            "No swimming in the canal (water hyacinth and boat traffic). "
            "No outside food delivery — kitchen fully stocked, or we arrange a local cook. "
            "Check-in by 12 PM strictly — houseboat schedule. "
            "Waste segregation is mandatory."
        ),
        "neighborhood_info": (
            "Located in Alleppey (Alappuzha), Kerala — the backwaters capital of India. "
            "Alleppey boat jetty (start of backwater cruises) is 10 min by auto. "
            "Alleppey beach is 4 km. "
            "Marari beach (quieter, cleaner) is 14 km. "
            "Kochi airport is 85 km, about 1.5 hrs. "
            "Alleppey railway station is 6 km. "
            "Famous local restaurant: Thaff — crab curry and karimeen pollichathu."
        ),
        "faq": [
            {"question": "Is the pool filtered and chlorinated?", "answer": "Yes, the pool is maintained daily and is safe for children."},
            {"question": "Can you arrange a private chef for Kerala food?", "answer": "Yes, Rema, a local Nair cook, can prepare a full Kerala sadya (feast) for Rs 600/person with 12 hours notice."},
            {"question": "Are there mosquitoes?", "answer": "Backwaters have mosquitoes, especially at dusk. We provide nets, repellent, and citronella candles on the jetty."},
        ],
    },
    {
        "name": "The Skybox — Bangalore Studio",
        "city": "Bangalore",
        "base_price": 3200,
        "max_guests": 2,
        "check_in_time": "15:00",
        "check_out_time": "11:00",
        "usp": "14th floor, floor-to-ceiling city views, 5 min from MG Road metro",
        "amenities": [
            "City view floor-to-ceiling windows", "WiFi 300 Mbps", "Air conditioning",
            "Fully equipped kitchen", "Smart TV (Netflix)", "Washing machine",
            "Work desk & monitor", "24/7 security", "Gym access", "Covered parking"
        ],
        "house_rules": (
            "No smoking anywhere in the building. "
            "No parties — residential society rules. Max 2 registered guests. "
            "Society gate closes at 11 PM — notify security for late check-in. "
            "Garbage in the chute outside the door only. "
            "Keep noise below 60 dB in the corridor."
        ),
        "neighborhood_info": (
            "MG Road metro station is a 5-minute walk. "
            "Brigade Road shopping is 800m. Commercial Street is 1.5 km. "
            "Bangalore airport (KIA) is 36 km — about 45-90 min depending on traffic. Take Namma Metro (Purple Line) to Majestic then the airport metro. "
            "Cubbon Park is 1.5 km. UB City Mall is 2 km. "
            "Best coffee: Third Wave Coffee at 100m on the same road."
        ),
        "faq": [
            {"question": "Is work-from-home setup good?", "answer": "Yes — 300 Mbps fibre, ergonomic desk chair, 27-inch external monitor, and uninterrupted power backup."},
            {"question": "Can extra guests visit during the day?", "answer": "Day visitors allowed till 10 PM but must be registered at the security desk downstairs."},
            {"question": "What's the Ola/Uber situation?", "answer": "Very good — MG Road area has high driver density. Average wait time 3-5 minutes."},
        ],
    },
    {
        "name": "Seaview 1604 — Bandra West",
        "city": "Mumbai",
        "base_price": 11500,
        "max_guests": 4,
        "check_in_time": "14:00",
        "check_out_time": "12:00",
        "usp": "Arabian Sea view, Bandra-Worli Sea Link visible at night, in the heart of Bandra",
        "amenities": [
            "Sea view balcony", "WiFi 500 Mbps", "Air conditioning", "Full kitchen",
            "Smart TV (Netflix + Prime)", "Washing machine", "Daily housekeeping",
            "24/7 security", "Covered parking", "Backup power", "Work desk"
        ],
        "house_rules": (
            "No smoking on the balcony (society rule — Rs 5000 fine from society). "
            "No house parties. "
            "Max 4 guests — no additional overnight visitors. "
            "Society parking is for one car — second car at street parking (Rs 50/hr). "
            "Checkout 12 PM sharp — cleaning crew has a tight window."
        ),
        "neighborhood_info": (
            "Bandra Fort (sunset spot) is 1.2 km walk. Carter Road promenade is 800m. "
            "Linking Road shopping is 1 km. Hill Road restaurants are 600m. "
            "Bandstand (Shah Rukh Khan's Mannat nearby) is 2 km. "
            "Bandra railway station (Western line) is 1.5 km. "
            "Mumbai airport (BOM) is 12 km — 30-60 min by cab depending on traffic. "
            "Best food: Pali Village Café (Continental), Bastian (seafood), Candies (breakfast)."
        ),
        "faq": [
            {"question": "Is parking included?", "answer": "One covered parking spot is included. For a second vehicle, street parking on Chapel Road is Rs 40-50/hr."},
            {"question": "What is the best way to get to South Mumbai?", "answer": "Take the Bandra-Worli Sea Link by cab (Rs 250 toll + cab fare ~Rs 400). Or take Western line local train to Churchgate (Rs 40, 30 min)."},
            {"question": "Is the sea view unobstructed?", "answer": "Yes — at 16 floors up, nothing blocks the view. You can see the Sea Link and Mahim Bay clearly."},
        ],
    },
    {
        "name": "Naini Retreat — Lakeside Cottage",
        "city": "Nainital",
        "base_price": 4200,
        "max_guests": 4,
        "check_in_time": "13:00",
        "check_out_time": "11:00",
        "usp": "Steps from Naini Lake, mountain horse trails accessible from the door, colonial-era building",
        "amenities": [
            "Lake view", "WiFi", "Heating", "Full kitchen",
            "Fireplace (seasonal)", "Garden seating", "Parking (1 car)",
            "Board games & books", "Hot water", "Smart TV"
        ],
        "house_rules": (
            "No generators allowed in Nainital municipality after 10 PM. "
            "Power cuts are common — candles and a torch provided. "
            "Horse riding from the door — horses are privately owned, tip the horse handler. "
            "No alcohol on the lakefront (municipal rule). "
            "Keep food sealed — monkeys in the area."
        ),
        "neighborhood_info": (
            "Located in Nainital, Uttarakhand — the lake district of the Himalayas. "
            "Naini Lake is a 2-minute walk — boating Rs 120/30 min. "
            "Mall Road is 400m. Tibetan market is 600m. "
            "Naina Devi temple is 800m. Snow View Point ropeway is 1 km. "
            "Kathgodam railway station (Nainital's railhead) is 34 km. "
            "Pantnagar airport is 65 km. "
            "Best café: Cafe de Mall for Maggi and hot chai with lake view."
        ),
        "faq": [
            {"question": "Is boating on Naini Lake arranged by you?", "answer": "We don't arrange boating directly but the ghat is a 2-minute walk — easily done independently, Rs 120 for 30 minutes."},
            {"question": "Can we drive to the cottage?", "answer": "Yes, one car fits in the private space. Nainital has restricted vehicle entry in peak season — we'll advise you on timings."},
            {"question": "Is there heating?", "answer": "Electric room heaters are provided. Temperatures drop to 2-5°C in December-January."},
        ],
    },
    {
        "name": "Pine & Mist Cabin — Kasauli",
        "city": "Kasauli",
        "base_price": 3800,
        "max_guests": 4,
        "check_in_time": "13:00",
        "check_out_time": "11:00",
        "usp": "Inside Kasauli Cantonment, army-area quiet, dense deodar and pine, no vehicles inside",
        "amenities": [
            "Forest views", "WiFi", "Heating", "Fireplace", "Full kitchen",
            "Outdoor seating", "BBQ grill", "Board games", "Hot water", "Parking at gate"
        ],
        "house_rules": (
            "Kasauli Cantonment has strict rules: no vehicles inside (park at the gate). "
            "No alcohol on public paths — only on private property. "
            "Photography of army installations is prohibited. "
            "Quiet hours 10 PM–7 AM — army area, strictly enforced. "
            "Visitors must register at the cantonment gate with ID."
        ),
        "neighborhood_info": (
            "Located in Kasauli, Himachal Pradesh — a quiet British-era cantonment hill town. "
            "Christ Church (colonial) is 10 min walk. Monkey Point (highest peak) is 3 km walk. "
            "Kasauli Brewery (British-era) is 1 km — tours available. "
            "Dharampur market (nearest full market) is 4 km by cab. "
            "Chandigarh airport is 65 km, about 1.5 hrs. "
            "Kalka railway station is 40 km. Shimla is 77 km. "
            "Sunset from Gilbert Trail — 15-minute walk from the cabin — is outstanding."
        ),
        "faq": [
            {"question": "Can we drive up to the cabin?", "answer": "No — Kasauli Cantonment doesn't allow private vehicles inside. Park at the cantonment gate (free) and walk 400m to the cabin. We provide a trolley for luggage."},
            {"question": "Is there a monkey problem?", "answer": "Kasauli has langurs and rhesus monkeys. Keep food inside, don't feed them, and secure the door. We brief you on arrival."},
            {"question": "Is the fireplace functional?", "answer": "Yes, fully functional wood-burning fireplace. Wood is provided from October to March."},
        ],
    },
]


async def seed():
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Check if demo user already exists
        result = await session.execute(select(User).where(User.email == DEMO_EMAIL))
        existing = result.scalar_one_or_none()

        if existing:
            print(f"Demo user already exists (id={existing.id}). Checking properties...")
            user = existing
        else:
            hashed = bcrypt.hashpw(DEMO_PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            user = User(email=DEMO_EMAIL, hashed_password=hashed, name=DEMO_NAME)
            session.add(user)
            await session.flush()
            print(f"Created demo user: {DEMO_EMAIL} / {DEMO_PASSWORD}")

        # Count existing properties
        result = await session.execute(select(Property).where(Property.user_id == user.id))
        existing_props = result.scalars().all()
        print(f"Existing properties: {len(existing_props)}")

        added = 0
        for p in PROPERTIES:
            name = p["name"]
            exists = any(ep.name == name for ep in existing_props)
            if exists:
                print(f"  skip (exists): {name}")
                continue

            prop = Property(
                user_id=user.id,
                name=p["name"],
                city=p["city"],
                base_price=p["base_price"],
                max_guests=p["max_guests"],
                check_in_time=p["check_in_time"],
                check_out_time=p["check_out_time"],
                usp=p["usp"],
                amenities=p["amenities"],
                house_rules=p["house_rules"],
                neighborhood_info=p["neighborhood_info"],
                faq=p["faq"],
            )
            session.add(prop)
            added += 1
            print(f"  added: {name}")

        await session.commit()
        print(f"\nDone — {added} properties added.")
        print(f"\nDemo login:")
        print(f"  Email:    {DEMO_EMAIL}")
        print(f"  Password: {DEMO_PASSWORD}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
