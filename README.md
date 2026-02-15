# APS Website Backend Setup 🚀

## 1. Create and Activate Virtual Environment 🧩

### Windows PowerShell

```powershell
python -m venv venv
.\venv\Scripts\activate
```

### macOS/Linux

```bash
python3 -m venv venv
source venv/bin/activate
```

---

## 2. Install Dependencies 📦

```bash
pip install -r requirements.txt
```

---

## 3. Set Up Environment Variables ⚙️

Before running migrations, you need to set up your environment variables:

1. **Copy the demo environment file:**

   ```bash
   cp demo.env .env
   ```

   > On Windows PowerShell, use:
   > ```powershell
   > Copy-Item demo.env .env
   > ```

2. **Open the new `.env` file** in your code editor and fill in the required values (such as secret keys, necessary credentials, etc.).

---

## 4. Apply Migrations 🛠️

```bash
python manage.py makemigrations
python manage.py migrate
```

---

## 5. Create Superuser 👤

```bash
python manage.py createsuperuser
```

---

## 6. Run the Development Server ▶️

```bash
python manage.py runserver
```

## Database

The project now uses **PostgreSQL** as the production and main development database (switched from SQLite in February 2026).

### Current Configuration (settings.py)

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'aps_db',
        'USER': 'aps_db_user',
        'PASSWORD': os.getenv('DB_PASSWORD'),           # ← never commit real password
        'HOST': 'localhost',                      # or remote host in production
        'PORT': '5432',
    }
}
