// ─── API helper ───────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const token = localStorage.getItem('token');
  const res = await fetch('/api' + path, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: 'Bearer ' + token } : {})
    },
    body: body ? JSON.stringify(body) : undefined
  });
  if (res.status === 401) {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    window._crmLogout && window._crmLogout();
    return null;
  }
  return res.json();
}
const get  = path      => api('GET',    path);
const post = (path, b) => api('POST',   path, b);
const put  = (path, b) => api('PUT',    path, b);
const del  = path      => api('DELETE', path);

// ─── Helpers ──────────────────────────────────────────────────────────────────
const PROJECT_STATUS = {
  cotizacion: { label: 'Cotización', bg: '#dbeafe', color: '#1e40af' },
  en_proceso: { label: 'En proceso', bg: '#fef9c3', color: '#854d0e' },
  completado:  { label: 'Completado', bg: '#dcfce7', color: '#166534' },
  cancelado:   { label: 'Cancelado',  bg: '#fee2e2', color: '#991b1b' },
};
const PRIORITY = {
  baja:  { label: 'Baja',  bg: '#f3f4f6', color: '#6b7280' },
  media: { label: 'Media', bg: '#dbeafe', color: '#1d4ed8' },
  alta:  { label: 'Alta',  bg: '#fee2e2', color: '#b91c1c' },
};
const ROLES = {
  admin:   { label: 'Administrador', bg: '#f3e8ff', color: '#7c3aed' },
  manager: { label: 'Gerente',       bg: '#dbeafe', color: '#1d4ed8' },
  staff:   { label: 'Colaborador',   bg: '#f3f4f6', color: '#6b7280' },
};
function fmt(n) {
  if (!n) return '—';
  return n >= 1000000 ? '$' + (n / 1000000).toFixed(1) + 'M' : '$' + (n / 1000).toFixed(0) + 'K';
}
function statusLabel(s) { return (PROJECT_STATUS[s] || { label: s }).label; }
function statusStyle(s) {
  const st = PROJECT_STATUS[s] || { bg: '#f3f4f6', color: '#374151' };
  return `background:${st.bg};color:${st.color}`;
}
function priorityLabel(s) { return (PRIORITY[s] || { label: s }).label; }
function priorityStyle(s) {
  const p = PRIORITY[s] || { bg: '#f3f4f6', color: '#374151' };
  return `background:${p.bg};color:${p.color}`;
}
function roleLabel(s) { return (ROLES[s] || { label: s }).label; }
function roleStyle(s) {
  const r = ROLES[s] || { bg: '#f3f4f6', color: '#374151' };
  return `background:${r.bg};color:${r.color}`;
}

// ─── Root CRM – owns all navigation and auth state ───────────────────────────
function crm() {
  return {
    // Auth state – initialized from localStorage immediately (no timing issue)
    token: localStorage.getItem('token') || '',
    user:  JSON.parse(localStorage.getItem('user') || 'null'),

    // Navigation state
    page:      'dashboard',
    projectId: null,

    // UI
    mobileOpen: false,
    loginForm:  { email: 'admin@constructora.com', password: 'admin123' },
    loginError: '',
    loginLoading: false,

    navItems: [
      { page: 'dashboard', icon: '📊', label: 'Dashboard' },
      { page: 'clients',   icon: '👥', label: 'Clientes' },
      { page: 'projects',  icon: '🏗️', label: 'Proyectos' },
      { page: 'tasks',     icon: '✅', label: 'Tareas' },
      { page: 'team',      icon: '👷', label: 'Equipo' },
    ],

    init() {
      // Expose logout for API helper
      window._crmLogout = () => { this.token = ''; this.user = null; };
      // Listen for child navigation events
      window.addEventListener('crm:navigate', e => {
        this.page = e.detail.page;
        if (e.detail.projectId) this.projectId = e.detail.projectId;
        this.mobileOpen = false;
      });
    },

    go(p) {
      this.page = p;
      this.mobileOpen = false;
    },
    goProject(id) {
      this.projectId = id;
      this.page = 'project-detail';
      this.mobileOpen = false;
    },

    async doLogin() {
      this.loginLoading = true;
      this.loginError = '';
      const data = await post('/auth/login', this.loginForm);
      this.loginLoading = false;
      if (data && data.token) {
        this.token = data.token;
        this.user  = data.user;
        localStorage.setItem('token', data.token);
        localStorage.setItem('user', JSON.stringify(data.user));
        this.page = 'dashboard';
      } else {
        this.loginError = (data && data.error) || 'Error al iniciar sesión';
      }
    },

    doLogout() {
      this.token = '';
      this.user  = null;
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      this.page = 'dashboard';
    }
  };
}

// ─── Helper for child components to navigate ─────────────────────────────────
function navigate(page, projectId) {
  window.dispatchEvent(new CustomEvent('crm:navigate', { detail: { page, projectId } }));
}

// ─── Dashboard ────────────────────────────────────────────────────────────────
function dashboardPage() {
  return {
    loading: true, data: null,
    fmt, statusLabel, statusStyle,
    async init() { await this.load(); },
    async load() {
      this.loading = true;
      this.data = await get('/dashboard');
      this.loading = false;
    }
  };
}

// ─── Clients ──────────────────────────────────────────────────────────────────
function clientsPage() {
  return {
    // Lista y búsqueda
    items: [], loading: true, modal: false, editing: null, search: '',
    form: {},

    // WhatsApp chat embebido
    waClient: null,

    // Importador
    imp: {
      open: false, tab: 'file', gsUrl: '', loading: false,
      result: null, error: '', dragOver: false, selectedFile: null,
    },

    get filtered() {
      const s = this.search.toLowerCase();
      return this.items.filter(c =>
        c.name.toLowerCase().includes(s) ||
        (c.city  || '').toLowerCase().includes(s) ||
        (c.email || '').toLowerCase().includes(s)
      );
    },

    async init() { await this.load(); },

    async load() {
      this.loading = true;
      this.items = await get('/clients') || [];
      this.loading = false;
    },

    // ── CRUD ──
    openNew() {
      this.editing = null;
      this.form = { name: '', email: '', phone: '', whatsapp: '', address: '', city: '', rfc: '', notes: '' };
      this.modal = true;
    },
    openEdit(c) { this.editing = c; this.form = { ...c }; this.modal = true; },
    async save() {
      if (this.editing) await put('/clients/' + this.editing.id, this.form);
      else await post('/clients', this.form);
      await this.load();
      this.modal = false;
    },
    async del(id) {
      if (!confirm('¿Eliminar este cliente?')) return;
      await del('/clients/' + id);
      await this.load();
    },

    // ── Importador ──
    impShow()  { this.imp = { ...this.imp, open: true, result: null, error: '', selectedFile: null, gsUrl: '' }; },
    impHide()  { this.imp.open = false; },
    impDrop(e) { this.imp.dragOver = false; this.imp.selectedFile = e.dataTransfer.files[0] || null; },
    impFile(e) { this.imp.selectedFile = e.target.files[0] || null; },
    downloadTemplate() { window.open('/api/clients/import/template', '_blank'); },

    async importFile() {
      if (!this.imp.selectedFile) { this.imp.error = 'Selecciona un archivo'; return; }
      this.imp.loading = true; this.imp.error = ''; this.imp.result = null;
      const fd = new FormData();
      fd.append('file', this.imp.selectedFile);
      const token = localStorage.getItem('token');
      try {
        const res = await fetch('/api/clients/import', {
          method: 'POST',
          headers: { Authorization: 'Bearer ' + token },
          body: fd
        });
        const data = await res.json();
        if (data.error) this.imp.error = data.error;
        else { this.imp.result = data; await this.load(); }
      } catch(e) { this.imp.error = 'Error de conexión'; }
      this.imp.loading = false;
    },

    async importGSheets() {
      if (!this.imp.gsUrl) { this.imp.error = 'Pega la URL del Google Sheet'; return; }
      this.imp.loading = true; this.imp.error = ''; this.imp.result = null;
      const token = localStorage.getItem('token');
      try {
        const res = await fetch('/api/clients/import', {
          method: 'POST',
          headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
          body: JSON.stringify({ gsheets_url: this.imp.gsUrl })
        });
        const data = await res.json();
        if (data.error) this.imp.error = data.error;
        else { this.imp.result = data; await this.load(); }
      } catch(e) { this.imp.error = 'Error de conexión'; }
      this.imp.loading = false;
    },

    // ── WhatsApp ──
    waLink(c) {
      const num = ((c || this.waClient)?.whatsapp || '').replace(/\D/g, '');
      return num ? `https://wa.me/${num}` : null;
    },
    waMessages: [], waLoading: false, waNew: '', waSending: false, waError: '',
    waConfigOpen: false, waConfig: { account_sid: '', auth_token: '', from_number: '' },

    async openWa(c) {
      this.waClient = c; this.waMessages = []; this.waLoading = true; this.waError = '';
      this.waMessages = await get('/whatsapp/messages/' + c.id) || [];
      this.waLoading = false;
    },
    async waSend() {
      if (!this.waNew.trim()) return;
      const num = (this.waClient?.whatsapp || '').replace(/\D/g, '');
      if (!num) { this.waError = 'El cliente no tiene número de WhatsApp'; return; }
      this.waSending = true; this.waError = '';
      const data = await post('/whatsapp/send', { client_id: this.waClient.id, to_number: num, body: this.waNew.trim() });
      this.waSending = false;
      if (data?.error) this.waError = data.error;
      else { this.waNew = ''; this.waMessages = await get('/whatsapp/messages/' + this.waClient.id) || []; }
    },
    async waLoadConfig() {
      const d = await get('/whatsapp/config');
      if (d) this.waConfig = d;
      this.waConfigOpen = true;
    },
    async waSaveConfig() {
      await post('/whatsapp/config', this.waConfig);
      this.waConfigOpen = false;
    }
  };
}

// ─── Projects ─────────────────────────────────────────────────────────────────
function projectsPage() {
  return {
    items: [], clients: [], team: [], loading: true,
    modal: false, editing: null, search: '', filter: 'all',
    form: {},
    filters: [
      { v: 'all', l: 'Todos' }, { v: 'cotizacion', l: 'Cotización' },
      { v: 'en_proceso', l: 'En proceso' }, { v: 'completado', l: 'Completado' },
      { v: 'cancelado', l: 'Cancelado' }
    ],
    get filtered() {
      return this.items
        .filter(p => this.filter === 'all' || p.status === this.filter)
        .filter(p =>
          p.name.toLowerCase().includes(this.search.toLowerCase()) ||
          (p.client_name || '').toLowerCase().includes(this.search.toLowerCase())
        );
    },
    fmt, statusLabel, statusStyle,
    async init() { await this.load(); },
    async load() {
      this.loading = true;
      const [projs, clients, team] = await Promise.all([get('/projects'), get('/clients'), get('/team')]);
      this.items = projs || []; this.clients = clients || []; this.team = team || [];
      this.loading = false;
    },
    openNew() {
      this.editing = null;
      this.form = { name: '', client_id: '', status: 'cotizacion', budget: '', start_date: '', end_date: '', location: '', description: '', assigned_to: '' };
      this.modal = true;
    },
    openEdit(p) {
      this.editing = p;
      this.form = { ...p, client_id: p.client_id || '', assigned_to: p.assigned_to || '' };
      this.modal = true;
    },
    async save() {
      if (this.editing) await put('/projects/' + this.editing.id, this.form);
      else await post('/projects', this.form);
      await this.load();
      this.modal = false;
    },
    async del(id) {
      if (!confirm('¿Eliminar este proyecto y todas sus tareas?')) return;
      await del('/projects/' + id);
      await this.load();
    }
  };
}

// ─── Project Detail ───────────────────────────────────────────────────────────
function projectDetailPage(pid) {
  return {
    project: null, team: [], loading: true,
    taskModal: false, editingTask: null, taskForm: {},
    kanbanCols: [
      { status: 'pendiente',  label: 'Pendientes',  border: 'border-gray-300' },
      { status: 'en_proceso', label: 'En proceso',  border: 'border-yellow-400' },
      { status: 'completado', label: 'Completadas', border: 'border-green-400' },
    ],
    fmt, statusLabel, statusStyle, priorityLabel, priorityStyle,
    tasksByStatus(s) { return (this.project?.tasks || []).filter(t => t.status === s); },
    doneTasks() { return (this.project?.tasks || []).filter(t => t.status === 'completado').length; },
    totalTasks() { return (this.project?.tasks || []).length; },
    pct() { const t = this.totalTasks(); return t > 0 ? Math.round(this.doneTasks() / t * 100) : 0; },
    async init() {
      await this.load();
    },
    async load() {
      this.loading = true;
      if (!pid) { this.loading = false; return; }
      const [p, team] = await Promise.all([get('/projects/' + pid), get('/team')]);
      this.project = p; this.team = team || [];
      this.loading = false;
    },
    openTask(t) {
      this.editingTask = t;
      this.taskForm = t
        ? { ...t, assigned_to: t.assigned_to || '' }
        : { title: '', description: '', assigned_to: '', status: 'pendiente', priority: 'media', due_date: '' };
      this.taskModal = true;
    },
    async saveTask() {
      if (this.editingTask) await put('/tasks/' + this.editingTask.id, { ...this.taskForm, project_id: pid });
      else await post('/tasks', { ...this.taskForm, project_id: pid });
      await this.load();
      this.taskModal = false;
    },
    async delTask(id) {
      if (!confirm('¿Eliminar esta tarea?')) return;
      await del('/tasks/' + id);
      await this.load();
    }
  };
}

// ─── Tasks ────────────────────────────────────────────────────────────────────
function tasksPage() {
  return {
    items: [], loading: true, filter: 'all', myUserId: null,
    filters: [
      { v: 'all',        l: 'Todas' },
      { v: 'mine',       l: 'Mis tareas' },
      { v: 'pendiente',  l: 'Pendientes' },
      { v: 'en_proceso', l: 'En proceso' },
      { v: 'completado', l: 'Completadas' }
    ],
    get filtered() {
      return this.items.filter(t =>
        this.filter === 'all'  ? true :
        this.filter === 'mine' ? t.assigned_to === this.myUserId :
        t.status === this.filter
      );
    },
    priorityLabel, priorityStyle,
    isOverdue(t) { return t.due_date && t.status !== 'completado' && new Date(t.due_date) < new Date(); },
    async init() {
      const u = JSON.parse(localStorage.getItem('user') || 'null');
      this.myUserId = u?.id;
      await this.load();
    },
    async load() {
      this.loading = true;
      this.items = await get('/tasks') || [];
      this.loading = false;
    },
    async changeStatus(task, status) {
      await put('/tasks/' + task.id, { ...task, status });
      task.status = status;
    }
  };
}

// ─── Team ─────────────────────────────────────────────────────────────────────
function teamPage() {
  return {
    items: [], loading: true, modal: false, editing: null, form: {},
    myRole: JSON.parse(localStorage.getItem('user') || 'null')?.role,
    myId:   JSON.parse(localStorage.getItem('user') || 'null')?.id,
    roleLabel, roleStyle,
    async init() { await this.load(); },
    async load() {
      this.loading = true;
      this.items = await get('/team') || [];
      this.loading = false;
    },
    openNew() { this.editing = null; this.form = { name: '', email: '', password: '', role: 'staff' }; this.modal = true; },
    openEdit(u) { this.editing = u; this.form = { ...u, password: '' }; this.modal = true; },
    async save() {
      if (this.editing) await put('/team/' + this.editing.id, this.form);
      else await post('/auth/register', this.form);
      await this.load();
      this.modal = false;
    },
    async toggle(u) {
      await put('/team/' + u.id, { ...u, active: u.active ? 0 : 1 });
      await this.load();
    }
  };
}
