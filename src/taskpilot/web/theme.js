/* Applies the chosen theme before the page paints, so there is no flash of the wrong colours. */
(function () {
  try {
    var s = JSON.parse(localStorage.getItem('pilot.settings') || '{}');
    if (s.theme === 'dark' || s.theme === 'light') document.documentElement.setAttribute('data-theme', s.theme);
  } catch (e) { /* no storage: follow the system */ }
})();
