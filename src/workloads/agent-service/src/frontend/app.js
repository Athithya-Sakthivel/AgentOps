function logout() {
  localStorage.removeItem('app_jwt');
  fetch('/auth/logout');
  window.location.href = '/';
}