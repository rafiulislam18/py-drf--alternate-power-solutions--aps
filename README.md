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

## Useful Links 🔗

- [http://127.0.0.1:8000/docs/](http://127.0.0.1:8000/docs/) to access the API Documentation
- [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/) to access the Django Admin
