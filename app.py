import os
import json
import secrets
import sqlite3
import uuid
from functools import wraps

import cloudinary
import cloudinary.uploader
from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from datetime import datetime
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

app = Flask(__name__)
IS_PRODUCTION = bool(os.environ.get('RENDER') or os.environ.get('VERCEL') or os.environ.get('APP_ENV') == 'production')
if IS_PRODUCTION:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
if IS_PRODUCTION and not os.environ.get('SECRET_KEY'):
    raise RuntimeError('La variable SECRET_KEY doit être configurée en production.')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = IS_PRODUCTION
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024
csrf = CSRFProtect(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=os.environ.get('RATELIMIT_STORAGE_URI') or 'memory://',
    default_limits=['1000 per day', '120 per hour'],
)
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
if IS_PRODUCTION and not DATABASE_URL:
    raise RuntimeError('DATABASE_URL doit pointer vers une base persistante en production.')
ADMIN_PASSWORD_HASH = os.environ.get('ADMIN_PASSWORD_HASH', '')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
if IS_PRODUCTION:
    required_settings = [
        'CLOUDINARY_CLOUD_NAME', 'CLOUDINARY_API_KEY', 'CLOUDINARY_API_SECRET',
        'LEGAL_OPERATOR_NAME', 'LEGAL_OPERATOR_ADDRESS', 'HOSTING_PROVIDER',
    ]
    missing_settings = [key for key in required_settings if not os.environ.get(key)]
    if missing_settings:
        raise RuntimeError('Variables de production manquantes : ' + ', '.join(missing_settings))
    if not ADMIN_PASSWORD and not ADMIN_PASSWORD_HASH:
        raise RuntimeError('Configurez ADMIN_PASSWORD ou ADMIN_PASSWORD_HASH pour protéger /admin.')
    if ADMIN_PASSWORD and len(ADMIN_PASSWORD) < 16:
        raise RuntimeError('ADMIN_PASSWORD doit contenir au moins 16 caractères.')
    if not (os.environ.get('SUPPORT_EMAIL') or os.environ.get('SUPPORT_WHATSAPP')):
        raise RuntimeError('Configurez au moins SUPPORT_EMAIL ou SUPPORT_WHATSAPP en production.')

cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME', '')
api_key = os.environ.get('CLOUDINARY_API_KEY', '')
api_secret = os.environ.get('CLOUDINARY_API_SECRET', '')

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
    'pianiste':    {'label': 'Pianiste, Musicien & Griot', 'emoji': '🎹'},
    'fleuriste':   {'label': 'Fleuriste & Art floral',     'emoji': '💐'},
    'location':    {'label': 'Location de matériel & Tentes', 'emoji': '⛺'},
    'salle':       {'label': 'Salle & Espace de réception', 'emoji': '🏛️'},
    'organisateur': {'label': 'Wedding planner & Coordination', 'emoji': '📋'},
    'styliste':    {'label': 'Tenues & Stylisme',          'emoji': '👗'},
    'transport':   {'label': 'Transport & Chauffeur',     'emoji': '🚘'},
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


class ClosingSQLiteConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def get_db():
    if DATABASE_URL:
        return PostgresConnection(DATABASE_URL)

    conn = sqlite3.connect(DATABASE, factory=ClosingSQLiteConnection)
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
                    password_hash TEXT NOT NULL DEFAULT '',
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
                    status TEXT NOT NULL DEFAULT 'pending',
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
                    password_hash TEXT NOT NULL DEFAULT '',
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
                    status      TEXT NOT NULL DEFAULT 'pending',
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
        if 'password_hash' not in cols:
            conn.execute("ALTER TABLE providers ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")

        if DATABASE_URL:
            review_cols = [row['column_name'] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'reviews'"
            ).fetchall()]
        else:
            review_cols = [row['name'] for row in conn.execute('PRAGMA table_info(reviews)').fetchall()]
        if 'status' not in review_cols:
            conn.execute("ALTER TABLE reviews ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")

        legacy_passwords = conn.execute(
            "SELECT id, password FROM providers WHERE password IS NOT NULL AND password != '' AND password_hash = ''"
        ).fetchall()
        for row in legacy_passwords:
            conn.execute(
                'UPDATE providers SET password_hash = ?, password = ? WHERE id = ?',
                [generate_password_hash(row['password']), '', row['id']]
            )

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
    p['dashboard_url'] = ''

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
    if file_storage.mimetype not in {'image/jpeg', 'image/png', 'image/webp'}:
        raise ValueError('Formats autorisés : JPG, PNG ou WebP.')
    cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME', '')
    api_key = os.environ.get('CLOUDINARY_API_KEY', '')
    api_secret = os.environ.get('CLOUDINARY_API_SECRET', '')
    if not all([cloud_name, api_key, api_secret]):
        raise ValueError('Le téléversement de photos n’est pas configuré. Contactez Events Connect.')

    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )

    try:
        upload_result = cloudinary.uploader.upload(file_storage, folder=folder, resource_type='image')
        return upload_result.get('secure_url', '')
    except Exception as exc:
        raise ValueError(f"Échec du téléversement d'image : {str(exc)}") from exc


@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    if IS_PRODUCTION:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


@app.context_processor
def inject_contact_details():
    return {
        'support_email': os.environ.get('SUPPORT_EMAIL', ''),
        'support_whatsapp': ''.join(char for char in os.environ.get('SUPPORT_WHATSAPP', '') if char.isdigit()),
        'legal_operator_name': os.environ.get('LEGAL_OPERATOR_NAME', ''),
        'legal_operator_address': os.environ.get('LEGAL_OPERATOR_ADDRESS', ''),
        'hosting_provider': os.environ.get('HOSTING_PROVIDER', ''),
        'legal_operator_registration': os.environ.get('LEGAL_OPERATOR_REGISTRATION', ''),
    }

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
         LEFT JOIN reviews r ON r.provider_id = p.id AND r.status = 'approved'
        WHERE p.verified = 1
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


@app.route('/confidentialite')
def privacy_policy():
    return render_template('confidentialite.html')


@app.route('/conditions')
def terms_of_service():
    return render_template('conditions.html')


@app.route('/mentions-legales')
def legal_notice():
    return render_template('mentions_legales.html')


@app.route('/contact')
def contact():
    return render_template('contact.html')

@app.route('/dashboard-access', methods=['GET', 'POST'])
@limiter.limit('5 per 15 minutes', methods=['POST'])
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
                'SELECT id, dashboard_token, password_hash FROM providers WHERE phone = ? LIMIT 1',
                [phone]
            ).fetchone()
            db.close()

            if row and row['password_hash'] and check_password_hash(row['password_hash'], password):
                session.clear()
                session['provider_id'] = row['id']
                return redirect(url_for('dashboard', dashboard_token=row['dashboard_token']))
            error = 'Numéro WhatsApp ou mot de passe incorrect.'

    return render_template('dashboard_access.html', error=error)


@app.route('/api/register-provider', methods=['POST'])
@csrf.exempt
@limiter.limit('5 per hour')
def register_provider():
    return jsonify({'error': 'Cette route n’est plus disponible. Utilisez le formulaire /inscription.'}), 410


@app.route('/dashboard/<dashboard_token>')
def dashboard(dashboard_token):
    db = get_db()
    row = db.execute('SELECT * FROM providers WHERE dashboard_token = ? LIMIT 1', [dashboard_token]).fetchone()
    db.close()

    if row is None:
        return render_template('404.html'), 404
    if session.get('provider_id') != row['id']:
        return redirect(url_for('dashboard_access'))

    provider = enrich(row)
    provider['public_url'] = url_for('detail', pid=provider['id'], _external=True)
    return render_template('dashboard.html', provider=provider)


@app.route('/dashboard/<dashboard_token>/delete', methods=['POST'])
def delete_dashboard(dashboard_token):
    db = get_db()
    row = db.execute('SELECT id FROM providers WHERE dashboard_token = ? LIMIT 1', [dashboard_token]).fetchone()
    if row is None:
        db.close()
        return render_template('404.html'), 404
    if session.get('provider_id') != row['id']:
        db.close()
        return redirect(url_for('dashboard_access'))

    if request.form.get('confirm', '').strip().upper() != 'SUPPRIMER':
        db.close()
        return redirect(url_for('dashboard', dashboard_token=dashboard_token))

    db.execute('DELETE FROM providers WHERE dashboard_token = ?', [dashboard_token])
    db.commit()
    db.close()
    session.clear()
    return redirect(url_for('home'))


@app.route('/prestataire/<int:pid>')
def detail(pid):
    db = get_db()
    row = db.execute('''
        SELECT p.*,
             COUNT(r.id)                AS review_count,
               COALESCE(AVG(r.rating), 0) AS avg_rating
        FROM providers p
         LEFT JOIN reviews r ON r.provider_id = p.id AND r.status = 'approved'
        WHERE p.id = ? AND p.verified = 1
        GROUP BY p.id
    ''', [pid]).fetchone()

    if row is None:
        db.close()
        return render_template('404.html'), 404

    provider = enrich(row)
    reviews  = [dict(r) for r in db.execute(
        "SELECT * FROM reviews WHERE provider_id = ? AND status = 'approved' ORDER BY created_at DESC",
        [pid]
    ).fetchall()]
    db.close()

    return render_template(
        'prestataire.html',
        provider=provider,
        reviews=reviews,
        event_types=EVENT_TYPES,
        review_submitted=request.args.get('avis') == 'submitted'
    )


@app.route('/prestataire/<int:pid>/avis', methods=['POST'])
@limiter.limit('3 per hour')
def add_review(pid):
    author  = request.form.get('author', '').strip()
    rating  = request.form.get('rating', '0').strip()
    comment = request.form.get('comment', '').strip()

    try:
        rating_int = int(rating)
        assert 1 <= rating_int <= 5
    except (ValueError, AssertionError):
        return redirect(url_for('detail', pid=pid))

    if not author or len(author) > 60 or len(comment) > 800:
        return redirect(url_for('detail', pid=pid) + '#avis')

    db = get_db()
    provider = db.execute('SELECT id FROM providers WHERE id = ? AND verified = 1', [pid]).fetchone()
    if provider:
        db.execute(
            "INSERT INTO reviews (provider_id, author, rating, comment, status, created_at) VALUES (?,?,?,?, 'pending', ?)",
            [pid, author, rating_int, comment, datetime.now().strftime('%Y-%m-%d %H:%M')]
        )
        db.commit()
    db.close()

    return redirect(url_for('detail', pid=pid, avis='submitted') + '#avis')


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('admin'))
        return view(*args, **kwargs)
    return wrapped


@app.route('/admin', methods=['GET', 'POST'])
@limiter.limit('5 per 15 minutes', methods=['POST'])
def admin():
    error = None
    if not ADMIN_PASSWORD and not ADMIN_PASSWORD_HASH:
        return render_template('admin.html', setup_required=True), 503

    if request.method == 'POST':
        password = request.form.get('password', '')
        if ADMIN_PASSWORD:
            authenticated = secrets.compare_digest(password, ADMIN_PASSWORD)
        else:
            try:
                authenticated = check_password_hash(ADMIN_PASSWORD_HASH, password)
            except ValueError:
                authenticated = False
        if authenticated:
            session.clear()
            session['is_admin'] = True
            return redirect(url_for('admin'))
        error = 'Mot de passe incorrect.'

    if not session.get('is_admin'):
        return render_template('admin.html', error=error)

    db = get_db()
    providers = db.execute(
        'SELECT id, name, category, city, phone, created_at FROM providers WHERE verified = 0 ORDER BY created_at ASC'
    ).fetchall()
    verified_providers = db.execute(
        'SELECT id, name, category, city FROM providers WHERE verified = 1 ORDER BY name ASC'
    ).fetchall()
    pending_reviews = db.execute(
        "SELECT r.id, r.provider_id, r.author, r.rating, r.comment, r.created_at, p.name AS provider_name "
        "FROM reviews r JOIN providers p ON p.id = r.provider_id WHERE r.status = 'pending' ORDER BY r.created_at ASC"
    ).fetchall()
    db.close()
    return render_template(
        'admin.html', providers=providers, verified_providers=verified_providers,
        pending_reviews=pending_reviews
    )


@app.route('/admin/logout', methods=['POST'])
@admin_required
def admin_logout():
    session.clear()
    return redirect(url_for('home'))


@app.route('/admin/provider/<int:provider_id>/verification', methods=['POST'])
@admin_required
def admin_set_verification(provider_id):
    verified = request.form.get('verified', '')
    if verified not in {'0', '1'}:
        return redirect(url_for('admin'))
    db = get_db()
    db.execute('UPDATE providers SET verified = ? WHERE id = ?', [int(verified), provider_id])
    db.commit()
    db.close()
    return redirect(url_for('admin'))


@app.route('/admin/review/<int:review_id>/<action>', methods=['POST'])
@admin_required
def admin_moderate_review(review_id, action):
    if action not in {'approve', 'reject'}:
        return redirect(url_for('admin'))
    status = 'approved' if action == 'approve' else 'rejected'
    db = get_db()
    db.execute(
        "UPDATE reviews SET status = ? WHERE id = ? AND status = 'pending'",
        [status, review_id]
    )
    db.commit()
    db.close()
    return redirect(url_for('admin'))


@app.route('/inscription', methods=['GET', 'POST'])
@limiter.limit('5 per hour', methods=['POST'])
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
        image_url = ''
        portfolio_url = ''

        selected_events = request.form.getlist('event_types')
        events_str = ','.join(selected_events) if selected_events else 'mariage,bapteme,anniversaire,soiree'

        portfolio_title = request.form.get('portfolio_title', '').strip()
        portfolio_items = []

        if not all([name, category, city, location, phone, password]):
            error = 'Veuillez remplir tous les champs obligatoires (*) et choisir un mot de passe.'
        elif confirm_password != password:
            error = 'La confirmation du mot de passe ne correspond pas.'
        elif len(password) > 128:
            error = 'Le mot de passe ne peut pas dépasser 128 caractères.'
        elif len(password) < 12:
            error = 'Choisissez un mot de passe d’au moins 12 caractères.'
        elif request.form.get('consent') != 'yes':
            error = 'Vous devez accepter les conditions et la politique de confidentialité.'
        elif not phone.isdigit() or not 8 <= len(phone) <= 15:
            error = 'Saisissez un numéro international valide, chiffres uniquement (ex: 221771234567).'
        elif category not in CATEGORIES:
            error = 'Catégorie invalide.'
        elif len(name) > 100 or len(city) > 60 or len(location) > 100:
            error = 'Vérifiez la longueur du nom, de la ville et de la zone.'
        elif len(price_label) > 120 or len(specialties) > 200 or len(description) > 1000 or len(portfolio_title) > 100:
            error = 'Un des champs descriptifs dépasse la longueur autorisée.'
        elif not selected_events or any(event not in EVENT_TYPES for event in selected_events):
            error = 'Choisissez au moins un type d’événement valide.'
        else:
            db = get_db()
            duplicate = db.execute('SELECT id FROM providers WHERE phone = ? LIMIT 1', [phone]).fetchone()
            db.close()
            if duplicate:
                error = 'Ce numéro WhatsApp est déjà inscrit. Connectez-vous ou contactez-nous.'

        if not error:
            try:
                price_int = int(price_from)
            except ValueError:
                return render_template(
                    'inscription.html', categories=CATEGORIES, event_types=EVENT_TYPES,
                    error='Saisissez un tarif valide en FCFA.'
                ), 400
            if price_int < 0:
                return render_template(
                    'inscription.html', categories=CATEGORIES, event_types=EVENT_TYPES,
                    error='Le tarif ne peut pas être négatif.'
                ), 400
            if price_int > 100_000_000:
                return render_template(
                    'inscription.html', categories=CATEGORIES, event_types=EVENT_TYPES,
                    error='Le tarif saisi dépasse la limite autorisée.'
                ), 400

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
                     specialties, phone, password, password_hash, image_url, description, verified, event_types, portfolio, dashboard_token)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)
            ''', [name, category, city, location, price_int, price_label,
                  specialties, phone, '', generate_password_hash(password), image_url, description, events_str, json.dumps(portfolio_items), dashboard_token])
            db.commit()
            provider_id = db.execute('SELECT id FROM providers WHERE dashboard_token = ?', [dashboard_token]).fetchone()['id']
            db.close()
            session.clear()
            session['provider_id'] = provider_id
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