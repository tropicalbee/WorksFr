export const CHECK_INPUT_ID = 'check-url'

/** Scrolls to the landing page's URL box and focuses it. Returns false when it is not on the page. */
export function focusCheckInput(): boolean {
  const input = document.getElementById(CHECK_INPUT_ID)
  if (!input) return false
  input.scrollIntoView({ block: 'center' })
  input.focus({ preventScroll: true })
  return true
}
