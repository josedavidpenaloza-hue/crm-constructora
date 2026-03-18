from flask import Flask, request, jsonify, render_template, g
import sqlite3
import bcrypt
import jwt
import os
import csv
import io
import re
import json
import urllib.request
from functools import wraps
from database import get_db, init_db

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
JWT_SECRET = os.environ.get('SECRET_KEY', os.environ.get('JWT_SECRET', 'crm-constructora-secret-2026'))


# ─── DB helpers ────────────────────────────────────────────────────────────────

def db():
    if 'db' not in g:
        g.db = get_db()
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    conn = g.pop('db', None)
    if conn:
        conn.close()


def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


def row(cursor):
    r = cursor.fetchone()
    return dict(r) if r else None


def log_activity(description, entity_type=None, entity_id=None, user_id=None):
    db().execute(
        "INSERT INTO activities (type, description, entity_type, entity_id, user_id) VALUES (?, ?, ?, ?, ?)",
        ('action', description, entity_type, entity_id, user_id)
    )
    db().commit()


# ─── Auth middleware ───────────────────────────────────────────────────────────

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify(error='No autorizado'), 401
        try:
            payload = jwt.decode(auth[7:], JWT_SECRET, algorithms=['HS256'])
            g.user = payload
        except jwt.PyJWTError:
            return jsonify(error='Token inválido'), 401
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if g.user.get('role') != 'admin':
            return jsonify(error='Sin permiso'), 403
        return f(*args, **kwargs)
    return decorated


# ─── Frontend SPA (catch-all, must be registered LAST) ───────────────────────

def register_spa_route():
    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def index(path):
        if path.startswith('api/'):
            from flask import abort
            abort(404)
        return render_template('index.html')


# ─── Auth routes ──────────────────────────────────────────────────────────────

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    email = (data.get('email') or '').strip()
    password = (data.get('password') or '').encode()
    if not email or not password:
        return jsonify(error='Campos requeridos'), 400

    user = row(db().execute('SELECT * FROM users WHERE email = ? AND active = 1', (email,)))
    if not user or not bcrypt.checkpw(password, user['password'].encode()):
        return jsonify(error='Credenciales incorrectas'), 401

    token = jwt.encode(
        {'id': user['id'], 'email': user['email'], 'role': user['role'], 'name': user['name']},
        JWT_SECRET, algorithm='HS256'
    )
    return jsonify(token=token, user={'id': user['id'], 'name': user['name'], 'email': user['email'], 'role': user['role']})


@app.route('/api/auth/register', methods=['POST'])
@require_auth
def register():
    if g.user.get('role') != 'admin':
        return jsonify(error='Sin permiso'), 403
    data = request.get_json()
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    role = data.get('role', 'staff')
    if not name or not email or not password:
        return jsonify(error='Campos requeridos'), 400
    try:
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        c = db().execute('INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)',
                         (name, email, hashed, role))
        db().commit()
        return jsonify(id=c.lastrowid, name=name, email=email, role=role)
    except sqlite3.IntegrityError:
        return jsonify(error='Email ya registrado'), 400


# ─── Perfil propio ────────────────────────────────────────────────────────────

@app.route('/api/auth/me', methods=['GET', 'PUT'])
@require_auth
def me():
    uid = g.user['id']
    if request.method == 'GET':
        u = row(db().execute('SELECT id,name,email,role,created_at FROM users WHERE id=?', (uid,)))
        return jsonify(u)
    d = request.get_json()
    name  = (d.get('name') or '').strip()
    email = (d.get('email') or '').strip()
    new_pw = d.get('new_password', '').strip()
    cur_pw = d.get('current_password', '').strip()
    if not name or not email:
        return jsonify(error='Nombre y email son requeridos'), 400
    # Verificar contraseña actual si quiere cambiarla
    if new_pw:
        if not cur_pw:
            return jsonify(error='Ingresa tu contraseña actual'), 400
        user_row = row(db().execute('SELECT password FROM users WHERE id=?', (uid,)))
        if not bcrypt.checkpw(cur_pw.encode(), user_row['password'].encode()):
            return jsonify(error='Contraseña actual incorrecta'), 400
        hashed = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
        db().execute('UPDATE users SET name=?,email=?,password=? WHERE id=?', (name, email, hashed, uid))
    else:
        db().execute('UPDATE users SET name=?,email=? WHERE id=?', (name, email, uid))
    db().commit()
    # Devolver token actualizado
    token = jwt.encode(
        {'id': uid, 'email': email, 'role': g.user['role'], 'name': name},
        JWT_SECRET, algorithm='HS256'
    )
    return jsonify(success=True, token=token, user={'id': uid, 'name': name, 'email': email, 'role': g.user['role']})


# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.route('/api/dashboard')
@require_auth
def dashboard():
    d = db()
    uid = g.user['id']
    stats = {
        'total_clients': d.execute('SELECT COUNT(*) FROM clients').fetchone()[0],
        'total_projects': d.execute('SELECT COUNT(*) FROM projects').fetchone()[0],
        'projects_en_proceso': d.execute("SELECT COUNT(*) FROM projects WHERE status='en_proceso'").fetchone()[0],
        'projects_cotizacion': d.execute("SELECT COUNT(*) FROM projects WHERE status='cotizacion'").fetchone()[0],
        'projects_completado': d.execute("SELECT COUNT(*) FROM projects WHERE status='completado'").fetchone()[0],
        'total_tasks_pending': d.execute("SELECT COUNT(*) FROM tasks WHERE status!='completado'").fetchone()[0],
        'total_budget': d.execute("SELECT COALESCE(SUM(budget),0) FROM projects WHERE status!='cancelado'").fetchone()[0],
        'my_tasks': d.execute("SELECT COUNT(*) FROM tasks WHERE assigned_to=? AND status!='completado'", (uid,)).fetchone()[0],
    }
    recent_projects = rows(d.execute("""
        SELECT p.id, p.name, p.status, p.budget, c.name as client_name
        FROM projects p LEFT JOIN clients c ON p.client_id=c.id
        ORDER BY p.created_at DESC LIMIT 5"""))
    recent_activities = rows(d.execute("""
        SELECT a.*, u.name as user_name FROM activities a
        LEFT JOIN users u ON a.user_id=u.id
        ORDER BY a.created_at DESC LIMIT 10"""))
    upcoming_tasks = rows(d.execute("""
        SELECT t.*, p.name as project_name, u.name as assigned_name
        FROM tasks t
        LEFT JOIN projects p ON t.project_id=p.id
        LEFT JOIN users u ON t.assigned_to=u.id
        WHERE t.status!='completado' AND t.due_date IS NOT NULL
        ORDER BY t.due_date ASC LIMIT 5"""))
    return jsonify(stats=stats, recent_projects=recent_projects,
                   recent_activities=recent_activities, upcoming_tasks=upcoming_tasks)


# ─── Clients ──────────────────────────────────────────────────────────────────

@app.route('/api/clients', methods=['GET', 'POST'])
@require_auth
def clients():
    if request.method == 'GET':
        data = rows(db().execute("""
            SELECT c.*, u.name as created_by_name,
              (SELECT COUNT(*) FROM projects p WHERE p.client_id=c.id) as project_count
            FROM clients c LEFT JOIN users u ON c.created_by=u.id
            ORDER BY c.created_at DESC"""))
        return jsonify(data)
    d = request.get_json()
    if not d.get('name'):
        return jsonify(error='Nombre requerido'), 400
    c = db().execute(
        'INSERT INTO clients (name,email,phone,whatsapp,address,city,rfc,notes,created_by) VALUES (?,?,?,?,?,?,?,?,?)',
        (d.get('name'), d.get('email'), d.get('phone'), d.get('whatsapp'), d.get('address'),
         d.get('city'), d.get('rfc'), d.get('notes'), g.user['id']))
    db().commit()
    log_activity(f"Cliente \"{d['name']}\" creado", 'client', c.lastrowid, g.user['id'])
    return jsonify(id=c.lastrowid, **d)


@app.route('/api/clients/<int:cid>', methods=['GET', 'PUT', 'DELETE'])
@require_auth
def client_detail(cid):
    if request.method == 'GET':
        c = row(db().execute('SELECT * FROM clients WHERE id=?', (cid,)))
        if not c:
            return jsonify(error='No encontrado'), 404
        projs = rows(db().execute('SELECT * FROM projects WHERE client_id=?', (cid,)))
        c['projects'] = projs
        return jsonify(c)
    if request.method == 'PUT':
        d = request.get_json()
        db().execute('UPDATE clients SET name=?,email=?,phone=?,whatsapp=?,address=?,city=?,rfc=?,notes=? WHERE id=?',
                     (d.get('name'), d.get('email'), d.get('phone'), d.get('whatsapp'), d.get('address'),
                      d.get('city'), d.get('rfc'), d.get('notes'), cid))
        db().commit()
        log_activity(f"Cliente \"{d.get('name')}\" actualizado", 'client', cid, g.user['id'])
        return jsonify(success=True)
    if g.user.get('role') != 'admin':
        return jsonify(error='Sin permiso'), 403
    db().execute('DELETE FROM clients WHERE id=?', (cid,))
    db().commit()
    return jsonify(success=True)


# ─── Projects ─────────────────────────────────────────────────────────────────

@app.route('/api/projects', methods=['GET', 'POST'])
@require_auth
def projects():
    if request.method == 'GET':
        data = rows(db().execute("""
            SELECT p.*, c.name as client_name, u.name as assigned_name,
              (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id) as task_count,
              (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id AND t.status='completado') as tasks_done
            FROM projects p
            LEFT JOIN clients c ON p.client_id=c.id
            LEFT JOIN users u ON p.assigned_to=u.id
            ORDER BY p.created_at DESC"""))
        return jsonify(data)
    d = request.get_json()
    if not d.get('name'):
        return jsonify(error='Nombre requerido'), 400
    c = db().execute("""
        INSERT INTO projects (name,client_id,status,budget,start_date,end_date,location,description,assigned_to,created_by)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (d.get('name'), d.get('client_id') or None, d.get('status', 'cotizacion'),
         d.get('budget') or None, d.get('start_date') or None, d.get('end_date') or None,
         d.get('location'), d.get('description'), d.get('assigned_to') or None, g.user['id']))
    db().commit()
    log_activity(f"Proyecto \"{d['name']}\" creado", 'project', c.lastrowid, g.user['id'])
    return jsonify(id=c.lastrowid, **d)


@app.route('/api/projects/<int:pid>', methods=['GET', 'PUT', 'DELETE'])
@require_auth
def project_detail(pid):
    if request.method == 'GET':
        p = row(db().execute("""
            SELECT p.*, c.name as client_name, u.name as assigned_name
            FROM projects p LEFT JOIN clients c ON p.client_id=c.id
            LEFT JOIN users u ON p.assigned_to=u.id WHERE p.id=?""", (pid,)))
        if not p:
            return jsonify(error='No encontrado'), 404
        p['tasks'] = rows(db().execute("""
            SELECT t.*, u.name as assigned_name FROM tasks t
            LEFT JOIN users u ON t.assigned_to=u.id
            WHERE t.project_id=? ORDER BY t.created_at DESC""", (pid,)))
        return jsonify(p)
    if request.method == 'PUT':
        d = request.get_json()
        db().execute("""UPDATE projects SET name=?,client_id=?,status=?,budget=?,spent=?,
                        start_date=?,end_date=?,location=?,description=?,assigned_to=? WHERE id=?""",
                     (d.get('name'), d.get('client_id') or None, d.get('status'),
                      d.get('budget') or None, d.get('spent') or 0,
                      d.get('start_date') or None, d.get('end_date') or None,
                      d.get('location'), d.get('description'), d.get('assigned_to') or None, pid))
        db().commit()
        log_activity(f"Proyecto \"{d.get('name')}\" actualizado", 'project', pid, g.user['id'])
        return jsonify(success=True)
    if g.user.get('role') != 'admin':
        return jsonify(error='Sin permiso'), 403
    db().execute('DELETE FROM tasks WHERE project_id=?', (pid,))
    db().execute('DELETE FROM projects WHERE id=?', (pid,))
    db().commit()
    return jsonify(success=True)


# ─── Tasks ────────────────────────────────────────────────────────────────────

@app.route('/api/tasks', methods=['GET', 'POST'])
@require_auth
def tasks():
    if request.method == 'GET':
        project_id = request.args.get('project_id')
        assigned_to = request.args.get('assigned_to')
        q = """SELECT t.*, u.name as assigned_name, p.name as project_name
               FROM tasks t LEFT JOIN users u ON t.assigned_to=u.id
               LEFT JOIN projects p ON t.project_id=p.id WHERE 1=1"""
        params = []
        if project_id:
            q += ' AND t.project_id=?'
            params.append(project_id)
        if assigned_to:
            q += ' AND t.assigned_to=?'
            params.append(assigned_to)
        q += ' ORDER BY t.created_at DESC'
        return jsonify(rows(db().execute(q, params)))
    d = request.get_json()
    if not d.get('title'):
        return jsonify(error='Título requerido'), 400
    c = db().execute("""
        INSERT INTO tasks (project_id,title,description,assigned_to,status,priority,due_date,created_by)
        VALUES (?,?,?,?,?,?,?,?)""",
        (d.get('project_id') or None, d['title'], d.get('description'),
         d.get('assigned_to') or None, d.get('status', 'pendiente'),
         d.get('priority', 'media'), d.get('due_date') or None, g.user['id']))
    db().commit()
    return jsonify(id=c.lastrowid, **d)


@app.route('/api/tasks/<int:tid>', methods=['PUT', 'DELETE'])
@require_auth
def task_detail(tid):
    if request.method == 'PUT':
        d = request.get_json()
        db().execute("""UPDATE tasks SET title=?,description=?,assigned_to=?,status=?,
                        priority=?,due_date=?,project_id=? WHERE id=?""",
                     (d.get('title'), d.get('description'), d.get('assigned_to') or None,
                      d.get('status'), d.get('priority'), d.get('due_date') or None,
                      d.get('project_id') or None, tid))
        db().commit()
        return jsonify(success=True)
    db().execute('DELETE FROM tasks WHERE id=?', (tid,))
    db().commit()
    return jsonify(success=True)


# ─── Team ─────────────────────────────────────────────────────────────────────

@app.route('/api/team', methods=['GET'])
@require_auth
def team():
    data = rows(db().execute("""
        SELECT id, name, email, role, active, created_at,
          (SELECT COUNT(*) FROM projects WHERE assigned_to=users.id) as project_count,
          (SELECT COUNT(*) FROM tasks WHERE assigned_to=users.id AND status!='completado') as pending_tasks
        FROM users ORDER BY created_at DESC"""))
    return jsonify(data)


@app.route('/api/team/<int:uid>', methods=['PUT', 'DELETE'])
@require_auth
def team_member(uid):
    if g.user.get('role') != 'admin':
        return jsonify(error='Sin permiso'), 403
    if request.method == 'PUT':
        d = request.get_json()
        if d.get('password'):
            hashed = bcrypt.hashpw(d['password'].encode(), bcrypt.gensalt()).decode()
            db().execute('UPDATE users SET name=?,email=?,role=?,active=?,password=? WHERE id=?',
                         (d['name'], d['email'], d['role'], d.get('active', 1), hashed, uid))
        else:
            db().execute('UPDATE users SET name=?,email=?,role=?,active=? WHERE id=?',
                         (d['name'], d['email'], d['role'], d.get('active', 1), uid))
        db().commit()
        return jsonify(success=True)
    if uid == g.user['id']:
        return jsonify(error='No puedes eliminarte'), 400
    db().execute('UPDATE users SET active=0 WHERE id=?', (uid,))
    db().commit()
    return jsonify(success=True)


# ─── Import clientes (Excel / CSV / Google Sheets) ────────────────────────────

IMPORT_COLUMNS = {
    'nombre': 'name', 'name': 'name', 'cliente': 'name', 'client': 'name',
    'email': 'email', 'correo': 'email', 'e-mail': 'email',
    'telefono': 'phone', 'teléfono': 'phone', 'phone': 'phone', 'tel': 'phone',
    'whatsapp': 'whatsapp', 'ws': 'whatsapp', 'wa': 'whatsapp',
    'ciudad': 'city', 'city': 'city',
    'direccion': 'address', 'dirección': 'address', 'address': 'address',
    'rfc': 'rfc',
    'notas': 'notes', 'notes': 'notes', 'observaciones': 'notes',
}

def normalize_header(h):
    return re.sub(r'[^a-z0-9]', '', h.lower().strip())

def map_headers(headers):
    mapping = {}
    for i, h in enumerate(headers):
        key = normalize_header(h)
        if key in IMPORT_COLUMNS:
            mapping[IMPORT_COLUMNS[key]] = i
    return mapping

def import_rows(rows_data, user_id):
    imported = 0
    skipped = 0
    errors = []
    d = db()
    for idx, row_data in enumerate(rows_data):
        name = (row_data.get('name') or '').strip()
        if not name:
            skipped += 1
            continue
        try:
            existing = d.execute('SELECT id FROM clients WHERE name=?', (name,)).fetchone()
            if existing:
                # Update whatsapp/phone if missing
                d.execute('''UPDATE clients SET
                    phone=COALESCE(NULLIF(phone,''), ?),
                    whatsapp=COALESCE(NULLIF(whatsapp,''), ?),
                    email=COALESCE(NULLIF(email,''), ?),
                    city=COALESCE(NULLIF(city,''), ?)
                    WHERE id=?''',
                    (row_data.get('phone'), row_data.get('whatsapp'),
                     row_data.get('email'), row_data.get('city'), existing['id']))
                skipped += 1
            else:
                d.execute('''INSERT INTO clients (name,email,phone,whatsapp,address,city,rfc,notes,created_by)
                             VALUES (?,?,?,?,?,?,?,?,?)''',
                          (name, row_data.get('email'), row_data.get('phone'),
                           row_data.get('whatsapp'), row_data.get('address'),
                           row_data.get('city'), row_data.get('rfc'),
                           row_data.get('notes'), user_id))
                imported += 1
        except Exception as e:
            errors.append(f'Fila {idx+2}: {str(e)}')
    d.commit()
    return imported, skipped, errors

def parse_csv_data(text):
    reader = csv.reader(io.StringIO(text))
    rows_list = list(reader)
    if not rows_list:
        return []
    headers = rows_list[0]
    mapping = map_headers(headers)
    result = []
    for row_vals in rows_list[1:]:
        item = {}
        for field, idx in mapping.items():
            item[field] = row_vals[idx].strip() if idx < len(row_vals) else ''
        result.append(item)
    return result

def parse_xlsx_data(file_bytes):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = iter(ws.rows)
    headers = [str(cell.value or '').strip() for cell in next(rows_iter)]
    mapping = map_headers(headers)
    result = []
    for row_vals in rows_iter:
        item = {}
        for field, idx in mapping.items():
            val = row_vals[idx].value if idx < len(row_vals) else None
            item[field] = str(val).strip() if val is not None else ''
        result.append(item)
    wb.close()
    return result

@app.route('/api/clients/import', methods=['POST'])
@require_auth
def import_clients():
    user_id = g.user['id']
    # Excel upload
    if 'file' in request.files:
        f = request.files['file']
        fname = f.filename.lower()
        if fname.endswith('.xlsx') or fname.endswith('.xls'):
            data = parse_xlsx_data(f.read())
        elif fname.endswith('.csv'):
            data = parse_csv_data(f.read().decode('utf-8-sig'))
        else:
            return jsonify(error='Formato no soportado. Usa .xlsx o .csv'), 400
        imported, skipped, errors = import_rows(data, user_id)
        log_activity(f'Importación Excel: {imported} clientes nuevos', 'client', None, user_id)
        return jsonify(imported=imported, skipped=skipped, errors=errors)
    # Google Sheets URL
    data_json = request.get_json(silent=True) or {}
    gs_url = data_json.get('gsheets_url', '')
    if gs_url:
        try:
            # Convert share URL to CSV export URL
            match = re.search(r'/spreadsheets/d/([a-zA-Z0-9_-]+)', gs_url)
            if not match:
                return jsonify(error='URL de Google Sheets inválida'), 400
            sheet_id = match.group(1)
            gid_match = re.search(r'gid=(\d+)', gs_url)
            gid = gid_match.group(1) if gid_match else '0'
            csv_url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}'
            req = urllib.request.Request(csv_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                text = resp.read().decode('utf-8-sig')
            data = parse_csv_data(text)
            imported, skipped, errors = import_rows(data, user_id)
            log_activity(f'Importación Google Sheets: {imported} clientes nuevos', 'client', None, user_id)
            return jsonify(imported=imported, skipped=skipped, errors=errors)
        except Exception as e:
            return jsonify(error=f'No se pudo acceder al Google Sheet: {str(e)}. Asegúrate de que el sheet sea público ("Cualquiera con el enlace puede ver").'), 400
    return jsonify(error='Envía un archivo o una URL de Google Sheets'), 400

@app.route('/api/clients/import/template', methods=['GET'])
@require_auth
def import_template():
    from flask import Response
    output = 'nombre,email,telefono,whatsapp,ciudad,direccion,rfc,notas\n'
    output += 'Empresa Ejemplo,contacto@empresa.com,555-1234,5551234567,Monterrey,"Av. Principal 100",ABC123456DEF,"Cliente potencial"\n'
    return Response(output, mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment;filename=plantilla_clientes.csv'})


# ─── Units ────────────────────────────────────────────────────────────────────

@app.route('/api/projects/<int:pid>/units', methods=['GET', 'POST'])
@require_auth
def project_units(pid):
    if request.method == 'GET':
        data = rows(db().execute('''
            SELECT u.*,
              (SELECT COUNT(*) FROM leads l WHERE l.unit_id=u.id AND l.stage NOT IN ("perdido")) as interested_count
            FROM units u WHERE u.project_id=? ORDER BY u.unit_number''', (pid,)))
        return jsonify(data)
    d = request.get_json()
    c = db().execute(
        'INSERT INTO units (project_id,unit_number,floor,area_m2,bedrooms,bathrooms,price,status,notes) VALUES (?,?,?,?,?,?,?,?,?)',
        (pid, d.get('unit_number'), d.get('floor'), d.get('area_m2'), d.get('bedrooms', 2),
         d.get('bathrooms', 1), d.get('price'), d.get('status', 'disponible'), d.get('notes')))
    db().commit()
    return jsonify(id=c.lastrowid, **d)

@app.route('/api/units/<int:uid>', methods=['PUT', 'DELETE'])
@require_auth
def unit_detail(uid):
    if request.method == 'PUT':
        d = request.get_json()
        db().execute('UPDATE units SET unit_number=?,floor=?,area_m2=?,bedrooms=?,bathrooms=?,price=?,status=?,notes=? WHERE id=?',
                     (d.get('unit_number'), d.get('floor'), d.get('area_m2'), d.get('bedrooms'),
                      d.get('bathrooms'), d.get('price'), d.get('status'), d.get('notes'), uid))
        db().commit()
        return jsonify(success=True)
    db().execute('DELETE FROM units WHERE id=?', (uid,))
    db().commit()
    return jsonify(success=True)

# ─── Leads / Pipeline ─────────────────────────────────────────────────────────

LEAD_STAGES = ['nuevo', 'contactado', 'seguimiento', 'visita', 'calificacion', 'negociacion', 'separacion', 'escriturado', 'perdido']

@app.route('/api/leads', methods=['GET', 'POST'])
@require_auth
def leads():
    if request.method == 'GET':
        project_id = request.args.get('project_id')
        q = '''SELECT l.*, p.name as project_name, u.unit_number,
                      tm.name as assigned_name,
                      (SELECT COUNT(*) FROM lead_activities a WHERE a.lead_id=l.id) as activity_count
               FROM leads l
               LEFT JOIN projects p ON l.project_id=p.id
               LEFT JOIN units u ON l.unit_id=u.id
               LEFT JOIN users tm ON l.assigned_to=tm.id
               WHERE 1=1'''
        params = []
        if project_id:
            q += ' AND l.project_id=?'
            params.append(project_id)
        q += ' ORDER BY l.created_at DESC'
        return jsonify(rows(db().execute(q, params)))
    d = request.get_json()
    if not d.get('name'):
        return jsonify(error='Nombre requerido'), 400
    c = db().execute('''INSERT INTO leads
        (project_id,unit_id,name,phone,whatsapp,email,stage,source,
         tiene_dinero_separacion,tiene_credito,tipo_credito,tiene_subsidio,
         caja_compensacion,puede_cubrir_faltante,budget,next_contact,notes,
         assigned_to,created_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (d.get('project_id'), d.get('unit_id'), d['name'], d.get('phone'), d.get('whatsapp'),
         d.get('email'), d.get('stage', 'nuevo'), d.get('source', 'directo'),
         int(d.get('tiene_dinero_separacion', 0)), int(d.get('tiene_credito', 0)),
         d.get('tipo_credito'), int(d.get('tiene_subsidio', 0)),
         d.get('caja_compensacion'), int(d.get('puede_cubrir_faltante', 0)),
         d.get('budget'), d.get('next_contact'), d.get('notes'),
         d.get('assigned_to') or None, g.user['id']))
    db().commit()
    lid = c.lastrowid
    db().execute("INSERT INTO lead_activities (lead_id,type,description,user_id) VALUES (?,?,?,?)",
                 (lid, 'nota', f"Lead creado. Fuente: {d.get('source', 'directo')}", g.user['id']))
    db().commit()
    log_activity(f"Lead \"{d['name']}\" creado", 'lead', lid, g.user['id'])
    return jsonify(id=lid, **d)

@app.route('/api/leads/<int:lid>', methods=['GET', 'PUT', 'DELETE'])
@require_auth
def lead_detail(lid):
    if request.method == 'GET':
        l = row(db().execute('''SELECT l.*, p.name as project_name, u.unit_number, u.price as unit_price,
                                       tm.name as assigned_name
                                FROM leads l LEFT JOIN projects p ON l.project_id=p.id
                                LEFT JOIN units u ON l.unit_id=u.id
                                LEFT JOIN users tm ON l.assigned_to=tm.id
                                WHERE l.id=?''', (lid,)))
        if not l:
            return jsonify(error='No encontrado'), 404
        l['activities'] = rows(db().execute('''SELECT a.*, u.name as user_name FROM lead_activities a
                                               LEFT JOIN users u ON a.user_id=u.id
                                               WHERE a.lead_id=? ORDER BY a.created_at DESC''', (lid,)))
        l['reminders'] = rows(db().execute('SELECT * FROM reminders WHERE lead_id=? AND done=0 ORDER BY due_datetime', (lid,)))
        return jsonify(l)
    if request.method == 'PUT':
        d = request.get_json()
        old = row(db().execute('SELECT stage FROM leads WHERE id=?', (lid,)))
        db().execute('''UPDATE leads SET project_id=?,unit_id=?,name=?,phone=?,whatsapp=?,email=?,
                        stage=?,source=?,tiene_dinero_separacion=?,tiene_credito=?,tipo_credito=?,
                        tiene_subsidio=?,caja_compensacion=?,puede_cubrir_faltante=?,budget=?,
                        next_contact=?,notes=?,assigned_to=?,last_contact=datetime('now') WHERE id=?''',
                     (d.get('project_id'), d.get('unit_id') or None, d.get('name'), d.get('phone'),
                      d.get('whatsapp'), d.get('email'), d.get('stage'), d.get('source'),
                      int(d.get('tiene_dinero_separacion', 0)), int(d.get('tiene_credito', 0)),
                      d.get('tipo_credito'), int(d.get('tiene_subsidio', 0)),
                      d.get('caja_compensacion'), int(d.get('puede_cubrir_faltante', 0)),
                      d.get('budget'), d.get('next_contact'), d.get('notes'),
                      d.get('assigned_to') or None, lid))
        # If unit is being sold/reserved update unit status
        if d.get('stage') == 'separacion' and d.get('unit_id'):
            db().execute("UPDATE units SET status='reservado' WHERE id=?", (d.get('unit_id'),))
        if d.get('stage') == 'escriturado' and d.get('unit_id'):
            db().execute("UPDATE units SET status='vendido' WHERE id=?", (d.get('unit_id'),))
        if old and old['stage'] != d.get('stage'):
            db().execute("INSERT INTO lead_activities (lead_id,type,description,user_id) VALUES (?,?,?,?)",
                         (lid, 'etapa', f"Etapa cambiada: {old['stage']} → {d.get('stage')}", g.user['id']))
        db().commit()
        return jsonify(success=True)
    db().execute('DELETE FROM lead_activities WHERE lead_id=?', (lid,))
    db().execute('DELETE FROM leads WHERE id=?', (lid,))
    db().commit()
    return jsonify(success=True)

@app.route('/api/leads/<int:lid>/activities', methods=['POST'])
@require_auth
def lead_add_activity(lid):
    d = request.get_json()
    db().execute("INSERT INTO lead_activities (lead_id,type,description,user_id) VALUES (?,?,?,?)",
                 (lid, d.get('type', 'nota'), d.get('description', ''), g.user['id']))
    db().commit()
    return jsonify(success=True)

# ─── Reminders ────────────────────────────────────────────────────────────────

@app.route('/api/reminders', methods=['GET', 'POST'])
@require_auth
def reminders():
    if request.method == 'GET':
        uid = g.user['id']
        data = rows(db().execute('''SELECT r.*, l.name as lead_name FROM reminders r
                                    LEFT JOIN leads l ON r.lead_id=l.id
                                    WHERE r.user_id=? AND r.done=0
                                    ORDER BY r.due_datetime''', (uid,)))
        return jsonify(data)
    d = request.get_json()
    db().execute('INSERT INTO reminders (lead_id,user_id,title,description,due_datetime) VALUES (?,?,?,?,?)',
                 (d.get('lead_id'), g.user['id'], d.get('title'), d.get('description'), d.get('due_datetime')))
    db().commit()
    return jsonify(success=True)

@app.route('/api/reminders/<int:rid>', methods=['PUT'])
@require_auth
def reminder_done(rid):
    db().execute('UPDATE reminders SET done=1 WHERE id=?', (rid,))
    db().commit()
    return jsonify(success=True)

# ─── AI WhatsApp Agent ────────────────────────────────────────────────────────

def get_ai_config():
    cfg = get_wa_config()
    ai_key = db().execute("SELECT auth_token FROM whatsapp_config WHERE id=1").fetchone()
    return cfg

@app.route('/api/whatsapp/ai-webhook', methods=['POST'])
def ai_whatsapp_webhook():
    """Webhook de Twilio que procesa mensajes con IA"""
    from_number = request.form.get('From', '').replace('whatsapp:', '')
    body = request.form.get('Body', '').strip()
    sid = request.form.get('MessageSid', '')
    if not body:
        return '<Response></Response>', 200, {'Content-Type': 'text/xml'}

    clean_num = re.sub(r'\D', '', from_number)

    # Buscar lead por whatsapp
    lead = row(db().execute(
        "SELECT l.*, p.name as project_name FROM leads l LEFT JOIN projects p ON l.project_id=p.id WHERE REPLACE(REPLACE(l.whatsapp,' ',''),'+','')=? OR REPLACE(REPLACE(l.phone,' ',''),'+','')=?",
        (clean_num, clean_num)))

    # Si no existe, crear lead nuevo
    if not lead:
        c = db().execute("INSERT INTO leads (name,whatsapp,stage,source,ai_active,created_by) VALUES (?,?,?,?,?,1)",
                         (f"Lead WA {from_number[-4:]}", from_number, 'nuevo', 'whatsapp', 1))
        db().commit()
        lead_id = c.lastrowid
        lead = row(db().execute("SELECT * FROM leads WHERE id=?", (lead_id,)))
    else:
        lead_id = lead['id']

    # Guardar mensaje entrante
    db().execute("INSERT INTO whatsapp_messages (client_id,direction,from_number,body,status,twilio_sid) VALUES (?,?,?,?,?,?)",
                 (lead_id, 'inbound', from_number, body, 'received', sid))
    db().execute("INSERT INTO lead_activities (lead_id,type,description) VALUES (?,?,?)",
                 (lead_id, 'whatsapp', f"Entrante: {body[:100]}"))
    db().execute("UPDATE leads SET last_contact=datetime('now') WHERE id=?", (lead_id,))
    db().commit()

    # Generar respuesta (IA con API key, o bot por reglas si no hay key)
    ai_key = None
    ai_cfg = db().execute("SELECT * FROM whatsapp_config WHERE id=1").fetchone()
    if ai_cfg:
        ai_cfg = dict(ai_cfg)
        ai_key = ai_cfg.get('anthropic_key')

    reply = None
    if ai_key:
        try:
            reply = generate_ai_reply(lead, body, ai_key)
        except Exception as e:
            reply = generate_rule_reply(lead, body)
    else:
        reply = generate_rule_reply(lead, body)

    if reply:
        # Enviar respuesta via Twilio
        wa_cfg = get_wa_config()
        if wa_cfg.get('account_sid') and wa_cfg.get('auth_token'):
            try:
                from twilio.rest import Client as TwilioClient
                tw = TwilioClient(wa_cfg['account_sid'], wa_cfg['auth_token'])
                to_wa = 'whatsapp:' + from_number
                from_wa = 'whatsapp:' + wa_cfg['from_number']
                msg = tw.messages.create(body=reply, from_=from_wa, to=to_wa)
                db().execute("INSERT INTO whatsapp_messages (client_id,direction,from_number,to_number,body,status,twilio_sid) VALUES (?,?,?,?,?,?,?)",
                             (lead_id, 'outbound', wa_cfg['from_number'], from_number, reply, 'sent', msg.sid))
                db().execute("INSERT INTO lead_activities (lead_id,type,description) VALUES (?,?,?)",
                             (lead_id, 'ai_whatsapp', f"IA respondio: {reply[:100]}"))
                db().commit()
            except Exception as e:
                pass
    return '<Response></Response>', 200, {'Content-Type': 'text/xml'}


def generate_rule_reply(lead, msg):
    """Bot por reglas — funciona sin API key de IA"""
    msg_l = msg.lower().strip()
    lead_id = lead['id']

    # Cargar proyectos disponibles
    projects = rows(db().execute("""
        SELECT p.name, p.location,
               COUNT(u.id) as total,
               SUM(CASE WHEN u.status='disponible' THEN 1 ELSE 0 END) as disp,
               MIN(u.price) as min_p, MAX(u.price) as max_p,
               MIN(u.bedrooms) as min_hab, MAX(u.bedrooms) as max_hab
        FROM projects p LEFT JOIN units u ON u.project_id=p.id
        WHERE p.status IN ('cotizacion','en_proceso') GROUP BY p.id"""))

    # ── Detección de intención ────────────────────────────────────────────────
    saludo     = any(w in msg_l for w in ['hola','buenas','buenos','buen día','buendia','hi','hello','saludos'])
    info_proy  = any(w in msg_l for w in ['proyecto','proyectos','casa','apartamento','apto','vivienda','inmueble','propiedad','unidad','disponible'])
    precio     = any(w in msg_l for w in ['precio','valor','costo','cuánto','cuanto','vale','cuesta'])
    credito    = any(w in msg_l for w in ['crédito','credito','préstamo','prestamo','hipoteca','banco','financiamiento','financiar','subsidio','caja','comfenalco','cafam','colsubsidio','compensar'])
    separacion = any(w in msg_l for w in ['separar','separación','separacion','reservar','reserva','apartar','inicial','cuota inicial'])
    si_tiene   = any(w in msg_l for w in ['sí','si','claro','tengo','cuento','afirmativo','efectivamente','correcto','ok','oki','dale','listo','ya tengo'])
    no_tiene   = any(w in msg_l for w in ['no tengo','no cuento','no tengo','todavía no','aun no','aún no','no sé','no se'])
    gracias    = any(w in msg_l for w in ['gracias','thank','perfecto','excelente','genial','buenísimo'])
    asesor     = any(w in msg_l for w in ['asesor','agente','persona','humano','hablar con','comunicar'])

    # ── Cualificación automática ──────────────────────────────────────────────
    updates = []
    if separacion and si_tiene and not lead.get('tiene_dinero_separacion'):
        db().execute("UPDATE leads SET tiene_dinero_separacion=1 WHERE id=?", (lead_id,))
        db().execute("INSERT INTO lead_activities (lead_id,type,description) VALUES (?,?,?)",
                     (lead_id,'whatsapp','✅ Bot: confirmó dinero para separación'))
        updates.append('separacion')
    if credito and si_tiene and not lead.get('tiene_credito'):
        db().execute("UPDATE leads SET tiene_credito=1 WHERE id=?", (lead_id,))
        db().execute("INSERT INTO lead_activities (lead_id,type,description) VALUES (?,?,?)",
                     (lead_id,'whatsapp','✅ Bot: confirmó acceso a crédito/subsidio'))
        updates.append('credito')
    if updates:
        db().commit()
        # Verificar si ya está completamente cualificado
        updated_lead = row(db().execute("SELECT * FROM leads WHERE id=?", (lead_id,)))
        if (updated_lead.get('tiene_dinero_separacion') and
            (updated_lead.get('tiene_credito') or updated_lead.get('tiene_subsidio'))):
            db().execute("UPDATE leads SET stage='calificacion' WHERE id=?", (lead_id,))
            db().commit()

    # ── Construir respuesta ───────────────────────────────────────────────────
    nombre = (lead.get('name') or 'amigo').split()[0]
    if nombre.startswith('Lead WA'):
        nombre = 'amigo'

    # Texto de proyectos disponibles
    def proyectos_texto():
        if not projects:
            return "En este momento estamos preparando nuevos proyectos. ¡Te avisamos pronto! 🔜"
        lines = []
        for p in projects:
            hab = f"{p['min_hab']}" if p['min_hab']==p['max_hab'] else f"{p['min_hab']}-{p['max_hab']}"
            precio_txt = f"desde ${p['min_p']:,.0f}" if p['min_p'] else "consultar precio"
            lines.append(f"🏠 *{p['name']}* — {p['location']}\n   {p['disp']} unidades disponibles | {hab} hab | {precio_txt}")
        return "\n\n".join(lines)

    # Solicitar asesor humano
    if asesor:
        db().execute("UPDATE leads SET stage='seguimiento' WHERE id=?", (lead_id,))
        db().commit()
        return f"¡Por supuesto, {nombre}! 😊 Voy a notificar a uno de nuestros asesores para que te contacte lo antes posible.\n\nMientras tanto, ¿hay algo más en lo que te pueda ayudar?"

    if gracias:
        return f"¡Con gusto, {nombre}! 😊 Estamos para servirte. Si tienes más preguntas sobre nuestros proyectos, aquí estaré. 🏗️"

    if saludo and lead.get('stage') == 'nuevo':
        db().execute("UPDATE leads SET stage='contactado' WHERE id=?", (lead_id,))
        db().commit()
        proy_txt = proyectos_texto()
        return (f"¡Hola! 👋 Soy Sofía, asesora de Constructora. ¡Qué bueno que nos contactas!\n\n"
                f"Actualmente tenemos estos proyectos disponibles:\n\n{proy_txt}\n\n"
                f"¿Cuál te llama más la atención? 😊")

    if saludo:
        return f"¡Hola de nuevo, {nombre}! 😊 ¿En qué te puedo ayudar hoy?"

    if precio:
        proy_txt = proyectos_texto()
        return (f"Claro, {nombre}! 💰 Aquí los precios de nuestros proyectos:\n\n{proy_txt}\n\n"
                f"¿Te gustaría conocer más detalles de alguno en particular?")

    if info_proy:
        proy_txt = proyectos_texto()
        return f"¡Con mucho gusto! 🏗️ Estos son nuestros proyectos:\n\n{proy_txt}\n\n¿Cuál te interesa más?"

    if separacion and not si_tiene and not no_tiene:
        return (f"{nombre}, para separar tu unidad manejamos una cuota inicial accesible. "
                f"¿Ya cuentas con los recursos para la separación? 💰")

    if credito and not si_tiene and not no_tiene:
        return (f"Excelente pregunta, {nombre}! 🏦 Trabajamos con los principales bancos del país "
                f"y también aplica subsidio de caja de compensación (Comfenalco, Cafam, Colsubsidio, etc).\n\n"
                f"¿Ya tienes un crédito aprobado o subsidio de tu caja?")

    if 'si_tiene' in updates:
        return f"¡Excelente, {nombre}! ✅ Eso nos ayuda mucho. Un asesor te contactará pronto para guiarte en el proceso. 🤝"

    # Respuesta genérica según etapa
    etapa = lead.get('stage','nuevo')
    if etapa in ['nuevo','contactado']:
        proy_txt = proyectos_texto()
        return (f"Hola {nombre}! 😊 Soy Sofía de Constructora.\n\n"
                f"Tenemos proyectos increíbles para ti:\n\n{proy_txt}\n\n"
                f"¿Te puedo contar más sobre alguno? 🏠")
    elif etapa == 'seguimiento':
        return f"¡Hola {nombre}! 😊 Seguimos aquí para ayudarte. ¿Tienes alguna duda sobre nuestros proyectos o el proceso de compra?"
    else:
        return (f"Hola {nombre}! 😊 Gracias por escribirnos. "
                f"¿En qué te puedo ayudar hoy? Puedo darte información sobre proyectos, precios, créditos o separación.")


def generate_ai_reply(lead, user_message, api_key):
    """Genera respuesta usando Claude - califica al lead naturalmente"""
    import anthropic as ant

    # Cargar historial de conversación reciente
    msgs = rows(db().execute("""SELECT direction, body FROM whatsapp_messages
                                WHERE client_id=? ORDER BY created_at DESC LIMIT 20""", (lead['id'],)))
    msgs.reverse()

    # Cargar proyectos disponibles con unidades
    projects_info = rows(db().execute("""
        SELECT p.name, p.location, p.description,
               COUNT(u.id) as total_units,
               SUM(CASE WHEN u.status='disponible' THEN 1 ELSE 0 END) as available_units,
               MIN(u.price) as min_price, MAX(u.price) as max_price,
               GROUP_CONCAT(u.bedrooms || 'hab $' || COALESCE(u.price,'?'), ' | ') as unit_types
        FROM projects p LEFT JOIN units u ON u.project_id=p.id
        WHERE p.status IN ('cotizacion','en_proceso')
        GROUP BY p.id"""))

    projects_text = ""
    for p in projects_info:
        projects_text += f"\n- {p['name']} en {p.get('location', '')}: {p.get('available_units', 0)} unidades disponibles, precios desde ${p.get('min_price', '?')} hasta ${p.get('max_price', '?')}. {p.get('description', '')}"

    # Estado actual del lead
    qual_status = []
    if lead.get('tiene_dinero_separacion'): qual_status.append("Tiene dinero para separacion")
    if lead.get('tiene_credito'): qual_status.append("Tiene/puede acceder a credito hipotecario")
    if lead.get('tiene_subsidio'): qual_status.append("Tiene subsidio de caja de compensacion")
    if lead.get('puede_cubrir_faltante'): qual_status.append("Puede cubrir el faltante")

    system_prompt = f"""Eres un asesor inmobiliario amigable y profesional de una constructora. Tu nombre es Sofia.
Tu objetivo es ayudar a las personas a encontrar su hogar ideal y cualificarlos como compradores potenciales.

PROYECTOS DISPONIBLES:{projects_text if projects_text else " (No hay proyectos disponibles en este momento)"}

INFORMACION DEL LEAD:
- Nombre: {lead.get('name', 'Cliente')}
- Etapa actual: {lead.get('stage', 'nuevo')}
- Cualificacion: {', '.join(qual_status) if qual_status else 'Sin cualificar aun'}

INSTRUCCIONES:
1. Responde de manera natural, calida y en espanol colombiano.
2. Presenta los proyectos de forma atractiva cuando sea relevante.
3. A lo largo de la conversacion, trata de identificar NATURALMENTE (sin ser invasivo):
   - Si tienen listo el dinero para la cuota de separacion/reserva
   - Si ya tienen un credito hipotecario aprobado o si pueden acceder a uno
   - Si cuentan con subsidio de caja de compensacion familiar
   - Si pueden cubrir la diferencia entre el credito y el precio
4. Cuando identifiques informacion financiera, responde con [QUAL:campo=valor] al FINAL del mensaje (el sistema lo procesara, no lo mostrara al cliente):
   - [QUAL:dinero_separacion=1] cuando confirmen tener cuota inicial/separacion
   - [QUAL:credito=1] cuando confirmen tener o poder acceder a credito hipotecario
   - [QUAL:subsidio=1] cuando confirmen tener subsidio de caja
   - [QUAL:faltante=1] cuando confirmen poder cubrir el faltante
5. Si el lead esta completamente cualificado (tiene todo), termina con [QUAL:listo=1]
6. Mensajes cortos y conversacionales (maximo 3-4 lineas).
7. NO menciones precios exactos a menos que te los pregunten directamente."""

    # Construir historial
    history = []
    for m in msgs[-10:]:
        role = "user" if m['direction'] == 'inbound' else "assistant"
        history.append({"role": role, "content": m['body']})

    # Agregar mensaje actual si no esta ya
    if not history or history[-1]['content'] != user_message:
        history.append({"role": "user", "content": user_message})

    client = ant.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-haiku-20240307",
        max_tokens=300,
        system=system_prompt,
        messages=history
    )
    reply_text = response.content[0].text

    # Procesar tags de cualificacion
    import re as _re
    qual_tags = _re.findall(r'\[QUAL:(\w+)=(\w+)\]', reply_text)
    for key, val in qual_tags:
        if key == 'dinero_separacion' and val == '1':
            db().execute("UPDATE leads SET tiene_dinero_separacion=1 WHERE id=?", (lead['id'],))
        elif key == 'credito' and val == '1':
            db().execute("UPDATE leads SET tiene_credito=1 WHERE id=?", (lead['id'],))
        elif key == 'subsidio' and val == '1':
            db().execute("UPDATE leads SET tiene_subsidio=1 WHERE id=?", (lead['id'],))
        elif key == 'faltante' and val == '1':
            db().execute("UPDATE leads SET puede_cubrir_faltante=1 WHERE id=?", (lead['id'],))
        elif key == 'listo' and val == '1':
            db().execute("UPDATE leads SET stage='calificacion' WHERE id=?", (lead['id'],))
    if qual_tags:
        db().commit()

    # Remover tags del mensaje final
    clean_reply = _re.sub(r'\[QUAL:[^\]]+\]', '', reply_text).strip()
    return clean_reply


# ─── WhatsApp ──────────────────────────────────────────────────────────────────

def get_wa_config():
    r = db().execute('SELECT * FROM whatsapp_config WHERE id=1').fetchone()
    return dict(r) if r else {}

@app.route('/api/whatsapp/config', methods=['GET', 'POST'])
@require_auth
def whatsapp_config():
    if g.user.get('role') != 'admin':
        return jsonify(error='Sin permiso'), 403
    if request.method == 'GET':
        cfg = get_wa_config()
        # Never return auth_token in full
        if cfg.get('auth_token'):
            cfg['auth_token'] = cfg['auth_token'][:6] + '••••••••'
        return jsonify(cfg)
    d = request.get_json()
    existing = get_wa_config()
    auth_token = d.get('auth_token', '')
    if auth_token and '••' in auth_token:
        auth_token = existing.get('auth_token', '')  # keep existing
    if existing:
        db().execute('''UPDATE whatsapp_config SET account_sid=?,auth_token=?,from_number=?,anthropic_key=?,updated_at=datetime('now')
                        WHERE id=1''',
                     (d.get('account_sid'), auth_token or existing.get('auth_token'), d.get('from_number'),
                      d.get('anthropic_key') or existing.get('anthropic_key')))
    else:
        db().execute('''INSERT INTO whatsapp_config (id,account_sid,auth_token,from_number,anthropic_key) VALUES (1,?,?,?,?)''',
                     (d.get('account_sid'), auth_token, d.get('from_number'), d.get('anthropic_key')))
    db().commit()
    return jsonify(success=True)

@app.route('/api/whatsapp/send', methods=['POST'])
@require_auth
def whatsapp_send():
    d = request.get_json()
    client_id = d.get('client_id')
    body = d.get('body', '').strip()
    to_number = d.get('to_number', '').strip()
    if not body or not to_number:
        return jsonify(error='Número y mensaje requeridos'), 400

    cfg = get_wa_config()
    if not cfg.get('account_sid') or not cfg.get('auth_token') or not cfg.get('from_number'):
        return jsonify(error='Configura primero las credenciales de Twilio en Configuración → WhatsApp'), 400

    try:
        from twilio.rest import Client as TwilioClient
        twilio = TwilioClient(cfg['account_sid'], cfg['auth_token'])
        to_wa = 'whatsapp:' + (to_number if to_number.startswith('+') else '+' + to_number)
        from_wa = 'whatsapp:' + cfg['from_number']
        msg = twilio.messages.create(body=body, from_=from_wa, to=to_wa)
        # Save message
        db().execute('''INSERT INTO whatsapp_messages (client_id,direction,from_number,to_number,body,status,twilio_sid,user_id)
                        VALUES (?,?,?,?,?,?,?,?)''',
                     (client_id, 'outbound', cfg['from_number'], to_number, body, msg.status, msg.sid, g.user['id']))
        db().commit()
        return jsonify(success=True, sid=msg.sid)
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route('/api/whatsapp/webhook', methods=['POST'])
def whatsapp_webhook():
    """Recibe mensajes entrantes de Twilio WhatsApp"""
    from_number = request.form.get('From', '').replace('whatsapp:', '')
    body = request.form.get('Body', '')
    sid = request.form.get('MessageSid', '')
    # Find client by whatsapp number
    clean = re.sub(r'\D', '', from_number)
    client = db().execute(
        "SELECT id FROM clients WHERE REPLACE(REPLACE(REPLACE(whatsapp,'+',''),'-',''),' ','')=?",
        (clean,)).fetchone()
    client_id = client['id'] if client else None
    db().execute('''INSERT INTO whatsapp_messages (client_id,direction,from_number,body,status,twilio_sid)
                    VALUES (?,?,?,?,?,?)''',
                 (client_id, 'inbound', from_number, body, 'received', sid))
    db().commit()
    return '<Response></Response>', 200, {'Content-Type': 'text/xml'}

@app.route('/api/whatsapp/messages/<int:client_id>', methods=['GET'])
@require_auth
def whatsapp_messages(client_id):
    msgs = rows(db().execute('''SELECT m.*, u.name as user_name FROM whatsapp_messages m
                                LEFT JOIN users u ON m.user_id=u.id
                                WHERE m.client_id=? ORDER BY m.created_at ASC''', (client_id,)))
    return jsonify(msgs)


# Inicializar DB y rutas SPA al importar el módulo (necesario para gunicorn)
init_db()
register_spa_route()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, port=port, host='0.0.0.0')
