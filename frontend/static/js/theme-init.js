// This runs before the page draws, so you never see a flash of the wrong theme.
(function () {
  try {
    var saved = localStorage.getItem('kasa_theme');
    var theme = saved ? JSON.parse(saved) : null;
    if (theme !== 'light' && theme !== 'dark') {
      theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    document.documentElement.setAttribute('data-theme', theme);
    var collapsed = JSON.parse(localStorage.getItem('kasa_sidebar_collapsed') || 'false');
    if (collapsed && window.innerWidth >= 900) document.documentElement.classList.add('sidebar-collapsed');
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();
