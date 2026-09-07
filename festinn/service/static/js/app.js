/** FestIn Monitor - Alpine.js single-page application */

// Login Form Component
function loginForm() {
  return {
    username: '',
    password: '',
    error: '',

    async login() {
      try {
        const resp = await fetch('/api/v1/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: this.username, password: this.password })
        });
        if (!resp.ok) {
          const data = await resp.json();
          this.error = data.detail || 'Invalid credentials';
          return;
        }
        const data = await resp.json();
        localStorage.setItem('festin_token', data.access_token);
        window.dispatchEvent(new CustomEvent('auth-changed', { detail: true }));
      } catch (e) {
        this.error = e.message || 'Login failed';
      }
    }
  };
}

// FestIn Main App Component  
function festinApp() {
  return {
    authenticated: false,
    username: '',
    view: 'dashboard',
    overview: null,
    newDomainName: '',

    async init() {
      const token = localStorage.getItem('festin_token');
      if (!token) return;
      try {
        const resp = await fetch('/api/v1/auth/me', {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (resp.ok) {
          const data = await resp.json();
          this.authenticated = true;
          this.username = data.username || 'Admin';
          await this.fetchOverview();
          setInterval(() => this.$refresh(), 30000);
        }
      } catch {
        localStorage.removeItem('festin_token');
      }
    },

    async $refresh() {
      if (!this.authenticated) return;
      await this.fetchOverview();
    },

    async fetchOverview() {
      const token = localStorage.getItem('festin_token');
      try {
        const resp = await fetch('/api/v1/scans/overview', {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (resp.ok) this.overview = await resp.json();
      } catch {}
    },

    async addDomain() {
      const name = this.newDomainName.trim();
      if (!name) return;
      const token = localStorage.getItem('festin_token');
      try {
        const resp = await fetch('/api/v1/domains/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
          body: JSON.stringify({ domain_name: name })
        });
        if (resp.ok) { this.newDomainName = ''; await this.fetchOverview(); }
      } catch {}
    },

    async removeDomain(domainId) {
      const token = localStorage.getItem('festin_token');
      try {
        const resp = await fetch(`/api/v1/domains/remove/${domainId}`, {
          method: 'DELETE',
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (resp.ok) await this.fetchOverview();
      } catch {}
    },

    async rescanDomain(dom) {
      const token = localStorage.getItem('festin_token');
      try {
        const resp = await fetch(`/api/v1/domains/rescan/${dom.id}`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (resp.ok) setTimeout(() => this.$refresh(), 5000);
      } catch {}
    },

    logout() {
      localStorage.removeItem('festin_token');
      this.authenticated = false;
      window.dispatchEvent(new CustomEvent('auth-changed', { detail: false }));
      location.reload();
    }
  };
}

document.addEventListener('DOMContentLoaded', () => {});
