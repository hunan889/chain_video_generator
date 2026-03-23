const ModuleLoader = {
  _cache: {},      // { moduleName: htmlString }
  _active: null,   // currently active module name
  _cleanups: {},   // { moduleName: cleanupFn }
  _loaded: {},     // { moduleName: true } — tracks modules whose scripts have executed

  async load(name, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    // Run cleanup for current module
    if (this._active && this._cleanups[this._active]) {
      try { this._cleanups[this._active](); } catch(e) { console.warn('Module cleanup error:', e); }
    }

    // Hide all module wrappers
    for (const child of container.children) {
      child.style.display = 'none';
    }

    // If module already loaded into DOM, just show it
    const existingWrapper = document.getElementById('module-wrapper-' + name);
    if (existingWrapper) {
      existingWrapper.style.display = '';
      this._active = name;
      return;
    }

    // Fetch HTML (use cache if available)
    if (!this._cache[name]) {
      try {
        const r = await fetch('/static/modules/' + name + '.html');
        if (!r.ok) { console.error('Failed to load module:', name); return; }
        this._cache[name] = await r.text();
      } catch(e) { console.error('Module fetch error:', e); return; }
    }

    // Create wrapper div and inject HTML
    const wrapper = document.createElement('div');
    wrapper.id = 'module-wrapper-' + name;
    wrapper.innerHTML = this._cache[name];
    container.appendChild(wrapper);

    // Re-execute <script> tags (innerHTML doesn't auto-execute them)
    const scriptPromises = [];
    wrapper.querySelectorAll('script').forEach(oldScript => {
      const newScript = document.createElement('script');
      Array.from(oldScript.attributes).forEach(attr => newScript.setAttribute(attr.name, attr.value));
      if (oldScript.src || oldScript.getAttribute('src')) {
        // External script: wait for it to load
        const p = new Promise((resolve, reject) => {
          newScript.onload = resolve;
          newScript.onerror = reject;
        });
        scriptPromises.push(p);
      } else {
        newScript.textContent = oldScript.textContent;
      }
      oldScript.parentNode.replaceChild(newScript, oldScript);
    });

    // Wait for all external scripts to load before calling init
    if (scriptPromises.length > 0) {
      try { await Promise.all(scriptPromises); } catch(e) { console.error('Script load error:', e); }
    }

    // Call module init function
    const initFn = window['__init_' + name];
    if (typeof initFn === 'function') {
      try { initFn(); } catch(e) { console.error('Module init error:', e); }
    }

    this._loaded[name] = true;
    this._active = name;
  },

  registerCleanup(name, fn) {
    this._cleanups[name] = fn;
  },

  invalidateCache(name) {
    if (name) {
      delete this._cache[name];
      // Also remove DOM wrapper so it gets re-created on next load
      const wrapper = document.getElementById('module-wrapper-' + name);
      if (wrapper) wrapper.remove();
      delete this._loaded[name];
    } else {
      this._cache = {};
      // Remove all module wrappers
      document.querySelectorAll('[id^="module-wrapper-"]').forEach(el => el.remove());
      this._loaded = {};
    }
  }
};
