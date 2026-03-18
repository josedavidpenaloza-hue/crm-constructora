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
      { page: 'sales',     icon: '💰', label: 'Ventas' },
      { page: 'tasks',     icon: '✅', label: 'Tareas' },
      { page: 'team',      icon: '👷', label: 'Equipo' },
      { page: 'settings',  icon: '⚙️', label: 'Ajustes' },
    ],

    init() {
      window._crmLogout = () => { this.token = ''; this.user = null; };
      window._crmUpdateUser = (token, user) => { this.token = token; this.user = user; };
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
    waConfigOpen: false, waConfig: { account_sid: '', auth_token: '', from_number: '', anthropic_key: '' },

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
    // ── Units (merged to avoid nested x-data with null project reference) ──
    units: [], unitsLoading: false, unitModal: false, editingUnit: null, unitForm: {},
    unitStatusColors: { disponible: 'bg-green-100 text-green-800', reservado: 'bg-yellow-100 text-yellow-800', vendido: 'bg-red-100 text-red-800' },
    fmt, statusLabel, statusStyle, priorityLabel, priorityStyle,
    tasksByStatus(s) { return (this.project?.tasks || []).filter(t => t.status === s); },
    doneTasks() { return (this.project?.tasks || []).filter(t => t.status === 'completado').length; },
    totalTasks() { return (this.project?.tasks || []).length; },
    pct() { const t = this.totalTasks(); return t > 0 ? Math.round(this.doneTasks() / t * 100) : 0; },
    unitSummary() {
      const total = this.units.length;
      const disp  = this.units.filter(u => u.status === 'disponible').length;
      const res   = this.units.filter(u => u.status === 'reservado').length;
      const vend  = this.units.filter(u => u.status === 'vendido').length;
      return { total, disp, res, vend };
    },
    async init() {
      await this.load();
    },
    async load() {
      this.loading = true;
      if (!pid) { this.loading = false; return; }
      const [p, team] = await Promise.all([get('/projects/' + pid), get('/team')]);
      this.project = p; this.team = team || [];
      this.loading = false;
      await this.loadUnits();
    },
    async loadUnits() {
      if (!pid) return;
      this.unitsLoading = true;
      this.units = await get('/projects/' + pid + '/units') || [];
      this.unitsLoading = false;
    },
    openNewUnit() {
      this.editingUnit = null;
      this.unitForm = { unit_number: '', floor: '', area_m2: '', bedrooms: 2, bathrooms: 1, price: '', status: 'disponible', notes: '' };
      this.unitModal = true;
    },
    openEditUnit(u) { this.editingUnit = u; this.unitForm = { ...u }; this.unitModal = true; },
    async saveUnit() {
      if (this.editingUnit) await put('/units/' + this.editingUnit.id, this.unitForm);
      else await post('/projects/' + pid + '/units', this.unitForm);
      await this.loadUnits();
      this.unitModal = false;
    },
    async delUnit(id) {
      if (!confirm('¿Eliminar esta unidad?')) return;
      await del('/units/' + id);
      await this.loadUnits();
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

// ─── Sales Pipeline / Leads ────────────────────────────────────────────────
const STAGES = [
  { id: 'nuevo',        label: 'Nuevo',         color: '#6b7280', bg: '#f3f4f6' },
  { id: 'contactado',   label: 'Contactado',     color: '#2563eb', bg: '#dbeafe' },
  { id: 'seguimiento',  label: 'Seguimiento',    color: '#7c3aed', bg: '#ede9fe' },
  { id: 'visita',       label: 'Visita',         color: '#d97706', bg: '#fef3c7' },
  { id: 'calificacion', label: 'Calificado',     color: '#059669', bg: '#d1fae5' },
  { id: 'negociacion',  label: 'Negociación',    color: '#ea580c', bg: '#ffedd5' },
  { id: 'separacion',   label: 'Separación',     color: '#16a34a', bg: '#dcfce7' },
  { id: 'escriturado',  label: 'Escriturado 🎉', color: '#166534', bg: '#bbf7d0' },
  { id: 'perdido',      label: 'Perdido',        color: '#991b1b', bg: '#fee2e2' },
];
function stageLabel(s) { return (STAGES.find(x=>x.id===s)||{label:s}).label; }
function stageBadge(s) { const st = STAGES.find(x=>x.id===s)||{color:'#6b7280',bg:'#f3f4f6'}; return `background:${st.bg};color:${st.color}`; }
function qualScore(lead) {
  return [lead.tiene_dinero_separacion, lead.tiene_credito, lead.tiene_subsidio||lead.puede_cubrir_faltante].filter(Boolean).length;
}

function leadsPage() {
  return {
    leads: [], projects: [], team: [], units: [],
    loading: true, filterProject: '', filterStage: '', search: '',
    selectedLead: null, leadDetail: null, detailLoading: false,
    showForm: false, editingLead: null,
    form: {},
    reminders: [], remindersOpen: false,
    newReminder: { title: '', due_datetime: '', description: '' },
    newActivity: { type: 'nota', description: '' },
    STAGES,
    stageLabel, stageBadge, qualScore, fmt,

    get filteredLeads() {
      return this.leads.filter(l => {
        if (this.filterProject && l.project_id != this.filterProject) return false;
        if (this.filterStage && l.stage !== this.filterStage) return false;
        if (this.search) {
          const s = this.search.toLowerCase();
          if (!l.name.toLowerCase().includes(s) && !(l.phone||'').includes(s) && !(l.email||'').toLowerCase().includes(s)) return false;
        }
        return true;
      });
    },

    get stageColumns() {
      return STAGES.filter(s => s.id !== 'perdido').map(s => ({
        ...s,
        leads: this.filteredLeads.filter(l => l.stage === s.id)
      }));
    },

    async init() {
      this.loading = true;
      const [leads, projects, team] = await Promise.all([get('/leads'), get('/projects'), get('/team')]);
      this.leads = leads || [];
      this.projects = projects || [];
      this.team = team || [];
      this.loading = false;
      this.loadReminders();
    },

    async loadReminders() {
      this.reminders = await get('/reminders') || [];
    },

    async loadDetail(id) {
      this.detailLoading = true;
      this.leadDetail = await get('/leads/' + id);
      this.detailLoading = false;
    },

    async openLead(lead) {
      this.selectedLead = lead;
      await this.loadDetail(lead.id);
      if (lead.project_id) {
        this.units = await get('/projects/' + lead.project_id + '/units') || [];
      }
    },

    closeLead() { this.selectedLead = null; this.leadDetail = null; },

    openNew() {
      this.editingLead = null;
      this.form = { name:'', phone:'', whatsapp:'', email:'', project_id:'', unit_id:'', stage:'nuevo', source:'directo',
        tiene_dinero_separacion:false, tiene_credito:false, tipo_credito:'', tiene_subsidio:false,
        caja_compensacion:'', puede_cubrir_faltante:false, budget:'', next_contact:'', notes:'', assigned_to:'' };
      this.showForm = true;
    },

    openEdit(lead) {
      this.editingLead = lead;
      this.form = { ...lead, tiene_dinero_separacion: !!lead.tiene_dinero_separacion,
        tiene_credito: !!lead.tiene_credito, tiene_subsidio: !!lead.tiene_subsidio,
        puede_cubrir_faltante: !!lead.puede_cubrir_faltante };
      this.showForm = true;
    },

    async save() {
      const payload = { ...this.form,
        tiene_dinero_separacion: this.form.tiene_dinero_separacion ? 1 : 0,
        tiene_credito: this.form.tiene_credito ? 1 : 0,
        tiene_subsidio: this.form.tiene_subsidio ? 1 : 0,
        puede_cubrir_faltante: this.form.puede_cubrir_faltante ? 1 : 0,
      };
      if (this.editingLead) await put('/leads/' + this.editingLead.id, payload);
      else await post('/leads', payload);
      this.showForm = false;
      await this.init();
      if (this.selectedLead) await this.loadDetail(this.selectedLead.id);
    },

    async moveStage(lead, stage) {
      await put('/leads/' + lead.id, { ...lead, stage });
      lead.stage = stage;
      if (this.leadDetail && this.leadDetail.id === lead.id) {
        this.leadDetail.stage = stage;
        await this.loadDetail(lead.id);
      }
    },

    async addActivity() {
      if (!this.newActivity.description) return;
      await post('/leads/' + this.selectedLead.id + '/activities', this.newActivity);
      this.newActivity = { type: 'nota', description: '' };
      await this.loadDetail(this.selectedLead.id);
    },

    async addReminder() {
      if (!this.newReminder.title || !this.newReminder.due_datetime) return;
      await post('/reminders', { ...this.newReminder, lead_id: this.selectedLead.id });
      this.newReminder = { title: '', due_datetime: '', description: '' };
      await this.loadDetail(this.selectedLead.id);
      await this.loadReminders();
    },

    async doneReminder(id) {
      await put('/reminders/' + id, {});
      await this.loadReminders();
      if (this.selectedLead) await this.loadDetail(this.selectedLead.id);
    },

    waLink(lead) {
      const num = ((lead||{}).whatsapp || (lead||{}).phone || '').replace(/\D/g,'');
      return num ? `https://wa.me/${num}` : null;
    },

    isOverdue(r) { return r.due_datetime && new Date(r.due_datetime) < new Date(); },

    activityIcon(type) {
      return { nota:'📝', llamada:'📞', whatsapp:'💬', email:'✉️', visita:'🏠', etapa:'🔄', ai_whatsapp:'🤖' }[type] || '📝';
    },

    async onProjectChange() {
      if (this.form.project_id) {
        this.units = await get('/projects/' + this.form.project_id + '/units') || [];
      } else { this.units = []; }
    },
  };
}

// ─── Project Units ─────────────────────────────────────────────────────────────
function projectUnits(pid) {
  return {
    units: [], loading: true, modal: false, editing: null,
    form: {},
    statusColors: { disponible:'bg-green-100 text-green-800', reservado:'bg-yellow-100 text-yellow-800', vendido:'bg-red-100 text-red-800' },
    fmt,
    async init() { await this.load(); },
    async load() {
      this.loading = true;
      this.units = await get('/projects/' + pid + '/units') || [];
      this.loading = false;
    },
    openNew() {
      this.editing = null;
      this.form = { unit_number:'', floor:'', area_m2:'', bedrooms:2, bathrooms:1, price:'', status:'disponible', notes:'' };
      this.modal = true;
    },
    openEdit(u) { this.editing = u; this.form = {...u}; this.modal = true; },
    async save() {
      if (this.editing) await put('/units/' + this.editing.id, this.form);
      else await post('/projects/' + pid + '/units', this.form);
      await this.load();
      this.modal = false;
    },
    async del(id) {
      if (!confirm('¿Eliminar esta unidad?')) return;
      await del('/units/' + id);
      await this.load();
    },
    summary() {
      const total = this.units.length;
      const disp = this.units.filter(u=>u.status==='disponible').length;
      const res = this.units.filter(u=>u.status==='reservado').length;
      const vend = this.units.filter(u=>u.status==='vendido').length;
      return { total, disp, res, vend };
    }
  };
}

// ─── Settings ─────────────────────────────────────────────────────────────────
function settingsPage() {
  return {
    // Perfil
    profile: { name: '', email: '' },
    profileLoading: false, profileSaved: false, profileError: '',

    // Contraseña
    pw: { current_password: '', new_password: '', confirm: '' },
    pwLoading: false, pwSaved: false, pwError: '',

    async init() {
      const u = JSON.parse(localStorage.getItem('user') || 'null');
      if (u) this.profile = { name: u.name, email: u.email };
    },

    async saveProfile() {
      this.profileLoading = true; this.profileSaved = false; this.profileError = '';
      const data = await api('PUT', '/auth/me', {
        name: this.profile.name,
        email: this.profile.email,
      });
      this.profileLoading = false;
      if (data?.error) { this.profileError = data.error; return; }
      // Actualizar localStorage y estado global
      if (data?.token) {
        localStorage.setItem('token', data.token);
        localStorage.setItem('user', JSON.stringify(data.user));
        window._crmUpdateUser && window._crmUpdateUser(data.token, data.user);
      }
      this.profileSaved = true;
      setTimeout(() => this.profileSaved = false, 3000);
    },

    async savePassword() {
      this.pwError = '';
      if (this.pw.new_password !== this.pw.confirm) {
        this.pwError = 'Las contraseñas nuevas no coinciden'; return;
      }
      if (this.pw.new_password.length < 6) {
        this.pwError = 'La contraseña debe tener al menos 6 caracteres'; return;
      }
      this.pwLoading = true; this.pwSaved = false;
      const data = await api('PUT', '/auth/me', {
        name: this.profile.name,
        email: this.profile.email,
        current_password: this.pw.current_password,
        new_password: this.pw.new_password,
      });
      this.pwLoading = false;
      if (data?.error) { this.pwError = data.error; return; }
      if (data?.token) {
        localStorage.setItem('token', data.token);
        localStorage.setItem('user', JSON.stringify(data.user));
        window._crmUpdateUser && window._crmUpdateUser(data.token, data.user);
      }
      this.pw = { current_password: '', new_password: '', confirm: '' };
      this.pwSaved = true;
      setTimeout(() => this.pwSaved = false, 3000);
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
