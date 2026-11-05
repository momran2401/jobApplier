// Applies the saved theme before the first paint so a dark-mode user never sees a cream flash.
try {
  if (localStorage.getItem('theme') === 'dark') {
    document.documentElement.dataset.theme = 'dark';
    var m = document.querySelector('meta[name=theme-color]');
    if (m) m.content = '#13110f';
  }
} catch (e) {}
