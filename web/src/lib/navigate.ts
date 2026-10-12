/** Leaves the app for another site (Google's consent screen). A function so tests can replace it. */
export function redirectTo(url: string): void {
  window.location.assign(url);
}
