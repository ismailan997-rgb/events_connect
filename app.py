import sqlite3
import os
from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime

app = Flask(__name__)
# On Vercel the filesystem is read-only except /tmp (data is ephemeral).
if os.environ.get('VERCEL'):
    DATABASE = '/tmp/events_connect.db'
else:
    DATABASE = os.path.join(os.path.dirname(__file__), 'events_connect.db')

# ------------------------------------------------------------------
# Database helpers
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
        conn.commit()

def seed_if_empty():
    with get_db() as conn:
        count = conn.execute('SELECT COUNT(*) FROM providers').fetchone()[0]
        if count:
            return
        conn.executemany(
            '''
            INSERT INTO providers
                (name, category, city, location, price_from, price_label,
                 specialties, phone, image_url, description, verified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ''',
            [
                (
                    'Studio Teranga Photo', 'photographe', 'Dakar', 'Almadies, Dakar',
                    150000, 'À partir de 150 000 FCFA', 'Mariage, portrait, drone',
                    '221771234567', '',
                    'Photographe et vidéaste pour mariages et cérémonies à Dakar.', 1
                ),
                (
                    'DJ Keur Sa Rew', 'dj', 'Dakar', 'Mermoz, Dakar',
                    80000, 'À partir de 80 000 FCFA', 'Mariage, soirée, animation',
                    '221761112233', '',
                    'DJ et animation sonore pour événements privés et professionnels.', 1
                ),
                (
                    'Saveurs du Sahel', 'traiteur', 'Thiès', 'Thiès centre',
                    5000, 'À partir de 5 000 FCFA / pers.', 'Buffet, cérémonie, cocktail',
                    '221775556677', '',
                    'Traiteur pour baptêmes, mariages et cocktails d’entreprise.', 0
                ),
            ]
        )
        conn.commit()

init_db()
seed_if_empty()

# ------------------------------------------------------------------
# Category definitions
# ------------------------------------------------------------------

CATEGORIES = {
    'photographe': {'label': 'Photographe & Vidéaste',   'emoji': '📷'},
    'dj':          {'label': 'DJ & Animation Sonore',    'emoji': '🎧'},
    'traiteur':    {'label': 'Traiteur & Pâtisserie',    'emoji': '🍽️'},
    'decorateur':  {'label': 'Décorateur & Scénographie','emoji': '🌸'},
    'animateur':   {'label': 'Animateur & MC',           'emoji': '🎤'},
    'makeup':      {'label': 'Maquillage & Coiffure',    'emoji': '💄'},
}

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
    return p

# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@app.route('/')
def home():
    cat_filter   = request.args.get('category', '').strip()
    city_filter  = request.args.get('city', '').strip().lower()
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

    return render_template('index.html',
                           providers=providers,
                           categories=CATEGORIES,
                           sel_category=cat_filter,
                           sel_city=city_filter,
                           sel_max_price=max_price)


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

    return render_template('prestataire.html', provider=provider, reviews=reviews)


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
        phone       = request.form.get('phone', '').strip()
        image_url   = request.form.get('image_url', '').strip()
        description = request.form.get('description', '').strip()

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
                     specialties, phone, image_url, description, verified)
                VALUES (?,?,?,?,?,?,?,?,?,?,0)
            ''', [name, category, city, location, price_int, price_label,
                  specialties, phone, image_url, description])
            db.commit()
            db.close()
            return redirect(url_for('home'))

    return render_template('inscription.html', categories=CATEGORIES, error=error)


if __name__ == '__main__':
    app.run(debug=True)