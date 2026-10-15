// Sets the theme before anything is painted, so that a light page does not flash dark at
// each load (and the other way round). It is a file of its own because the admin's
// Content-Security-Policy allows no inline script. Plain and small on purpose: it runs
// before the application. The rule is the one of src/lib/theme.ts; keep the two in step.
(function () {
  var choice = null
  try {
    choice = window.localStorage.getItem('wata.theme')
  } catch {
    // Nothing is kept in this browser: the system decides.
  }
  var dark = choice === 'dark' || (choice !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark)
})()
