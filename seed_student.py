"""
seed_students.py
Run once:  python seed_students.py
Inserts 50,000 fake verified students from around the world.
"""

import os, random, string, json
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ── paste your app setup here so we reuse the same db ──────────────
from app import app, db
from models import User

# ═══════════════════════════════════════════════════════════════════
#  DATA POOLS
# ═══════════════════════════════════════════════════════════════════

COUNTRIES = [
    "Nigeria","Ghana","Kenya","South Africa","Egypt","Ethiopia","Tanzania","Uganda","Senegal","Cameroon",
    "United States","Canada","United Kingdom","Australia","New Zealand","Ireland",
    "Germany","France","Netherlands","Sweden","Norway","Denmark","Finland","Switzerland","Austria","Belgium","Spain","Italy","Portugal",
    "Brazil","Mexico","Argentina","Colombia","Chile","Peru","Venezuela","Ecuador",
    "India","Pakistan","Bangladesh","Sri Lanka","Nepal","Philippines","Indonesia","Malaysia","Singapore","Vietnam","Thailand","South Korea","Japan","China","Taiwan","Hong Kong",
    "Saudi Arabia","UAE","Qatar","Kuwait","Jordan","Lebanon","Turkey","Israel","Iran","Iraq",
    "Russia","Ukraine","Poland","Czech Republic","Hungary","Romania","Bulgaria","Serbia","Croatia",
    "South Sudan","Zimbabwe","Zambia","Mozambique","Rwanda","Ivory Coast","Mali","Burkina Faso","Congo",
    "Jamaica","Trinidad and Tobago","Barbados","Haiti","Cuba","Dominican Republic",
    "Morocco","Tunisia","Algeria","Libya","Sudan","Somalia","Eritrea","Djibouti",
    "Afghanistan","Kazakhstan","Uzbekistan","Azerbaijan","Georgia","Armenia","Moldova","Belarus",
]

FIRST_NAMES = [
    # English/Western
    "James","John","Robert","Michael","William","David","Richard","Joseph","Thomas","Charles",
    "Mary","Patricia","Jennifer","Linda","Barbara","Elizabeth","Susan","Jessica","Sarah","Karen",
    "Emma","Olivia","Noah","Liam","Sophia","Isabella","Mia","Charlotte","Amelia","Evelyn",
    # African
    "Chidi","Emeka","Ngozi","Amaka","Yemi","Seun","Tunde","Bola","Kemi","Adeola",
    "Kwame","Abena","Kofi","Ama","Yaw","Akosua","Fiifi","Efua","Nana","Esi",
    "Amara","Fatou","Mariama","Ibrahima","Ousmane","Aissatou","Moussa","Adama","Fatoumata","Boubacar",
    "Tendai","Chipo","Takoda","Rudo","Tinashe","Rutendo","Farai","Kudzai","Simba","Tariro",
    # South Asian
    "Arjun","Priya","Rahul","Sneha","Vikram","Ananya","Rohan","Kavya","Aditya","Pooja",
    "Muhammad","Ayesha","Ahmed","Fatima","Hassan","Zainab","Omar","Sana","Ali","Nadia",
    "Rajan","Meera","Suresh","Lakshmi","Vijay","Sunita","Ramesh","Geetha","Mohan","Radha",
    # East Asian
    "Wei","Jing","Ming","Ling","Fang","Hui","Xiao","Yan","Peng","Lei",
    "Sakura","Yuki","Haruto","Aoi","Sota","Hina","Ren","Yuna","Kaito","Nao",
    "Ji-ho","Soo-yeon","Min-jun","Ji-woo","Seung-hyun","Ye-jin","Hyun","Da-eun","Joon","Eun",
    # Latin American
    "Carlos","Maria","Juan","Ana","Luis","Sofia","Jorge","Isabella","Miguel","Valentina",
    "Pedro","Camila","Diego","Lucia","Alejandro","Gabriela","Andres","Natalia","Felipe","Daniela",
    # Middle Eastern
    "Yusuf","Layla","Khalid","Mariam","Tariq","Hana","Samir","Nour","Rami","Dina",
    "Amir","Yasmin","Bilal","Rania","Ziad","Sara","Faris","Lina","Karim","Mona",
    # European
    "Luca","Elena","Matteo","Sofia","Marco","Giulia","Alessandro","Chiara","Lorenzo","Francesca",
    "Pierre","Marie","Jean","Claire","Antoine","Camille","Nicolas","Julie","Etienne","Amelie",
    "Lars","Ingrid","Erik","Astrid","Bjorn","Freya","Magnus","Sigrid","Olaf","Helga",
    "Dmitri","Natasha","Ivan","Olga","Alexei","Tatiana","Pavel","Irina","Sergei","Yana",
]

LAST_NAMES = [
    # Western
    "Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Wilson","Moore",
    "Taylor","Anderson","Thomas","Jackson","White","Harris","Martin","Thompson","Young","Lewis",
    # African
    "Okafor","Adeyemi","Nwosu","Chukwu","Okonkwo","Adesanya","Babatunde","Olawale","Eze","Nwachukwu",
    "Mensah","Asante","Boateng","Owusu","Adjei","Darko","Antwi","Kyei","Amponsah","Osei",
    "Diallo","Camara","Sylla","Bah","Barry","Sow","Keita","Conde","Toure","Traore",
    "Moyo","Dube","Ncube","Ndlovu","Sibanda","Mpofu","Nkosi","Zulu","Dlamini","Nxumalo",
    # South Asian
    "Sharma","Patel","Singh","Kumar","Gupta","Verma","Mishra","Yadav","Shah","Joshi",
    "Khan","Ahmed","Ali","Hussain","Rahman","Chowdhury","Islam","Begum","Hasan","Akhtar",
    # East Asian
    "Wang","Li","Zhang","Chen","Liu","Yang","Huang","Zhao","Wu","Zhou",
    "Tanaka","Suzuki","Sato","Watanabe","Yamamoto","Kobayashi","Ito","Kato","Nakamura","Hayashi",
    "Kim","Lee","Park","Choi","Jung","Kang","Cho","Yoon","Chang","Lim",
    # Latin American
    "Rodriguez","Gonzalez","Hernandez","Lopez","Martinez","Sanchez","Perez","Torres","Ramirez","Flores",
    "Silva","Santos","Oliveira","Souza","Costa","Ferreira","Alves","Rodrigues","Lima","Carvalho",
    # Middle Eastern
    "Al-Hassan","Al-Rashid","Al-Farsi","Al-Sayed","Al-Amin","Bakr","Qureshi","Malik","Raza","Mirza",
    # European
    "Rossi","Ferrari","Russo","Bianchi","Romano","Gallo","Conti","Ricci","Marino","Greco",
    "Dubois","Laurent","Simon","Michel","Lefevre","Moreau","Girard","Andre","Mercier","Dupont",
    "Müller","Schmidt","Schneider","Fischer","Weber","Meyer","Wagner","Becker","Schulz","Hoffmann",
    "Ivanov","Petrov","Sidorov","Kozlov","Novikov","Morozov","Volkov","Popov","Lebedev","Sokolov",
    "Kowalski","Nowak","Wiśniewski","Wójcik","Kowalczyk","Kaminski","Lewandowski","Zielinski","Szymanski","Woźniak",
]

UNIVERSITIES = [
    # Nigeria
    "University of Lagos","University of Ibadan","Ahmadu Bello University","Obafemi Awolowo University",
    "University of Nigeria Nsukka","Lagos State University","Covenant University","Babcock University",
    "Federal University of Technology Akure","University of Benin","Nnamdi Azikiwe University",
    # Ghana
    "University of Ghana","Kwame Nkrumah University of Science and Technology","University of Cape Coast",
    "Ashesi University","Ghana Institute of Management and Public Administration",
    # Kenya
    "University of Nairobi","Kenyatta University","Strathmore University","Moi University",
    "Jomo Kenyatta University of Agriculture and Technology",
    # South Africa
    "University of Cape Town","University of the Witwatersrand","Stellenbosch University",
    "University of Johannesburg","University of Pretoria","University of KwaZulu-Natal",
    # Egypt
    "Cairo University","American University in Cairo","Ain Shams University","Alexandria University",
    # US
    "Harvard University","MIT","Stanford University","Yale University","Princeton University",
    "Columbia University","University of Chicago","Duke University","Johns Hopkins University",
    "University of Michigan","UCLA","UC Berkeley","NYU","Boston University","Georgetown University",
    "University of Texas at Austin","Penn State University","Ohio State University",
    "University of Florida","Arizona State University","Purdue University","Indiana University",
    # UK
    "University of Oxford","University of Cambridge","Imperial College London","UCL",
    "London School of Economics","King's College London","University of Manchester",
    "University of Edinburgh","University of Birmingham","University of Leeds","University of Bristol",
    "University of Warwick","University of Glasgow","University of Nottingham","Durham University",
    # Canada
    "University of Toronto","University of British Columbia","McGill University",
    "University of Alberta","McMaster University","University of Waterloo","Queen's University",
    # Australia
    "University of Melbourne","Australian National University","University of Sydney",
    "University of Queensland","Monash University","UNSW Sydney","University of Adelaide",
    # Germany
    "Technical University of Munich","Ludwig Maximilian University","Humboldt University Berlin",
    "Heidelberg University","RWTH Aachen University","Free University of Berlin",
    # France
    "Sorbonne University","Sciences Po","École Polytechnique","University of Paris",
    "Pierre and Marie Curie University","University of Lyon","University of Strasbourg",
    # India
    "Indian Institute of Technology Bombay","IIT Delhi","IIT Madras","IIT Kanpur",
    "University of Delhi","Jawaharlal Nehru University","Anna University",
    "Bangalore University","Pune University","Calcutta University","Mumbai University",
    # China
    "Peking University","Tsinghua University","Fudan University","Zhejiang University",
    "Shanghai Jiao Tong University","Sun Yat-sen University","Nanjing University",
    # Japan
    "University of Tokyo","Kyoto University","Osaka University","Tohoku University",
    "Tokyo Institute of Technology","Nagoya University","Waseda University","Keio University",
    # South Korea
    "Seoul National University","KAIST","Yonsei University","Korea University","POSTECH",
    # Brazil
    "University of São Paulo","UNICAMP","Federal University of Rio de Janeiro",
    "Federal University of Minas Gerais","Pontifical Catholic University",
    # Middle East
    "King Abdulaziz University","King Fahd University of Petroleum","American University of Beirut",
    "University of Jordan","Qatar University","UAE University","Kuwait University",
    "Istanbul University","Boğaziçi University","Middle East Technical University",
    # Russia / Eastern Europe
    "Moscow State University","Saint Petersburg State University","Novosibirsk State University",
    "Warsaw University","Charles University Prague","Budapest University of Technology",
    # Southeast Asia
    "National University of Singapore","Nanyang Technological University","University of Malaya",
    "University of the Philippines","Ateneo de Manila University","University of Indonesia",
    "Chulalongkorn University","Vietnam National University",
    # Others
    "University of Auckland","University of Cape Town","Makerere University",
    "Addis Ababa University","University of Dar es Salaam","University of Zambia",
    "University of Zimbabwe","University of the West Indies",
    "Pontifical Catholic University of Chile","Universidad de Buenos Aires",
    "Universidad Nacional Autónoma de México","Tecnológico de Monterrey",
]

PROFESSIONS = [
    "Student","Engineer","Doctor","Nurse","Teacher","Researcher","Software Developer",
    "Accountant","Lawyer","Architect","Pharmacist","Dentist","Data Scientist",
    "Business Analyst","Marketing Specialist","Graphic Designer","Journalist","Economist",
    "Biologist","Chemist","Physicist","Mathematician","Psychologist","Social Worker",
    "Entrepreneur","Product Manager","Financial Analyst","HR Specialist","Consultant",
]

STUDY_LEVELS = [
    "High School","Undergraduate","Postgraduate","PhD","Professional","Self-learner"
]

STUDY_LEVEL_WEIGHTS = [15, 45, 25, 8, 4, 3]   # % roughly

TAGS_POOL = [
    "Mathematics","Physics","Chemistry","Biology","Computer Science","Engineering",
    "Medicine","Law","Economics","Business","Marketing","Psychology","History",
    "Literature","Philosophy","Art","Music","Sports","Technology","AI","Machine Learning",
    "Data Science","Web Development","Mobile Dev","Cybersecurity","Finance","Accounting",
    "Architecture","Education","Research","Writing","Public Speaking","Leadership",
]

# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════

def rand_username(first, last, idx):
    sep = random.choice(["_",".",""]);
    suffix = random.choice([str(idx % 9999), str(random.randint(1,999)), ""])
    base = f"{first.lower()}{sep}{last.lower()}{suffix}"
    # keep only valid chars
    clean = ''.join(c for c in base if c.isalnum() or c == '_')
    return clean[:48] or f"user{idx}"

def rand_email(first, last, idx):
    domains = ["gmail.com","yahoo.com","outlook.com","hotmail.com","protonmail.com",
               "icloud.com","edu.ng","student.ac.uk","students.com","university.edu",
               "mail.com","yandex.com","zoho.com"]
    sep = random.choice(["_",".",""]);
    return f"{first.lower()}{sep}{last.lower()}{idx}@{random.choice(domains)}"

def rand_date_joined():
    days_ago = random.randint(0, 730)
    return datetime.utcnow() - timedelta(days=days_ago)

def study_level():
    return random.choices(STUDY_LEVELS, weights=STUDY_LEVEL_WEIGHTS, k=1)[0]

def fake_password_hash():
    # All fake students get a known hash so you can login as any of them
    # password = "FakePass1!" for all
    from werkzeug.security import generate_password_hash
    return generate_password_hash("FakePass1!")

# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════

TOTAL = 50_000
BATCH = 500   # insert in batches to avoid memory blow-up

def run():
    with app.app_context():
        existing = db.session.query(User.username).all()
        taken_usernames = {r[0].lower() for r in existing}
        taken_emails    = {r[0].lower() for r in db.session.query(User.email).all()}

        print(f"Starting seed. Existing users: {len(taken_usernames)}")

        pw_hash = fake_password_hash()
        inserted = 0
        batch_users = []

        for i in range(1, TOTAL + 1):
            first = random.choice(FIRST_NAMES)
            last  = random.choice(LAST_NAMES)
            country = random.choice(COUNTRIES)
            school  = random.choice(UNIVERSITIES)

            username = rand_username(first, last, i)
            # ensure unique
            attempt = 0
            base_username = username
            while username.lower() in taken_usernames:
                attempt += 1
                username = f"{base_username}{attempt}"
            taken_usernames.add(username.lower())

            email = rand_email(first, last, i)
            attempt = 0
            base_email = email
            while email.lower() in taken_emails:
                attempt += 1
                parts = base_email.split("@")
                email = f"{parts[0]}{attempt}@{parts[1]}"
            taken_emails.add(email.lower())

            u = User(
                name        = f"{first} {last}",
                username    = username,
                email       = email,
                school      = school,
                profession  = random.choice(PROFESSIONS),
                study_level = study_level(),
                country     = country,
                is_verified = True,
                created_at  = rand_date_joined(),
            )
            u.password_hash = pw_hash   # skip hashing 50k times — reuse same hash

            batch_users.append(u)

            if len(batch_users) >= BATCH:
                db.session.bulk_save_objects(batch_users)
                db.session.commit()
                inserted += len(batch_users)
                batch_users = []
                print(f"  Inserted {inserted:,} / {TOTAL:,}…")

        # final batch
        if batch_users:
            db.session.bulk_save_objects(batch_users)
            db.session.commit()
            inserted += len(batch_users)

        print(f"\n✅ Done! {inserted:,} fake students inserted.")
        print("   All fake accounts use password: FakePass1!")

if __name__ == "__main__":
    run()