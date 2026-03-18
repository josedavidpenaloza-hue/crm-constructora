import sqlite3
import bcrypt
import os

# En producción (Render) usa /data/crm.db (disco persistente montado)
# En local usa el directorio del proyecto
_data_dir = '/data' if os.path.isdir('/data') else os.path.dirname(__file__)
os.makedirs(_data_dir, exist_ok=True)
DB_PATH = os.path.join(_data_dir, 'crm.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'staff',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT,
        phone TEXT,
        whatsapp TEXT,
        address TEXT,
        city TEXT,
        rfc TEXT,
        notes TEXT,
        created_by INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS whatsapp_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER,
        direction TEXT NOT NULL DEFAULT 'outbound',
        from_number TEXT,
        to_number TEXT,
        body TEXT NOT NULL,
        status TEXT DEFAULT 'sent',
        twilio_sid TEXT,
        user_id INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS whatsapp_config (
        id INTEGER PRIMARY KEY DEFAULT 1,
        account_sid TEXT,
        auth_token TEXT,
        from_number TEXT,
        anthropic_key TEXT,
        webhook_url TEXT,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        client_id INTEGER,
        status TEXT NOT NULL DEFAULT 'cotizacion',
        budget REAL,
        spent REAL DEFAULT 0,
        start_date TEXT,
        end_date TEXT,
        location TEXT,
        description TEXT,
        assigned_to INTEGER,
        created_by INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        title TEXT NOT NULL,
        description TEXT,
        assigned_to INTEGER,
        status TEXT NOT NULL DEFAULT 'pendiente',
        priority TEXT NOT NULL DEFAULT 'media',
        due_date TEXT,
        created_by INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        description TEXT NOT NULL,
        entity_type TEXT,
        entity_id INTEGER,
        user_id INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS units (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        unit_number TEXT NOT NULL,
        floor INTEGER,
        area_m2 REAL,
        bedrooms INTEGER DEFAULT 2,
        bathrooms INTEGER DEFAULT 1,
        price REAL,
        status TEXT DEFAULT 'disponible',
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        unit_id INTEGER,
        name TEXT NOT NULL,
        phone TEXT,
        whatsapp TEXT,
        email TEXT,
        stage TEXT DEFAULT 'nuevo',
        source TEXT DEFAULT 'directo',
        tiene_dinero_separacion INTEGER DEFAULT 0,
        tiene_credito INTEGER DEFAULT 0,
        tipo_credito TEXT,
        tiene_subsidio INTEGER DEFAULT 0,
        caja_compensacion TEXT,
        puede_cubrir_faltante INTEGER DEFAULT 0,
        budget REAL,
        last_contact TEXT,
        next_contact TEXT,
        notes TEXT,
        ai_active INTEGER DEFAULT 0,
        assigned_to INTEGER,
        created_by INTEGER,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS lead_activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id INTEGER NOT NULL,
        type TEXT DEFAULT 'nota',
        description TEXT NOT NULL,
        user_id INTEGER,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id INTEGER,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        due_datetime TEXT NOT NULL,
        done INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)

    # Seed admin user if DB is empty
    row = c.execute("SELECT COUNT(*) FROM users").fetchone()
    if row[0] == 0:
        hashed = bcrypt.hashpw(b'admin123', bcrypt.gensalt()).decode()
        c.execute("INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
                  ('Administrador', 'admin@constructora.com', hashed, 'admin'))

        c.execute("""INSERT INTO clients (name, email, phone, city, created_by)
                     VALUES (?, ?, ?, ?, 1)""",
                  ('Grupo Inmobiliario Norte', 'contacto@ginorte.com', '555-1234', 'Monterrey'))
        client_id = c.lastrowid

        c.execute("""INSERT INTO projects (name, client_id, status, budget, location,
                     start_date, end_date, description, created_by, assigned_to)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1)""",
                  ('Torre Residencial Norte', client_id, 'en_proceso', 4500000,
                   'Av. Constitución 200, Monterrey', '2026-01-15', '2026-12-31',
                   'Construcción de torre residencial de 12 pisos'))
        project_id = c.lastrowid

        tasks_seed = [
            (project_id, 'Revisión de planos estructurales', 'completado', 'alta', '2026-01-20'),
            (project_id, 'Permisos de construcción', 'en_proceso', 'alta', '2026-02-15'),
            (project_id, 'Compra de materiales fase 1', 'pendiente', 'media', '2026-04-01'),
        ]
        c.executemany("""INSERT INTO tasks (project_id, title, status, priority, due_date, created_by, assigned_to)
                         VALUES (?, ?, ?, ?, ?, 1, 1)""", tasks_seed)

        c.execute("INSERT INTO activities (type, description, entity_type, entity_id, user_id) VALUES (?, ?, ?, ?, 1)",
                  ('create', 'Sistema CRM iniciado', 'system', 0))

    # ── Migraciones seguras (ALTER TABLE si la columna no existe) ──────────────
    existing_cols = [r[1] for r in c.execute('PRAGMA table_info(clients)').fetchall()]
    if 'whatsapp' not in existing_cols:
        c.execute("ALTER TABLE clients ADD COLUMN whatsapp TEXT")

    # Crear tablas nuevas si no existen (legacy migration)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS whatsapp_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER,
        direction TEXT NOT NULL DEFAULT 'outbound',
        from_number TEXT,
        to_number TEXT,
        body TEXT NOT NULL,
        status TEXT DEFAULT 'sent',
        twilio_sid TEXT,
        user_id INTEGER,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS whatsapp_config (
        id INTEGER PRIMARY KEY DEFAULT 1,
        account_sid TEXT,
        auth_token TEXT,
        from_number TEXT,
        updated_at TEXT
    );
    """)

    # Migrar whatsapp_config para agregar anthropic_key si no existe
    wa_cols = [r[1] for r in c.execute('PRAGMA table_info(whatsapp_config)').fetchall()]
    if 'anthropic_key' not in wa_cols and wa_cols:
        c.execute("ALTER TABLE whatsapp_config ADD COLUMN anthropic_key TEXT")

    # Migrar leads si no existe tabla (las tablas se crean via executescript)
    lead_tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    # (las tablas se crean via executescript, no necesita migración extra aquí)

    conn.commit()
    conn.close()
