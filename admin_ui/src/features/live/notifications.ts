const PREFERENCE_KEY = 'wata-admin-notifications'

export function notificationsSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window
}

function preference(): boolean {
  try {
    return window.localStorage.getItem(PREFERENCE_KEY) === 'on'
  } catch {
    return false
  }
}

/** On when the operator asked for them here and the browser still allows them. */
export function notificationsEnabled(): boolean {
  return notificationsSupported() && Notification.permission === 'granted' && preference()
}

/** Turn browser notifications on (asking the browser for permission if needed) or off. Returns the new state. */
export async function setNotificationsEnabled(enabled: boolean): Promise<boolean> {
  if (!notificationsSupported()) return false
  if (enabled && Notification.permission !== 'granted') {
    const permission = await Notification.requestPermission()
    if (permission !== 'granted') return false
  }
  try {
    window.localStorage.setItem(PREFERENCE_KEY, enabled ? 'on' : 'off')
  } catch {
    return false
  }
  return enabled
}
