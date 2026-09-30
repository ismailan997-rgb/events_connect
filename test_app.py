import io
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

    def tearDown(self):
        app_module.ADMIN_PASSWORD_HASH = self.previous_admin_hash
        try:
            os.unlink(self.tmp_db.name)
        except FileNotFoundError:
            pass

    def test_public_pages_csrf_and_deprecated_api(self):
        client = app_module.app.test_client()
        for path in ['/confidentialite', '/conditions', '/mentions-legales', '/contact']:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)

        app_module.app.config['WTF_CSRF_ENABLED'] = True
        response = client.post('/dashboard-access', data={'phone': '221771234567', 'password': 'x'})
        self.assertEqual(response.status_code, 400)

        app_module.app.config['WTF_CSRF_ENABLED'] = False
        api_response = client.post('/api/register-provider')
        self.assertEqual(api_response.status_code, 410)

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
                {'secure_url': 'https://cdn.example.com/portfolio.jpg'},
            ]

            response = client.post(
                '/inscription',
                data={
                    'name': 'Studio Lumière',
                    'category': 'photographe',
                    'city': 'Dakar',
                    'location': 'Almadies',
                    'price_from': '150000',
                    'price_label': 'À partir de 150 000 FCFA',
                    'specialties': 'Mariages & portrait',
                    'phone': '221771234567',
                    'password': 'monmotdepasse',
                    'confirm_password': 'monmotdepasse',
                    'consent': 'yes',
                    'description': 'Photographe premium',
                    'event_types': ['mariage', 'anniversaire'],
                    'image': (io.BytesIO(b'fake-image-content'), 'photo.jpg'),
                    'portfolio_image': (io.BytesIO(b'fake-portfolio-content'), 'portfolio.jpg'),
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
