// Adapted from Magic UI "Shine Border" (magicui.design), recoloured to near-white at low alpha.
// Keyframes live in components/landing/landing.css; the sweep runs three slow passes and stops.
export function ShineBorder() {
  return (
    <div
      aria-hidden="true"
      className="ui-shine-border pointer-events-none absolute inset-0 rounded-[inherit]"
      style={{
        padding: 1,
        backgroundImage: 'radial-gradient(transparent, transparent, rgba(255,255,255,0.28), transparent, transparent)',
        backgroundSize: '300% 300%',
        mask: 'linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)',
        WebkitMask: 'linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)',
        maskComposite: 'exclude',
        WebkitMaskComposite: 'xor',
      }}
    />
  )
}
