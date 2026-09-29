import os
import json
import sqlite3
import uuid

import cloudinary
import cloudinary.uploader
from flask import Flask, render_template, request, redirect, url_for, jsonify
from datetime import datetime

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'events-connect-dev-secret')
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()

cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME', 'gyvienzc')
api_key = os.environ.get('CLOUDINARY_API_KEY', '152763732461972')
api_secret = os.environ.get('CLOUDINARY_API_SECRET', 'jFkBCsKE1zHr1H_-4ZjBmwEkD8A')

cloudinary.config(
    cloud_name=cloud_name,
    api_key=api_key,
    api_secret=api_secret,
    secure=True,
)

# On Vercel the filesystem is read-only except /tmp (data is ephemeral).
if os.environ.get('DATABASE_PATH'):
    DATABASE = os.environ.get('DATABASE_PATH')
elif os.environ.get('VERCEL'):
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
# Database helpers
# ------------------------------------------------------------------

class PostgresConnection:
    def __init__(self, database_url):
        if psycopg is None:
            raise RuntimeError('Le paquet psycopg[binary] est requis pour utiliser Supabase.')
        self.connection = psycopg.connect(database_url, row_factory=dict_row)

    def execute(self, query, params=()):
        return self.connection.execute(query.replace('?', '%s'), params)

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type:
            self.connection.rollback()
        else:
            self.connection.commit()
        self.connection.close()


def get_db():
    if DATABASE_URL:
        return PostgresConnection(DATABASE_URL)

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn

def init_db():
    with get_db() as conn:
        if DATABASE_URL:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS providers (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    city TEXT NOT NULL,
                    location TEXT NOT NULL,
                    price_from INTEGER NOT NULL DEFAULT 0,
                    price_label TEXT NOT NULL DEFAULT '',
                    specialties TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL,
                    password TEXT NOT NULL DEFAULT '',
                    image_url TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    verified INTEGER NOT NULL DEFAULT 0,
                    event_types TEXT NOT NULL DEFAULT 'mariage,bapteme,anniversaire,soiree',
                    portfolio TEXT NOT NULL DEFAULT '[]',
                    dashboard_token TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS reviews (
                    id BIGSERIAL PRIMARY KEY,
                    provider_id BIGINT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
                    author TEXT NOT NULL,
                    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                    comment TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        else:
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
                    password    TEXT NOT NULL DEFAULT '',
                    image_url   TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    verified    INTEGER NOT NULL DEFAULT 0,
                    event_types TEXT NOT NULL DEFAULT 'mariage,bapteme,anniversaire,soiree',
                    portfolio   TEXT NOT NULL DEFAULT '[]',
                    dashboard_token TEXT NOT NULL DEFAULT '',
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
        if DATABASE_URL:
            cols = [row['column_name'] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'providers'"
            ).fetchall()]
        else:
            cols = [row['name'] for row in conn.execute('PRAGMA table_info(providers)').fetchall()]
        if 'event_types' not in cols:
            conn.execute("ALTER TABLE providers ADD COLUMN event_types TEXT NOT NULL DEFAULT 'mariage,bapteme,anniversaire,soiree'")
        if 'portfolio' not in cols:
            conn.execute("ALTER TABLE providers ADD COLUMN portfolio TEXT NOT NULL DEFAULT '[]'")
        if 'dashboard_token' not in cols:
            conn.execute("ALTER TABLE providers ADD COLUMN dashboard_token TEXT NOT NULL DEFAULT ''")
        if 'password' not in cols:
            conn.execute("ALTER TABLE providers ADD COLUMN password TEXT NOT NULL DEFAULT ''")

        rows = conn.execute("SELECT id FROM providers WHERE dashboard_token IS NULL OR dashboard_token = ''").fetchall()
        for row in rows:
            conn.execute(
                'UPDATE providers SET dashboard_token = ? WHERE id = ?',
                [uuid.uuid4().hex, row['id']]
            )
        conn.commit()

init_db()

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
    p['dashboard_url'] = url_for('dashboard', dashboard_token=p.get('dashboard_token', ''), _external=True) if p.get('dashboard_token') else ''

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


def upload_file_to_cloudinary(file_storage, folder='events-connect'):
    if not file_storage or not getattr(file_storage, 'filename', None):
        return ''

    cloudinary.config(
        cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME', 'gyvienzc'),
        api_key=os.environ.get('CLOUDINARY_API_KEY', '152763732461972'),
        api_secret=os.environ.get('CLOUDINARY_API_SECRET', 'jFkBCsKE1zHr1H_-4ZjBmwEkD8A'),
        secure=True,
    )

    try:
        upload_result = cloudinary.uploader.upload(file_storage, folder=folder, resource_type='image')
        return upload_result.get('secure_url', '')
    except Exception as exc:
        raise ValueError(f"Échec du téléversement d'image : {str(exc)}") from exc

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

@app.route('/dashboard-access', methods=['GET', 'POST'])
def dashboard_access():
    error = None

    if request.method == 'POST':
        token = request.form.get('dashboard_token', '').strip()
        if token:
            return redirect(url_for('dashboard', dashboard_token=token))

        phone = request.form.get('phone', '').strip().replace(' ', '').replace('+', '')
        password = request.form.get('password', '').strip()

        if not phone or not password:
            error = 'Saisissez votre numéro WhatsApp et votre mot de passe.'
        else:
            db = get_db()
            row = db.execute(
                'SELECT dashboard_token FROM providers WHERE phone = ? AND password = ? LIMIT 1',
                [phone, password]
            ).fetchone()
            db.close()

            if row:
                return redirect(url_for('dashboard', dashboard_token=row['dashboard_token']))
            error = 'Numéro WhatsApp ou mot de passe incorrect.'

    return render_template('dashboard_access.html', error=error)


@app.route('/api/register-provider', methods=['POST'])
def register_provider():
    nom = request.form.get('nom', '').strip()
    service = request.form.get('service', '').strip()

    if 'photo' not in request.files or not request.files['photo'].filename:
        return jsonify({'error': 'Aucune photo fournie'}), 400

    try:
        photo_url = upload_file_to_cloudinary(request.files['photo'], 'events-connect/providers')
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 500

    db = get_db()
    dashboard_token = uuid.uuid4().hex
    db.execute(
        '''
        INSERT INTO providers (name, category, city, location, price_from, price_label,
        specialties, phone, image_url, description, verified, event_types, portfolio, dashboard_token)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
        ''',
        [
            nom or 'Prestataire',
            service or 'photographe',
            request.form.get('city', 'Dakar'),
            request.form.get('location', 'Dakar'),
            int(request.form.get('price_from', '0') or 0),
            request.form.get('price_label', ''),
            request.form.get('specialties', ''),
            request.form.get('phone', ''),
            photo_url,
            request.form.get('description', ''),
            request.form.get('event_types', 'mariage,bapteme,anniversaire,soiree'),
            '[]',
            dashboard_token,
        ]
    )
    db.commit()
    provider_id = db.execute('SELECT id FROM providers WHERE dashboard_token = ?', [dashboard_token]).fetchone()['id']
    db.close()

    return jsonify({
        'message': 'Prestataire inscrit avec succès !',
        'photo_url': photo_url,
        'dashboard_token': dashboard_token,
        'provider_id': provider_id,
    }), 201
@app.route('/dashboard/<dashboard_token>')
def dashboard(dashboard_token):
    db = get_db()
    row = db.execute('SELECT * FROM providers WHERE dashboard_token = ? LIMIT 1', [dashboard_token]).fetchone()
    db.close()

    if row is None:
        return render_template('404.html'), 404

    provider = enrich(row)
    provider['public_url'] = url_for('detail', pid=provider['id'], _external=True)
    return render_template('dashboard.html', provider=provider)


@app.route('/dashboard/<dashboard_token>/delete', methods=['POST'])
def delete_dashboard(dashboard_token):
    if request.form.get('confirm', '').strip().upper() != 'SUPPRIMER':
        return redirect(url_for('dashboard', dashboard_token=dashboard_token))

    db = get_db()
    row = db.execute('SELECT id FROM providers WHERE dashboard_token = ? LIMIT 1', [dashboard_token]).fetchone()
    if row is None:
        db.close()
        return render_template('404.html'), 404

    db.execute('DELETE FROM providers WHERE dashboard_token = ?', [dashboard_token])
    db.commit()
    db.close()
    return redirect(url_for('home'))


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
        password    = request.form.get('password', '').strip()
        confirm_password = request.form.get('confirm_password', password).strip()
        description = request.form.get('description', '').strip()

        image_file = request.files.get('image')
        portfolio_file = request.files.get('portfolio_image')
        image_url = request.form.get('image_url', '').strip()
        portfolio_url = request.form.get('portfolio_url', '').strip()

        selected_events = request.form.getlist('event_types')
        events_str = ','.join(selected_events) if selected_events else 'mariage,bapteme,anniversaire,soiree'

        portfolio_title = request.form.get('portfolio_title', '').strip()
        portfolio_items = []

        if not all([name, category, city, location, phone, password]):
            error = 'Veuillez remplir tous les champs obligatoires (*) et choisir un mot de passe.'
        elif confirm_password != password:
            error = 'La confirmation du mot de passe ne correspond pas.'
        elif category not in CATEGORIES:
            error = 'Catégorie invalide.'
        else:
            try:
                price_int = int(price_from)
            except ValueError:
                price_int = 0

            if not price_label:
                price_label = f'À partir de {price_int:,} FCFA'.replace(',', ' ')

            try:
                if image_file and image_file.filename:
                    image_url = upload_file_to_cloudinary(image_file, 'events-connect/providers')
                if portfolio_file and portfolio_file.filename:
                    portfolio_url = upload_file_to_cloudinary(portfolio_file, 'events-connect/portfolio')
            except ValueError as exc:
                error = str(exc)
                return render_template(
                    'inscription.html',
                    categories=CATEGORIES,
                    event_types=EVENT_TYPES,
                    error=error
                )

            if portfolio_url:
                portfolio_items.append({
                    'url': portfolio_url,
                    'title': portfolio_title or 'Réalisation récente',
                    'event_type': 'Événement',
                    'caption': 'Prestation réalisée par nos soins'
                })

            dashboard_token = uuid.uuid4().hex
            db = get_db()
            db.execute('''
                INSERT INTO providers
                    (name, category, city, location, price_from, price_label,
                     specialties, phone, password, image_url, description, verified, event_types, portfolio, dashboard_token)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)
            ''', [name, category, city, location, price_int, price_label,
                  specialties, phone, password, image_url, description, events_str, json.dumps(portfolio_items), dashboard_token])
            db.commit()
            db.close()
            return redirect(url_for('dashboard', dashboard_token=dashboard_token))

    return render_template(
        'inscription.html',
        categories=CATEGORIES,
        event_types=EVENT_TYPES,
        error=error
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)