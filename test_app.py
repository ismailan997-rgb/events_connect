import io
import json
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch
from werkzeug.security import check_password_hash, generate_password_hash

import app as app_module


class EventsConnectTests(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.tmp_db.close()
        app_module.DATABASE = self.tmp_db.name
        app_module.init_db()
        app_module.app.config['TESTING'] = True
        app_module.app.config['WTF_CSRF_ENABLED'] = False
        app_module.app.config['RATELIMIT_ENABLED'] = False
        self.previous_admin_hash = app_module.ADMIN_PASSWORD_HASH
        self.previous_admin_password = app_module.ADMIN_PASSWORD

    def tearDown(self):
        app_module.ADMIN_PASSWORD_HASH = self.previous_admin_hash
        app_module.ADMIN_PASSWORD = self.previous_admin_password
        try:
            os.unlink(self.tmp_db.name)
        except FileNotFoundError:
            pass

    def test_public_pages_csrf_and_deprecated_api(self):
        client = app_module.app.test_client()
        for path in ['/confidentialite', '/conditions', '/mentions-legales', '/contact']:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)

        registration_page = client.get('/inscription').get_data(as_text=True)
        self.assertIn('name="price_from"', registration_page)
        self.assertNotIn('Libellé affiché', registration_page)
        self.assertIn('name="portfolio_images"', registration_page)
        self.assertIn('multiple', registration_page)
        self.assertIn('jusqu’à 5 photos'.lower(), registration_page.lower())
        for category in ['pianiste', 'fleuriste', 'location', 'salle', 'organisateur', 'styliste', 'transport']:
            with self.subTest(category=category):
                self.assertIn(f'value="{category}"', registration_page)

            login_page = client.get('/dashboard-access').get_data(as_text=True)
            self.assertIn('--gold-d:#d4880a', login_page)
            self.assertIn('background:linear-gradient(135deg,var(--gold),var(--gold-d))', login_page)

        app_module.app.config['WTF_CSRF_ENABLED'] = True
        response = client.post('/dashboard-access', data={'phone': '221771234567', 'password': 'x'})
        self.assertEqual(response.status_code, 400)

        app_module.app.config['WTF_CSRF_ENABLED'] = False
        api_response = client.post('/api/register-provider')
        self.assertEqual(api_response.status_code, 410)

    def test_contact_displays_default_support_email(self):
        with patch.dict(os.environ, {'SUPPORT_EMAIL': ''}):
            response = app_module.app.test_client().get('/contact')

        self.assertEqual(response.status_code, 200)
        self.assertIn('mailto:contact@eventsconnect.site', response.get_data(as_text=True))

    def test_daily_unique_visitors_require_consent_and_deduplicate(self):
        client = app_module.app.test_client()
        client.get('/')
        db = app_module.get_db()
        self.assertEqual(db.execute('SELECT COUNT(*) FROM daily_visitors').fetchone()[0], 0)
        db.close()

        client.post('/analytics-consent', data={'choice': 'accepted', 'next': '/'})
        client.get('/contact')
        client.get('/')

        second_client = app_module.app.test_client()
        second_client.post('/analytics-consent', data={'choice': 'accepted', 'next': '/'})
        second_client.get('/')

        declined_client = app_module.app.test_client()
        declined_client.post('/analytics-consent', data={'choice': 'declined', 'next': '/'})
        declined_client.get('/')

        db = app_module.get_db()
        visitors_today = db.execute('SELECT unique_visitors FROM daily_visitors').fetchone()[0]
        db.close()
        self.assertEqual(visitors_today, 2)

        app_module.ADMIN_PASSWORD = 'un mot de passe admin long'
        admin_client = app_module.app.test_client()
        admin_client.post('/admin', data={'password': app_module.ADMIN_PASSWORD})
        admin_page = admin_client.get('/admin').get_data(as_text=True)
        self.assertIn('Visiteurs uniques ayant accepté la mesure', admin_page)
        self.assertIn('Somme sur 30 jours', admin_page)

    def test_registration_requires_privacy_consent(self):
        client = app_module.app.test_client()
        response = client.post('/inscription', data={
            'name': 'Studio Sans Consentement',
            'category': 'photographe',
            'city': 'Dakar',
            'location': 'Plateau',
            'phone': '221771222333',
            'password': 'motdepasse-long',
            'event_types': ['mariage'],
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('Vous devez accepter', response.get_data(as_text=True))
        db = app_module.get_db()
        provider = db.execute(
            'SELECT id FROM providers WHERE phone = ?', ['221771222333']
        ).fetchone()
        db.close()
        self.assertIsNone(provider)

    def test_registration_without_starting_price_shows_quote_on_request(self):
        client = app_module.app.test_client()
        response = client.post('/inscription', data={
            'name': 'Studio Sur Devis',
            'category': 'photographe',
            'city': 'Dakar',
            'location': 'Plateau',
            'phone': '221771222398',
            'password': 'motdepasse-long',
            'consent': 'yes',
            'event_types': ['mariage'],
            'price_from': '',
        })
        self.assertEqual(response.status_code, 302)
        db = app_module.get_db()
        provider = db.execute(
            'SELECT price_from, price_label FROM providers WHERE phone = ?', ['221771222398']
        ).fetchone()
        db.close()
        self.assertEqual(provider['price_from'], 0)
        self.assertEqual(provider['price_label'], 'Sur devis')

    def test_registration_rejects_more_than_five_portfolio_images(self):
        client = app_module.app.test_client()
        files = [
            (io.BytesIO(f'image-{index}'.encode()), f'portfolio-{index}.jpg')
            for index in range(6)
        ]
        response = client.post('/inscription', data={
            'name': 'Studio Six Photos',
            'category': 'photographe',
            'city': 'Dakar',
            'location': 'Plateau',
            'phone': '221771222399',
            'password': 'motdepasse-long',
            'consent': 'yes',
            'event_types': ['mariage'],
            'portfolio_images': files,
        }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200)
        self.assertIn('jusqu’à 5 photos', response.get_data(as_text=True).lower())

    def test_registration_requires_and_allows_up_to_three_categories(self):
        client = app_module.app.test_client()
        base_data = {
            'name': 'Studio Sans Métier',
            'city': 'Dakar',
            'location': 'Plateau',
            'phone': '221771222334',
            'password': 'motdepasse-long',
            'consent': 'yes',
            'event_types': ['mariage'],
        }
        missing_category_response = client.post('/inscription', data=base_data)
        self.assertEqual(missing_category_response.status_code, 200)
        self.assertIn('Choisissez entre 1 et 3 métiers valides.', missing_category_response.get_data(as_text=True))

        multi_data = dict(base_data)
        multi_data.update({
            'name': 'Studio Multi-Métiers',
            'phone': '221771222335',
            'categories': ['photographe', 'dj', 'traiteur'],
        })
        multi_response = client.post('/inscription', data=multi_data)
        self.assertEqual(multi_response.status_code, 302)

        db = app_module.get_db()
        multi_provider = db.execute(
            'SELECT category, categories, dashboard_token FROM providers WHERE phone = ?', ['221771222335']
        ).fetchone()
        db.close()

        self.assertEqual(multi_provider['category'], 'photographe')
        self.assertEqual(multi_provider['categories'], 'photographe,dj,traiteur')

        dashboard_response = client.get(f"/dashboard/{multi_provider['dashboard_token']}")
        self.assertIn('Photographe &amp; Vidéaste, DJ &amp; Animation Sonore, Traiteur &amp; Pâtisserie', dashboard_response.get_data(as_text=True))

        db = app_module.get_db()
        db.execute('UPDATE providers SET verified = 1 WHERE phone = ?', ['221771222335'])
        db.commit()
        db.close()
        filtered_page = client.get('/?category=dj').get_data(as_text=True)
        self.assertIn('Studio Multi-Métiers', filtered_page)

        too_many_data = dict(base_data)
        too_many_data.update({
            'name': 'Studio Trop de Métiers',
            'phone': '221771222336',
            'categories': ['photographe', 'dj', 'traiteur', 'fleuriste'],
        })
        too_many_response = client.post('/inscription', data=too_many_data)
        self.assertIn('Choisissez entre 1 et 3 métiers valides.', too_many_response.get_data(as_text=True))

    def test_registration_password_requires_eight_characters(self):
        client = app_module.app.test_client()
        client.environ_base['REMOTE_ADDR'] = '198.51.100.8'
        data = {
            'name': 'Studio Mot de Passe',
            'city': 'Dakar',
            'location': 'Plateau',
            'phone': '221771222337',
            'password': '12345678',
            'categories': ['photographe'],
            'consent': 'yes',
            'event_types': ['mariage'],
        }
        accepted = client.post('/inscription', data=data)
        self.assertEqual(accepted.status_code, 302)

        data.update({'name': 'Studio Mot de Passe Court', 'phone': '221771222338', 'password': '1234567'})
        rejected = client.post('/inscription', data=data)
        self.assertIn('Choisissez un mot de passe d’au moins 8 caractères.', rejected.get_data(as_text=True))

    def test_admin_accepts_direct_environment_password(self):
        app_module.ADMIN_PASSWORD_HASH = ''
        app_module.ADMIN_PASSWORD = 'un mot de passe admin long'
        client = app_module.app.test_client()

        wrong_password = client.post('/admin', data={'password': 'mot de passe incorrect'})
        self.assertEqual(wrong_password.status_code, 200)
        self.assertIn('Mot de passe incorrect', wrong_password.get_data(as_text=True))

        correct_password = client.post('/admin', data={'password': 'un mot de passe admin long'})
        self.assertEqual(correct_password.status_code, 302)
        with client.session_transaction() as admin_session:
            self.assertTrue(admin_session.get('is_admin'))

    def test_existing_plaintext_password_is_migrated(self):
        db = app_module.get_db()
        db.execute(
            '''INSERT INTO providers (name, category, city, location, phone, password)
               VALUES (?, ?, ?, ?, ?, ?)''',
            ['Ancien compte', 'dj', 'Dakar', 'Médina', '221771222334', 'ancien-mot-de-passe']
        )
        db.commit()
        db.close()

        app_module.init_db()
        db = app_module.get_db()
        provider = db.execute(
            'SELECT password, password_hash FROM providers WHERE phone = ?', ['221771222334']
        ).fetchone()
        db.close()
        self.assertEqual(provider['password'], '')
        self.assertTrue(check_password_hash(provider['password_hash'], 'ancien-mot-de-passe'))

    def test_provider_can_edit_own_profile_and_manage_portfolio(self):
        client = app_module.app.test_client()
        dashboard_token = uuid.uuid4().hex
        existing_portfolio = [
            {'url': 'https://cdn.example.com/keep.jpg', 'title': 'À garder'},
            {'url': 'https://cdn.example.com/remove.jpg', 'title': 'À retirer'},
        ]
        db = app_module.get_db()
        db.execute(
                '''INSERT INTO providers (name, category, categories, city, location, phone,
                    image_url, verified, event_types, portfolio, dashboard_token)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)''',
            ['Studio Éditable', 'photographe', 'photographe', 'Dakar', 'Plateau',
                 '221771234567', 'https://cdn.example.com/cover.jpg', 'mariage,bapteme',
                 json.dumps(existing_portfolio), dashboard_token]
        )
        provider_id = db.execute(
            'SELECT id FROM providers WHERE dashboard_token = ?', [dashboard_token]
        ).fetchone()['id']
        db.commit()
        db.close()

        with client.session_transaction() as provider_session:
            provider_session['provider_id'] = provider_id

        self.assertEqual(client.get(f'/dashboard/{dashboard_token}').status_code, 200)

        with patch.dict(os.environ, {
            'CLOUDINARY_CLOUD_NAME': 'test-cloud',
            'CLOUDINARY_API_KEY': 'test-key',
            'CLOUDINARY_API_SECRET': 'test-secret',
        }), patch('cloudinary.uploader.upload') as upload_mock:
            upload_mock.return_value = {'secure_url': 'https://cdn.example.com/new.jpg'}
            response = client.post(f'/dashboard/{dashboard_token}/update', data={
                'name': 'Studio Éditable',
                'categories': ['photographe'],
                'city': 'Thiès',
                'location': 'Centre-ville',
                'phone': '221771234567',
                'price_from': '',
                'cover_position_x': '23',
                'cover_position_y': '72',
                'specialties': 'Mariages',
                'description': 'Nouvelle présentation',
                'event_types': ['mariage', 'soiree'],
                'remove_portfolio': ['1'],
                'portfolio_images': [(io.BytesIO(b'new-image'), 'nouvelle.jpg')],
            }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 302)

        db = app_module.get_db()
        updated = db.execute('SELECT * FROM providers WHERE id = ?', [provider_id]).fetchone()
        db.close()
        self.assertEqual(updated['city'], 'Thiès')
        self.assertEqual(updated['price_label'], 'Sur devis')
        self.assertEqual(updated['cover_position_x'], 23)
        self.assertEqual(updated['cover_position_y'], 72)
        self.assertEqual(updated['verified'], 1)
        dashboard_page = client.get(f'/dashboard/{dashboard_token}').get_data(as_text=True)
        self.assertIn('object-position:23% 72%', dashboard_page)
        updated_portfolio = json.loads(updated['portfolio'])
        self.assertEqual([item['title'] for item in updated_portfolio], ['À garder', 'Réalisation'])
        self.assertEqual(updated_portfolio[1]['url'], 'https://cdn.example.com/new.jpg')

        annuaire_page = client.get('/').get_data(as_text=True)
        self.assertIn('object-position:23% 72%', annuaire_page)
        detail_page = client.get(f'/prestataire/{provider_id}').get_data(as_text=True)
        self.assertIn('object-position:23% 72%', detail_page)

        unauthorized = app_module.app.test_client().post(
            f'/dashboard/{dashboard_token}/update', data={'city': 'Saint-Louis'}
        )
        self.assertEqual(unauthorized.status_code, 302)
        self.assertIn('/dashboard-access', unauthorized.headers['Location'])

        changed_name = client.post(f'/dashboard/{dashboard_token}/update', data={
            'name': 'Nouveau nom',
            'categories': ['photographe'],
            'city': 'Thiès',
            'location': 'Centre-ville',
            'phone': '221771234567',
            'event_types': ['mariage'],
        })
        self.assertEqual(changed_name.status_code, 302)
        db = app_module.get_db()
        verification = db.execute(
            'SELECT verified FROM providers WHERE id = ?', [provider_id]
        ).fetchone()['verified']
        db.close()
        self.assertEqual(verification, 0)

    def test_reviews_wait_for_admin_and_provider_badge_is_moderated(self):
        client = app_module.app.test_client()
        app_module.ADMIN_PASSWORD_HASH = generate_password_hash('admin-passphrase-local')
        db = app_module.get_db()
        db.execute(
            '''INSERT INTO providers (name, category, city, location, phone, dashboard_token)
               VALUES (?, ?, ?, ?, ?, ?)''',
            ['Studio Test', 'photographe', 'Dakar', 'Plateau', '221771111111', uuid.uuid4().hex]
        )
        provider_id = db.execute('SELECT id FROM providers WHERE name = ?', ['Studio Test']).fetchone()['id']
        db.commit()
        db.close()

        self.assertEqual(client.get(f'/prestataire/{provider_id}').status_code, 404)
        unauthenticated_action = client.post(
            f'/admin/provider/{provider_id}/verification', data={'verified': '1'}
        )
        self.assertEqual(unauthenticated_action.status_code, 302)

        login = client.post('/admin', data={'password': 'admin-passphrase-local'})
        self.assertEqual(login.status_code, 302)
        verify = client.post(
            f'/admin/provider/{provider_id}/verification', data={'verified': '1'}
        )
        self.assertEqual(verify.status_code, 302)

        review_response = client.post(
            f'/prestataire/{provider_id}/avis',
            data={'author': 'Awa', 'rating': '5', 'comment': 'Prestation confirmée'}
        )
        self.assertEqual(review_response.status_code, 302)
        public_before = client.get(f'/prestataire/{provider_id}').get_data(as_text=True)
        self.assertNotIn('Prestation confirmée', public_before)

        admin_page = client.get('/admin').get_data(as_text=True)
        self.assertIn('Studio Test', admin_page)
        self.assertIn('Prestation confirmée', admin_page)

        db = app_module.get_db()
        review_id = db.execute('SELECT id FROM reviews').fetchone()['id']
        db.close()
        approve = client.post(f'/admin/review/{review_id}/approve')
        self.assertEqual(approve.status_code, 302)

        public_after = client.get(f'/prestataire/{provider_id}').get_data(as_text=True)
        self.assertIn('Prestation confirmée', public_after)
        self.assertIn('Vérifié', public_after)

    def test_inscription_upload_and_dashboard_delete(self):
        client = app_module.app.test_client()

        with patch.dict(os.environ, {
            'CLOUDINARY_CLOUD_NAME': 'test-cloud',
            'CLOUDINARY_API_KEY': 'test-key',
            'CLOUDINARY_API_SECRET': 'test-secret',
        }), patch('cloudinary.uploader.upload') as upload_mock:
            upload_mock.side_effect = [
                {'secure_url': 'https://cdn.example.com/photo.jpg'},
                {'secure_url': 'https://cdn.example.com/portfolio-1.jpg'},
                {'secure_url': 'https://cdn.example.com/portfolio-2.jpg'},
            ]

            response = client.post(
                '/inscription',
                data={
                    'name': 'Studio Lumière',
                    'category': 'photographe',
                    'city': 'Dakar',
                    'location': 'Almadies',
                    'price_from': '150000',
                    'price_label': 'Libellé personnalisé ignoré',
                    'specialties': 'Mariages & portrait',
                    'phone': '221771234567',
                    'password': 'monmotdepasse',
                    'confirm_password': 'monmotdepasse',
                    'consent': 'yes',
                    'description': 'Photographe premium',
                    'event_types': ['mariage', 'anniversaire'],
                    'image': (io.BytesIO(b'fake-image-content'), 'photo.jpg'),
                    'portfolio_images': [
                        (io.BytesIO(b'fake-portfolio-content-1'), 'portfolio-1.jpg'),
                        (io.BytesIO(b'fake-portfolio-content-2'), 'portfolio-2.jpg'),
                    ],
                },
                content_type='multipart/form-data',
            )

        self.assertEqual(response.status_code, 302)

        db = app_module.get_db()
        provider = db.execute(
            "SELECT * FROM providers WHERE name = ?",
            ['Studio Lumière']
        ).fetchone()
        db.close()

        self.assertIsNotNone(provider)
        self.assertEqual(provider['category'], 'photographe')
        self.assertEqual(provider['categories'], 'photographe')
        self.assertEqual(provider['price_label'], 'À partir de 150 000 FCFA')
        portfolio = json.loads(provider['portfolio'])
        self.assertEqual(len(portfolio), 2)
        self.assertEqual(portfolio[0]['url'], 'https://cdn.example.com/portfolio-1.jpg')
        self.assertEqual(portfolio[1]['url'], 'https://cdn.example.com/portfolio-2.jpg')
        self.assertTrue(provider['dashboard_token'])
        self.assertEqual(provider['password'], '')
        self.assertTrue(check_password_hash(provider['password_hash'], 'monmotdepasse'))
        public_profile = client.get(f"/prestataire/{provider['id']}")
        self.assertEqual(public_profile.status_code, 404)
        self.assertNotIn(provider['dashboard_token'], client.get('/').get_data(as_text=True))

        anonymous_client = app_module.app.test_client()
        protected_response = anonymous_client.get(f"/dashboard/{provider['dashboard_token']}")
        self.assertEqual(protected_response.status_code, 302)
        self.assertIn('/dashboard-access', protected_response.headers['Location'])

        login_response = client.post(
            '/dashboard-access',
            data={'phone': '221771234567', 'password': 'monmotdepasse'}
        )
        self.assertEqual(login_response.status_code, 302)

        dashboard_response = client.get(f"/dashboard/{provider['dashboard_token']}")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertIn('Studio Lumière', dashboard_response.get_data(as_text=True))

        delete_response = client.post(
            f"/dashboard/{provider['dashboard_token']}/delete",
            data={'confirm': 'SUPPRIMER'}
        )
        self.assertEqual(delete_response.status_code, 302)

        db = app_module.get_db()
        row = db.execute(
            "SELECT id FROM providers WHERE name = ?",
            ['Studio Lumière']
        ).fetchone()
        db.close()

        self.assertIsNone(row)


if __name__ == '__main__':
    unittest.main()
