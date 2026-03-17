from flask import Flask, request, jsonify, render_template, g
import sqlite3
import bcrypt
import jwt
import os
from functools import wraps
from database import get_db, init_db

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
JWT_SECRET = os.environ.get('JWT_SECRET', 'crm-constructora-secret-2026')


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
        'INSERT INTO clients (name,email,phone,address,city,rfc,notes,created_by) VALUES (?,?,?,?,?,?,?,?)',
        (d.get('name'), d.get('email'), d.get('phone'), d.get('address'),
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
        db().execute('UPDATE clients SET name=?,email=?,phone=?,address=?,city=?,rfc=?,notes=? WHERE id=?',
                     (d.get('name'), d.get('email'), d.get('phone'), d.get('address'),
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


if __name__ == '__main__':
    init_db()
    register_spa_route()
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, port=port, host='0.0.0.0')
