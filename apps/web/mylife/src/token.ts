const KEY = 'mylife_token'

export function initToken(): void {
  const token = new URLSearchParams(location.search).get('token')
  if (token) sessionStorage.setItem(KEY, token)
}

export function getToken(): string {
  return sessionStorage.getItem(KEY) ?? ''
}
