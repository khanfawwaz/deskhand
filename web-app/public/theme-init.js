/* Applies the chosen theme before the page paints, so there is no flash of the wrong colours.
   Reads the same storage key as the original app, so a person's choice carries over. */
(function () {
  try {
    var s = JSON.parse(localStorage.getItem('pilot.settings') || '{}');
    if (s.theme === 'dark' || s.theme === 'light') document.documentElement.setAttribute('data-theme', s.theme);
  } catch (e) { /* no storage: follow the system */ }
})();
