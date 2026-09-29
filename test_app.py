import io
import os
import tempfile
import unittest
from unittest.mock import patch

import app as app_module


class EventsConnectTests(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.tmp_db.close()
        app_module.DATABASE = self.tmp_db.name
        app_module.init_db()
        app_module.app.config['TESTING'] = True

    def tearDown(self):
        try:
            os.unlink(self.tmp_db.name)
        except FileNotFoundError:
            pass

    def test_inscription_upload_and_dashboard_delete(self):
        client = app_module.app.test_client()

        with patch('cloudinary.uploader.upload') as upload_mock:
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
        self.assertEqual(provider['password'], 'monmotdepasse')

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
