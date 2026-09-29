import sqlite3
import os
import json
from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime

app = Flask(__name__)

# On Vercel the filesystem is read-only except /tmp (data is ephemeral).
if os.environ.get('VERCEL'):
    DATABASE = '/tmp/events_connect.db'
else:
    DATABASE = os.path.join(os.path.dirname(__file__), 'events_connect.db')

# ------------------------------------------------------------------
# Types d'événements & Catégories de métiers
# ------------------------------------------------------------------

EVENT_TYPES = {
    'mariage':      {'label': 'Mariage',             'emoji': '💍', 'badge': 'Mariage 💍'},
    'bapteme':      {'label': 'Baptême',             'emoji': '🕊️', 'badge': 'Baptême 🕊️'},
    'anniversaire': {'label': 'Anniversaire',         'emoji': '🎂', 'badge': 'Anniversaire 🎂'},
    'soiree':       {'label': 'Soirée & Gala',       'emoji': '🍸', 'badge': 'Soirée 🍸'},
    'pro':          {'label': 'Événement Pro',       'emoji': '💼', 'badge': 'Pro 💼'},
}

CATEGORIES = {
    'photographe': {'label': 'Photographe & Vidéaste',    'emoji': '📷'},
    'dj':          {'label': 'DJ & Animation Sonore',     'emoji': '🎧'},
    'traiteur':    {'label': 'Traiteur & Pâtisserie',     'emoji': '🍽️'},
    'decorateur':  {'label': 'Décorateur & Scénographie', 'emoji': '🌸'},
    'animateur':   {'label': 'Animateur & MC',            'emoji': '🎤'},
    'makeup':      {'label': 'Maquillage & Coiffure',     'emoji': '💄'},
}

# ------------------------------------------------------------------
# Database helpers & migrations
# ------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS providers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                category    TEXT NOT NULL,
                city        TEXT NOT NULL,
                location    TEXT NOT NULL,
                price_from  INTEGER NOT NULL DEFAULT 0,
                price_label TEXT NOT NULL DEFAULT '',
                specialties TEXT NOT NULL DEFAULT '',
                phone       TEXT NOT NULL,
                image_url   TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                verified    INTEGER NOT NULL DEFAULT 0,
                event_types TEXT NOT NULL DEFAULT 'mariage,bapteme,anniversaire,soiree',
                portfolio   TEXT NOT NULL DEFAULT '[]',
                created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS reviews (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id INTEGER NOT NULL,
                author      TEXT NOT NULL,
                rating      INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                comment     TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(provider_id) REFERENCES providers(id) ON DELETE CASCADE
            );
        ''')

        # Migrations automatiques si la table existait sans ces colonnes
        cols = [row['name'] for row in conn.execute('PRAGMA table_info(providers)').fetchall()]
        if 'event_types' not in cols:
            conn.execute("ALTER TABLE providers ADD COLUMN event_types TEXT NOT NULL DEFAULT 'mariage,bapteme,anniversaire,soiree'")
        if 'portfolio' not in cols:
            conn.execute("ALTER TABLE providers ADD COLUMN portfolio TEXT NOT NULL DEFAULT '[]'")
        conn.commit()

def seed_if_empty():
    with get_db() as conn:
        count = conn.execute('SELECT COUNT(*) FROM providers').fetchone()[0]
        # Si la base est vide ou contient les anciennes données sans réalisations complètes
        if count > 0:
            sample = conn.execute('SELECT portfolio FROM providers LIMIT 1').fetchone()
            if sample and sample['portfolio'] and len(sample['portfolio']) > 10:
                return
            # Si les données étaient sommaires, on réinitialise pour garantir une démo complète
            conn.execute('DELETE FROM reviews')
            conn.execute('DELETE FROM providers')

        providers_data = [
            (
                'Studio Teranga Vision Pro',
                'photographe',
                'Dakar',
                'Almadies, Dakar',
                150000,
                'À partir de 150 000 FCFA',
                'Mariages de prestige, Baptêmes, Vidéos Drone 4K',
                '221771234567',
                'https://images.unsplash.com/photo-1537633552985-df8429e8048b?w=900&auto=format&fit=crop&q=80',
                'Spécialistes des mariages élégants et des grandes réceptions au Sénégal. Notre équipe capture chaque instant avec du matériel cinéma haut de gamme (boîtiers Sony, drone Mavic 3, stabilisateurs). Plus de 8 ans d\'expérience dans l\'événementiel à Dakar et dans la sous-région.',
                1,
                'mariage,bapteme,anniversaire,soiree',
                json.dumps([
                    {
                        'url': 'https://images.unsplash.com/photo-1519741497674-611481863552?w=900&auto=format&fit=crop&q=80',
                        'title': 'Cérémonie de Mariage féerique',
                        'event_type': 'Mariage 💍',
                        'caption': 'Couverture photo complète d\'une union aux Almadies'
                    },
                    {
                        'url': 'https://images.unsplash.com/photo-1511285560929-80b456fea0bc?w=900&auto=format&fit=crop&q=80',
                        'title': 'Séance couple au coucher de soleil',
                        'event_type': 'Mariage 💍',
                        'caption': 'Shooting intimiste sur la corniche des Almadies'
                    },
                    {
                        'url': 'https://images.unsplash.com/photo-1519225438150-7d1b354f5292?w=900&auto=format&fit=crop&q=80',
                        'title': 'Cérémonie de Baptême traditionnel',
                        'event_type': 'Baptême 🕊️',
                        'caption': 'Moments d\'émotions en famille capturés sur le vif'
                    },
                    {
                        'url': 'https://images.unsplash.com/photo-1492684223066-81342ee5ff30?w=900&auto=format&fit=crop&q=80',
                        'title': 'Gala & Soirée d\'anniversaire VIP',
                        'event_type': 'Soirée 🍸',
                        'caption': 'Reportage événementiel avec éclairage studio'
                    }
                ])
            ),
            (
                'DJ Keur Sa Rew & Sound System',
                'dj',
                'Dakar',
                'Mermoz & Ngor, Dakar',
                120000,
                'À partir de 120 000 FCFA / soirée',
                'Mbalax, Afrobeat, Amapiano, Années 80/90, House',
                '221761112233',
                'https://images.unsplash.com/photo-1516450360452-9312f5e86fc7?w=900&auto=format&fit=crop&q=80',
                'Ambianceur professionnel de référence à Dakar. Équipé d\'un système son Line Array JBL, platines Pioneer Nexus 2, jeux de lumières robotisés et machine à fumée lourde pour l\'ouverture de bal. Nous assurons le tempo parfait pour vos invités.',
                1,
                'mariage,anniversaire,soiree,bapteme',
                json.dumps([
                    {
                        'url': 'https://images.unsplash.com/photo-1470225620780-dba8ba36b745?w=900&auto=format&fit=crop&q=80',
                        'title': 'Régie DJ & Scénographie Sonore',
                        'event_type': 'Soirée 🍸',
                        'caption': 'Installation sonore complète pour 300 convives'
                    },
                    {
                        'url': 'https://images.unsplash.com/photo-1492684223066-81342ee5ff30?w=900&auto=format&fit=crop&q=80',
                        'title': 'Ouverture de Bal Mariage féerique',
                        'event_type': 'Mariage 💍',
                        'caption': 'Fumée lourde et jeux de faisceaux dorés'
                    },
                    {
                        'url': 'https://images.unsplash.com/photo-1533174072545-7a4b6ad7a6c3?w=900&auto=format&fit=crop&q=80',
                        'title': 'Anniversaire privé en villa',
                        'event_type': 'Anniversaire 🎂',
                        'caption': 'Set Afrobeat & Pop jusqu\'au petit matin'
                    }
                ])
            ),
            (
                'Saveurs & Délices du Sahel',
                'traiteur',
                'Dakar',
                'Plateau & Fann Résidence, Dakar',
                6500,
                'À partir de 6 500 FCFA / invité',
                'Buffets Gastronomiques, Thiéboudienne royale, Mignardises, Cocktails',
                '221775556677',
                'https://images.unsplash.com/photo-1555244162-803834f70033?w=900&auto=format&fit=crop&q=80',
                'Service traiteur d\'excellence alliant authenticité des recettes locales sénégalaises et créativité internationale. Menus sur-mesure pour mariages, baptêmes et cocktails de direction, avec une équipe de serveurs en tenue impeccable.',
                1,
                'mariage,bapteme,soiree,pro,anniversaire',
                json.dumps([
                    {
                        'url': 'https://images.unsplash.com/photo-1555244162-803834f70033?w=900&auto=format&fit=crop&q=80',
                        'title': 'Buffet de Cérémonie Raffiné',
                        'event_type': 'Mariage 💍',
                        'caption': 'Assortiment d\'entrées chaudes et froides prestigieuses'
                    },
                    {
                        'url': 'https://images.unsplash.com/photo-1504674900247-0877df9cc836?w=900&auto=format&fit=crop&q=80',
                        'title': 'Plats signatures sénégalais revisités',
                        'event_type': 'Baptême 🕊️',
                        'caption': 'Agneau farci au feu de bois et déclinaisons de riz'
                    },
                    {
                        'url': 'https://images.unsplash.com/photo-1535141192574-5d4897c13136?w=900&auto=format&fit=crop&q=80',
                        'title': 'Pièce Montée & Pâtisseries Fines',
                        'event_type': 'Anniversaire 🎂',
                        'caption': 'Gâteau sur-mesure personnalisé aux saveurs mangue-passion'
                    }
                ])
            ),
            (
                'Luxe & Scénographie Événementielle',
                'decorateur',
                'Dakar',
                'Saly & Dakar, Sénégal',
                180000,
                'À partir de 180 000 FCFA',
                'Trônes des mariés, Arches florales, Tables féeriques, Lustres cristal',
                '221778889900',
                'https://images.unsplash.com/photo-1519167758481-83f550bb49b3?w=900&auto=format&fit=crop&q=80',
                'Nous transformons votre lieu de réception en un décor digne des contes de fées. Conception 3D préalable, fleurs fraîches d\'importation et compositions locales, mobilier VIP, voilages drapés et éclairage d\'ambiance tamisé.',
                1,
                'mariage,bapteme,anniversaire,soiree',
                json.dumps([
                    {
                        'url': 'https://images.unsplash.com/photo-1519167758481-83f550bb49b3?w=900&auto=format&fit=crop&q=80',
                        'title': 'Table d\'Honneur Impériale',
                        'event_type': 'Mariage 💍',
                        'caption': 'Chaises Napoléon cristal, chemins de table dorés et bouquets hauts'
                    },
                    {
                        'url': 'https://images.unsplash.com/photo-1520854221256-17451cc331bf?w=900&auto=format&fit=crop&q=80',
                        'title': 'Arche Florale pour Cérémonie',
                        'event_type': 'Mariage 💍',
                        'caption': 'Compositions de roses blanches, orchidées et feuillages tropicaux'
                    },
                    {
                        'url': 'https://images.unsplash.com/photo-1464366400600-7168b8af9bc3?w=900&auto=format&fit=crop&q=80',
                        'title': 'Espace Lounge & Bar Festif',
                        'event_type': 'Soirée 🍸',
                        'caption': 'Canapés velours et guirlandes suspendues pour fête privée'
                    }
                ])
            ),
            (
                'MC Amadou — Maître de Cérémonie',
                'animateur',
                'Dakar',
                'Dakar & Régions',
                90000,
                'À partir de 90 000 FCFA',
                'Bilingue Français/Wolof, Rituels traditionnels, Timing parfait',
                '221774443322',
                'https://images.unsplash.com/photo-1475721027785-f74eccf877e2?w=900&auto=format&fit=crop&q=80',
                'Une célébration réussie passe par une coordination sans faille et une énergie communicative. Maître de cérémonie expérimenté, je gère les temps forts, l\'accueil des mariés, la distribution des discours et l\'harmonie avec le DJ et le traiteur.',
                1,
                'mariage,bapteme,pro,soiree',
                json.dumps([
                    {
                        'url': 'https://images.unsplash.com/photo-1475721027785-f74eccf877e2?w=900&auto=format&fit=crop&q=80',
                        'title': 'Accueil & Présentation Officielle',
                        'event_type': 'Mariage 💍',
                        'caption': 'Coordination de l\'entrée triomphale des mariés'
                    },
                    {
                        'url': 'https://images.unsplash.com/photo-1511578314322-379afb476865?w=900&auto=format&fit=crop&q=80',
                        'title': 'Animation de Gala d\'Entreprise',
                        'event_type': 'Pro 💼',
                        'caption': 'Gestion du protocole et de la tombola pour 250 cadres'
                    }
                ])
            ),
            (
                'Glamour Bridal Touch',
                'makeup',
                'Saly',
                'Saly Portudal & Mbour',
                45000,
                'À partir de 45 000 FCFA',
                'Mise en beauté mariée, Attache de foulard haute couture, Coiffure soirée',
                '221782221100',
                'https://images.unsplash.com/photo-1487412720507-e7ab37603c6f?w=900&auto=format&fit=crop&q=80',
                'Artiste maquilleuse certifiée spécialisée dans le maquillage de mariée longue tenue, résistant à la chaleur et aux émotions. Produits professionnels waterproof (Fenty, MAC, Nars). Déplacement possible sur le lieu des préparatifs.',
                1,
                'mariage,soiree,anniversaire,bapteme',
                json.dumps([
                    {
                        'url': 'https://images.unsplash.com/photo-1487412720507-e7ab37603c6f?w=900&auto=format&fit=crop&q=80',
                        'title': 'Make-up Mariée Éclat Doré',
                        'event_type': 'Mariage 💍',
                        'caption': 'Teint lumineux naturel et regard souligné longue tenue'
                    },
                    {
                        'url': 'https://images.unsplash.com/photo-1522337360788-8b13dee7a37e?w=900&auto=format&fit=crop&q=80',
                        'title': 'Mise en beauté Soirée d\'Anniversaire',
                        'event_type': 'Anniversaire 🎂',
                        'caption': 'Smoky eyes glamour et lèvres nudes'
                    }
                ])
            )
        ]

        conn.executemany('''
            INSERT INTO providers
                (name, category, city, location, price_from, price_label,
                 specialties, phone, image_url, description, verified, event_types, portfolio)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', providers_data)

        # Ajout de quelques avis d'exemple crédibles
        reviews_data = [
            (1, 'Aminata & Modou Diallo', 5, 'Une équipe formidable ! Nos photos de mariage sont juste magnifiques, le rendu du drone a bluffé tous nos invités. Très pro et ponctuels !', '2025-02-14 18:30'),
            (1, 'Cheikh Ndiaye', 5, 'Le shooting pour le baptême de notre fils était parfait. Les photos ont été livrées en moins d\'une semaine sur galerie privée.', '2025-01-20 14:15'),
            (2, 'Fatou Bintou Sow', 5, 'DJ Keur Sa Rew a mis le feu à notre soirée de mariage du début à la fin ! La fumée lourde lors de l\'ouverture de bal était magique.', '2025-02-28 23:45'),
            (3, 'Moussa Seck', 5, 'Un traiteur irréprochable. Le buffet était copieux, savoureux et très bien présenté. Mention spéciale pour le cocktail de bienvenue.', '2025-03-05 12:00'),
            (4, 'Awa Ba', 5, 'La décoration a dépassé toutes nos attentes ! La table d\'honneur était somptueuse. Merci pour votre écoute et votre talent.', '2025-02-10 16:20'),
            (6, 'Marième Cissé', 5, 'Maquillage impeccable du matin jusqu\'à tard dans la nuit malgré les danses. Je recommande à 100% pour vos mariages !', '2025-03-12 19:10')
        ]
        conn.executemany('''
            INSERT INTO reviews (provider_id, author, rating, comment, created_at)
            VALUES (?,?,?,?,?)
        ''', reviews_data)

        conn.commit()

init_db()
seed_if_empty()

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def enrich(row):
    """Convert a sqlite3.Row to a plain dict and add computed fields."""
    p = dict(row)
    cat = CATEGORIES.get(p['category'], {'label': p['category'], 'emoji': '🎭'})
    p['category_display'] = cat['label']
    p['category_emoji']   = cat['emoji']
    p['stars_full']  = int(round(p.get('avg_rating', 0)))
    p['avg_rating']  = round(p.get('avg_rating', 0), 1)

    # Parsing du portfolio
    raw_portfolio = p.get('portfolio') or '[]'
    try:
        p['portfolio_list'] = json.loads(raw_portfolio)
    except Exception:
        p['portfolio_list'] = []

    # Parsing des types d'événements
    raw_events = p.get('event_types') or ''
    event_keys = [k.strip().lower() for k in raw_events.split(',') if k.strip()]
    p['event_types_list'] = [EVENT_TYPES[k] for k in event_keys if k in EVENT_TYPES]
    p['event_keys'] = event_keys

    return p

# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@app.route('/')
def home():
    cat_filter   = request.args.get('category', '').strip().lower()
    city_filter  = request.args.get('city', '').strip().lower()
    event_filter = request.args.get('event_type', '').strip().lower()
    max_price    = request.args.get('max_price', '').strip()

    sql = '''
        SELECT p.*,
               COUNT(r.id)          AS review_count,
               COALESCE(AVG(r.rating), 0) AS avg_rating
        FROM providers p
        LEFT JOIN reviews r ON r.provider_id = p.id
        WHERE 1=1
    '''
    params = []

    if cat_filter:
        sql += ' AND p.category = ?'
        params.append(cat_filter)
    if city_filter:
        sql += ' AND (LOWER(p.city) LIKE ? OR LOWER(p.location) LIKE ?)'
        params += [f'%{city_filter}%', f'%{city_filter}%']
    if event_filter:
        sql += ' AND LOWER(p.event_types) LIKE ?'
        params.append(f'%{event_filter}%')
    if max_price:
        try:
            sql += ' AND p.price_from <= ?'
            params.append(float(max_price))
        except ValueError:
            pass

    sql += ' GROUP BY p.id ORDER BY avg_rating DESC, p.created_at DESC'

    db = get_db()
    rows = db.execute(sql, params).fetchall()
    db.close()

    providers = [enrich(r) for r in rows]

    return render_template(
        'index.html',
        providers=providers,
        categories=CATEGORIES,
        event_types=EVENT_TYPES,
        sel_category=cat_filter,
        sel_city=city_filter,
        sel_event_type=event_filter,
        sel_max_price=max_price
    )


@app.route('/prestataire/<int:pid>')
def detail(pid):
    db = get_db()
    row = db.execute('''
        SELECT p.*,
               COUNT(r.id)                AS review_count,
               COALESCE(AVG(r.rating), 0) AS avg_rating
        FROM providers p
        LEFT JOIN reviews r ON r.provider_id = p.id
        WHERE p.id = ?
        GROUP BY p.id
    ''', [pid]).fetchone()

    if row is None:
        db.close()
        return render_template('404.html'), 404

    provider = enrich(row)
    reviews  = [dict(r) for r in db.execute(
        'SELECT * FROM reviews WHERE provider_id = ? ORDER BY created_at DESC',
        [pid]
    ).fetchall()]
    db.close()

    return render_template(
        'prestataire.html',
        provider=provider,
        reviews=reviews,
        event_types=EVENT_TYPES
    )


@app.route('/prestataire/<int:pid>/avis', methods=['POST'])
def add_review(pid):
    author  = request.form.get('author', '').strip()
    rating  = request.form.get('rating', '0').strip()
    comment = request.form.get('comment', '').strip()

    try:
        rating_int = int(rating)
        assert 1 <= rating_int <= 5
    except (ValueError, AssertionError):
        return redirect(url_for('detail', pid=pid))

    if author:
        db = get_db()
        db.execute(
            'INSERT INTO reviews (provider_id, author, rating, comment, created_at) VALUES (?,?,?,?,?)',
            [pid, author, rating_int, comment, datetime.now().strftime('%Y-%m-%d %H:%M')]
        )
        db.commit()
        db.close()

    return redirect(url_for('detail', pid=pid) + '#avis')


@app.route('/inscription', methods=['GET', 'POST'])
def inscription():
    error = None

    if request.method == 'POST':
        name        = request.form.get('name', '').strip()
        category    = request.form.get('category', '').strip()
        city        = request.form.get('city', '').strip()
        location    = request.form.get('location', '').strip()
        price_from  = request.form.get('price_from', '0').strip()
        price_label = request.form.get('price_label', '').strip()
        specialties = request.form.get('specialties', '').strip()
        phone       = request.form.get('phone', '').strip().replace(' ', '').replace('+', '')
        image_url   = request.form.get('image_url', '').strip()
        description = request.form.get('description', '').strip()

        # Récupération des types d'événements cochés
        selected_events = request.form.getlist('event_types')
        events_str = ','.join(selected_events) if selected_events else 'mariage,bapteme,anniversaire,soiree'

        # Récupération d'une réalisation initiale si fournie
        portfolio_url = request.form.get('portfolio_url', '').strip()
        portfolio_title = request.form.get('portfolio_title', '').strip()
        portfolio_items = []
        if portfolio_url:
            portfolio_items.append({
                'url': portfolio_url,
                'title': portfolio_title or 'Réalisation récente',
                'event_type': 'Événement',
                'caption': 'Prestation réalisée par nos soins'
            })

        if not all([name, category, city, location, phone]):
            error = 'Veuillez remplir tous les champs obligatoires (*)'
        elif category not in CATEGORIES:
            error = 'Catégorie invalide.'
        else:
            try:
                price_int = int(price_from)
            except ValueError:
                price_int = 0

            if not price_label:
                price_label = f'À partir de {price_int:,} FCFA'.replace(',', ' ')

            db = get_db()
            db.execute('''
                INSERT INTO providers
                    (name, category, city, location, price_from, price_label,
                     specialties, phone, image_url, description, verified, event_types, portfolio)
                VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?)
            ''', [name, category, city, location, price_int, price_label,
                  specialties, phone, image_url, description, events_str, json.dumps(portfolio_items)])
            db.commit()
            db.close()
            return redirect(url_for('home'))

    return render_template(
        'inscription.html',
        categories=CATEGORIES,
        event_types=EVENT_TYPES,
        error=error
    )


if __name__ == '__main__':
    app.run(debug=True)